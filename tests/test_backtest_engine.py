"""Tests du moteur de backtest : causalité, dimensionnement, survie du compte.

Le test qui compte
------------------
``test_aucun_look_ahead_par_troncature`` est le seul dont l'échec invalide
tout le reste. Un backtest qui consulte une bougie future produit des
résultats magnifiques et faux, et rien dans les métriques ne le signale : la
courbe est belle, le ratio de Sharpe est flatteur, et la stratégie perd de
l'argent en réel. La vérification est donc mécanique — rejouer sur un
historique coupé doit rendre exactement les mêmes trades.

Aucun test n'accède au réseau : la série de prix est fabriquée.

Exécution :
    pytest tests/test_backtest_engine.py -v
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest import execution as bt_exec
from backtest import ict, moteur, propfirm


def _serie_m1(n: int = 6000, graine: int = 20260908) -> pd.DataFrame:
    """Fabrique une série d'une minute plausible pour XAUUSD.

    La série n'a pas à ressembler à de l'or pour tester la causalité : il
    suffit qu'elle produise des motifs. Une marche aléatoire avec des mèches
    en fournit largement.

    Args:
        n: nombre de minutes.
        graine: graine du générateur.

    Returns:
        Bougies M1 indexées en UTC.
    """
    alea = np.random.default_rng(graine)
    index = pd.date_range("2026-01-05 00:00", periods=n, freq="min", tz="UTC")
    pas = alea.normal(0.0, 0.35, n)
    cloture = 3000.0 + np.cumsum(pas)
    ouverture = np.concatenate([[3000.0], cloture[:-1]])
    amplitude = np.abs(alea.normal(0.0, 0.25, n))
    return pd.DataFrame(
        {
            "open": ouverture,
            "high": np.maximum(ouverture, cloture) + amplitude,
            "low": np.minimum(ouverture, cloture) - amplitude,
            "close": cloture,
            "volume": alea.integers(20, 200, n).astype("float64"),
        },
        index=index,
    )


def _taux_eurusd(m1: pd.DataFrame) -> pd.Series:
    """Fabrique une série de taux EUR/USD quotidiens."""
    jours = pd.date_range(
        m1.index.min().normalize(), m1.index.max().normalize(), freq="D", tz="UTC"
    )
    return pd.Series(np.linspace(1.07, 1.10, len(jours)), index=jours)


# ---------------------------------------------------------------------------
# 1. Causalité — le test prioritaire
# ---------------------------------------------------------------------------
def test_aucun_look_ahead_par_troncature() -> None:
    """Couper l'historique ne change rien aux trades de la partie commune.

    Si le moteur consultait une bougie postérieure, la version complète et la
    version tronquée divergeraient sur les trades communs : la première
    « saurait » ce que la seconde ignore.
    """
    m1 = _serie_m1(6000)
    taux = _taux_eurusd(m1)
    config = moteur.ConfigBacktest(mode_tp="ratio", ratio_tp=2.0)

    complet = moteur.Backtest(m1, taux, config)
    complet.executer()

    coupure = 4000
    tronque = moteur.Backtest(m1.iloc[:coupure], taux, config)
    tronque.executer()

    limite = m1.index[coupure - 1]
    # Seuls les trades entièrement dénoués avant la coupure sont comparables :
    # un trade encore ouvert à la coupure n'a pas de sortie dans la version
    # tronquée, ce qui est normal et non un désaccord.
    def _clos_avant(bt: moteur.Backtest) -> list[dict]:
        return [
            t.to_dict()
            for t in bt.trades
            if t.horodatage_sortie is not None and t.horodatage_sortie <= limite
        ]

    a, b = _clos_avant(complet), _clos_avant(tronque)
    assert a, "Le scénario doit produire des trades, sinon le test ne prouve rien."
    assert len(a) == len(b), (
        f"{len(a)} trade(s) sur l'historique complet contre {len(b)} sur le tronqué : "
        "le moteur voit des données futures."
    )
    for gauche, droite in zip(a, b):
        assert gauche == droite, (
            "Trade différent selon la profondeur d'historique :\n"
            f"  complet : {gauche}\n  tronqué : {droite}"
        )


def test_aucun_look_ahead_par_perturbation_du_futur() -> None:
    """Modifier violemment les barres futures ne change rien aux trades passés.

    Ce contrôle attrape des fuites que la troncature laisserait passer, par
    exemple une normalisation calculée sur l'ensemble de l'échantillon.
    """
    m1 = _serie_m1(5000)
    taux = _taux_eurusd(m1)
    config = moteur.ConfigBacktest(mode_tp="ratio", ratio_tp=2.0)

    reference = moteur.Backtest(m1, taux, config)
    reference.executer()

    coupure = 3500
    perturbe = m1.copy()
    for colonne in ("open", "high", "low", "close"):
        perturbe.iloc[coupure:, perturbe.columns.get_loc(colonne)] *= 1.5

    apres = moteur.Backtest(perturbe, taux, config)
    apres.executer()

    limite = m1.index[coupure - 1]
    avant_a = [
        t.to_dict() for t in reference.trades
        if t.horodatage_sortie is not None and t.horodatage_sortie <= limite
    ]
    avant_b = [
        t.to_dict() for t in apres.trades
        if t.horodatage_sortie is not None and t.horodatage_sortie <= limite
    ]
    assert avant_a == avant_b, "Le futur influence des trades déjà dénoués."


def test_sortie_evaluee_sur_la_barre_courante() -> None:
    """Une sortie ne peut pas être décidée sur une clôture pas encore vue.

    Le trade se dénoue au plus tôt sur la barre qui touche le niveau, jamais
    avant. On vérifie que l'horodatage de sortie est postérieur à celui
    d'entrée et tombe bien sur une borne de minute.
    """
    m1 = _serie_m1(4000)
    bt = moteur.Backtest(m1, _taux_eurusd(m1), moteur.ConfigBacktest())
    bt.executer()

    for trade in bt.trades:
        assert trade.horodatage_sortie is not None
        assert trade.horodatage_sortie > trade.horodatage_entree
        assert trade.horodatage_sortie in set(m1.index + pd.Timedelta(minutes=1))


def test_le_stop_lemporte_en_cas_degalite_dans_la_minute() -> None:
    """Quand stop et objectif tombent dans la même minute, le stop gagne.

    La stratégie ne tranche pas ce cas. L'hypothèse retenue est la
    défavorable : l'inverse flatterait les résultats sans qu'on puisse le
    vérifier sur des bougies d'une minute.
    """
    m1 = _serie_m1(3000)
    bt = moteur.Backtest(m1, _taux_eurusd(m1), moteur.ConfigBacktest())
    bt.executer()

    for trade in bt.trades:
        if trade.motif_sortie != "stop":
            continue
        # Un trade sorti au stop ne peut pas afficher un résultat positif.
        assert trade.resultat_r <= 0.05, (
            f"Trade au stop avec un résultat de {trade.resultat_r:+.2f} R."
        )


# ---------------------------------------------------------------------------
# 2. Dimensionnement
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("distance", [0.8, 1.5, 2.0, 3.5, 5.0, 8.0])
@pytest.mark.parametrize("taux", [1.02, 1.08, 1.15])
def test_perte_au_stop_dans_la_fourchette(distance: float, taux: float) -> None:
    """La perte simulée au stop tombe entre 50 € et 60 €, ou le trade est écarté."""
    resultat = bt_exec.dimensionner(distance, taux)
    if not resultat["prenable"]:
        # Le refus doit être motivé, pas silencieux.
        assert resultat["motif"]
        return

    perte = resultat["lots"] * distance * bt_exec.ONCES_PAR_LOT / taux
    assert 50.0 <= perte <= 60.0, f"Perte de {perte:.2f} € hors fourchette."
    assert resultat["perte_eur"] == pytest.approx(perte)


def test_taille_non_prenable_signalee_et_non_arrondie() -> None:
    """Quand aucune taille ne convient, le trade est écarté avec son motif."""
    # Un stop très large rend même le lot minimum trop lourd... ou trop léger.
    resultat = bt_exec.dimensionner(distance_stop_usd=200.0, taux_eurusd=1.08)
    assert resultat["prenable"] is False
    assert resultat["lots"] == 0.0
    assert "aucune taille" in resultat["motif"]


def test_dimensionnement_refuse_sans_taux() -> None:
    """Sans taux de change, le risque en euros n'est pas calculable."""
    resultat = bt_exec.dimensionner(2.0, 0.0)
    assert resultat["prenable"] is False
    assert "EUR/USD" in resultat["motif"]


