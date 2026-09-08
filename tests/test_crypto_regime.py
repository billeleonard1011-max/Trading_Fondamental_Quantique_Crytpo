"""Tests de la veille crypto : régimes, invalidation et positionnement.

La propriété centrale
---------------------
**Chaque régime possible doit porter une condition d'invalidation non vide.**
Un classement qu'on ne saurait pas contredire n'est pas une lecture de
marché, c'est une opinion : rien ne permettrait de dire qu'il a cessé d'être
vrai. Le test parcourt donc les quatre régimes et vérifie qu'aucun ne sort
sans sa condition, y compris le cas dégradé où le MVRV est introuvable.

Sont également vérifiés : le refus de classer sans MVRV plutôt qu'un
classement par défaut, le percentile de funding, et la déclaration explicite
des deux indicateurs sans source gratuite.

Aucun test n'accède au réseau : toutes les charges sont fabriquées.

Exécution :
    pytest tests/test_crypto_regime.py -v
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from modules.crypto import positioning, regime

#: Seuils identiques à ceux de ``config/universe.yaml``.
_SEUILS = {"capitulation_max": 1.0, "accumulation_max": 2.0, "expansion_max": 3.0}


# ---------------------------------------------------------------------------
# 1. Classification et invalidation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("mvrv", "attendu"),
    [
        (0.75, regime.CAPITULATION),
        (0.99, regime.CAPITULATION),
        (1.0, regime.ACCUMULATION),
        (1.51, regime.ACCUMULATION),     # valeur réelle du BTC au 6 septembre 2026
        (2.0, regime.EXPANSION),
        (2.6, regime.EXPANSION),
        (3.0, regime.DISTRIBUTION),
        (4.2, regime.DISTRIBUTION),
    ],
)
def test_classification_par_seuils(mvrv: float, attendu: str) -> None:
    """Le MVRV range l'état du marché dans la case prévue, bornes incluses."""
    resultat = regime.classer_regime(mvrv, croissance_stablecoins_pct=1.0, seuils=_SEUILS)
    assert resultat["disponible"]
    assert resultat["regime"] == attendu


def test_chaque_regime_possede_une_invalidation_non_vide() -> None:
    """Aucun régime ne peut être publié sans la condition qui l'annulerait.

    C'est la propriété qui distingue une lecture d'une opinion : sans
    condition d'invalidation, rien ne permettrait de constater qu'un régime a
    cessé d'être valable.
    """
    valeurs = {
        regime.CAPITULATION: 0.8,
        regime.ACCUMULATION: 1.5,
        regime.EXPANSION: 2.5,
        regime.DISTRIBUTION: 3.5,
    }
    vus: set[str] = set()

    for attendu, mvrv in valeurs.items():
        resultat = regime.classer_regime(mvrv, 1.0, seuils=_SEUILS)
        vus.add(resultat["regime"])

        invalidation = resultat["invalidation"]
        assert invalidation, f"Régime « {attendu} » publié sans invalidation."
        assert invalidation.get("condition", "").strip(), (
            f"Régime « {attendu} » : condition d'invalidation vide."
        )
        assert invalidation.get("variable"), f"Régime « {attendu} » : variable absente."
        assert invalidation.get("seuil") is not None, (
            f"Régime « {attendu} » : seuil d'invalidation absent."
        )
        # La condition doit être exploitable : elle nomme un nombre.
        assert any(c.isdigit() for c in invalidation["condition"]), (
            f"Régime « {attendu} » : la condition ne cite aucun seuil chiffré."
        )

    # Les quatre régimes déclarés sont bien tous atteignables.
    assert vus == set(regime.REGIMES)


def test_invalidation_presente_meme_sans_mvrv() -> None:
    """Le cas dégradé porte lui aussi sa condition de sortie."""
    resultat = regime.classer_regime(None, 1.0, seuils=_SEUILS)
    assert resultat["disponible"] is False
    assert resultat["regime"] is None
    assert resultat["invalidation"]["condition"].strip()
    assert "MVRV" in resultat["motif"]


