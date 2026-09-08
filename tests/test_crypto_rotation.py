"""Tests de la rotation BTC / alts : synthèse, réserves et largeur de marché.

Ce que ces tests protègent
--------------------------
1. **La synthèse ne force pas un résultat.** Quand les trois mesures
   divergent, l'état doit rester ``indetermine``. Trancher à la majorité
   donnerait une réponse nette là où le marché n'en donne pas.
2. **La réserve sur le ratio ETH/BTC ne disparaît jamais.** Elle est un champ
   du JSON, pas un commentaire : un lecteur qui l'ignorerait lirait le ratio
   comme on le lisait en 2021, avant que les Layer 2 et les ETF ne cassent la
   relation.
3. **La largeur de marché exclut ce qui la fausserait.** Un stablecoin ne
   sous-performe pas le bitcoin, il est stable par construction.

Aucun test n'accède au réseau.

Exécution :
    pytest tests/test_crypto_rotation.py -v
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from modules.crypto import rotation


def _contexte(dominance: float = 58.9) -> dict[str, Any]:
    """Fabrique un contexte de marché."""
    return {
        "disponible": True,
        "dominance_btc_pct": dominance,
        "dominance_eth_pct": 11.3,
        "capitalisation_totale_usd": 2.6e12,
    }


def _historique(depart: float, arrivee: float, n: int = 60) -> list[dict[str, Any]]:
    """Fabrique un historique de dominance allant de ``depart`` à ``arrivee``."""
    dates = pd.date_range("2026-07-01", periods=n, freq="D")
    valeurs = np.linspace(depart, arrivee, n)
    return [
        {"date": d.strftime("%Y-%m-%d"), "dominance_btc_pct": float(v)}
        for d, v in zip(dates, valeurs)
    ]


def _marches(part_surperformante: float, n: int = 50) -> list[dict[str, Any]]:
    """Fabrique un panier dont une part donnée bat le bitcoin sur 30 jours."""
    perf_btc = 10.0
    actifs = [
        {
            "id": "bitcoin",
            "symbol": "btc",
            "name": "Bitcoin",
            "price_change_percentage_30d_in_currency": perf_btc,
        }
    ]
    n_gagnants = round(n * part_surperformante / 100.0)
    for i in range(n):
        actifs.append(
            {
                "id": f"actif-{i}",
                "symbol": f"a{i}",
                "name": f"Actif {i}",
                "price_change_percentage_30d_in_currency": (
                    perf_btc + 5.0 if i < n_gagnants else perf_btc - 5.0
                ),
            }
        )
    return actifs


# ---------------------------------------------------------------------------
# 1. Synthèse
# ---------------------------------------------------------------------------
def test_trois_mesures_concordantes_donnent_un_etat_net() -> None:
    """Dominance en baisse, ratio en hausse, largeur élevée : rotation alts."""
    resultat = rotation.analyser_rotation(
        chemin_cache=Path("/nonexistent/cache.json"),
        aujourd_hui=date(2026, 9, 8),
        contexte=_contexte(55.0),
        marches=_marches(84.0),
        html_indice="Altcoin Season ( 82 )",
        prix_eth=pd.Series(np.linspace(2000.0, 2600.0, 120)),
        prix_btc=pd.Series(np.full(120, 80_000.0)),
    )
    # La dominance descend de 60 à 55 sur l'historique fabriqué plus bas ;
    # ici le cache est absent, donc la dominance s'abstient.
    synthese = resultat["synthese"]
    assert synthese["etat"] == rotation.ROTATION_ALTS
    assert synthese["n_mesures_exprimees"] >= 2
    votes = {c["mesure"]: c["vote"] for c in synthese["contributions"]}
    assert votes["ratio_eth_btc"] == rotation.ROTATION_ALTS
    assert votes["largeur_marche"] == rotation.ROTATION_ALTS


def test_mesures_contradictoires_donnent_indetermine() -> None:
    """Ratio et largeur qui se contredisent : aucun état ne se dégage."""
    dominance = rotation.analyser_dominance(_contexte(60.0), _historique(55.0, 60.0))
    ratio = {
        "disponible": True,
        "valeur": 0.031,
        "variation_30j_pct": 8.0,          # penche vers les alts
        "fiabilite_historique": "reduite_depuis_2024",
    }
    largeur = {
        "disponible": True,
        "part_surperformant_btc_pct": 12.0,  # penche vers le bitcoin
        "horizon_effectif_jours": 30,
    }
    synthese = rotation.synthetiser(dominance, ratio, largeur)

    assert synthese["etat"] == rotation.INDETERMINE
    assert "se contredisent" in synthese["justification"]
    votes = {c["mesure"]: c["vote"] for c in synthese["contributions"]}
    assert votes["ratio_eth_btc"] == rotation.ROTATION_ALTS
    assert votes["largeur_marche"] == rotation.DOMINANCE_BTC
    assert votes["dominance_btc"] == rotation.DOMINANCE_BTC


def test_une_seule_mesure_ne_suffit_pas() -> None:
    """Avec une seule mesure exprimée, on refuse de conclure."""
    synthese = rotation.synthetiser(
        {"disponible": False, "motif": "cache vide"},
        {"disponible": False, "motif": "prix absents"},
        {"disponible": True, "part_surperformant_btc_pct": 90.0},
    )
    assert synthese["etat"] == rotation.INDETERMINE
    assert synthese["n_mesures_exprimees"] == 1
    assert "au moins deux" in synthese["justification"]


def test_chaque_contribution_est_detaillee() -> None:
    """La synthèse expose le vote de chaque mesure, pas seulement le résultat."""
    synthese = rotation.synthetiser(
        rotation.analyser_dominance(_contexte(60.0), _historique(55.0, 60.0)),
        {"disponible": True, "valeur": 0.03, "variation_30j_pct": -5.0},
        {"disponible": True, "part_surperformant_btc_pct": 10.0},
    )
    assert len(synthese["contributions"]) == 3
    for contribution in synthese["contributions"]:
        assert "mesure" in contribution
        assert "vote" in contribution
        assert "abstention" in contribution
        # Une abstention doit toujours être motivée.
        if contribution["abstention"]:
            assert contribution["motif_abstention"].strip()
    assert synthese["invalidation"]["condition"].strip()


def test_chaque_etat_possede_une_invalidation() -> None:
    """Les trois états sortent avec la condition qui les annulerait."""
    cas = [
        ({"disponible": True, "valeur": 0.03, "variation_30j_pct": 8.0},
         {"disponible": True, "part_surperformant_btc_pct": 90.0}),
        ({"disponible": True, "valeur": 0.03, "variation_30j_pct": -8.0},
         {"disponible": True, "part_surperformant_btc_pct": 10.0}),
        ({"disponible": True, "valeur": 0.03, "variation_30j_pct": 8.0},
         {"disponible": True, "part_surperformant_btc_pct": 10.0}),
    ]
    etats = set()
    for ratio, largeur in cas:
        synthese = rotation.synthetiser({"disponible": False, "motif": "x"}, ratio, largeur)
        etats.add(synthese["etat"])
        assert synthese["invalidation"]["condition"].strip()
        assert synthese["invalidation"]["variable"]
    assert etats == {rotation.ROTATION_ALTS, rotation.DOMINANCE_BTC, rotation.INDETERMINE}


# ---------------------------------------------------------------------------
# 2. Ratio ETH/BTC : la réserve est obligatoire
# ---------------------------------------------------------------------------
def test_fiabilite_historique_toujours_presente() -> None:
    """Le champ de réserve figure dans tous les cas, y compris en échec."""
    disponible = rotation.analyser_ratio_eth_btc(
        prix_eth=pd.Series(np.linspace(2000.0, 2600.0, 120)),
        prix_btc=pd.Series(np.full(120, 80_000.0)),
    )
    assert disponible["fiabilite_historique"] == "reduite_depuis_2024"
    assert "Layer 2" in disponible["explication_fiabilite"]
    assert "ETF" in disponible["explication_fiabilite"]

    # Même en échec, la réserve ne disparaît pas : un consommateur du JSON ne
    # doit jamais avoir à gérer son absence.
    # Séries explicitement vides plutôt que None : passer None déclencherait
    # un chargement réseau, ce qu'aucun test ne doit faire.
    vide = pd.Series(dtype="float64")
    for absent in (
        rotation.analyser_ratio_eth_btc(prix_eth=vide, prix_btc=pd.Series([1.0])),
        rotation.analyser_ratio_eth_btc(prix_eth=vide, prix_btc=vide),
        rotation.analyser_ratio_eth_btc(prix_eth=pd.Series([1.0]), prix_btc=pd.Series([0.0])),
    ):
        assert absent["disponible"] is False
        assert absent["fiabilite_historique"] == "reduite_depuis_2024"
        assert absent["explication_fiabilite"].strip()


def test_fenetre_de_percentile_declaree() -> None:
    """La fenêtre réellement utilisée est publiée à côté de celle demandée."""
    resultat = rotation.analyser_ratio_eth_btc(
        prix_eth=pd.Series(np.linspace(2000.0, 2600.0, 365)),
        prix_btc=pd.Series(np.full(365, 80_000.0)),
    )
    assert resultat["fenetre_demandee_jours"] == 730
    assert resultat["fenetre_effective_jours"] == 365
    assert "plafonne son historique" in resultat["motif_fenetre"]


# ---------------------------------------------------------------------------
# 3. Largeur de marché
# ---------------------------------------------------------------------------
def test_largeur_de_marche_sur_panier_connu() -> None:
    """Sur un panier fabriqué, la part surperformante est celle attendue."""
    resultat = rotation.calculer_largeur_marche(_marches(80.0), taille_panier=50)
    assert resultat["disponible"]
    assert resultat["part_surperformant_btc_pct"] == pytest.approx(80.0)
    assert resultat["n_actifs_panier"] == 50
    assert resultat["performance_btc_pct"] == pytest.approx(10.0)


def test_stablecoins_exclus_du_panier() -> None:
    """Un stablecoin ne « sous-performe » pas : il fausserait la largeur.

    Sans exclusion, dix stablecoins plats dans un panier de vingt feraient
    tomber la largeur de 100 % à 50 % sans qu'aucun actif n'ait changé.
    """
    marches = [
        {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin",
         "price_change_percentage_30d_in_currency": 10.0},
    ]
    for i in range(10):
        marches.append({
            "id": f"gagnant-{i}", "symbol": f"g{i}", "name": f"Gagnant {i}",
            "price_change_percentage_30d_in_currency": 25.0,
        })
    for identifiant, symbole, nom in (
        ("tether", "usdt", "Tether"), ("usd-coin", "usdc", "USDC"),
        ("dai", "dai", "Dai"), ("wrapped-bitcoin", "wbtc", "Wrapped Bitcoin"),
        ("staked-ether", "steth", "Lido Staked Ether"),
    ):
        marches.append({
            "id": identifiant, "symbol": symbole, "name": nom,
            "price_change_percentage_30d_in_currency": 0.0,
        })

    resultat = rotation.calculer_largeur_marche(marches, taille_panier=50)
    assert resultat["n_actifs_panier"] == 10
    assert resultat["part_surperformant_btc_pct"] == pytest.approx(100.0)


def test_horizon_effectif_declare_quand_il_differe() -> None:
    """Quand l'horizon à 90 jours manque, le repli est annoncé."""
    resultat = rotation.calculer_largeur_marche(_marches(60.0), horizon_jours=90)
    assert resultat["horizon_demande_jours"] == 90
    assert resultat["horizon_effectif_jours"] == 30
    assert "ne renseigne pas la variation à 90 jours" in resultat["motif_horizon"]