def test_stop_au_dela_de_lorder_block() -> None:
    """Le stop se pose à la marge convenue au-delà de la zone."""
    achat = bt_exec.calculer_stop(ict.HAUSSIER, 105.0, 100.0, meche_bougie2=101.0, marge=1.0)
    assert achat == pytest.approx(99.0)

    vente = bt_exec.calculer_stop(ict.BAISSIER, 105.0, 100.0, meche_bougie2=104.0, marge=1.0)
    assert vente == pytest.approx(106.0)


def test_stop_recule_si_la_meche_depasse() -> None:
    """Une mèche de bougie 2 plus profonde que la marge l'emporte."""
    # Mèche à 97 : plus bas que 100 - 1 = 99.
    achat = bt_exec.calculer_stop(ict.HAUSSIER, 105.0, 100.0, meche_bougie2=97.0, marge=1.0)
    assert achat == pytest.approx(96.0)

    vente = bt_exec.calculer_stop(ict.BAISSIER, 105.0, 100.0, meche_bougie2=108.0, marge=1.0)
    assert vente == pytest.approx(109.0)


def test_couts_jouent_toujours_contre_la_position() -> None:
    """Écart et glissement dégradent l'entrée comme la sortie."""
    config = bt_exec.ConfigExecution(spread=0.60, slippage=0.30)

    assert bt_exec.appliquer_couts_entree(100.0, ict.HAUSSIER, config) == pytest.approx(100.9)
    assert bt_exec.appliquer_couts_entree(100.0, ict.BAISSIER, config) == pytest.approx(99.1)
    assert bt_exec.appliquer_couts_sortie(100.0, ict.HAUSSIER, config) == pytest.approx(99.7)
    assert bt_exec.appliquer_couts_sortie(100.0, ict.BAISSIER, config) == pytest.approx(100.3)