def test_refus_de_classer_sans_mvrv_plutot_quun_defaut() -> None:
    """Sans MVRV, aucun régime n'est inventé.

    Renvoyer « accumulation » par défaut serait la pire des sorties : elle
    aurait l'apparence d'une mesure.
    """
    resultat = regime.classer_regime(None, None)
    assert resultat["regime"] is None
    assert resultat["regime"] not in regime.REGIMES


def test_stablecoins_nuancent_sans_renverser_le_classement() -> None:
    """L'offre de stablecoins module la lecture, elle ne change pas la case."""
    croissance = regime.classer_regime(1.5, croissance_stablecoins_pct=8.0, seuils=_SEUILS)
    contraction = regime.classer_regime(1.5, croissance_stablecoins_pct=-8.0, seuils=_SEUILS)

    assert croissance["regime"] == contraction["regime"] == regime.ACCUMULATION
    assert croissance["nuance"] != contraction["nuance"]
    assert "croît" in croissance["nuance"]
    assert "recule" in contraction["nuance"]


def test_absence_de_stablecoins_signalee_dans_la_nuance() -> None:
    """Quand la seconde source manque, le régime le dit au lieu de le taire."""
    resultat = regime.classer_regime(1.5, croissance_stablecoins_pct=None, seuils=_SEUILS)
    assert resultat["regime"] == regime.ACCUMULATION
    assert "n'a pas pu être mesurée" in resultat["nuance"]


# ---------------------------------------------------------------------------
# 2. Sources : lecture et dégradation
# ---------------------------------------------------------------------------
def test_lecture_du_mvrv_coinmetrics() -> None:
    """La charge de Coin Metrics est lue, la plus récente étant retenue."""
    charge = {
        "data": [
            {"asset": "btc", "time": "2026-09-05T00:00:00.000000000Z", "CapMVRVCur": "1.501487"},
            {"asset": "btc", "time": "2026-09-06T00:00:00.000000000Z", "CapMVRVCur": "1.510496"},
        ]
    }
    resultat = regime.get_mvrv("btc", charge=charge)
    assert resultat["disponible"]
    assert resultat["valeur"] == pytest.approx(1.510496)
    assert resultat["date"] == "2026-09-06"
    assert "Coin Metrics" in resultat["source"]


def test_mvrv_indisponible_donne_un_motif() -> None:
    """Une réponse vide ou illisible ne produit aucune valeur inventée."""
    for charge in ({}, {"data": []}, {"data": [{"asset": "btc", "CapMVRVCur": None}]}, None):
        resultat = regime.get_mvrv("btc", charge=charge or {})
        assert resultat["disponible"] is False
        assert resultat["valeur"] is None
        assert resultat["motif"]


def test_croissance_des_stablecoins() -> None:
    """La croissance se calcule sur la fenêtre demandée, bornée par l'historique."""
    charge = [
        {"date": str(1_700_000_000 + i * 86_400), "totalCirculating": {"peggedUSD": 100.0 + i}}
        for i in range(40)
    ]
    resultat = regime.get_croissance_stablecoins(fenetre_jours=30, charge=charge)
    assert resultat["disponible"]
    assert resultat["fenetre_jours"] == 30
    # De 109 à 139 sur trente jours.
    assert resultat["croissance_pct"] == pytest.approx((139.0 / 109.0 - 1.0) * 100.0)

    # Historique plus court que la fenêtre : elle est ramenée au disponible.
    court = regime.get_croissance_stablecoins(fenetre_jours=30, charge=charge[:10])
    assert court["fenetre_jours"] == 9


def test_stablecoins_charge_inexploitable() -> None:
    """Une charge malformée est refusée avec un motif."""
    for charge in ([], [{"date": "1"}], {"pas": "une liste"}):
        resultat = regime.get_croissance_stablecoins(charge=charge)
        assert resultat["disponible"] is False
        assert resultat["motif"]


