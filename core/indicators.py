"""Indicateurs techniques causaux calculés sur pandas.

Convention de données
---------------------
Toutes les fonctions attendent un ``DataFrame`` indexé par date
(``DatetimeIndex`` trié en ordre croissant), dont les colonnes sont en
minuscules : ``open``, ``high``, ``low``, ``close``, ``volume``.

Contrainte absolue : aucun regard vers le futur
-----------------------------------------------
La valeur produite à la position ``i`` ne dépend que des observations de
position ``0`` à ``i`` incluses. C'est la propriété qui garantit qu'un
backtest et le scan du matin voient exactement la même chose.

Sont donc interdits dans ce module :

* les fenêtres centrées (``rolling(..., center=True)``) ;
* tout remplissage arrière (``bfill``, ``interpolate`` bidirectionnel) ;
* tout décalage négatif (``shift(-n)``) ;
* toute normalisation par une statistique calculée sur l'échantillon
  complet (moyenne globale, min/max globaux, z-score plein historique).

La fonction :func:`verifier_absence_look_ahead` vérifie mécaniquement
cette propriété : elle calcule les indicateurs sur l'historique complet
puis sur des historiques tronqués, et exige une égalité exacte sur la
partie commune.
"""

from __future__ import annotations

import logging
from typing import Final

import numpy as np
import pandas as pd

_LOG: Final = logging.getLogger(__name__)

#: Colonnes OHLCV attendues, en minuscules.
COLONNES_OHLCV: Final[tuple[str, ...]] = ("open", "high", "low", "close", "volume")

__all__ = [
    "COLONNES_OHLCV",
    "sma",
    "ema",
    "rsi",
    "true_range",
    "atr",
    "macd",
    "bollinger",
    "adx",
    "relative_strength",
    "realized_vol",
    "distance_to_ma",
    "enrich",
    "generer_ohlcv_synthetique",
    "verifier_absence_look_ahead",
]


# ---------------------------------------------------------------------------
# Contrôles d'entrée
# ---------------------------------------------------------------------------
def _verifier_ohlcv(df: pd.DataFrame, colonnes: tuple[str, ...] = COLONNES_OHLCV) -> None:
    """Vérifie la présence des colonnes et le tri croissant de l'index.

    Args:
        df: DataFrame à contrôler.
        colonnes: colonnes obligatoires.

    Raises:
        ValueError: si une colonne manque ou si l'index n'est pas trié.
    """
    manquantes = [c for c in colonnes if c not in df.columns]
    if manquantes:
        raise ValueError(
            f"Colonnes manquantes : {manquantes}. "
            f"Attendu (en minuscules) : {list(colonnes)}."
        )
    if not df.index.is_monotonic_increasing:
        raise ValueError(
            "L'index doit être trié en ordre chronologique croissant : un index "
            "désordonné rend toute fenêtre glissante non causale."
        )


# ---------------------------------------------------------------------------
# Moyennes
# ---------------------------------------------------------------------------
def sma(series: pd.Series, window: int = 20) -> pd.Series:
    """Moyenne mobile simple sur une fenêtre glissante fermée à droite.

    Args:
        series: série de valeurs (typiquement le cours de clôture).
        window: taille de la fenêtre, en nombre de barres.

    Returns:
        Série de même index ; les ``window - 1`` premières valeurs sont ``NaN``.
    """
    if window < 1:
        raise ValueError("window doit valoir au moins 1.")
    # min_periods=window : on refuse de produire une valeur partielle, qui
    # serait comparable à rien dans un backtest.
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, window: int = 20) -> pd.Series:
    """Moyenne mobile exponentielle (récurrence causale, ``adjust=False``).

    Args:
        series: série de valeurs.
        window: portée (``span``) de la moyenne exponentielle.

    Returns:
        Série de même index.
    """
    if window < 1:
        raise ValueError("window doit valoir au moins 1.")
    return series.ewm(span=window, adjust=False, min_periods=window).mean()