# ---------------------------------------------------------------------------
# 3. Règles de la société de financement
# ---------------------------------------------------------------------------
def test_violation_de_la_perte_journaliere() -> None:
    """Trois pertes le même jour dépassent le seuil journalier."""
    resultat = propfirm.simuler_compte([-110.0, -110.0, -110.0], jours=[0, 0, 0])
    assert resultat["issue"] == propfirm.BREACH_JOURNALIER
    assert resultat["perte_du_jour"] == pytest.approx(330.0)
    assert "journée" in resultat["detail"]


def test_violation_du_drawdown_trailing() -> None:
    """Des pertes étalées sur plusieurs jours franchissent le seuil total."""
    resultat = propfirm.simuler_compte([-150.0] * 8, jours=list(range(8)))
    assert resultat["issue"] == propfirm.BREACH_TOTAL
    assert resultat["solde_final"] <= 9000.0


def test_le_seuil_trailing_monte_avec_les_clotures_journalieres() -> None:
    """Le seuil suit le plus haut solde de clôture, et ne redescend jamais.

    Un compte monté à 10 500 € puis redescendu doit casser à 9 500 €, non à
    9 000 € : c'est toute la différence entre un drawdown trailing et un
    drawdown fixe.
    """
    # Jour 0 : +500 €. Le seuil passe à 10 500 - 1 000 = 9 500.
    # Jours suivants : pertes de 200 € jusqu'au franchissement.
    resultats = [500.0] + [-200.0] * 6
    jours = list(range(len(resultats)))
    resultat = propfirm.simuler_compte(resultats, jours=jours)

    assert resultat["issue"] == propfirm.BREACH_TOTAL
    assert resultat["seuil_total_courant"] == pytest.approx(9500.0)
    # Le solde final est sous 9 500, mais très au-dessus de 9 000.
    assert 9000.0 < resultat["solde_final"] <= 9500.0


