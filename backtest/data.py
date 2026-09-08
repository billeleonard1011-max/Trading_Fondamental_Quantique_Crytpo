"""Agrégation des unités de temps à partir d'une unique série M1.

Pourquoi une seule source
-------------------------
Toutes les unités de temps du backtest — M3, M5, M15, M30, H1 — sont
construites ici à partir de la même série d'une minute. Mélanger deux
fournisseurs pour deux unités différentes suffirait à fabriquer des signaux
qui n'ont jamais existé : un décalage d'une seconde entre deux séries déplace
une clôture d'un côté ou de l'autre d'un niveau, et le motif apparaît ou
disparaît sans que rien ne le signale.

Bougies closes uniquement
-------------------------
Une bougie H1 n'existe pour le moteur qu'une fois sa dernière minute écoulée.
:func:`fenetre_close` applique cette règle : à l'instant ``t``, elle ne rend
que les bougies dont la fin est **antérieure ou égale** à ``t``. C'est la
première ligne de défense contre le look-ahead, et elle est vérifiée par test.
"""

from __future__ import annotations

import logging
from typing import Final

import pandas as pd

_LOG: Final = logging.getLogger(__name__)

#: Unités de temps construites, avec leur règle de rééchantillonnage pandas.
UNITES: Final[dict[str, str]] = {
    "M1": "1min",
    "M3": "3min",
    "M5": "5min",
    "M15": "15min",
    "M30": "30min",
    "H1": "1h",
}

#: Durée de chaque unité, pour savoir quand une bougie est close.
DUREES: Final[dict[str, pd.Timedelta]] = {
    nom: pd.Timedelta(regle) for nom, regle in UNITES.items()
}

#: Unités sur lesquelles les order blocks sont cherchés.
UNITES_ORDER_BLOCK: Final[tuple[str, ...]] = ("H1", "M30", "M15")

#: Unités où l'on cherche un FVG de confirmation, dans cet ordre.
UNITES_FVG: Final[tuple[str, ...]] = ("M5", "M3", "M1")

__all__ = ["UNITES", "DUREES", "UNITES_ORDER_BLOCK", "UNITES_FVG", "agreger", "fenetre_close"]


def agreger(m1: pd.DataFrame, unite: str) -> pd.DataFrame:
    """Construit une unité de temps supérieure à partir du M1.

    L'horodatage d'une bougie est celui de son **ouverture**, convention
    usuelle. Sa clôture se déduit en ajoutant la durée de l'unité, ce dont
    :func:`fenetre_close` a besoin pour savoir si elle est terminée.

    Args:
        m1: bougies d'une minute, indexées par horodatage.
        unite: nom de l'unité voulue, clé de :data:`UNITES`.

    Returns:
        DataFrame OHLCV indexé par ouverture. Vide si l'entrée l'est ou si
        l'unité est inconnue.
    """
    if m1 is None or m1.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    regle = UNITES.get(unite)
    if regle is None:
        _LOG.error("Unité de temps inconnue : %s", unite)
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    if unite == "M1":
        return m1[["open", "high", "low", "close", "volume"]].copy()

    groupes = m1.resample(regle, label="left", closed="left")
    cadre = pd.DataFrame(
        {
            "open": groupes["open"].first(),
            "high": groupes["high"].max(),
            "low": groupes["low"].min(),
            "close": groupes["close"].last(),
            "volume": groupes["volume"].sum(),
        }
    )
    # Les périodes sans la moindre minute cotée — week-end, fermeture — ne
    # sont pas des bougies plates : elles n'existent pas.
    return cadre.dropna(subset=["open", "high", "low", "close"])


def fenetre_close(cadre: pd.DataFrame, unite: str, instant: pd.Timestamp) -> pd.DataFrame:
    """Ne rend que les bougies effectivement closes à un instant donné.

    C'est la barrière anti-look-ahead du moteur. Une bougie H1 ouverte à
    10:00 n'est close qu'à 11:00 : la consulter à 10:30 reviendrait à lire
    une clôture qui n'a pas encore eu lieu.

    Args:
        cadre: bougies de l'unité, indexées par ouverture.
        unite: nom de l'unité, pour connaître sa durée.
        instant: instant courant du moteur.

    Returns:
        Sous-ensemble des bougies dont la clôture est antérieure ou égale à
        ``instant``.
    """
    if cadre is None or cadre.empty:
        return cadre if cadre is not None else pd.DataFrame()

    duree = DUREES.get(unite)
    if duree is None:
        _LOG.error("Unité de temps inconnue : %s", unite)
        return cadre.iloc[:0]

    fins = cadre.index + duree
    return cadre[fins <= pd.Timestamp(instant)]