# ---------------------------------------------------------------------------
# Oscillateurs
# ---------------------------------------------------------------------------
def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """RSI de Wilder, lissage exponentiel de facteur ``1 / window``.

    Le lissage de Wilder est exactement une moyenne exponentielle
    d'``alpha = 1 / window`` appliquée récursivement, donc strictement causale.

    Args:
        close: cours de clôture.
        window: période de Wilder (14 par convention).

    Returns:
        Série dans l'intervalle [0, 100].
    """
    if window < 1:
        raise ValueError("window doit valoir au moins 1.")
    variation = close.diff()
    hausses = variation.clip(lower=0.0)
    baisses = (-variation).clip(lower=0.0)

    alpha = 1.0 / window
    moy_hausse = hausses.ewm(alpha=alpha, adjust=False, min_periods=window).mean()
    moy_baisse = baisses.ewm(alpha=alpha, adjust=False, min_periods=window).mean()

    rs = moy_hausse / moy_baisse
    valeur = 100.0 - (100.0 / (1.0 + rs))

    # Cas dégénérés : aucune baisse sur la fenêtre -> 100 ; marché parfaitement
    # plat (ni hausse ni baisse) -> 50. Les positions NaN restent NaN car une
    # comparaison avec NaN est fausse et ``where`` conserve alors l'original.
    valeur = valeur.where(moy_baisse != 0.0, 100.0)
    valeur = valeur.where(~((moy_baisse == 0.0) & (moy_hausse == 0.0)), 50.0)
    return valeur


# ---------------------------------------------------------------------------
# Volatilité
# ---------------------------------------------------------------------------
def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range de Wilder.

    Maximum entre l'amplitude de la barre et les écarts à la clôture
    précédente. Sur la toute première barre, la clôture précédente est
    inconnue : le True Range se réduit à ``high - low``.

    Args:
        df: DataFrame OHLCV.

    Returns:
        Série du True Range.
    """
    _verifier_ohlcv(df, ("high", "low", "close"))
    cloture_prec = df["close"].shift(1)
    composantes = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - cloture_prec).abs(),
            (df["low"] - cloture_prec).abs(),
        ],
        axis=1,
    )
    return composantes.max(axis=1, skipna=True)


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average True Range, lissé à la Wilder.

    Args:
        df: DataFrame OHLCV.
        window: période de Wilder.

    Returns:
        Série de l'ATR, dans l'unité du prix.
    """
    if window < 1:
        raise ValueError("window doit valoir au moins 1.")
    return true_range(df).ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def realized_vol(
    close: pd.Series, window: int = 20, periodes_par_an: int = 252
) -> pd.Series:
    """Volatilité réalisée annualisée, à partir des rendements logarithmiques.

    Args:
        close: cours de clôture.
        window: fenêtre d'estimation, en barres.
        periodes_par_an: 252 pour des barres journalières d'actions.

    Returns:
        Volatilité annualisée exprimée en fraction (0.18 = 18 %).
    """
    if window < 2:
        raise ValueError("window doit valoir au moins 2 pour un écart-type.")
    rendements = np.log(close / close.shift(1))
    return rendements.rolling(window=window, min_periods=window).std(ddof=1) * np.sqrt(
        periodes_par_an
    )


# ---------------------------------------------------------------------------
# Tendance
# ---------------------------------------------------------------------------
def macd(
    close: pd.Series, rapide: int = 12, lente: int = 26, signal: int = 9
) -> pd.DataFrame:
    """MACD : différence de deux moyennes exponentielles, plus sa ligne de signal.

    Args:
        close: cours de clôture.
        rapide: portée de la moyenne courte.
        lente: portée de la moyenne longue.
        signal: portée du lissage de la ligne MACD.

    Returns:
        DataFrame à trois colonnes : ``macd``, ``macd_signal``, ``macd_hist``.
    """
    if not (rapide < lente):
        raise ValueError("La portée rapide doit être strictement inférieure à la lente.")
    ema_rapide = close.ewm(span=rapide, adjust=False, min_periods=rapide).mean()
    ema_lente = close.ewm(span=lente, adjust=False, min_periods=lente).mean()
    ligne = ema_rapide - ema_lente
    ligne_signal = ligne.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame(
        {
            "macd": ligne,
            "macd_signal": ligne_signal,
            "macd_hist": ligne - ligne_signal,
        }
    )