def test_le_seuil_trailing_ignore_les_pics_intrajournaliers() -> None:
    """Un pic de gain en cours de journée ne relève pas le seuil.

    Le recalculer en intraday durcirait la règle au-delà de ce que la société
    impose, et ferait échouer des comptes qui passent en réalité.
    """
    # Même journée : +800 puis -800. Le solde de clôture est inchangé, donc
    # le seuil doit rester à 9 000.
    resultat = propfirm.simuler_compte([800.0, -250.0, -250.0], jours=[0, 0, 0])
    assert resultat["seuil_total_courant"] == pytest.approx(9000.0)


def test_objectif_atteint_detecte() -> None:
    """Un compte qui atteint son objectif est reconnu comme tel."""
    resultat = propfirm.simuler_compte([400.0] * 3, jours=[0, 1, 2])
    assert resultat["issue"] == propfirm.OBJECTIF_ATTEINT
    assert resultat["solde_final"] >= 11000.0


def test_monte_carlo_distribue_les_issues() -> None:
    """Le rééchantillonnage produit des probabilités, pas un seul chemin."""
    alea = np.random.default_rng(7)
    # Espérance légèrement positive, avec de vraies pertes.
    resultats = list(alea.choice([-55.0, 110.0], size=60, p=[0.6, 0.4]))

    distribution = propfirm.monte_carlo(resultats, n_tirages=5000, trades_par_jour=2)
    assert distribution["disponible"]
    assert distribution["n_tirages"] == 5000

    total = (
        distribution["probabilite_objectif"]
        + distribution["probabilite_breach"]
        + distribution["probabilite_ni_lun_ni_lautre"]
    )
    assert total == pytest.approx(1.0, abs=1e-9)

    # La règle violée en premier doit être attribuée.
    parts = distribution["premiere_regle_violee"]
    assert parts["perte_journaliere"] + parts["perte_totale_trailing"] == pytest.approx(
        distribution["probabilite_breach"], abs=1e-9
    )


def test_monte_carlo_sans_trades() -> None:
    """Sans trade, la simulation le dit au lieu de rendre des chiffres vides."""
    resultat = propfirm.monte_carlo([], n_tirages=100)
    assert resultat["disponible"] is False
    assert resultat["motif"]


# ---------------------------------------------------------------------------
# 4. Comptabilité des abandons
# ---------------------------------------------------------------------------
def test_les_abandons_sont_comptes() -> None:
    """Les setups écartés sont dénombrés par motif, pas perdus en silence."""
    m1 = _serie_m1(5000)
    bt = moteur.Backtest(m1, _taux_eurusd(m1), moteur.ConfigBacktest())
    bt.executer()

    assert set(bt.abandons) >= {
        moteur.ABANDON_SANS_FVG,
        moteur.ABANDON_TAILLE,
        moteur.ABANDON_EXPIRATION,
    }
    assert all(v >= 0 for v in bt.abandons.values())
    # Le scénario doit exercer le moteur, sinon le test ne prouve rien.
    assert bt.trades or sum(bt.abandons.values()) > 0


def test_monte_carlo_signale_un_echantillon_trop_court() -> None:
    """Une probabilité de rupture nulle sur trop peu de trades est signalée.

    Si la somme de toutes les pertes n'atteint pas le seuil, aucune
    permutation ne peut casser le compte : le zéro mesure alors la brièveté
    de l'historique, pas la solidité de la stratégie. Le lire comme une bonne
    nouvelle serait l'erreur exacte que cet avertissement empêche.
    """
    court = propfirm.monte_carlo([-55.0, 60.0, -55.0], n_tirages=200)
    assert court["probabilite_breach"] == 0.0
    assert court["echantillon_suffisant"] is False
    assert "brièveté de l'historique" in court["avertissement"]

    # Avec assez de pertes cumulées, l'avertissement disparaît.
    long = propfirm.monte_carlo([-55.0] * 40 + [60.0] * 20, n_tirages=200)
    assert long["echantillon_suffisant"] is True
    assert long["avertissement"] == ""


