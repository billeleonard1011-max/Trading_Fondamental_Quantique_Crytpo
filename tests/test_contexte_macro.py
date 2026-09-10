"""Tests du contexte macro en prose (inflation, chômage, pétrole, appétit).

La règle vérifiée ici est celle du projet entier, appliquée au texte : une
affirmation sans chiffre daté derrière n'a pas le droit d'être publiée. Le
défaut corrigé sur « le marché ne relaie pas l'événement par les canaux
habituels » ne doit pas réapparaître sous une autre forme.

Aucun test n'accède au réseau : les séries FRED sont fabriquées ici.

Exécution :
    pytest tests/test_contexte_macro.py -v
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from dataio import macro


# ---------------------------------------------------------------------------
# Fabriques
# ---------------------------------------------------------------------------
def _series_macro(n: int = 600, inflation_pct: float = 2.5) -> pd.DataFrame:
    """Fabrique un DataFrame FRED plausible, aligné en jours ouvrés.

    Args:
        n: nombre de séances.
        inflation_pct: glissement annuel visé pour ``CPIAUCSL``.

    Returns:
        DataFrame contenant les séries utilisées par le contexte macro.
    """
    index = pd.bdate_range("2024-01-01", periods=n, name="date")
    # CPI : croissance géométrique calibrée pour donner le glissement voulu.
    croissance = (1.0 + inflation_pct / 100.0) ** (1.0 / macro.JOURS_OUVRES_PAR_AN)
    cpi = 300.0 * croissance ** np.arange(n)
    return pd.DataFrame(
        {
            "CPIAUCSL": cpi,
            "UNRATE": np.linspace(3.8, 4.3, n),
            "DCOILWTICO": np.linspace(70.0, 78.0, n),
            "VIXCLS": np.linspace(20.0, 14.0, n),
            "BAMLH0A0HYM2": np.linspace(4.0, 3.2, n),
            "T10Y2Y": np.linspace(-0.2, 0.4, n),
            "DGS10": np.linspace(4.0, 4.2, n),
            "DGS2": np.linspace(4.2, 3.8, n),
        },
        index=index,
    )


def _prix_secteurs(n: int = 60, avantage_cyclique: float = 4.0) -> pd.DataFrame:
    """Fabrique des clôtures sectorielles, cycliques en avance sur défensives."""
    index = pd.bdate_range("2026-06-01", periods=n, name="date")
    colonnes = {}
    for ticker in macro.GROUPE_CYCLIQUES:
        colonnes[ticker] = np.linspace(100.0, 100.0 + avantage_cyclique, n)
    for ticker in macro.GROUPE_DEFENSIFS:
        colonnes[ticker] = np.full(n, 100.0)
    return pd.DataFrame(colonnes, index=index)


# ---------------------------------------------------------------------------
# 1. Les nouveaux axes
# ---------------------------------------------------------------------------
def test_inflation_est_lue_en_glissement_annuel() -> None:
    """L'inflation publiée est un taux annuel, jamais le niveau de l'indice."""
    regime = macro.compute_macro_regime(_series_macro(inflation_pct=2.5))
    assert regime.inflation.disponible
    assert regime.inflation.valeur == pytest.approx(2.5, abs=0.15)
    assert "%" in regime.inflation.commentaire


def test_chomage_donne_le_niveau_et_son_inflexion() -> None:
    regime = macro.compute_macro_regime(_series_macro())
    assert regime.chomage.disponible
    assert "4," in regime.chomage.commentaire or "4." in regime.chomage.commentaire
    assert "sur un an" in regime.chomage.commentaire


def test_petrole_est_lu_en_variation_mensuelle() -> None:
    regime = macro.compute_macro_regime(_series_macro())
    assert regime.petrole.disponible
    assert "sur un mois" in regime.petrole.commentaire
    assert "$" in regime.petrole.commentaire


def test_appetit_risque_combine_vix_credit_et_rotation() -> None:
    """Les trois signaux sont cités nommément dans le commentaire."""
    regime = macro.compute_macro_regime(_series_macro(), _prix_secteurs())
    axe = regime.appetit_risque
    assert axe.disponible
    assert "VIX" in axe.commentaire
    assert "spread haut rendement" in axe.commentaire
    assert "cycliques" in axe.commentaire
    assert axe.niveau in {"risk-on", "risk-off", "neutre"}


def test_appetit_risque_sans_prix_sectoriels_reste_lisible() -> None:
    """Sans les prix, les deux autres signaux suffisent — pas d'échec global."""
    axe = macro.compute_macro_regime(_series_macro()).appetit_risque
    assert axe.disponible
    assert "cycliques" not in axe.commentaire
    assert "2 signal(aux)" in axe.commentaire


def test_vix_bas_et_credit_calme_donnent_un_climat_risk_on() -> None:
    """Un VIX au plus bas et un crédit détendu se lisent comme un appétit."""
    axe = macro.compute_macro_regime(_series_macro(), _prix_secteurs()).appetit_risque
    assert axe.niveau == "risk-on"
    assert axe.valeur > 0


def test_axes_indisponibles_sont_nommes_pas_contournes() -> None:
    """Une série absente est signalée, jamais remplacée par une formule vague."""
    partiel = _series_macro()[["UNRATE"]]
    regime = macro.compute_macro_regime(partiel)
    assert not regime.inflation.disponible
    assert "CPIAUCSL" in regime.inflation.commentaire
    assert regime.chomage.disponible