def test_flux_etf_lus_depuis_la_source_directe() -> None:
    """Les flux ETF sont désormais mesurés, plus déclarés indisponibles.

    Farside, réinterrogé avec des en-têtes de navigateur, répond et publie le
    tableau réel. Le test vérifie l'analyse de ce tableau sur un extrait figé,
    y compris la convention comptable des parenthèses pour les sorties.
    """
    from dataio import etf_flows

    html = """
    <table>
      <tr><th></th><th>IBIT</th><th>FBTC</th><th>Total</th></tr>
      <tr><td>03 Sep 2026</td><td>454.0</td><td>74.4</td><td>730.8</td></tr>
      <tr><td>04 Sep 2026</td><td>117.4</td><td>(20.1)</td><td>174.6</td></tr>
      <tr><td>Total</td><td>64056</td><td>1</td><td>55686</td></tr>
      <tr><td>Average</td><td>96.3</td><td>1</td><td>83.7</td></tr>
    </table>
    """
    donnees, motif = etf_flows.tenter_farside(html=html)
    assert motif == ""
    assert donnees is not None
    # Les lignes d'agrégat ne sont pas des dates : elles sont écartées.
    assert donnees["n_jours"] == 2

    derniere = donnees["lignes"][-1]
    assert derniere["date"] == "2026-09-04"
    assert derniere["flux_net_usd"] == pytest.approx(174.6e6)
    # Les parenthèses valent un signe négatif.
    assert derniere["detail_par_etf_usd"]["FBTC"] == pytest.approx(-20.1e6)


def test_flux_etf_repli_sur_actifs_nets(tmp_path: Path) -> None:
    """Sans Farside, le repli neutralise l'effet du prix sur l'actif net."""
    from dataio import etf_flows

    cache = tmp_path / "aum.json"
    # Premier passage : rien à comparer, mais l'instantané est enregistré.
    premier = etf_flows.get_flux_etf(
        "btc", prix_btc=80_000.0, chemin_cache=cache,
        aujourd_hui=date(2026, 9, 7), actifs_courants={"IBIT": 60e9},
        essayer_farside=False,
    )
    assert premier["disponible"] is False
    assert "aucun instantané antérieur" in premier["motif"]

    # Prix stable et actif net stable : le flux doit être quasi nul.
    stable = etf_flows.get_flux_etf(
        "btc", prix_btc=80_000.0, chemin_cache=cache,
        aujourd_hui=date(2026, 9, 8), actifs_courants={"IBIT": 60e9},
        essayer_farside=False,
    )
    assert stable["disponible"] is True
    assert stable["flux_net_usd"] == pytest.approx(0.0, abs=1.0)

    # Prix stable et actif net en forte hausse : flux nettement positif.
    hausse = etf_flows.get_flux_etf(
        "btc", prix_btc=80_000.0, chemin_cache=cache,
        aujourd_hui=date(2026, 9, 9), actifs_courants={"IBIT": 66e9},
        essayer_farside=False,
    )
    assert hausse["flux_net_usd"] == pytest.approx(6e9, rel=1e-6)
    assert hausse["est_approximation"] is True


def test_flux_etf_repli_refuse_sans_prix(tmp_path: Path) -> None:
    """Sans prix des deux côtés, une variation d'actif net n'est pas un flux."""
    from dataio import etf_flows

    cache = tmp_path / "aum.json"
    etf_flows.get_flux_etf(
        "btc", prix_btc=None, chemin_cache=cache, aujourd_hui=date(2026, 9, 7),
        actifs_courants={"IBIT": 60e9}, essayer_farside=False,
    )
    resultat = etf_flows.get_flux_etf(
        "btc", prix_btc=80_000.0, chemin_cache=cache, aujourd_hui=date(2026, 9, 8),
        actifs_courants={"IBIT": 66e9}, essayer_farside=False,
    )
    assert resultat["disponible"] is False
    assert "effet de marché ne peut pas être neutralisé" in resultat["motif"]