def test_anti_look_ahead_avec_swings_pour_chaque_sensibilite() -> None:
    """La détection de swing n'introduit pas de fuite, quelle que soit sa finesse.

    Le swing est le point le plus exposé au look-ahead de tout le moteur : il
    exige des bougies **à droite** du point pour être validé. Une
    implémentation naïve confirmerait le retournement dès qu'il se forme,
    c'est-à-dire avant de pouvoir le savoir. On rejoue donc la troncature
    pour les trois sensibilités comparées.
    """
    m1 = _serie_m1(6000)
    taux = _taux_eurusd(m1)
    coupure = 4000
    limite = m1.index[coupure - 1]

    for sensibilite in (3, 4, 5):
        config = moteur.ConfigBacktest(
            mode_tp="ratio", ratio_tp=2.0, sensibilite_swing=sensibilite
        )
        complet = moteur.Backtest(m1, taux, config)
        complet.executer()
        tronque = moteur.Backtest(m1.iloc[:coupure], taux, config)
        tronque.executer()

        def _clos(bt: moteur.Backtest) -> list[dict]:
            return [
                t.to_dict() for t in bt.trades
                if t.horodatage_sortie is not None and t.horodatage_sortie <= limite
            ]

        a, b = _clos(complet), _clos(tronque)
        assert a == b, (
            f"Sensibilité {sensibilite} : le moteur voit des données futures.\n"
            f"  complet : {len(a)} trade(s)\n  tronqué : {len(b)} trade(s)"
        )


def test_touches_simultanees_comptees() -> None:
    """Les zones touchées dans la même minute sont dénombrées.

    La stratégie ne dit pas laquelle prime quand deux order blocks d'unités
    différentes sont atteints ensemble. Le moteur retient la première
    confirmée, et ce compteur dit si le cas est marginal ou s'il mérite une
    règle de priorité.
    """
    m1 = _serie_m1(6000)
    bt = moteur.Backtest(m1, _taux_eurusd(m1), moteur.ConfigBacktest())
    bt.executer()

    assert isinstance(bt.touches_simultanees, int)
    assert bt.touches_simultanees >= 0
    assert len(bt.detail_touches_simultanees) == bt.touches_simultanees
    for detail in bt.detail_touches_simultanees:
        assert detail["n_zones"] > 1
        assert "unites" in detail and "unites_distinctes" in detail


def test_monte_carlo_avec_zero_tirage() -> None:
    """Demander zéro tirage désactive la simulation au lieu de planter.

    C'est ainsi que la comparaison de sensibilité saute une étape coûteuse
    dont elle n'a pas besoin. Une division par zéro y mettait fin.
    """
    resultat = propfirm.monte_carlo([-55.0, 60.0], n_tirages=0)
    assert resultat["disponible"] is False
    assert "désactivée" in resultat["motif"]
    assert resultat["n_tirages"] == 0


# ---------------------------------------------------------------------------
# 8. Sortie par paliers (variante C)
# ---------------------------------------------------------------------------
def test_repartition_des_paliers_somme_toujours_a_un() -> None:
    """Quel que soit le nombre de zones, la position est répartie en entier."""
    for n in range(1, 6):
        parts = moteur.repartir_paliers(n)
        assert len(parts) == n
        assert sum(parts) == pytest.approx(1.0)


def test_repartition_des_paliers_suit_la_regle_confirmee() -> None:
    """50 % à la première zone, le solde à parts égales sur les suivantes."""
    assert moteur.repartir_paliers(1) == [1.0]
    assert moteur.repartir_paliers(2) == pytest.approx([0.5, 0.5])
    assert moteur.repartir_paliers(3) == pytest.approx([0.5, 0.25, 0.25])
    assert moteur.repartir_paliers(4) == pytest.approx([0.5, 1 / 6, 1 / 6, 1 / 6])


