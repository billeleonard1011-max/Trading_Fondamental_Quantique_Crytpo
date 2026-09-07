"""Tests du socle : causalité des indicateurs et cohérence du contrat de stratégie.

Le test central est celui de l'absence de look-ahead. Un indicateur qui
regarde le futur donne un backtest flatteur et un scan inutile ; c'est la
seule erreur de ce projet qui soit à la fois invisible et fatale.

Deux vérifications indépendantes sont faites :

1. **Troncature** — enrichir l'historique complet puis un historique coupé
   doit produire exactement les mêmes valeurs sur la partie commune.
2. **Perturbation du futur** — modifier violemment les barres postérieures à
   une date ne doit rien changer aux valeurs antérieures à cette date.

La seconde attrape des fuites que la première laisserait passer, par exemple
une normalisation par un extremum calculé sur tout l'échantillon.

Exécution :
    pytest tests/ -v
    python -m tests.test_indicators      (sans pytest)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators
from core.strategy import Position, RiskConfig, Signal, Strategy


# ---------------------------------------------------------------------------
# Causalité
# ---------------------------------------------------------------------------
def test_absence_look_ahead_par_troncature() -> None:
    """Les indicateurs sont identiques sur historique complet et tronqué."""
    df = indicators.generer_ohlcv_synthetique(600)
    rapport = indicators.verifier_absence_look_ahead(df, troncatures=(250, 400, 500, 599))

    assert rapport["n_colonnes"] > 20, "Trop peu d'indicateurs testés."
    assert rapport["ecart_max_global"] == 0.0, (
        f"Écart non nul entre calcul complet et tronqué : "
        f"{rapport['ecart_max_global']:.3e}"
    )
    assert rapport["ok"], f"Fuite détectée : {rapport['echecs'][:5]}"


def test_absence_look_ahead_par_perturbation_du_futur() -> None:
    """Modifier le futur ne change aucune valeur passée."""
    df = indicators.generer_ohlcv_synthetique(600)
    coupure = 400

    perturbe = df.copy()
    # Choc massif et asymétrique sur toutes les barres postérieures : si un
    # indicateur regarde le futur, il ne peut pas y survivre.
    for colonne in ("open", "high", "low", "close"):
        perturbe.iloc[coupure:, perturbe.columns.get_loc(colonne)] *= 3.5
    perturbe.iloc[coupure:, perturbe.columns.get_loc("volume")] *= 100.0

    reference = indicators.enrich(df).iloc[:coupure]
    apres_choc = indicators.enrich(perturbe).iloc[:coupure]

    colonnes = [c for c in reference.columns if c not in indicators.COLONNES_OHLCV]
    divergentes: list[str] = []
    for colonne in colonnes:
        a, b = reference[colonne], apres_choc[colonne]
        if not np.array_equal(a.isna().to_numpy(), b.isna().to_numpy()):
            divergentes.append(f"{colonne} (NaN)")
            continue
        masque = ~a.isna().to_numpy()
        if masque.any() and not np.array_equal(a.to_numpy()[masque], b.to_numpy()[masque]):
            divergentes.append(colonne)

    assert not divergentes, f"Ces indicateurs lisent le futur : {divergentes}"


def test_force_relative_est_causale() -> None:
    """La force relative ne dépend pas des valeurs futures du benchmark."""
    df = indicators.generer_ohlcv_synthetique(400)
    benchmark = indicators.generer_ohlcv_synthetique(400, graine=99)["close"]
    benchmark.index = df.index
    coupure = 300

    complet = indicators.relative_strength(df["close"], benchmark, 63).iloc[:coupure]
    tronque = indicators.relative_strength(
        df["close"].iloc[:coupure], benchmark.iloc[:coupure], 63
    )
    pd.testing.assert_series_equal(complet, tronque, check_exact=True)


# ---------------------------------------------------------------------------
# Exactitude des formules
# ---------------------------------------------------------------------------
def test_sma_valeurs_connues() -> None:
    """La moyenne mobile simple donne la valeur attendue à la main."""
    serie = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    resultat = indicators.sma(serie, 3)
    assert resultat.isna().sum() == 2, "Les deux premières valeurs doivent être NaN."
    assert resultat.iloc[2] == 2.0
    assert resultat.iloc[4] == 4.0


def test_rsi_reste_borne() -> None:
    """Le RSI reste dans [0, 100] et vaut 100 sur une hausse ininterrompue."""
    df = indicators.generer_ohlcv_synthetique(300)
    valeurs = indicators.rsi(df["close"], 14).dropna()
    assert valeurs.between(0.0, 100.0).all(), "RSI hors de l'intervalle [0, 100]."

    monotone = pd.Series(np.arange(1.0, 60.0))
    assert indicators.rsi(monotone, 14).dropna().iloc[-1] == 100.0


def test_atr_positif_et_borne_par_amplitude() -> None:
    """L'ATR est positif et jamais inférieur à l'amplitude minimale des barres."""
    df = indicators.generer_ohlcv_synthetique(300)
    valeurs = indicators.atr(df, 14).dropna()
    assert (valeurs > 0.0).all(), "ATR négatif ou nul."
    assert valeurs.max() <= (df["high"] - df["low"]).max() * 5.0


def test_bollinger_ordre_des_bandes() -> None:
    """La bande haute est au-dessus de la médiane, elle-même au-dessus de la basse."""
    df = indicators.generer_ohlcv_synthetique(300)
    bandes = indicators.bollinger(df["close"], 20, 2.0).dropna()
    assert (bandes["bb_upper"] >= bandes["bb_mid"]).all()
    assert (bandes["bb_mid"] >= bandes["bb_lower"]).all()


def test_adx_borne() -> None:
    """L'ADX et les composantes directionnelles restent dans [0, 100]."""
    df = indicators.generer_ohlcv_synthetique(400)
    resultat = indicators.adx(df, 14).dropna()
    for colonne in ("adx", "plus_di", "minus_di"):
        assert resultat[colonne].between(0.0, 100.0).all(), f"{colonne} hors bornes."


