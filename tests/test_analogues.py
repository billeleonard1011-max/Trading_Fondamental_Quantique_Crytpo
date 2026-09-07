"""Tests des précédents historiques.

Les deux garde-fous du module sont ce qui distingue une statistique d'une
anecdote, et ce sont eux que ces tests visent en priorité :

1. **Séparation minimale** — sans elle, quinze jours consécutifs de la même
   semaine passeraient pour quinze précédents indépendants, et l'intervalle
   de confiance serait faux d'un facteur quatre ;
2. **Nombre minimal de cas** — sous le seuil, le module doit refuser de
   conclure plutôt que publier une médiane sur trois observations.

Un troisième point est vérifié : les rendements futurs et le drawdown, sur
des séries de prix dont le résultat se calcule à la main.

Aucun test n'accède au réseau.

Exécution :
    pytest tests/test_analogues.py -v
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from modules.gold import analogues


# ---------------------------------------------------------------------------
# Fabriques
# ---------------------------------------------------------------------------
def _historique_fv(dates: pd.DatetimeIndex, z: np.ndarray, taux: np.ndarray) -> pd.DataFrame:
    """Fabrique un historique de juste valeur au format attendu.

    Args:
        dates: index des dates.
        z: z-scores de juste valeur.
        taux: niveaux de taux réel.

    Returns:
        DataFrame minimal accepté par :func:`analogues.construire_base`.
    """
    return pd.DataFrame({"z_score": z, "taux_reel": taux}, index=dates)


#: Écart imposé entre deux précédents dans les tests de séparation. Les
#: grappes durent vingt séances, soit environ vingt-huit jours calendaires :
#: un seuil de quarante jours garantit qu'au plus un précédent survit par
#: grappe, ce qui rend le décompte attendu exact et non approximatif.
SEPARATION_TEST = 40


def _base_avec_grappe() -> tuple[pd.DataFrame, dict[str, float]]:
    """Construit une base où les configurations proches sont regroupées.

    Trois grappes de vingt séances consécutives portent un z-score voisin de
    la cible ; tout le reste de l'historique en est très éloigné. Sans filtre
    de séparation, les quinze précédents les plus proches sortiraient tous de
    la première grappe.

    Les trois grappes sont volontairement à des distances *différentes* de la
    cible. Si elles étaient équidistantes, les quinze plus proches seraient
    des ex aequo répartis arbitrairement entre les grappes, et le test ne
    mesurerait plus le filtre mais l'ordre de tri.

    Returns:
        Couple ``(base, etat_du_jour)``.
    """
    n = 900
    dates = pd.bdate_range("2015-01-01", periods=n, name="date")

    # Fond très éloigné de la cible.
    z = np.full(n, -3.0)
    # Trois grappes contiguës, la première étant la plus proche de la cible.
    for depart, decalage in ((100, 0.00), (400, 0.10), (700, 0.20)):
        z[depart : depart + 20] = 2.0 + decalage + np.linspace(0.0, 0.02, 20)

    taux = np.full(n, 1.0)
    prix = pd.Series(100.0 * np.exp(np.cumsum(np.full(n, 0.0005))), index=dates)

    base, _ = analogues.construire_base(
        _historique_fv(dates, z, taux), prix, cot=None, debut="2015-01-01"
    )
    return base, {"z_fair_value": 2.0, "regime_taux_reels": 1.0}


# ---------------------------------------------------------------------------
# 1. Garde-fou de séparation
# ---------------------------------------------------------------------------
def test_precedents_separes_dans_le_temps() -> None:
    """Deux précédents retenus ne peuvent pas appartenir à la même semaine."""
    base, etat = _base_avec_grappe()
    separation = SEPARATION_TEST

    resultat = analogues.find_analogues(
        etat, base, n=15, separation_min_jours=separation, min_cas=3
    )
    assert resultat["disponible"], resultat["motif"]

    dates = sorted(pd.Timestamp(p["date"]) for p in resultat["precedents"])
    for precedente, suivante in zip(dates, dates[1:]):
        ecart = (suivante - precedente).days
        assert ecart >= separation, (
            f"Deux précédents distants de {ecart} jour(s) seulement "
            f"({precedente.date()} et {suivante.date()}) : la même configuration "
            "est comptée plusieurs fois."
        )


def test_sans_separation_les_precedents_se_collent() -> None:
    """Preuve par contraste : sans filtre, les précédents sont consécutifs.

    Ce test ne valide pas un comportement souhaitable — il démontre que le
    filtre de séparation corrige un défaut réel, et non théorique. On demande
    trois précédents : sans filtre, les trois sortent de la même grappe ;
    avec filtre, ils viennent de trois grappes distinctes.
    """
    base, etat = _base_avec_grappe()

    sans_filtre = analogues.find_analogues(
        etat, base, n=3, separation_min_jours=0, min_cas=3
    )
    dates = sorted(pd.Timestamp(p["date"]) for p in sans_filtre["precedents"])
    etendue_sans_filtre = (dates[-1] - dates[0]).days

    avec_filtre = analogues.find_analogues(
        etat, base, n=3, separation_min_jours=SEPARATION_TEST, min_cas=3
    )
    dates_filtrees = sorted(pd.Timestamp(p["date"]) for p in avec_filtre["precedents"])
    etendue_avec_filtre = (dates_filtrees[-1] - dates_filtrees[0]).days

    assert etendue_sans_filtre <= 7, (
        f"Les trois précédents non filtrés s'étalent sur {etendue_sans_filtre} jours : "
        "ils devaient tous provenir de la même grappe."
    )
    assert etendue_avec_filtre > 300, (
        f"Les précédents filtrés ne couvrent que {etendue_avec_filtre} jours : "
        "le filtre devait forcer un précédent par grappe."
    )


def test_separation_impose_un_ecart_entre_precedents_voisins() -> None:
    """Le filtre écarte réellement des candidats plus proches au profit d'autres.

    Sans filtre, les trois précédents les plus proches sont trois séances
    consécutives. Avec filtre, deux d'entre eux disparaissent au profit de
    dates éloignées : le filtre ne se contente donc pas de réordonner, il
    substitue.
    """
    base, etat = _base_avec_grappe()

    sans = analogues.find_analogues(etat, base, n=3, separation_min_jours=0, min_cas=3)
    avec = analogues.find_analogues(
        etat, base, n=3, separation_min_jours=SEPARATION_TEST, min_cas=3
    )

    dates_sans = {p["date"] for p in sans["precedents"]}
    dates_avec = {p["date"] for p in avec["precedents"]}

    assert sans["n_cas"] == 3 and avec["n_cas"] == 3
    assert len(dates_sans & dates_avec) == 1, (
        "Un seul précédent de la grappe la plus proche devait survivre au filtre."
    )
    # La distance moyenne augmente forcément : on renonce à des voisins proches.
    distance_sans = sum(p["distance"] for p in sans["precedents"])
    distance_avec = sum(p["distance"] for p in avec["precedents"])
    assert distance_avec > distance_sans, (
        "Le filtre doit coûter en proximité ce qu'il gagne en indépendance."
    )


# ---------------------------------------------------------------------------
# 2. Garde-fou du nombre minimal de cas
# ---------------------------------------------------------------------------
def test_refus_sous_le_nombre_minimal_de_cas() -> None:
    """Sous le seuil, le module refuse de conclure et dit pourquoi.

    L'historique est volontairement court : quatre-vingts séances, soit
    environ cent seize jours calendaires, ne peuvent pas contenir dix
    précédents séparés de quarante jours. Le module doit le constater et
    s'abstenir, au lieu de publier une médiane sur trois cas.
    """
    n = 80
    dates = pd.bdate_range("2016-01-01", periods=n, name="date")
    alea = np.random.default_rng(21)
    prix = pd.Series(100.0 * np.exp(np.cumsum(alea.normal(0.0, 0.01, n))), index=dates)

    base, _ = analogues.construire_base(
        _historique_fv(dates, alea.normal(0.0, 1.0, n), np.linspace(0.5, 1.5, n)),
        prix,
        cot=None,
        debut="2016-01-01",
        horizons=(1, 5, 20),
    )

    resultat = analogues.find_analogues(
        {"z_fair_value": 0.0, "regime_taux_reels": 1.0},
        base,
        n=15,
        separation_min_jours=SEPARATION_TEST,
        min_cas=10,
    )
    assert not resultat["disponible"]
    assert resultat["agregation"] == {}
    assert resultat["precedents"] == []
    assert "requis" in resultat["motif"]
    assert "10" in resultat["motif"]


def test_refus_sur_base_vide() -> None:
    """Une base vide donne un refus explicite, pas une exception."""
    resultat = analogues.find_analogues({"z_fair_value": 1.0}, pd.DataFrame())
    assert not resultat["disponible"]
    assert resultat["motif"]


def test_refus_si_aucune_variable_commune() -> None:
    """Un état du jour sans variable descriptive commune est refusé."""
    base, _ = _base_avec_grappe()
    resultat = analogues.find_analogues({"variable_inconnue": 1.0}, base, min_cas=3)
    assert not resultat["disponible"]
    assert "commune" in resultat["motif"]


# ---------------------------------------------------------------------------
# 3. Rendements futurs et drawdown
# ---------------------------------------------------------------------------
def test_rendements_futurs_exacts() -> None:
    """Sur une croissance géométrique constante, les rendements sont connus."""
    n = 400
    dates = pd.bdate_range("2016-01-01", periods=n, name="date")
    # +1 % par séance exactement.
    prix = pd.Series(100.0 * (1.01 ** np.arange(n)), index=dates)

    base, _ = analogues.construire_base(
        _historique_fv(dates, np.zeros(n), np.ones(n)),
        prix,
        cot=None,
        debut="2016-01-01",
        horizons=(1, 5, 20),
    )
    assert not base.empty

    ligne = base.iloc[0]
    assert float(ligne["rendement_1j"]) == pytest.approx((1.01 - 1) * 100.0, abs=1e-9)
    assert float(ligne["rendement_5j"]) == pytest.approx((1.01**5 - 1) * 100.0, abs=1e-9)
    assert float(ligne["rendement_20j"]) == pytest.approx((1.01**20 - 1) * 100.0, abs=1e-9)

    # Série strictement croissante : aucune excursion sous le prix d'entrée.
    assert float(ligne["drawdown_max_pct"]) == pytest.approx(0.0, abs=1e-9)


def test_drawdown_capte_la_pire_excursion() -> None:
    """Le drawdown mesure la pire baisse sous le prix d'entrée, pas l'écart final."""
    n = 200
    dates = pd.bdate_range("2017-01-01", periods=n, name="date")
    valeurs = np.full(n, 100.0)
    # Creux à -10 % au cinquième jour, puis retour au point de départ.
    valeurs[5] = 90.0

    base, _ = analogues.construire_base(
        _historique_fv(dates, np.zeros(n), np.ones(n)),
        pd.Series(valeurs, index=dates),
        cot=None,
        debut="2017-01-01",
        horizons=(1, 5, 20),
    )

    premiere = base.iloc[0]
    # Le prix revient à son niveau : le rendement à 20 jours est nul...
    assert float(premiere["rendement_20j"]) == pytest.approx(0.0, abs=1e-9)
    # ...mais le trajet est passé 10 % plus bas, et c'est ce que le trader subit.
    assert float(premiere["drawdown_max_pct"]) == pytest.approx(-10.0, abs=1e-9)


def test_dates_trop_recentes_exclues_de_la_base() -> None:
    """Les dates sans rendement complet à l'horizon le plus long sont écartées.

    Les garder biaiserait toutes les statistiques vers zéro, puisque leur
    rendement serait tronqué.
    """
    n = 300
    dates = pd.bdate_range("2018-01-01", periods=n, name="date")
    prix = pd.Series(100.0 * (1.001 ** np.arange(n)), index=dates)

    base, _ = analogues.construire_base(
        _historique_fv(dates, np.zeros(n), np.ones(n)),
        prix,
        cot=None,
        debut="2018-01-01",
        horizons=(1, 5, 20),
    )
    # Les vingt dernières séances ne peuvent pas avoir de rendement à 20 jours.
    assert len(base) == n - 20
    assert base.index.max() == dates[n - 21]
    assert base["rendement_20j"].notna().all()


# ---------------------------------------------------------------------------
# 4. Normalisation et agrégation
# ---------------------------------------------------------------------------
def test_normalisation_empeche_une_variable_de_dominer() -> None:
    """Le percentile COT, coté de 0 à 100, ne doit pas écraser le z-score.

    La vérification est directe : les distances renvoyées sont comparées à
    celles que l'on calcule à la main **sur les variables normalisées**. Si
    le module oubliait de normaliser, le percentile — cinquante fois plus
    étendu que le z-score — dicterait à lui seul le classement, et les
    distances mesurées ne correspondraient pas.
    """
    n = 40
    dates = pd.bdate_range("2016-01-01", periods=n, name="date")

    # Deux moitiés bien séparées : les écarts-types sont ainsi non nuls et
    # très différents d'une variable à l'autre, ce qui est tout l'enjeu.
    z = np.concatenate([np.zeros(n // 2), np.full(n // 2, 2.0)])
    percentile = np.concatenate([np.zeros(n // 2), np.full(n // 2, 100.0)])

    base = pd.DataFrame(
        {
            "z_fair_value": z,
            "percentile_cot": percentile,
            "rendement_1j": np.zeros(n),
            "rendement_5j": np.zeros(n),
            "rendement_20j": np.zeros(n),
            "drawdown_max_pct": np.zeros(n),
        },
        index=dates,
    )

    cible = {"z_fair_value": 1.0, "percentile_cot": 50.0}
    resultat = analogues.find_analogues(
        cible, base, n=n, separation_min_jours=0, min_cas=2
    )
    assert resultat["disponible"], resultat["motif"]

    moyennes = base[["z_fair_value", "percentile_cot"]].mean()
    ecarts = base[["z_fair_value", "percentile_cot"]].std(ddof=0)
    assert float(ecarts["percentile_cot"]) > 40.0 * float(ecarts["z_fair_value"]), (
        "Les deux variables doivent avoir des échelles très différentes, "
        "sinon le test ne prouve rien."
    )

    for precedent in resultat["precedents"]:
        ligne = base.loc[pd.Timestamp(precedent["date"])]
        attendue = float(
            np.sqrt(
                sum(
                    (
                        (float(ligne[variable]) - float(moyennes[variable])) / float(ecarts[variable])
                        - (cible[variable] - float(moyennes[variable])) / float(ecarts[variable])
                    )
                    ** 2
                    for variable in ("z_fair_value", "percentile_cot")
                )
            )
        )
        assert precedent["distance"] == pytest.approx(attendue, abs=1e-4), (
            f"Distance {precedent['distance']} au {precedent['date']} alors que la "
            f"distance normalisée vaut {attendue:.4f} : la normalisation n'a pas été appliquée."
        )


def test_agregation_publie_letendue_et_la_proportion() -> None:
    """L'agrégation expose la dispersion, pas seulement la médiane."""
    n = 600
    dates = pd.bdate_range("2015-01-01", periods=n, name="date")
    alea = np.random.default_rng(11)
    prix = pd.Series(100.0 * np.exp(np.cumsum(alea.normal(0.0, 0.01, n))), index=dates)

    base, absentes = analogues.construire_base(
        _historique_fv(dates, alea.normal(0.0, 1.0, n), np.ones(n)),
        prix,
        cot=None,
        debut="2015-01-01",
    )
    assert "percentile_cot" in absentes
    assert "stress_geopolitique" in absentes

    resultat = analogues.find_analogues(
        {"z_fair_value": 0.5, "regime_taux_reels": 1.0},
        base,
        n=15,
        separation_min_jours=21,
        min_cas=5,
    )
    assert resultat["disponible"], resultat["motif"]

    bloc = resultat["agregation"]["20j"]
    assert bloc["disponible"]
    assert bloc["pire_pct"] <= bloc["rendement_median_pct"] <= bloc["meilleur_pct"]
    assert bloc["etendue_pct"] == pytest.approx(bloc["meilleur_pct"] - bloc["pire_pct"], abs=1e-6)
    assert 0.0 <= bloc["proportion_haussiers"] <= 1.0
    assert resultat["agregation"]["drawdown"]["median_pct"] <= 0.0


def test_variables_absentes_signalees_et_non_inventees() -> None:
    """Une variable sans historique est retirée et signalée, jamais remplacée."""
    n = 200
    dates = pd.bdate_range("2019-01-01", periods=n, name="date")
    prix = pd.Series(np.linspace(100.0, 120.0, n), index=dates)

    base, absentes = analogues.construire_base(
        _historique_fv(dates, np.zeros(n), np.ones(n)),
        prix,
        cot=None,
        stress_geopolitique=None,
        debut="2019-01-01",
    )
    assert set(absentes) == {"percentile_cot", "stress_geopolitique"}
    assert "percentile_cot" not in base.columns
    assert "stress_geopolitique" not in base.columns