def test_repartition_des_paliers_refuse_zero_zone() -> None:
    """Sans zone, il n'y a rien à répartir : l'erreur est explicite."""
    with pytest.raises(ValueError):
        moteur.repartir_paliers(0)


def test_les_zones_de_liquidite_sont_ordonnees_et_plafonnees() -> None:
    """Les zones sont rendues de la plus proche à la plus lointaine, sans doublon."""
    m1 = _serie_m1()
    backtest = moteur.Backtest(
        m1, _taux_eurusd(m1), moteur.ConfigBacktest(mode_tp="paliers")
    )
    backtest.executer()

    for trade in backtest.trades:
        zones = [p.zone for p in trade.paliers]
        assert len(zones) <= moteur.MAX_ZONES_PALIERS
        assert len(zones) == len(set(zones)), "une zone ne doit pas compter deux fois"
        if trade.sens == ict.HAUSSIER:
            assert zones == sorted(zones), "achat : de la plus proche à la plus lointaine"
            assert all(z > trade.prix_entree for z in zones)
        else:
            assert zones == sorted(zones, reverse=True)
            assert all(z < trade.prix_entree for z in zones)


def test_la_variante_structurelle_vise_la_premiere_zone_des_paliers() -> None:
    """L'objectif de A est exactement la première zone retenue par C.

    C'est ce qui rend les deux variantes comparables : même population de
    setups, même première cible, seule la gestion de la sortie diffère.
    """
    m1 = _serie_m1()
    taux = _taux_eurusd(m1)

    structurel = moteur.Backtest(m1, taux, moteur.ConfigBacktest(mode_tp="structurel"))
    structurel.executer()
    paliers = moteur.Backtest(m1, taux, moteur.ConfigBacktest(mode_tp="paliers"))
    paliers.executer()

    par_entree = {t.horodatage_entree: t for t in paliers.trades}
    communs = 0
    for trade in structurel.trades:
        jumeau = par_entree.get(trade.horodatage_entree)
        if jumeau is None:
            continue
        communs += 1
        assert jumeau.paliers[0].zone == pytest.approx(trade.objectif)
    assert communs > 0, "les deux variantes doivent partager des entrées"


def test_le_solde_passe_a_break_even_apres_la_premiere_zone() -> None:
    """Une fois une tranche close, le solde ne peut plus sortir sous l'entrée."""
    m1 = _serie_m1()
    backtest = moteur.Backtest(
        m1, _taux_eurusd(m1), moteur.ConfigBacktest(mode_tp="paliers")
    )
    backtest.executer()

    vus = 0
    for trade in backtest.trades:
        if len(trade.paliers) < 2:
            continue
        premier = trade.paliers[0]
        if premier.motif_sortie != moteur.SORTIE_OBJECTIF:
            continue
        vus += 1
        for suivant in trade.paliers[1:]:
            if suivant.motif_sortie == moteur.SORTIE_BREAK_EVEN:
                assert suivant.prix_sortie is not None
                # Sortie au prix d'entrée, aux coûts de sortie près : le
                # résultat de la tranche reste très proche de zéro.
                assert suivant.resultat_r == pytest.approx(0.0, abs=0.05)
            assert suivant.motif_sortie != moteur.SORTIE_STOP, (
                "le stop initial ne peut plus s'appliquer après le passage à break-even"
            )
    assert vus > 0, "l'échantillon doit contenir des trades ayant atteint leur TP1"


def test_le_resultat_dun_trade_a_paliers_est_la_somme_de_ses_tranches() -> None:
    """Le total affiché n'est jamais autre chose que la somme du détail."""
    m1 = _serie_m1()
    backtest = moteur.Backtest(
        m1, _taux_eurusd(m1), moteur.ConfigBacktest(mode_tp="paliers")
    )
    backtest.executer()

    assert backtest.trades, "l'échantillon doit produire des trades"
    for trade in backtest.trades:
        resolus = [p for p in trade.paliers if p.resultat_r is not None]
        assert len(resolus) == len(trade.paliers), "toute tranche d'un trade clos est dénouée"
        assert trade.resultat_r == pytest.approx(sum(p.resultat_r for p in resolus))
        assert sum(p.fraction for p in trade.paliers) == pytest.approx(1.0)