def test_largeur_refusee_sur_panier_trop_petit() -> None:
    """Moins de dix actifs exploitables : on ne publie pas de part."""
    resultat = rotation.calculer_largeur_marche(_marches(50.0, n=5))
    assert resultat["disponible"] is False
    assert "10 au minimum" in resultat["motif"]


def test_largeur_refusee_sans_bitcoin() -> None:
    """Sans le bitcoin, il n'y a pas de référence de comparaison."""
    marches = [
        {"id": "x", "symbol": "x", "name": "X", "price_change_percentage_30d_in_currency": 5.0}
    ]
    resultat = rotation.calculer_largeur_marche(marches)
    assert resultat["disponible"] is False
    assert "bitcoin absent" in resultat["motif"]


# ---------------------------------------------------------------------------
# 4. Dominance et indice externe
# ---------------------------------------------------------------------------
def test_dominance_sans_historique_sabstient() -> None:
    """Sans cache, la dominance publie sa valeur mais ni tendance ni rang."""
    resultat = rotation.analyser_dominance(_contexte(58.9), [])
    assert resultat["disponible"] is True
    assert resultat["valeur_pct"] == pytest.approx(58.9)
    assert resultat["variation_30j_points"] is None
    assert resultat["percentile"] is None
    assert "n'est pas assez" in resultat["motif_fenetre"] or "observation" in resultat["motif_fenetre"]


def test_dominance_avec_historique() -> None:
    """Avec assez d'observations, tendance et percentile apparaissent."""
    resultat = rotation.analyser_dominance(_contexte(60.0), _historique(55.0, 60.0))
    assert resultat["variation_30j_points"] is not None
    assert resultat["variation_30j_points"] > 0.0
    assert resultat["tendance"] == "en hausse"
    assert resultat["percentile"] is not None


def test_indice_externe_extrait_malgre_le_balisage() -> None:
    """La valeur est lue même quand des commentaires coupent le libellé."""
    html = '<button type="button">Altcoin Season (<!-- -->43<!-- -->)</button>'
    resultat = rotation.lire_indice_externe(html=html)
    assert resultat["disponible"] is True
    assert resultat["valeur"] == 43
    assert resultat["qualification"] == "altcoin"


def test_indice_externe_absent_signale() -> None:
    """Une page qui a changé de forme est déclarée indisponible."""
    resultat = rotation.lire_indice_externe(html="<html>rien d'utile</html>")
    assert resultat["disponible"] is False
    assert "structure" in resultat["motif"]
