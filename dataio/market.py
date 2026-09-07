"""Accès aux prix d'actions et d'ETF, avec source de secours.

Deux sources, essayées dans l'ordre :

1. **yfinance** (Yahoo Finance) — gratuit, sans clé, couverture large,
   fournit le cours ajusté.
2. **Stooq** — CSV public, gratuit, sans clé. Sert de filet quand Yahoo
   limite le débit ou renvoie une réponse vide.

Toutes les sources sont ramenées au même format : index ``DatetimeIndex``
nommé ``date``, colonnes ``open, high, low, close, volume`` en minuscules.

Cours ajusté
------------
La colonne ``close`` contient le **cours ajusté** dès que la source le
fournit. Sur un cours brut, un split 4:1 se lit comme une chute de 75 %
et déclenche des signaux qui n'ont jamais existé.
"""

from __future__ import annotations

import io
import logging
import os
from datetime import date, datetime
from typing import Final

import pandas as pd
import requests

_LOG: Final = logging.getLogger(__name__)

#: URL du service de téléchargement CSV de Stooq.
URL_STOOQ: Final = "https://stooq.com/q/d/l/"

#: Délai maximal, en secondes, accordé à un appel réseau.
TIMEOUT: Final[float] = float(os.environ.get("HTTP_TIMEOUT", "20"))

#: Colonnes du format normalisé.
COLONNES: Final[tuple[str, ...]] = ("open", "high", "low", "close", "volume")

_ENTETES: Final[dict[str, str]] = {
    "User-Agent": "Mozilla/5.0 (compatible; veille-marches/1.0)"
}

__all__ = ["get_prices", "get_universe", "get_prices_yfinance", "get_prices_stooq"]


# ---------------------------------------------------------------------------
# Outils communs
# ---------------------------------------------------------------------------
def _en_date(valeur: str | date | datetime | None, defaut: str) -> str:
    """Convertit une date hétérogène en chaîne ``AAAA-MM-JJ``.

    Args:
        valeur: date sous forme de chaîne, ``date``, ``datetime`` ou ``None``.
        defaut: valeur de repli si ``valeur`` est ``None``.

    Returns:
        Date au format ISO court.
    """
    if valeur is None:
        return defaut
    if isinstance(valeur, (datetime, date)):
        return valeur.strftime("%Y-%m-%d")
    return str(valeur)[:10]