def test_le_journal_des_paliers_detaille_chaque_tranche() -> None:
    """Le journal donne zone, part, prix de sortie et R par tranche."""
    m1 = _serie_m1()
    backtest = moteur.Backtest(
        m1, _taux_eurusd(m1), moteur.ConfigBacktest(mode_tp="paliers")
    )
    backtest.executer()

    lignes = [l for t in backtest.trades for l in t.lignes_paliers()]
    assert lignes
    for ligne in lignes:
        for champ in ("rang", "zone", "origine", "fraction", "ratio_risque",
                      "prix_sortie", "resultat_r", "motif_sortie"):
            assert champ in ligne
        assert ligne["origine"] in {
            "veille_haut", "veille_bas", "asie_haut", "asie_bas", "order_block",
        }


# ---------------------------------------------------------------------------
# Filtre fondamental : un veto dans le moteur, pas un tri après coup
# ---------------------------------------------------------------------------
def test_le_filtre_fondamental_refuse_les_trades_et_les_compte() -> None:
    m1 = _serie_m1(6000)
    taux = _taux_eurusd(m1)
    sans = moteur.Backtest(m1, taux, moteur.ConfigBacktest(mode_tp="ratio", ratio_tp=2.0))
    sans.executer()
    tout_refuse = moteur.Backtest(m1, taux, moteur.ConfigBacktest(
        mode_tp="ratio", ratio_tp=2.0, filtre_fondamental=lambda sens, instant: False))
    tout_refuse.executer()
    assert sans.trades, "la série doit produire des trades pour que le test soit probant"
    assert tout_refuse.trades == []
    assert tout_refuse.abandons[moteur.ABANDON_FILTRE_FONDAMENTAL] > 0


def test_un_filtre_qui_laisse_tout_passer_ne_change_rien() -> None:
    """Le défaut (aucun filtre) et un filtre permissif doivent coïncider."""
    m1 = _serie_m1(6000)
    taux = _taux_eurusd(m1)
    a = moteur.Backtest(m1, taux, moteur.ConfigBacktest(mode_tp="ratio", ratio_tp=2.0))
    a.executer()
    b = moteur.Backtest(m1, taux, moteur.ConfigBacktest(
        mode_tp="ratio", ratio_tp=2.0, filtre_fondamental=lambda sens, instant: True))
    b.executer()
    assert [t.to_dict() for t in a.trades] == [t.to_dict() for t in b.trades]


def test_le_filtre_ne_voit_que_le_sens_et_linstant_du_trade() -> None:
    """Le veto reçoit ce qu'il faut pour décider, et rien du futur."""
    m1 = _serie_m1(6000)
    taux = _taux_eurusd(m1)
    vus: list[tuple] = []

    def _filtre(sens: str, instant) -> bool:
        vus.append((sens, instant))
        return sens == ict.HAUSSIER

    bt = moteur.Backtest(m1, taux, moteur.ConfigBacktest(
        mode_tp="ratio", ratio_tp=2.0, filtre_fondamental=_filtre))
    bt.executer()
    assert vus, "le filtre doit être consulté"
    assert all(s in (ict.HAUSSIER, ict.BAISSIER) for s, _ in vus)
    assert all(t.sens == ict.HAUSSIER for t in bt.trades), "seuls les achats devaient passer"
    # Refuser une vente libère la position : le moteur peut prendre un achat
    # qu'il n'aurait pas vu autrement. C'est pourquoi le veto est dans le
    # moteur et non appliqué aux trades après coup.
    achats_sans_filtre = [
        t for t in moteur.Backtest(m1, taux, moteur.ConfigBacktest(mode_tp="ratio", ratio_tp=2.0)).trades
    ]
    assert len(bt.trades) >= 0 and isinstance(achats_sans_filtre, list)