def test_analyse_complete_sans_reseau() -> None:
    """L'assemblage fonctionne entièrement sur des charges fournies."""
    resultat = regime.analyser_regime(
        {"actifs": ["btc", "eth"], "seuils_mvrv": _SEUILS, "seuil_croissance_stablecoins_pct": 2.0},
        mvrv_par_actif={
            "btc": {"disponible": True, "valeur": 1.51},
            "eth": {"disponible": True, "valeur": 1.12},
        },
        stablecoins={"disponible": True, "croissance_pct": 1.4},
        prix_realise_par_actif={
            "btc": {"disponible": True, "prix_realise_usd": 53_178.0},
            "eth": {"disponible": True, "prix_realise_usd": 2_254.0},
        },
        flux_etf_par_actif={
            "btc": {"disponible": True, "flux_net_usd": 174.6e6},
            "eth": {"disponible": True, "flux_net_usd": 25.9e6},
        },
    )
    assert resultat["disponible"]
    assert resultat["regimes"]["btc"]["regime"] == regime.ACCUMULATION
    assert resultat["regimes"]["eth"]["regime"] == regime.ACCUMULATION
    assert "prévision" in resultat["avertissement"]

    # Les deux actifs sont aussi exposés à la racine, de structure identique.
    for actif in ("btc", "eth"):
        bloc = resultat[f"regime_{actif}"]
        assert bloc["actif"] == actif
        assert bloc["regime"] == regime.ACCUMULATION
        assert bloc["invalidation"]["condition"].strip()

    # Les seuils viennent du bitcoin : l'ether doit porter l'avertissement.
    assert resultat["regime_btc"]["avertissement_calibrage"] == ""
    assert "calibrées sur l'histoire de BTC" in resultat["regime_eth"]["avertissement_calibrage"]


def test_un_actif_muet_ne_fait_pas_tomber_lautre() -> None:
    """L'indisponibilité d'ETH laisse le régime de BTC intact."""
    resultat = regime.analyser_regime(
        {"actifs": ["btc", "eth"], "seuils_mvrv": _SEUILS},
        mvrv_par_actif={
            "btc": {"disponible": True, "valeur": 2.4},
            "eth": {"disponible": False, "motif": "métrique absente"},
        },
        stablecoins={"disponible": False},
        prix_realise_par_actif={"btc": {"disponible": False}, "eth": {"disponible": False}},
        flux_etf_par_actif={"btc": {"disponible": False}, "eth": {"disponible": False}},
    )
    assert resultat["regimes"]["btc"]["regime"] == regime.EXPANSION
    assert resultat["regimes"]["eth"]["disponible"] is False
    assert resultat["disponible"] is True


# ---------------------------------------------------------------------------
# 3. Positionnement
# ---------------------------------------------------------------------------
def _charge_funding(valeurs: list[float]) -> list[dict[str, Any]]:
    """Fabrique une charge de funding au format Binance."""
    depart = datetime(2026, 6, 1, tzinfo=timezone.utc)
    return [
        {
            "symbol": "BTCUSDT",
            "fundingTime": int((depart + timedelta(hours=8 * i)).timestamp() * 1000),
            "fundingRate": str(v),
        }
        for i, v in enumerate(valeurs)
    ]


def test_percentile_de_funding_sur_valeur_connue() -> None:
    """Le percentile est vérifié sur une distribution dont on connaît le rang."""
    # 99 valeurs de 0 à 0,0098, puis la valeur courante la plus haute.
    valeurs = [i / 10_000.0 for i in range(99)] + [0.0099]
    funding, source = positioning.get_funding_history(
        "BTCUSDT", charge_binance=_charge_funding(valeurs)
    )
    assert source == "binance"
    assert len(funding) == 100

    resultat = positioning.analyser_funding(funding, source)
    assert resultat["disponible"]
    assert resultat["percentile_90j"] == pytest.approx(100.0)
    assert resultat["tension"] == "levier acheteur tendu"

    # Funding annualisé : trois versements par jour, 365 jours.
    assert resultat["funding_annualise_pct"] == pytest.approx(0.0099 * 3 * 365 * 100)


