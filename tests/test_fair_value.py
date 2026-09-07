"""Tests du modèle de juste valeur de l'or.

Trois propriétés sont vérifiées, par ordre d'importance :

1. **Absence de look-ahead.** Le modèle estimé à la date ``t`` doit donner
   exactement le même résultat, que l'historique fourni s'arrête à ``t`` ou
   se prolonge de plusieurs années. C'est la propriété critique : un modèle
   qui regarde le futur produit un backtest flatteur et un signal inutile.
2. **Exactitude du z-score**, contrôlée sur des échantillons dont le
   résultat se calcule à la main.
3. **Honnêteté du drapeau de fiabilité** : un modèle qui n'explique rien
   doit le dire.

Aucun test n'accède au réseau : toutes les séries sont fabriquées.

Exécution :
    pytest tests/test_fair_value.py -v
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from modules.gold import fair_value


# ---------------------------------------------------------------------------
# Données factices
# ---------------------------------------------------------------------------
def _donnees_synthetiques(n: int = 1200, graine: int = 20260907) -> pd.DataFrame:
    """Fabrique un historique où l'or dépend vraiment des deux facteurs.

    Le prix est construit à partir de la relation que le modèle cherche à
    retrouver, plus un bruit : la régression doit donc afficher un R² élevé
    et des coefficients de signe négatif, ce qui rend les tests de
    fiabilité significatifs.

    Args:
        n: nombre de séances.
        graine: graine du générateur, pour un résultat reproductible.

    Returns:
        DataFrame au format attendu par :func:`fair_value.preparer_donnees`.
    """
    alea = np.random.default_rng(graine)
    dates = pd.bdate_range("2018-01-01", periods=n, name="date")

    # Marches aléatoires bornées, plausibles pour un taux réel et un indice.
    # L'amplitude du taux doit rester réaliste tout en variant assez, sur une
    # fenêtre de deux ans, pour que la régression ait quelque chose à
    # expliquer : un régresseur quasi constant donnerait un R² faible sans
    # que le modèle soit en cause, et le test de fiabilité ne testerait rien.
    taux = np.cumsum(alea.normal(0.0, 0.02, n)) + 0.5
    dollar = 100.0 * np.exp(np.cumsum(alea.normal(0.0, 0.002, n)))

    # Relation vraie : -12 % d'or par point de taux réel, -0,8 % par % de dollar.
    log_or = 7.4 - 0.12 * taux - 0.8 * np.log(dollar / 100.0) + alea.normal(0.0, 0.015, n)

    return pd.DataFrame(
        {
            fair_value.COL_OR: np.exp(log_or),
            fair_value.COL_TAUX: taux,
            fair_value.COL_DOLLAR: dollar,
        },
        index=dates,
    )


# ---------------------------------------------------------------------------
# 1. Absence de look-ahead
# ---------------------------------------------------------------------------
def test_estimation_identique_sur_historique_tronque() -> None:
    """Le modèle à la date t ignore tout ce qui vient après t.

    C'est le test central du module. L'historique complet est comparé à un
    historique coupé net à la date d'estimation : les deux doivent produire
    des chiffres rigoureusement identiques, pas seulement proches.
    """
    complet = _donnees_synthetiques(1200)

    for position in (400, 700, 1000, 1199):
        date_test = complet.index[position]
        tronque = complet.loc[:date_test]

        sur_complet = fair_value.estimate_fair_value(complet, date=date_test, fenetre=252)
        sur_tronque = fair_value.estimate_fair_value(tronque, date=date_test, fenetre=252)

        assert sur_complet.disponible and sur_tronque.disponible

        for champ in ("prix_theorique", "ecart_usd", "ecart_pct", "z_score", "r2"):
            valeur_complet = getattr(sur_complet, champ)
            valeur_tronque = getattr(sur_tronque, champ)
            assert valeur_complet == pytest.approx(valeur_tronque, abs=1e-12), (
                f"Fuite de données futures sur « {champ} » au {date_test.date()} : "
                f"{valeur_complet} avec le futur, {valeur_tronque} sans."
            )

        assert sur_complet.coefficients == pytest.approx(sur_tronque.coefficients, abs=1e-12)


def test_perturbation_du_futur_sans_effet_sur_le_present() -> None:
    """Modifier violemment les barres futures ne change rien à l'estimation.

    Ce test attrape des fuites que la troncature laisserait passer, par
    exemple une normalisation par un extremum calculé sur tout l'échantillon.
    """
    reference = _donnees_synthetiques(1000)
    coupure = 700
    date_test = reference.index[coupure]

    perturbe = reference.copy()
    perturbe.iloc[coupure + 1 :, perturbe.columns.get_loc(fair_value.COL_OR)] *= 4.0
    perturbe.iloc[coupure + 1 :, perturbe.columns.get_loc(fair_value.COL_TAUX)] += 3.0
    perturbe.iloc[coupure + 1 :, perturbe.columns.get_loc(fair_value.COL_DOLLAR)] *= 1.5

    avant = fair_value.estimate_fair_value(reference, date=date_test, fenetre=252)
    apres = fair_value.estimate_fair_value(perturbe, date=date_test, fenetre=252)

    assert avant.z_score == pytest.approx(apres.z_score, abs=1e-12)
    assert avant.prix_theorique == pytest.approx(apres.prix_theorique, abs=1e-12)
    assert avant.r2 == pytest.approx(apres.r2, abs=1e-12)


def test_historique_glissant_causal() -> None:
    """Chaque ligne de l'historique vaut ce qu'aurait donné une estimation ce jour-là."""
    donnees = _donnees_synthetiques(900)
    historique = fair_value.fair_value_history(donnees, fenetre=252, min_observations=252)

    assert not historique.empty
    assert historique.index.is_monotonic_increasing

    # Trois dates tirées de l'historique sont recalculées une par une.
    for date_test in (historique.index[0], historique.index[len(historique) // 2], historique.index[-1]):
        ponctuelle = fair_value.estimate_fair_value(
            donnees.loc[:date_test], date=date_test, fenetre=252, min_observations=252
        )
        assert ponctuelle.disponible
        assert float(historique.loc[date_test, "z_score"]) == pytest.approx(
            ponctuelle.z_score, abs=1e-12
        )
        assert float(historique.loc[date_test, "prix_theorique"]) == pytest.approx(
            ponctuelle.prix_theorique, rel=1e-12
        )


# ---------------------------------------------------------------------------
# 2. Z-score sur données synthétiques au résultat connu
# ---------------------------------------------------------------------------
def test_zscore_valeur_connue_analytiquement() -> None:
    """Le z-score est vérifié sur des cas dont le résultat se calcule à la main.

    Sur ``[1, 2, 3, 4, 5]`` : moyenne 3, écart-type de population
    ``sqrt(2) ≈ 1,4142``. Le z-score de 5 vaut donc ``2 / sqrt(2) = sqrt(2)``.
    """
    echantillon = np.array([1.0, 2.0, 3.0, 4.0, 5.0])

    assert fair_value.zscore(5.0, echantillon) == pytest.approx(math.sqrt(2.0), abs=1e-12)
    assert fair_value.zscore(1.0, echantillon) == pytest.approx(-math.sqrt(2.0), abs=1e-12)
    assert fair_value.zscore(3.0, echantillon) == pytest.approx(0.0, abs=1e-12)
    assert fair_value.zscore(4.0, echantillon) == pytest.approx(1.0 / math.sqrt(2.0), abs=1e-12)

    # Loi normale centrée réduite de grande taille : le z-score d'une valeur
    # doit retrouver approximativement cette valeur.
    alea = np.random.default_rng(1234)
    normale = alea.normal(0.0, 1.0, 200_000)
    assert fair_value.zscore(2.0, normale) == pytest.approx(2.0, abs=0.02)


def test_zscore_cas_degeneres() -> None:
    """Un échantillon vide ou constant renvoie zéro, jamais une division par zéro."""
    assert fair_value.zscore(5.0, np.array([])) == 0.0
    assert fair_value.zscore(5.0, np.array([3.0, 3.0, 3.0])) == 0.0
    assert fair_value.zscore(1.0, np.array([np.nan, np.nan])) == 0.0
    # Les valeurs non finies sont écartées avant le calcul.
    assert fair_value.zscore(5.0, np.array([1.0, 2.0, 3.0, 4.0, 5.0, np.nan])) == pytest.approx(
        math.sqrt(2.0), abs=1e-12
    )


def test_zscore_coherent_avec_le_residu_du_modele() -> None:
    """Le z-score publié est bien celui du résidu, calculé sur la fenêtre."""
    donnees = _donnees_synthetiques(800)
    lecture = fair_value.estimate_fair_value(donnees, fenetre=252)

    assert lecture.disponible
    # Le résidu du jour rapporté à la dispersion des résidus : par
    # construction des moindres carrés, la moyenne des résidus est nulle,
    # donc le z-score vaut résidu / écart-type.
    residu = math.log(lecture.prix_observe) - math.log(lecture.prix_theorique)
    assert abs(lecture.z_score) == pytest.approx(abs(residu) / (abs(residu) / abs(lecture.z_score)), rel=1e-9)
    assert math.copysign(1.0, lecture.z_score) == math.copysign(1.0, residu)


# ---------------------------------------------------------------------------
# 3. Fiabilité, contributions, dégradation
# ---------------------------------------------------------------------------
def test_r2_eleve_et_coefficients_de_bon_signe() -> None:
    """Sur des données construites selon la relation, le modèle la retrouve."""
    donnees = _donnees_synthetiques(1000)
    lecture = fair_value.estimate_fair_value(donnees, fenetre=504)

    assert lecture.disponible and lecture.fiable
    assert lecture.r2 > 0.5
    # Taux réel et dollar pèsent négativement sur l'or : c'est la thèse même
    # du modèle, et les données ont été fabriquées ainsi.
    assert lecture.coefficients["taux_reel"] < 0.0
    assert lecture.coefficients["dollar"] < 0.0


def test_modele_sans_pouvoir_explicatif_marque_non_fiable() -> None:
    """Un or indépendant de ses facteurs doit être signalé, pas interprété."""
    alea = np.random.default_rng(7)
    n = 800
    donnees = pd.DataFrame(
        {
            # Prix purement aléatoire : aucun lien avec les deux régresseurs.
            fair_value.COL_OR: 2000.0 * np.exp(np.cumsum(alea.normal(0.0, 0.01, n))),
            fair_value.COL_TAUX: alea.normal(1.0, 0.3, n),
            fair_value.COL_DOLLAR: alea.normal(100.0, 2.0, n),
        },
        index=pd.bdate_range("2019-01-01", periods=n, name="date"),
    )

    lecture = fair_value.estimate_fair_value(donnees, fenetre=504, seuil_r2=0.5)
    assert lecture.disponible, "Le modèle doit produire un résultat, même peu explicatif."
    assert not lecture.fiable, f"R² de {lecture.r2:.3f} : le modèle devait être marqué non fiable."
    assert "modèle n'explique plus" in lecture.lecture()


def test_contributions_additives_en_logarithme() -> None:
    """Les contributions reconstituent exactement le prix théorique.

    Par construction des moindres carrés avec constante, la somme de la
    moyenne de la fenêtre et des deux contributions doit redonner le
    logarithme du prix théorique.
    """
    donnees = _donnees_synthetiques(900)
    fenetre = 504
    lecture = fair_value.estimate_fair_value(donnees, fenetre=fenetre)

    assert lecture.disponible
    moyenne_log = float(np.log(donnees[fair_value.COL_OR].iloc[-fenetre:]).mean())
    somme = (
        moyenne_log
        + lecture.contributions["taux_reel"]["contribution_log"]
        + lecture.contributions["dollar"]["contribution_log"]
    )
    assert somme == pytest.approx(math.log(lecture.prix_theorique), abs=1e-10)
    assert lecture.facteur_dominant in ("taux_reel", "dollar")


def test_percentile_historique_calcule_sans_regarder_le_futur() -> None:
    """Le percentile de l'écart n'utilise que les écarts antérieurs."""
    donnees = _donnees_synthetiques(1000)
    historique = fair_value.fair_value_history(donnees, fenetre=252, min_observations=252)
    date_test = historique.index[-200]

    lecture = fair_value.estimate_fair_value(
        donnees, date=date_test, fenetre=252, ecarts_historiques=historique["ecart_pct"]
    )
    assert lecture.percentile_historique is not None

    # Le même appel avec l'historique déjà coupé doit donner le même rang.
    coupe = historique.loc[:date_test, "ecart_pct"]
    lecture_coupee = fair_value.estimate_fair_value(
        donnees, date=date_test, fenetre=252, ecarts_historiques=coupe
    )
    assert lecture.percentile_historique == pytest.approx(
        lecture_coupee.percentile_historique, abs=1e-12
    )
    assert 0.0 <= lecture.percentile_historique <= 100.0


# ---------------------------------------------------------------------------
# 4. Dégradation gracieuse
# ---------------------------------------------------------------------------
def test_donnees_absentes_ou_insuffisantes() -> None:
    """Sans données exploitables, le module renvoie un motif, pas une exception."""
    vide = fair_value.estimate_fair_value(pd.DataFrame())
    assert not vide.disponible and vide.motif

    trop_court = _donnees_synthetiques(50)
    lecture = fair_value.estimate_fair_value(trop_court, min_observations=250)
    assert not lecture.disponible
    assert "observation" in lecture.motif
    assert lecture.to_dict()["disponible"] is False


def test_preparation_aligne_et_ecarte_les_valeurs_impossibles() -> None:
    """La préparation joint sur les dates communes et retire prix et indices non positifs."""
    index_or = pd.bdate_range("2024-01-01", periods=10, name="date")
    prix = pd.Series(np.linspace(2000.0, 2100.0, 10), index=index_or)
    # Le taux ne couvre que six dates : la jointure doit se limiter à celles-là.
    taux = pd.Series(np.linspace(1.0, 1.5, 6), index=index_or[:6])
    dollar = pd.Series(np.linspace(100.0, 102.0, 10), index=index_or)

    prepare = fair_value.preparer_donnees(prix, taux, dollar)
    assert len(prepare) == 6
    assert list(prepare.columns) == [fair_value.COL_OR, fair_value.COL_TAUX, fair_value.COL_DOLLAR]

    # Un prix nul rend le logarithme indéfini : la ligne doit disparaître.
    prix_abime = prix.copy()
    prix_abime.iloc[2] = 0.0
    assert len(fair_value.preparer_donnees(prix_abime, taux, dollar)) == 5