def bollinger(
    close: pd.Series, window: int = 20, n_ecarts: float = 2.0
) -> pd.DataFrame:
    """Bandes de Bollinger.

    L'écart-type est calculé avec ``ddof=0`` (population), convention retenue
    par la formulation d'origine de Bollinger.

    Args:
        close: cours de clôture.
        window: fenêtre de la moyenne et de l'écart-type.
        n_ecarts: nombre d'écarts-types de part et d'autre.

    Returns:
        DataFrame : ``bb_mid``, ``bb_upper``, ``bb_lower``, ``bb_width``,
        ``bb_pct_b``.
    """
    if window < 2:
        raise ValueError("window doit valoir au moins 2.")
    milieu = close.rolling(window=window, min_periods=window).mean()
    ecart = close.rolling(window=window, min_periods=window).std(ddof=0)
    haute = milieu + n_ecarts * ecart
    basse = milieu - n_ecarts * ecart
    largeur = (haute - basse) / milieu
    # %B : position relative du cours dans le canal (0 = bande basse, 1 = haute).
    amplitude = haute - basse
    pct_b = (close - basse) / amplitude.where(amplitude != 0.0)
    return pd.DataFrame(
        {
            "bb_mid": milieu,
            "bb_upper": haute,
            "bb_lower": basse,
            "bb_width": largeur,
            "bb_pct_b": pct_b,
        }
    )


def adx(df: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    """ADX de Wilder et ses deux composantes directionnelles.

    Args:
        df: DataFrame OHLCV.
        window: période de Wilder.

    Returns:
        DataFrame : ``adx``, ``plus_di``, ``minus_di``.
    """
    if window < 1:
        raise ValueError("window doit valoir au moins 1.")
    _verifier_ohlcv(df, ("high", "low", "close"))

    hausse = df["high"].diff()
    baisse = -df["low"].diff()

    # Mouvement directionnel : on ne retient que le mouvement dominant.
    # Les comparaisons impliquant NaN sont fausses et donnent donc 0 ; on
    # remet explicitement NaN sur la première barre, où le mouvement n'existe
    # pas encore, pour ne pas biaiser l'amorçage du lissage.
    plus_dm = pd.Series(
        np.where((hausse > baisse) & (hausse > 0.0), hausse, 0.0),
        index=df.index,
        dtype="float64",
    )
    minus_dm = pd.Series(
        np.where((baisse > hausse) & (baisse > 0.0), baisse, 0.0),
        index=df.index,
        dtype="float64",
    )
    if len(df) > 0:
        plus_dm.iloc[0] = np.nan
        minus_dm.iloc[0] = np.nan

    alpha = 1.0 / window
    tr_lisse = true_range(df).ewm(alpha=alpha, adjust=False, min_periods=window).mean()
    tr_lisse = tr_lisse.where(tr_lisse != 0.0)  # évite une division par zéro

    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False, min_periods=window).mean() / tr_lisse
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False, min_periods=window).mean() / tr_lisse

    somme_di = plus_di + minus_di
    dx = 100.0 * (plus_di - minus_di).abs() / somme_di.where(somme_di != 0.0)
    ligne_adx = dx.ewm(alpha=alpha, adjust=False, min_periods=window).mean()

    return pd.DataFrame({"adx": ligne_adx, "plus_di": plus_di, "minus_di": minus_di})


# ---------------------------------------------------------------------------
# Force relative et position par rapport à une moyenne
# ---------------------------------------------------------------------------
def relative_strength(
    series: pd.Series, benchmark: pd.Series, window: int = 63
) -> pd.Series:
    """Force relative : surperformance sur ``window`` barres face à un benchmark.

    Le benchmark est réaligné sur l'index de la série puis complété vers
    l'avant (``ffill``), ce qui n'utilise que des valeurs passées : un jour
    férié sur le benchmark reprend son dernier cours connu, jamais le suivant.

    Args:
        series: série étudiée (cours de clôture de l'actif).
        benchmark: série de référence (par exemple SPY).
        window: horizon de comparaison, en barres (63 ≈ un trimestre boursier).

    Returns:
        Écart de performance exprimé en fraction (0.05 = 5 points de mieux).
    """
    if window < 1:
        raise ValueError("window doit valoir au moins 1.")
    actif = series.astype("float64")
    reference = benchmark.reindex(actif.index).ffill().astype("float64")

    perf_actif = actif / actif.shift(window) - 1.0
    perf_reference = reference / reference.shift(window) - 1.0
    return perf_actif - perf_reference