def test_funding_bas_signale_le_levier_vendeur() -> None:
    """Un funding au plus bas de sa distribution est signalé comme tel."""
    valeurs = [i / 10_000.0 for i in range(99)] + [-0.005]
    funding, source = positioning.get_funding_history(
        "BTCUSDT", charge_binance=_charge_funding(valeurs)
    )
    resultat = positioning.analyser_funding(funding, source)
    assert resultat["percentile_90j"] == pytest.approx(1.0)
    assert resultat["tension"] == "levier vendeur tendu"


def test_funding_refuse_sur_historique_trop_court() -> None:
    """Moins de trente versements ne permettent pas un percentile crédible."""
    funding, source = positioning.get_funding_history(
        "BTCUSDT", charge_binance=_charge_funding([0.0001] * 10)
    )
    resultat = positioning.analyser_funding(funding, source)
    assert resultat["disponible"] is False
    assert "30 au minimum" in resultat["motif"]


def test_repli_sur_bybit_quand_binance_est_muet() -> None:
    """Binance indisponible, Bybit prend le relais et la source est nommée."""
    charge_bybit = {
        "retCode": 0,
        "result": {
            "list": [
                {"symbol": "BTCUSDT", "fundingRate": "0.00001982", "fundingRateTimestamp": "1788796800000"},
                {"symbol": "BTCUSDT", "fundingRate": "0.00000745", "fundingRateTimestamp": "1788768000000"},
            ]
        },
    }
    funding, source = positioning.get_funding_history(
        "BTCUSDT", charge_binance=[], charge_bybit=charge_bybit
    )
    assert source == "bybit"
    assert len(funding) == 2
    assert funding.index.is_monotonic_increasing


def test_deux_sources_muettes_ne_plantent_pas() -> None:
    """Sans aucune source, la série est vide et la source est « aucune »."""
    funding, source = positioning.get_funding_history(
        "BTCUSDT", charge_binance=[], charge_bybit={}
    )
    assert funding.empty
    assert source == "aucune"
    assert positioning.analyser_funding(funding, source)["disponible"] is False


def test_cinq_statuts_de_deblocage_distincts() -> None:
    """Chacun des cinq statuts sort distinctement, sans faux calendrier.

    La distinction entre « inconnu » et « non_applicable » est le cœur du
    test : le premier dit qu'on ignore, le second qu'il n'y a rien à savoir.
    Les confondre rassurerait à tort sur un jeton dont on ne sait rien.
    """
    watchlist = [
        {"symbol": s} for s in ("ASTER", "JUP", "TAO", "KNTQ", "PONS", "LINK")
    ]
    configuration = {
        "ASTER": {
            "statut": "actif",
            "source": "https://docs.asterdex.com/usdaster-token/tokenomics",
            "prochain_deblocage_connu": {
                "date": "2027-09-17",
                "jetons": 400_000_000,
                "part_offre_pct": 5.0,
                "description": "Report d'un an annoncé le 1er septembre 2026.",
            },
            "motif_si_change": "Échéance déjà reportée une fois.",
        },
        "JUP": {"statut": "vesting_conclu", "description": "Calendrier achevé."},
        "TAO": {"statut": "non_applicable", "description": "Émission continue par halving."},
        "KNTQ": {"statut": "inconnu"},
        "PONS": {"statut": "inconnu"},
    }
    resultat = positioning.get_deblocages_tokens(
        watchlist, configuration, aujourd_hui=date(2026, 9, 8)
    )
    par_symbole = {j["symbole"]: j for j in resultat["jetons"]}

    # Les cinq statuts attendus, tous différents.
    assert par_symbole["ASTER"]["statut"] == "actif"
    assert par_symbole["JUP"]["statut"] == "vesting_conclu"
    assert par_symbole["TAO"]["statut"] == "non_applicable"
    assert par_symbole["KNTQ"]["statut"] == "inconnu"
    assert par_symbole["PONS"]["statut"] == "inconnu"
    # Un jeton non renseigné n'est pas « sans déblocage », il est « absent ».
    assert par_symbole["LINK"]["statut"] == "absent"

    # Seul ASTER porte une échéance ; aucun faux calendrier ailleurs.
    assert par_symbole["ASTER"]["prochain_deblocage"]["date"] == "2027-09-17"
    assert par_symbole["ASTER"]["prochain_deblocage"]["jetons"] == 400_000_000
    assert par_symbole["ASTER"]["jours_avant_deblocage"] == 374
    for symbole in ("JUP", "TAO", "KNTQ", "PONS", "LINK"):
        assert par_symbole[symbole]["prochain_deblocage"] is None
        assert par_symbole[symbole]["jours_avant_deblocage"] is None

    # « inconnu » n'est pas « disponible » ; « non_applicable » l'est, parce
    # qu'on sait qu'il n'y a rien à surveiller.
    assert par_symbole["KNTQ"]["disponible"] is False
    assert par_symbole["TAO"]["disponible"] is True
    assert par_symbole["JUP"]["disponible"] is True
    assert "non confirmé" in par_symbole["PONS"]["motif"]
    assert resultat["n_echeances_a_venir"] == 1