def _normaliser(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Ramène un DataFrame brut au format commun.

    Args:
        df: données brutes issues d'une source quelconque.
        ticker: symbole, utilisé dans les messages de journalisation.

    Returns:
        DataFrame normalisé, éventuellement vide.
    """
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=list(COLONNES))

    out = df.copy()

    # yfinance renvoie parfois un index de colonnes à deux niveaux
    # (indicateur, ticker) même pour un symbole unique.
    if isinstance(out.columns, pd.MultiIndex):
        niveaux = [n for n in range(out.columns.nlevels)]
        for niveau in reversed(niveaux[1:]):
            if out.columns.get_level_values(niveau).nunique() == 1:
                out.columns = out.columns.droplevel(niveau)
        if isinstance(out.columns, pd.MultiIndex):
            out.columns = ["_".join(str(p) for p in col if p) for col in out.columns]

    out.columns = [str(c).strip().lower().replace(" ", "_") for c in out.columns]

    # Cours ajusté prioritaire sur le cours brut.
    if "adj_close" in out.columns:
        out["close"] = out["adj_close"]
    elif "adjclose" in out.columns:
        out["close"] = out["adjclose"]

    manquantes = [c for c in COLONNES if c not in out.columns]
    if manquantes:
        _LOG.warning("%s : colonnes absentes après normalisation %s.", ticker, manquantes)
        for colonne in manquantes:
            out[colonne] = pd.NA

    out = out.loc[:, list(COLONNES)]
    for colonne in COLONNES:
        out[colonne] = pd.to_numeric(out[colonne], errors="coerce")

    out.index = pd.to_datetime(out.index, errors="coerce", utc=True).tz_localize(None)
    out.index.name = "date"
    out = out[out.index.notna()]
    out = out[out["close"].notna()]
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


# ---------------------------------------------------------------------------
# Source 1 : yfinance
# ---------------------------------------------------------------------------
def get_prices_yfinance(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Télécharge un historique depuis Yahoo Finance.

    ``auto_adjust=True`` renvoie directement des cours ajustés des splits et
    des dividendes.

    Args:
        ticker: symbole Yahoo (par exemple ``SPY``).
        start: date de début au format ISO.
        end: date de fin au format ISO, exclusive.

    Returns:
        DataFrame normalisé, vide en cas d'échec.
    """
    try:
        import yfinance as yf
    except ImportError:
        _LOG.warning("yfinance n'est pas installé : source primaire indisponible.")
        return pd.DataFrame(columns=list(COLONNES))

    options = dict(
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        threads=False,
        timeout=TIMEOUT,
    )
    try:
        brut = yf.download(ticker, **options)
    except TypeError:
        # Certaines versions de yfinance n'exposent pas tous ces paramètres.
        options.pop("timeout", None)
        options.pop("threads", None)
        try:
            brut = yf.download(ticker, **options)
        except Exception as exc:  # noqa: BLE001 - une source ne doit jamais tout arrêter
            _LOG.warning("yfinance a échoué sur %s : %s", ticker, exc)
            return pd.DataFrame(columns=list(COLONNES))
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("yfinance a échoué sur %s : %s", ticker, exc)
        return pd.DataFrame(columns=list(COLONNES))

    return _normaliser(brut, ticker)


# ---------------------------------------------------------------------------
# Source 2 : Stooq
# ---------------------------------------------------------------------------
def _symbole_stooq(ticker: str) -> str:
    """Traduit un symbole Yahoo en symbole Stooq.

    Stooq attend des symboles en minuscules suffixés ``.us`` pour les valeurs
    américaines, et sépare les classes d'actions par un tiret.

    Args:
        ticker: symbole d'origine.

    Returns:
        Symbole au format Stooq.
    """
    symbole = ticker.strip().lower()
    if symbole.startswith("^"):
        # Les indices Stooq portent déjà leur propre préfixe.
        return symbole
    symbole = symbole.replace(".", "-")
    if not symbole.endswith(".us"):
        symbole = f"{symbole}.us"
    return symbole


def get_prices_stooq(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Télécharge un historique depuis le CSV public de Stooq.

    Attention : les cours Stooq sont ajustés des splits, mais pas
    systématiquement des dividendes. Cette source reste un secours, elle
    n'est pas équivalente à Yahoo pour un calcul de performance totale.

    Args:
        ticker: symbole d'origine.
        start: date de début au format ISO.
        end: date de fin au format ISO.

    Returns:
        DataFrame normalisé, vide en cas d'échec.
    """
    parametres = {
        "s": _symbole_stooq(ticker),
        "d1": start.replace("-", ""),
        "d2": end.replace("-", ""),
        "i": "d",
    }
    try:
        reponse = requests.get(
            URL_STOOQ, params=parametres, timeout=TIMEOUT, headers=_ENTETES
        )
        reponse.raise_for_status()
    except requests.RequestException as exc:
        _LOG.warning("Stooq a échoué sur %s : %s", ticker, exc)
        return pd.DataFrame(columns=list(COLONNES))

    texte = reponse.text.strip()
    if not texte or "No data" in texte[:200] or "Date" not in texte[:200]:
        _LOG.warning("Stooq n'a renvoyé aucune donnée exploitable pour %s.", ticker)
        return pd.DataFrame(columns=list(COLONNES))

    try:
        brut = pd.read_csv(io.StringIO(texte), parse_dates=["Date"], index_col="Date")
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("CSV Stooq illisible pour %s : %s", ticker, exc)
        return pd.DataFrame(columns=list(COLONNES))

    return _normaliser(brut, ticker)


# ---------------------------------------------------------------------------
# Façade publique
# ---------------------------------------------------------------------------
def get_prices(
    ticker: str,
    start: str | date | datetime | None = "2015-01-01",
    end: str | date | datetime | None = None,
) -> pd.DataFrame:
    """Récupère l'historique d'un titre en essayant les sources dans l'ordre.

    Args:
        ticker: symbole du titre ou de l'ETF.
        start: date de début. ``2015-01-01`` par défaut.
        end: date de fin. Aujourd'hui par défaut.

    Returns:
        DataFrame normalisé indexé par date. Vide si aucune source ne répond ;
        l'appelant doit tester ``df.empty``.
    """
    debut = _en_date(start, "2015-01-01")
    fin = _en_date(end, date.today().strftime("%Y-%m-%d"))

    df = get_prices_yfinance(ticker, debut, fin)
    if not df.empty:
        _LOG.debug("%s : %d barres via yfinance.", ticker, len(df))
        return df

    _LOG.info("%s : bascule sur la source de secours Stooq.", ticker)
    df = get_prices_stooq(ticker, debut, fin)
    if not df.empty:
        _LOG.debug("%s : %d barres via Stooq.", ticker, len(df))
        return df

    _LOG.warning("%s : aucune source n'a renvoyé de données.", ticker)
    return pd.DataFrame(columns=list(COLONNES))


def get_universe(
    tickers: list[str],
    start: str | date | datetime | None = "2015-01-01",
    end: str | date | datetime | None = None,
    min_barres: int = 30,
) -> dict[str, pd.DataFrame]:
    """Récupère un lot de titres. L'échec d'un symbole n'arrête pas le lot.

    Args:
        tickers: liste de symboles.
        start: date de début commune.
        end: date de fin commune.
        min_barres: nombre minimal de barres pour retenir un symbole. En
            dessous, la série est trop courte pour calculer les indicateurs.

    Returns:
        Dictionnaire ``{symbole: DataFrame}`` ne contenant que les symboles
        effectivement récupérés.
    """
    resultats: dict[str, pd.DataFrame] = {}
    echecs: list[str] = []

    for ticker in tickers:
        try:
            df = get_prices(ticker, start, end)
        except Exception as exc:  # noqa: BLE001 - isolation stricte par symbole
            _LOG.warning("Erreur inattendue sur %s : %s", ticker, exc)
            echecs.append(ticker)
            continue

        if df.empty or len(df) < min_barres:
            _LOG.warning(
                "%s ignoré : %d barre(s) récupérée(s), minimum %d.",
                ticker,
                len(df),
                min_barres,
            )
            echecs.append(ticker)
            continue
        resultats[ticker] = df

    _LOG.info(
        "Univers récupéré : %d/%d symboles%s",
        len(resultats),
        len(tickers),
        f" (échecs : {', '.join(echecs)})" if echecs else "",
    )
    return resultats