# ---------------------------------------------------------------------------
# 2. Le narratif — la discipline du chiffre daté
# ---------------------------------------------------------------------------
def _phrases(texte: str) -> list[str]:
    """Découpe un texte en phrases non vides."""
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+", texte) if p.strip()]


def test_chaque_phrase_du_narratif_porte_un_chiffre() -> None:
    """La règle centrale : aucune affirmation sans donnée derrière."""
    df = _series_macro()
    contexte = macro.rediger_contexte_macro(macro.compute_macro_regime(df, _prix_secteurs()), df)
    assert contexte["disponible"]
    for phrase in _phrases(contexte["texte"]):
        assert re.search(r"\d", phrase), f"phrase sans chiffre : « {phrase} »"


def test_le_narratif_date_chaque_serie_citee() -> None:
    """Les chiffres cités sont datés : sinon ils ne sont pas vérifiables."""
    df = _series_macro()
    contexte = macro.rediger_contexte_macro(macro.compute_macro_regime(df), df)
    for serie in ("CPIAUCSL", "UNRATE", "DCOILWTICO", "VIXCLS", "BAMLH0A0HYM2"):
        assert serie in contexte["dates_series"], f"{serie} citée sans date"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", contexte["dates_series"][serie])
    assert contexte["date_lecture"]


def test_le_narratif_se_termine_par_ce_qui_linvaliderait() -> None:
    df = _series_macro()
    contexte = macro.rediger_contexte_macro(macro.compute_macro_regime(df, _prix_secteurs()), df)
    assert contexte["invalidation"].startswith("Cette lecture serait invalidée si")
    # Chaque condition d'invalidation porte elle aussi son seuil chiffré.
    assert re.search(r"\d", contexte["invalidation"])


def test_le_narratif_nomme_les_axes_indisponibles() -> None:
    """Un axe muet est listé, pas passé sous silence ni comblé."""
    partiel = _series_macro()[["UNRATE", "VIXCLS", "BAMLH0A0HYM2"]]
    contexte = macro.rediger_contexte_macro(macro.compute_macro_regime(partiel), partiel)
    muets = {a["axe"] for a in contexte["axes_indisponibles"]}
    assert "inflation" in muets
    assert "petrole" in muets
    for entree in contexte["axes_indisponibles"]:
        assert entree["motif"], f"axe {entree['axe']} sans motif"


def test_sans_aucune_serie_le_narratif_refuse_de_conclure() -> None:
    """Aucune donnée : pas de texte inventé, un motif explicite."""
    contexte = macro.rediger_contexte_macro(macro.compute_macro_regime(pd.DataFrame()))
    assert not contexte["disponible"]
    assert contexte["texte"] == ""
    assert contexte["motif"]


def test_le_narratif_ne_contient_aucune_formulation_vague_sans_chiffre() -> None:
    """Les tournures creuses déjà bannies ailleurs ne doivent pas revenir."""
    df = _series_macro()
    contexte = macro.rediger_contexte_macro(macro.compute_macro_regime(df, _prix_secteurs()), df)
    for creuse in (
        "canaux habituels",
        "les marchés semblent",
        "il semblerait que",
        "on peut penser",
    ):
        assert creuse not in contexte["texte"].lower()


# ---------------------------------------------------------------------------
# 3. Recul historique — le récit long terme vient des séries, pas des rapports
# ---------------------------------------------------------------------------
def test_recul_historique_situe_chaque_serie_sur_six_mois() -> None:
    recul = macro.recul_historique(_series_macro(n=600))
    assert recul["disponible"]
    for cle in ("inflation", "chomage", "petrole", "vix", "spread_credit"):
        assert cle in recul, f"série {cle} absente du recul"
    assert recul["petrole"]["variation_6_mois_pct"] is not None
    assert recul["inflation"]["il_y_a_6_mois_pct"] is not None
    # Les séries absentes de la fabrique ne sont pas comblées.
    assert "taux_reels" not in recul and "dollar" not in recul


def test_recul_historique_mesure_les_taux_reels_en_points_de_base() -> None:
    df = _series_macro(n=600)
    df["DFII10"] = np.linspace(1.8, 2.3, len(df))     # +50 pb sur la fenêtre
    recul = macro.recul_historique(df)
    taux = recul["taux_reels"]
    assert taux["actuel_pct"] == 2.3
    assert taux["ecart_points_base"] > 0
    assert abs(taux["ecart_points_base"] - (2.3 - taux["il_y_a_6_mois_pct"]) * 100) < 0.6


def test_recul_historique_ne_comble_jamais_une_serie_trop_courte() -> None:
    court = _series_macro(n=80)          # moins de six mois de séances
    recul = macro.recul_historique(court)
    assert "inflation" not in recul      # un an d'historique requis
    assert "vix" not in recul            # six mois requis


def test_recul_historique_sans_donnees_est_marque_indisponible() -> None:
    recul = macro.recul_historique(pd.DataFrame())
    assert recul["disponible"] is False
    assert "inflation" not in recul


def test_recul_historique_arrondit_pour_etre_citable() -> None:
    """Deux décimales au plus : au-delà, le vérificateur numérique lit un séparateur de milliers."""
    recul = macro.recul_historique(_series_macro(n=600))
    for bloc in recul.values():
        if isinstance(bloc, dict):
            for valeur in bloc.values():
                if isinstance(valeur, float):
                    assert round(valeur, 2) == valeur
