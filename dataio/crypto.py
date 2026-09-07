"""Client CoinGecko : prix, contexte de marché et instantané de portefeuille.

L'API publique de CoinGecko fonctionne sans clé, avec une limite de débit
basse. Une clé de démonstration gratuite, placée dans ``COINGECKO_API_KEY``,
relève cette limite ; elle est envoyée dans l'en-tête ``x-cg-demo-api-key``
et n'apparaît jamais dans une URL, où elle finirait dans les journaux du
serveur.

Le code 429 (débit dépassé) est traité par attente exponentielle : c'est la
réponse normale de ce service, pas une panne.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Final

import pandas as pd
import requests

_LOG: Final = logging.getLogger(__name__)

#: Racine de l'API publique CoinGecko v3.
BASE_URL: Final = "https://api.coingecko.com/api/v3"

#: Délai maximal, en secondes, accordé à un appel réseau.
TIMEOUT: Final[float] = float(os.environ.get("HTTP_TIMEOUT", "20"))

#: Nombre de tentatives avant d'abandonner un appel.
MAX_TENTATIVES: Final[int] = 4

#: Attente initiale, en secondes, doublée à chaque nouvelle tentative.
ATTENTE_INITIALE: Final[float] = 2.0

#: Attente maximale entre deux tentatives, pour ne pas bloquer un rapport.
ATTENTE_MAX: Final[float] = 30.0

#: Au-delà de 90 jours, CoinGecko ne renvoie plus qu'un point par jour :
#: reconstruire un OHLC intrajournalier devient impossible.
SEUIL_GRANULARITE_JOURNALIERE: Final[int] = 90

__all__ = ["get_ohlc", "get_market_context", "get_snapshot"]


def _entetes() -> dict[str, str]:
    """Construit les en-têtes HTTP, clé d'API comprise si elle existe.

    Returns:
        En-têtes prêts pour ``requests``.
    """
    entetes = {
        "Accept": "application/json",
        "User-Agent": "veille-marches/1.0",
    }
    cle = os.environ.get("COINGECKO_API_KEY", "").strip()
    if cle:
        entetes["x-cg-demo-api-key"] = cle
    else:
        _LOG.debug("COINGECKO_API_KEY absente : appels en débit public réduit.")
    return entetes


def _appeler(chemin: str, parametres: dict[str, Any] | None = None) -> Any | None:
    """Appelle CoinGecko avec attente exponentielle sur le code 429.

    Args:
        chemin: chemin relatif à :data:`BASE_URL`, par exemple ``/global``.
        parametres: paramètres de requête.

    Returns:
        Charge JSON décodée, ou ``None`` si toutes les tentatives échouent.
    """
    url = f"{BASE_URL}{chemin}"
    attente = ATTENTE_INITIALE

    for tentative in range(1, MAX_TENTATIVES + 1):
        try:
            reponse = requests.get(
                url, params=parametres, timeout=TIMEOUT, headers=_entetes()
            )
        except requests.RequestException as exc:
            _LOG.warning(
                "CoinGecko injoignable sur %s (tentative %d/%d) : %s",
                chemin, tentative, MAX_TENTATIVES, exc,
            )
            if tentative == MAX_TENTATIVES:
                return None
            time.sleep(attente)
            attente = min(attente * 2.0, ATTENTE_MAX)
            continue

        if reponse.status_code == 429:
            # CoinGecko indique parfois lui-même le délai à respecter.
            entete_attente = reponse.headers.get("Retry-After")
            try:
                delai = float(entete_attente) if entete_attente else attente
            except ValueError:
                delai = attente
            delai = min(delai, ATTENTE_MAX)
            _LOG.warning(
                "Débit CoinGecko dépassé sur %s : pause de %.0f s (tentative %d/%d).",
                chemin, delai, tentative, MAX_TENTATIVES,
            )
            if tentative == MAX_TENTATIVES:
                _LOG.warning("Abandon de %s après %d tentatives.", chemin, MAX_TENTATIVES)
                return None
            time.sleep(delai)
            attente = min(attente * 2.0, ATTENTE_MAX)
            continue

        if reponse.status_code >= 500:
            _LOG.warning(
                "CoinGecko en erreur %d sur %s (tentative %d/%d).",
                reponse.status_code, chemin, tentative, MAX_TENTATIVES,
            )
            if tentative == MAX_TENTATIVES:
                return None
            time.sleep(attente)
            attente = min(attente * 2.0, ATTENTE_MAX)
            continue

        try:
            reponse.raise_for_status()
            return reponse.json()
        except requests.HTTPError as exc:
            _LOG.warning("CoinGecko a refusé %s : %s", chemin, exc)
            return None
        except ValueError as exc:
            _LOG.warning("Réponse CoinGecko illisible sur %s : %s", chemin, exc)
            return None

    return None


def get_ohlc(coin_id: str, days: int = 90) -> pd.DataFrame:
    """Construit un OHLCV journalier à partir de ``market_chart``.

    CoinGecko adapte sa granularité à la fenêtre demandée : cinq minutes sur
    un jour, une heure jusqu'à 90 jours, un point par jour au-delà. L'OHLC est
    donc reconstruit par agrégation journalière des points intrajournaliers.
    Au-delà de 90 jours, il n'y a plus qu'un point par jour et les quatre
    valeurs deviennent identiques : c'est une limite de la source, signalée
    par un avertissement.

    Args:
        coin_id: identifiant CoinGecko (``bitcoin``, ``ethereum``...).
        days: profondeur d'historique en jours.

    Returns:
        DataFrame indexé par date, colonnes ``open, high, low, close, volume``.
        Vide en cas d'échec.
    """
    colonnes = ["open", "high", "low", "close", "volume"]
    if days > SEUIL_GRANULARITE_JOURNALIERE:
        _LOG.warning(
            "%s : au-delà de %d jours CoinGecko ne fournit qu'un point par jour ; "
            "l'OHLC sera dégénéré (ouverture = plus haut = plus bas = clôture).",
            coin_id, SEUIL_GRANULARITE_JOURNALIERE,
        )

    charge = _appeler(
        f"/coins/{coin_id}/market_chart",
        {"vs_currency": "usd", "days": int(days)},
    )
    if not charge or not charge.get("prices"):
        _LOG.warning("Aucun historique de prix pour %s.", coin_id)
        return pd.DataFrame(columns=colonnes)

    prix = pd.DataFrame(charge["prices"], columns=["ts", "prix"])
    prix["date"] = pd.to_datetime(prix["ts"], unit="ms", utc=True).dt.tz_localize(None)
    prix = prix.set_index("date").sort_index()

    ohlc = prix["prix"].resample("1D").ohlc()
    ohlc.columns = ["open", "high", "low", "close"]

    # ``total_volumes`` est un volume glissant sur 24 h, pas un flux : on
    # retient la dernière observation du jour plutôt que d'additionner, ce
    # qui compterait plusieurs fois les mêmes échanges.
    if charge.get("total_volumes"):
        volumes = pd.DataFrame(charge["total_volumes"], columns=["ts", "volume"])
        volumes["date"] = pd.to_datetime(volumes["ts"], unit="ms", utc=True).dt.tz_localize(None)
        serie_volume = volumes.set_index("date").sort_index()["volume"].resample("1D").last()
        ohlc["volume"] = serie_volume
    else:
        ohlc["volume"] = float("nan")

    ohlc = ohlc.dropna(subset=["close"])
    ohlc.index.name = "date"
    _LOG.debug("%s : %d barre(s) journalière(s).", coin_id, len(ohlc))
    return ohlc.loc[:, colonnes]


def get_market_context() -> dict[str, Any]:
    """Récupère le contexte global du marché crypto.

    Returns:
        Dictionnaire : ``capitalisation_totale_usd``, ``volume_24h_usd``,
        ``dominance_btc_pct``, ``dominance_eth_pct``,
        ``variation_capitalisation_24h_pct``, ``nb_cryptos_actives``,
        ``disponible``. Les valeurs numériques valent ``None`` si l'appel
        échoue.
    """
    vide: dict[str, Any] = {
        "capitalisation_totale_usd": None,
        "volume_24h_usd": None,
        "dominance_btc_pct": None,
        "dominance_eth_pct": None,
        "variation_capitalisation_24h_pct": None,
        "nb_cryptos_actives": None,
        "disponible": False,
    }

    charge = _appeler("/global")
    if not charge or "data" not in charge:
        _LOG.warning("Contexte de marché crypto indisponible.")
        return vide

    donnees = charge["data"]
    dominance = donnees.get("market_cap_percentage") or {}
    return {
        "capitalisation_totale_usd": (donnees.get("total_market_cap") or {}).get("usd"),
        "volume_24h_usd": (donnees.get("total_volume") or {}).get("usd"),
        "dominance_btc_pct": dominance.get("btc"),
        "dominance_eth_pct": dominance.get("eth"),
        "variation_capitalisation_24h_pct": donnees.get(
            "market_cap_change_percentage_24h_usd"
        ),
        "nb_cryptos_actives": donnees.get("active_cryptocurrencies"),
        "disponible": True,
    }


def get_snapshot(coin_ids: list[str]) -> pd.DataFrame:
    """Instantané de plusieurs cryptomonnaies.

    Args:
        coin_ids: identifiants CoinGecko.

    Returns:
        DataFrame indexé par identifiant, colonnes : ``symbole``, ``nom``,
        ``prix_usd``, ``var_24h_pct``, ``var_7j_pct``, ``var_30j_pct``,
        ``volume_24h_usd``, ``capitalisation_usd``, ``ath_usd``,
        ``distance_ath_pct``. Vide en cas d'échec.
    """
    colonnes = [
        "symbole", "nom", "prix_usd", "var_24h_pct", "var_7j_pct", "var_30j_pct",
        "volume_24h_usd", "capitalisation_usd", "ath_usd", "distance_ath_pct",
    ]
    if not coin_ids:
        _LOG.warning("Aucun identifiant crypto fourni.")
        return pd.DataFrame(columns=colonnes)

    charge = _appeler(
        "/coins/markets",
        {
            "vs_currency": "usd",
            "ids": ",".join(coin_ids),
            "order": "market_cap_desc",
            "per_page": max(len(coin_ids), 1),
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h,7d,30d",
        },
    )
    if not charge:
        _LOG.warning("Instantané crypto indisponible pour %s.", ", ".join(coin_ids))
        return pd.DataFrame(columns=colonnes)

    lignes = []
    for actif in charge:
        lignes.append(
            {
                "id": actif.get("id"),
                "symbole": str(actif.get("symbol", "")).upper(),
                "nom": actif.get("name"),
                "prix_usd": actif.get("current_price"),
                "var_24h_pct": actif.get("price_change_percentage_24h_in_currency"),
                "var_7j_pct": actif.get("price_change_percentage_7d_in_currency"),
                "var_30j_pct": actif.get("price_change_percentage_30d_in_currency"),
                "volume_24h_usd": actif.get("total_volume"),
                "capitalisation_usd": actif.get("market_cap"),
                "ath_usd": actif.get("ath"),
                # CoinGecko exprime déjà l'écart au plus haut historique en
                # pourcentage négatif : -62 signifie 62 % sous le sommet.
                "distance_ath_pct": actif.get("ath_change_percentage"),
            }
        )

    if not lignes:
        return pd.DataFrame(columns=colonnes)

    cadre = pd.DataFrame(lignes).set_index("id")

    absents = [c for c in coin_ids if c not in cadre.index]
    if absents:
        _LOG.warning("Identifiants crypto inconnus de CoinGecko : %s", ", ".join(absents))

    return cadre.loc[:, colonnes]