def distance_to_ma(close: pd.Series, window: int = 200) -> pd.Series:
    """Écart en pourcentage entre le cours et sa moyenne mobile simple.

    Args:
        close: cours de clôture.
        window: période de la moyenne mobile.

    Returns:
        Écart en pourcentage (+3.2 = le cours est 3,2 % au-dessus de la moyenne).
    """
    moyenne = sma(close, window)
    return (close / moyenne.where(moyenne != 0.0) - 1.0) * 100.0


# ---------------------------------------------------------------------------
# Enrichissement complet
# ---------------------------------------------------------------------------
def enrich(df: pd.DataFrame, benchmark: pd.Series | None = None) -> pd.DataFrame:
    """Ajoute le jeu complet d'indicateurs à un DataFrame OHLCV.

    La fonction ne modifie pas l'objet reçu : elle renvoie une copie enrichie.

    Args:
        df: DataFrame OHLCV indexé par date, trié en ordre croissant.
        benchmark: série de clôtures de référence. Si elle est fournie, la
            colonne ``rs_63`` (force relative trimestrielle) est ajoutée.

    Returns:
        Copie du DataFrame augmentée des colonnes d'indicateurs.
    """
    _verifier_ohlcv(df)
    out = df.copy()
    cloture = out["close"].astype("float64")

    # Moyennes mobiles
    out["sma_20"] = sma(cloture, 20)
    out["sma_50"] = sma(cloture, 50)
    out["sma_200"] = sma(cloture, 200)
    out["ema_12"] = ema(cloture, 12)
    out["ema_26"] = ema(cloture, 26)

    # Momentum
    out["rsi_14"] = rsi(cloture, 14)
    out = out.join(macd(cloture))

    # Volatilité
    out["atr_14"] = atr(out, 14)
    out["atr_pct"] = out["atr_14"] / cloture.where(cloture != 0.0) * 100.0
    out["vol_20"] = realized_vol(cloture, 20)
    out["vol_60"] = realized_vol(cloture, 60)
    out = out.join(bollinger(cloture))

    # Tendance
    out = out.join(adx(out, 14))

    # Position par rapport aux moyennes
    out["dist_sma_50"] = distance_to_ma(cloture, 50)
    out["dist_sma_200"] = distance_to_ma(cloture, 200)

    # Rendements et volume
    out["ret_1"] = cloture.pct_change(periods=1, fill_method=None)
    out["ret_5"] = cloture.pct_change(periods=5, fill_method=None)
    out["ret_21"] = cloture.pct_change(periods=21, fill_method=None)
    volume = out["volume"].astype("float64")
    moy_volume = volume.rolling(window=20, min_periods=20).mean()
    out["volume_ratio_20"] = volume / moy_volume.where(moy_volume != 0.0)

    if benchmark is not None:
        out["rs_63"] = relative_strength(cloture, benchmark, 63)

    return out


# ---------------------------------------------------------------------------
# Validation : jeu de données synthétique et contrôle anti-look-ahead
# ---------------------------------------------------------------------------
def generer_ohlcv_synthetique(n: int = 600, graine: int = 20260907) -> pd.DataFrame:
    """Fabrique un historique OHLCV reproductible pour les tests.

    Aucun appel réseau : la validation du socle ne doit dépendre d'aucune
    source externe.

    Args:
        n: nombre de barres journalières.
        graine: graine du générateur pseudo-aléatoire.

    Returns:
        DataFrame OHLCV indexé par jours ouvrés.
    """
    rng = np.random.default_rng(graine)
    dates = pd.bdate_range("2022-01-03", periods=n, name="date")

    rendements = rng.normal(loc=0.0003, scale=0.011, size=n)
    cloture = 100.0 * np.exp(np.cumsum(rendements))

    amplitude = np.abs(rng.normal(loc=0.008, scale=0.004, size=n)) * cloture
    ouverture = cloture + rng.normal(loc=0.0, scale=0.004, size=n) * cloture
    haut = np.maximum(ouverture, cloture) + amplitude * rng.uniform(0.2, 0.8, size=n)
    bas = np.minimum(ouverture, cloture) - amplitude * rng.uniform(0.2, 0.8, size=n)
    volume = rng.integers(500_000, 5_000_000, size=n).astype("float64")

    return pd.DataFrame(
        {
            "open": ouverture,
            "high": haut,
            "low": bas,
            "close": cloture,
            "volume": volume,
        },
        index=dates,
    )