def test_enrich_preserve_ohlcv_et_index() -> None:
    """L'enrichissement ajoute des colonnes sans toucher aux données d'origine."""
    df = indicators.generer_ohlcv_synthetique(300)
    enrichi = indicators.enrich(df)

    assert enrichi.index.equals(df.index)
    for colonne in indicators.COLONNES_OHLCV:
        pd.testing.assert_series_equal(enrichi[colonne], df[colonne])
    assert len(enrichi.columns) > len(df.columns)
    assert list(df.columns) == list(indicators.COLONNES_OHLCV), "df d'origine modifié."


def test_enrich_refuse_colonnes_absentes() -> None:
    """Un DataFrame incomplet est rejeté explicitement."""
    df = indicators.generer_ohlcv_synthetique(50).drop(columns=["volume"])
    try:
        indicators.enrich(df)
    except ValueError as exc:
        assert "volume" in str(exc)
    else:
        raise AssertionError("enrich aurait dû refuser un DataFrame sans volume.")


# ---------------------------------------------------------------------------
# Contrat de stratégie
# ---------------------------------------------------------------------------
def test_dimensionnement_par_le_risque() -> None:
    """La perte au stop égale exactement le risque par trade paramétré."""

    class _Muette(Strategy):
        """Stratégie minimale, uniquement pour tester le dimensionnement."""

        def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
            return df

        def entry_signal(self, df, i, context=None):  # type: ignore[no-untyped-def]
            return None

        def exit_signal(self, df, i, position):  # type: ignore[no-untyped-def]
            return False, ""

    strategie = _Muette(RiskConfig(risk_per_trade_pct=0.5, max_daily_loss_pct=3.0))
    capital = 10_000.0
    position = Position(signal=Signal.LONG, entry=2_400.0, stop=2_388.0)

    unites = strategie.position_size(capital, position)
    perte_au_stop = unites * position.risk_per_unit

    assert abs(perte_au_stop - capital * 0.005) < 1e-9, (
        f"Perte au stop {perte_au_stop:.4f} au lieu de {capital * 0.005:.4f}."
    )
    assert position.size_pct is not None


def test_plafond_exposition_reduit_la_taille() -> None:
    """Le plafond d'exposition réduit la taille, il ne l'augmente jamais."""

    class _Muette(Strategy):
        def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
            return df

        def entry_signal(self, df, i, context=None):  # type: ignore[no-untyped-def]
            return None

        def exit_signal(self, df, i, position):  # type: ignore[no-untyped-def]
            return False, ""

    strategie = _Muette(
        RiskConfig(risk_per_trade_pct=1.0, max_exposure_pct=20.0, max_daily_loss_pct=3.0)
    )
    capital = 10_000.0
    # Stop très serré : le dimensionnement par le risque demanderait un
    # notionnel bien supérieur au plafond de 20 %.
    position = Position(signal=Signal.LONG, entry=100.0, stop=99.9)

    unites = strategie.position_size(capital, position)
    notionnel = unites * position.entry

    assert notionnel <= capital * 0.20 + 1e-9, "Plafond d'exposition non respecté."
    assert unites * position.risk_per_unit < capital * 0.01, (
        "Le plafond doit réduire le risque effectif, pas l'augmenter."
    )


def test_stop_obligatoire_du_bon_cote() -> None:
    """Une position dont le stop est du mauvais côté est refusée."""
    for arguments in (
        dict(signal=Signal.LONG, entry=100.0, stop=101.0),
        dict(signal=Signal.SHORT, entry=100.0, stop=99.0),
    ):
        try:
            Position(**arguments)  # type: ignore[arg-type]
        except ValueError:
            continue
        raise AssertionError(f"Position incohérente acceptée : {arguments}")


def test_risque_par_trade_inferieur_a_la_perte_journaliere() -> None:
    """Un risque par trade supérieur à la perte journalière est refusé."""
    try:
        RiskConfig(risk_per_trade_pct=5.0, max_daily_loss_pct=3.0)
    except ValueError:
        return
    raise AssertionError("Paramétrage incohérent accepté.")


# ---------------------------------------------------------------------------
# Exécution directe, sans pytest
# ---------------------------------------------------------------------------
def _executer_sans_pytest() -> int:
    """Lance tous les tests du module et affiche un rapport lisible.

    Returns:
        0 si tous les tests passent, 1 sinon.
    """
    tests = [
        (nom, fonction)
        for nom, fonction in sorted(globals().items())
        if nom.startswith("test_") and callable(fonction)
    ]

    print("Tests du socle")
    print("=" * 70)
    echecs: list[tuple[str, str]] = []

    for nom, fonction in tests:
        try:
            fonction()
        except AssertionError as exc:
            echecs.append((nom, str(exc)))
            print(f"ÉCHEC  {nom}\n       {exc}")
        except Exception as exc:  # noqa: BLE001
            echecs.append((nom, f"{type(exc).__name__}: {exc}"))
            print(f"ERREUR {nom}\n       {type(exc).__name__}: {exc}")
        else:
            print(f"OK     {nom}")

    print("=" * 70)
    print(f"{len(tests) - len(echecs)}/{len(tests)} test(s) réussi(s).")
    return 1 if echecs else 0


if __name__ == "__main__":
    raise SystemExit(_executer_sans_pytest())