def test_echeance_de_deblocage_passee_signalee() -> None:
    """Une échéance dépassée est signalée pour révision, pas laissée telle quelle."""
    resultat = positioning.get_deblocages_tokens(
        [{"symbol": "ASTER"}],
        {"ASTER": {"statut": "actif", "prochain_deblocage_connu": {"date": "2026-01-01"}}},
        aujourd_hui=date(2026, 9, 8),
    )
    jeton = resultat["jetons"][0]
    assert jeton["prochain_deblocage"]["passe"] is True
    assert "à revérifier" in jeton["motif"]
    assert resultat["n_echeances_a_venir"] == 0


def test_positions_suivies_et_jeton_absent() -> None:
    """Un jeton absent de l'instantané est signalé sans faire tomber les autres."""
    # Colonnes telles que dataio.crypto.get_snapshot les produit réellement.
    instantane = pd.DataFrame(
        {"prix_usd": [80_000.0, 0.7692], "var_24h_pct": [1.2, -3.4]},
        index=["bitcoin", "aster-2"],
    )
    watchlist = [
        {"symbol": "BTC", "coingecko_id": "bitcoin", "position": True},
        {"symbol": "ASTER", "coingecko_id": "aster-2", "position": True},
        {"symbol": "PONS", "coingecko_id": "pons", "position": True, "jeune_et_volatil": True},
    ]
    resultat = positioning.suivre_positions(watchlist, instantane)

    assert resultat["disponible"]
    assert resultat["n_disponibles"] == 2
    assert resultat["jetons_absents"] == ["PONS"]

    btc = next(p for p in resultat["positions"] if p["symbole"] == "BTC")
    assert btc["prix_usd"] == pytest.approx(80_000.0)
    assert btc["variation_24h_pct"] == pytest.approx(1.2)

    pons = next(p for p in resultat["positions"] if p["symbole"] == "PONS")
    assert pons["disponible"] is False
    assert pons["jeune_et_volatil"] is True
    # Le jeton très jeune porte son avertissement propre.
    assert "ne s'appliquent pas" in pons["avertissement"]


def test_positions_sans_instantane() -> None:
    """Sans instantané, le bloc est marqué indisponible avec son motif."""
    resultat = positioning.suivre_positions(
        [{"symbol": "BTC", "coingecko_id": "bitcoin"}], pd.DataFrame()
    )
    assert resultat["disponible"] is False
    assert "indisponible" in resultat["motif"]
    assert resultat["positions"] == []