def _comparer_exactement(
    complete: pd.Series, tronquee: pd.Series
) -> tuple[bool, float, str]:
    """Compare deux séries sur leur index commun, en exigeant l'égalité exacte.

    Args:
        complete: valeurs issues de l'historique complet.
        tronquee: valeurs issues de l'historique tronqué.

    Returns:
        Triplet ``(identiques, ecart_max, motif)``.
    """
    a = complete.reindex(tronquee.index)
    b = tronquee

    nan_a, nan_b = a.isna().to_numpy(), b.isna().to_numpy()
    if not np.array_equal(nan_a, nan_b):
        n_diff = int(np.sum(nan_a != nan_b))
        return False, float("nan"), f"{n_diff} position(s) NaN divergente(s)"

    masque = ~nan_a
    if not masque.any():
        return True, 0.0, "aucune valeur définie"

    ecarts = np.abs(a.to_numpy()[masque] - b.to_numpy()[masque])
    ecart_max = float(np.max(ecarts))
    return ecart_max == 0.0, ecart_max, "" if ecart_max == 0.0 else "écart numérique"


def verifier_absence_look_ahead(
    df: pd.DataFrame | None = None,
    troncatures: tuple[int, ...] = (250, 400, 500, 599),
    benchmark: pd.Series | None = None,
) -> dict[str, object]:
    """Vérifie qu'aucun indicateur ne regarde le futur.

    Principe : on enrichit l'historique complet, puis on enrichit des
    historiques tronqués à différentes dates. Si un indicateur est causal, ses
    valeurs sur la partie commune sont rigoureusement identiques dans les deux
    calculs. Le moindre écart signale une fuite d'information du futur vers le
    passé.

    Args:
        df: historique OHLCV à tester. Un jeu synthétique est produit si absent.
        troncatures: positions auxquelles couper l'historique.
        benchmark: série de référence facultative pour la force relative.

    Returns:
        Dictionnaire de rapport : ``ok``, ``n_colonnes``, ``n_troncatures``,
        ``ecart_max_global`` et la liste ``echecs``.
    """
    if df is None:
        df = generer_ohlcv_synthetique()
    if benchmark is None:
        benchmark = generer_ohlcv_synthetique(len(df), graine=1234)["close"]
        benchmark.index = df.index

    reference = enrich(df, benchmark=benchmark)
    colonnes = [c for c in reference.columns if c not in COLONNES_OHLCV]

    echecs: list[dict[str, object]] = []
    ecart_max_global = 0.0

    for coupe in troncatures:
        if coupe < 2 or coupe > len(df):
            _LOG.warning("Troncature %s ignorée : hors de l'historique.", coupe)
            continue
        partiel = enrich(df.iloc[:coupe], benchmark=benchmark.iloc[:coupe])

        for colonne in colonnes:
            identiques, ecart, motif = _comparer_exactement(
                reference[colonne], partiel[colonne]
            )
            if np.isfinite(ecart):
                ecart_max_global = max(ecart_max_global, ecart)
            if not identiques:
                echecs.append(
                    {"colonne": colonne, "troncature": coupe, "ecart": ecart, "motif": motif}
                )

    return {
        "ok": not echecs,
        "n_colonnes": len(colonnes),
        "n_troncatures": len([c for c in troncatures if 2 <= c <= len(df)]),
        "colonnes": colonnes,
        "ecart_max_global": ecart_max_global,
        "echecs": echecs,
    }


def _main() -> int:
    """Point d'entrée : ``python -m core.indicators`` lance la vérification."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    rapport = verifier_absence_look_ahead()

    print("Contrôle anti-look-ahead des indicateurs")
    print("-" * 52)
    print(f"Colonnes d'indicateurs testées : {rapport['n_colonnes']}")
    print(f"Troncatures appliquées         : {rapport['n_troncatures']}")
    print(f"Écart maximal observé          : {rapport['ecart_max_global']:.3e}")
    if rapport["ok"]:
        print("Résultat : OK, aucun indicateur ne regarde le futur.")
        return 0
    print(f"Résultat : ÉCHEC sur {len(rapport['echecs'])} cas.")
    for echec in rapport["echecs"][:20]:
        print(f"  - {echec['colonne']} @ {echec['troncature']} : {echec['motif']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
