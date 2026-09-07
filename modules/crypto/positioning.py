"""Positionnement des dérivés crypto et suivi des positions détenues.

Ce que mesure ce module
-----------------------
Le **funding rate** est le paiement périodique entre acheteurs et vendeurs
d'un contrat perpétuel, qui maintient son prix arrimé au comptant. Un funding
positif signifie que les acheteurs à levier paient les vendeurs : ils sont
majoritaires et pressés. Un funding négatif dit l'inverse.

Le chiffre brut ne veut rien dire — 0,01 % par période, est-ce beaucoup ? Sa
place dans sa propre distribution récente, si. D'où le percentile sur 90
jours : il répond à la seule question exploitable, *le levier est-il tendu
par rapport à son régime habituel ?*

L'**open interest** complète la lecture : c'est le nombre de contrats
ouverts, donc la taille du pari collectif. Un funding extrême sur un open
interest en hausse n'a pas la même portée que sur un marché qui se vide.

Sources vérifiées le 7 septembre 2026
-------------------------------------
Binance (``fapi.binance.com``) expose l'historique de funding et l'open
interest **sans clé** : les deux points d'accès répondent 200. Bybit
(``api.bybit.com/v5``) sert de repli, également sans clé. Aucune des deux ne
demande d'authentification pour ces données publiques de marché.

Déblocages de tokens : non alimenté
-----------------------------------
Aucune source gratuite et fiable n'a été trouvée. DefiLlama expose bien un
point d'accès ``/emissions``, mais il répond **HTTP 402 « Upgrade to the paid
API plan »** ; CryptoRank répond 401 sans clé. Les calendriers de déblocage
sont une donnée que les agrégateurs réservent à leurs offres payantes. Le
bloc est donc publié avec ``disponible: false`` et le détail des tentatives,
plutôt que rempli au jugé — un calendrier de déblocage faux serait pire
qu'absent, puisqu'il ferait attendre une pression vendeuse au mauvais moment.

Ce module décrit un état de marché. Il ne recommande rien.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Final

import pandas as pd
import requests

_LOG: Final = logging.getLogger(__name__)

#: Historique du funding des perpétuels Binance.
URL_BINANCE_FUNDING: Final = "https://fapi.binance.com/fapi/v1/fundingRate"

#: Open interest courant d'un perpétuel Binance.
URL_BINANCE_OI: Final = "https://fapi.binance.com/fapi/v1/openInterest"

#: Repli : historique du funding chez Bybit.
URL_BYBIT_FUNDING: Final = "https://api.bybit.com/v5/market/funding/history"

#: Délai maximal, en secondes, accordé à un appel réseau.
TIMEOUT: Final[float] = float(os.environ.get("HTTP_TIMEOUT", "20"))

#: Binance verse le funding toutes les huit heures, soit trois fois par jour.
VERSEMENTS_PAR_JOUR: Final[int] = 3

#: Plafond d'enregistrements accepté par Binance sur un appel.
LIMITE_BINANCE: Final[int] = 1000

#: Sources de calendrier de déblocage testées et écartées.
SOURCES_DEBLOCAGES_TESTEES: Final[tuple[tuple[str, str, str], ...]] = (
    ("DefiLlama", "https://api.llama.fi/emissions", "HTTP 402 — offre payante"),
    ("DefiLlama (par protocole)", "https://api.llama.fi/emission/{protocole}", "HTTP 402 — offre payante"),
    ("CryptoRank", "https://api.cryptorank.io/v1/currencies", "HTTP 401 — clé requise"),
)

_ENTETES: Final[dict[str, str]] = {
    "Accept": "application/json",
    "User-Agent": "veille-marches/1.0",
}

__all__ = [
    "get_funding_history",
    "get_open_interest",
    "analyser_funding",
    "get_deblocages_tokens",
    "suivre_positions",
    "analyser_positionnement",
]


def _appeler(url: str, parametres: dict[str, Any] | None = None) -> Any | None:
    """Appelle un point d'accès public de marché.

    Args:
        url: adresse complète.
        parametres: paramètres de requête.

    Returns:
        Charge JSON décodée, ou ``None`` en cas d'échec.
    """
    try:
        reponse = requests.get(url, params=parametres, timeout=TIMEOUT, headers=_ENTETES)
        reponse.raise_for_status()
        return reponse.json()
    except requests.RequestException as exc:
        _LOG.warning("Source de dérivés injoignable (%s) : %s", url, exc)
        return None
    except ValueError as exc:
        _LOG.warning("Réponse de dérivés illisible (%s) : %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Funding
# ---------------------------------------------------------------------------
def get_funding_history(
    symbole: str = "BTCUSDT",
    jours: int = 90,
    charge_binance: Any | None = None,
    charge_bybit: Any | None = None,
) -> tuple[pd.Series, str]:
    """Récupère l'historique du funding, Binance puis Bybit en repli.

    Args:
        symbole: contrat perpétuel, par exemple ``BTCUSDT``.
        jours: profondeur d'historique souhaitée.
        charge_binance: réponse déjà obtenue, pour les tests hors ligne.
        charge_bybit: réponse de repli déjà obtenue, pour les tests.

    Returns:
        Couple ``(serie, source)``. La série est indexée par horodatage et
        vide si les deux sources échouent, auquel cas ``source`` vaut
        ``"aucune"``.
    """
    vide = pd.Series(dtype="float64", name="funding")
    limite = min(int(jours) * VERSEMENTS_PAR_JOUR, LIMITE_BINANCE)

    if charge_binance is None:
        charge_binance = _appeler(
            URL_BINANCE_FUNDING, {"symbol": symbole, "limit": limite}
        )

    if isinstance(charge_binance, list) and charge_binance:
        horodatages, valeurs = [], []
        for point in charge_binance:
            try:
                horodatages.append(
                    datetime.fromtimestamp(int(point["fundingTime"]) / 1000.0, tz=timezone.utc)
                )
                valeurs.append(float(point["fundingRate"]))
            except (KeyError, TypeError, ValueError):
                continue
        if valeurs:
            serie = pd.Series(valeurs, index=pd.DatetimeIndex(horodatages), name="funding")
            return serie.sort_index(), "binance"

    _LOG.warning("Funding Binance indisponible pour %s : repli sur Bybit.", symbole)
    if charge_bybit is None:
        charge_bybit = _appeler(
            URL_BYBIT_FUNDING,
            {"category": "linear", "symbol": symbole, "limit": 200},
        )

    if isinstance(charge_bybit, dict):
        liste = ((charge_bybit.get("result") or {}).get("list")) or []
        horodatages, valeurs = [], []
        for point in liste:
            try:
                horodatages.append(
                    datetime.fromtimestamp(
                        int(point["fundingRateTimestamp"]) / 1000.0, tz=timezone.utc
                    )
                )
                valeurs.append(float(point["fundingRate"]))
            except (KeyError, TypeError, ValueError):
                continue
        if valeurs:
            serie = pd.Series(valeurs, index=pd.DatetimeIndex(horodatages), name="funding")
            return serie.sort_index(), "bybit"

    return vide, "aucune"


def get_open_interest(symbole: str = "BTCUSDT", charge: Any | None = None) -> dict[str, Any]:
    """Récupère l'open interest courant d'un perpétuel.

    Args:
        symbole: contrat perpétuel.
        charge: réponse déjà obtenue, pour les tests.

    Returns:
        Bloc avec ``disponible`` et la valeur en contrats.
    """
    if charge is None:
        charge = _appeler(URL_BINANCE_OI, {"symbol": symbole})

    if not isinstance(charge, dict) or "openInterest" not in charge:
        return {
            "disponible": False,
            "motif": "open interest Binance injoignable ou illisible",
            "valeur_contrats": None,
            "source": "binance",
        }

    try:
        valeur = float(charge["openInterest"])
    except (TypeError, ValueError):
        return {
            "disponible": False,
            "motif": "open interest non numérique",
            "valeur_contrats": None,
            "source": "binance",
        }

    horodatage = charge.get("time")
    return {
        "disponible": True,
        "motif": "",
        "valeur_contrats": valeur,
        "symbole": symbole,
        "horodatage_utc": (
            datetime.fromtimestamp(int(horodatage) / 1000.0, tz=timezone.utc).isoformat()
            if horodatage
            else None
        ),
        "source": "binance",
    }


def analyser_funding(
    funding: pd.Series,
    source: str,
    percentile_haut: float = 90.0,
    percentile_bas: float = 10.0,
) -> dict[str, Any]:
    """Situe le funding courant dans sa distribution récente.

    Args:
        funding: historique du funding.
        source: origine des données.
        percentile_haut: rang au-delà duquel le levier acheteur est jugé tendu.
        percentile_bas: rang en deçà duquel le levier vendeur est jugé tendu.

    Returns:
        Bloc sérialisable. ``disponible`` est ``False`` si l'historique est
        trop court pour qu'un percentile ait un sens.
    """
    propre = funding.dropna() if funding is not None else pd.Series(dtype="float64")
    if len(propre) < 30:
        return {
            "disponible": False,
            "motif": (
                f"{len(propre)} versement(s) de funding, 30 au minimum requis pour "
                "un percentile interprétable"
            ),
            "source": source,
        }

    courant = float(propre.iloc[-1])
    echantillon = propre.to_numpy(dtype="float64")
    percentile = float((echantillon <= courant).mean() * 100.0)

    # Le funding est versé trois fois par jour : l'annualiser rend le chiffre
    # comparable à un taux, seule forme sous laquelle il parle vraiment.
    annualise = courant * VERSEMENTS_PAR_JOUR * 365.0 * 100.0

    if percentile >= percentile_haut:
        lecture = (
            f"Funding au {percentile:.0f}e percentile sur {len(propre)} versements : "
            "les acheteurs à levier paient cher pour tenir leur position, un niveau "
            "rarement atteint sur la période observée."
        )
        tension = "levier acheteur tendu"
    elif percentile <= percentile_bas:
        lecture = (
            f"Funding au {percentile:.0f}e percentile sur {len(propre)} versements : "
            "ce sont les vendeurs à levier qui paient, configuration également rare."
        )
        tension = "levier vendeur tendu"
    else:
        lecture = (
            f"Funding au {percentile:.0f}e percentile sur {len(propre)} versements : "
            "le levier reste dans son régime habituel."
        )
        tension = "aucune tension particulière"

    return {
        "disponible": True,
        "motif": "",
        "source": source,
        "funding_courant": courant,
        "funding_annualise_pct": annualise,
        "percentile_90j": percentile,
        "n_versements": len(propre),
        "percentile_extreme_haut": percentile_haut,
        "percentile_extreme_bas": percentile_bas,
        "tension": tension,
        "lecture": lecture,
        "definition": (
            "Paiement périodique entre acheteurs et vendeurs du contrat perpétuel, "
            "versé toutes les huit heures. Positif, les acheteurs paient les vendeurs."
        ),
    }


# ---------------------------------------------------------------------------
# Déblocages de tokens : non alimenté
# ---------------------------------------------------------------------------
def get_deblocages_tokens(symboles: list[str] | None = None) -> dict[str, Any]:
    """Signale l'absence de source gratuite de calendrier de déblocage.

    Aucun appel réseau n'est fait : les sources ont été testées et écartées,
    et les réinterroger chaque jour coûterait du temps pour un échec connu.
    La liste des tentatives est conservée pour que la vérification reste
    rejouable à la main.

    Args:
        symboles: jetons concernés, repris pour information.

    Returns:
        Bloc marqué indisponible, avec le détail des tentatives.
    """
    return {
        "disponible": False,
        "motif": (
            "aucune source gratuite et fiable de calendrier de déblocage. "
            "DefiLlama expose un point d'accès /emissions mais le réserve à son offre "
            "payante (HTTP 402) ; CryptoRank exige une clé (HTTP 401). Vérifié le "
            "7 septembre 2026."
        ),
        "jetons_concernes": list(symboles or []),
        "sources_testees": [
            {"nom": n, "url": u, "constat": c} for n, u, c in SOURCES_DEBLOCAGES_TESTEES
        ],
        "consequence": (
            "Les échéances de déblocage ne sont pas connues du système. Un déblocage "
            "important peut donc survenir sans que ce rapport l'ait annoncé."
        ),
    }


# ---------------------------------------------------------------------------
# Positions suivies
# ---------------------------------------------------------------------------
def suivre_positions(
    watchlist: list[dict[str, Any]],
    instantane: pd.DataFrame | None,
) -> dict[str, Any]:
    """Met en forme les prix et variations des positions suivies.

    Args:
        watchlist: entrées ``crypto_watchlist`` de la configuration.
        instantane: sortie de :func:`dataio.crypto.get_snapshot`, indexée par
            identifiant CoinGecko.

    Returns:
        Bloc sérialisable, une entrée par jeton suivi.
    """
    if instantane is None or instantane.empty:
        return {
            "disponible": False,
            "motif": "instantané CoinGecko indisponible",
            "n_positions": len(watchlist),
            "positions": [],
        }

    # Noms de colonnes acceptés, par ordre de préférence. Le premier de chaque
    # ligne est celui que produit dataio.crypto.get_snapshot, qui renomme déjà
    # les champs de CoinGecko en français ; les suivants sont les noms bruts de
    # l'API, acceptés au cas où un appelant fournirait un cadre non normalisé.
    colonnes = {
        "prix_usd": ("prix_usd", "current_price", "usd"),
        "variation_24h_pct": (
            "var_24h_pct", "price_change_percentage_24h_in_currency",
            "price_change_percentage_24h", "usd_24h_change",
        ),
        "variation_7j_pct": ("var_7j_pct", "price_change_percentage_7d_in_currency"),
        "variation_30j_pct": ("var_30j_pct", "price_change_percentage_30d_in_currency"),
        "capitalisation_usd": ("capitalisation_usd", "market_cap", "usd_market_cap"),
    }

    def _lire(identifiant: str, champ: str) -> float | None:
        """Lit une valeur de l'instantané, quel que soit son nom de colonne."""
        if identifiant not in instantane.index:
            return None
        ligne = instantane.loc[identifiant]
        for nom in colonnes[champ]:
            if nom in instantane.columns:
                valeur = ligne.get(nom)
                if pd.notna(valeur):
                    try:
                        return float(valeur)
                    except (TypeError, ValueError):
                        continue
        return None

    positions: list[dict[str, Any]] = []
    absents: list[str] = []

    for entree in watchlist:
        identifiant = str(entree.get("coingecko_id", ""))
        symbole = str(entree.get("symbol", identifiant)).upper()
        present = identifiant in instantane.index
        if not present:
            absents.append(symbole)

        bloc: dict[str, Any] = {
            "symbole": symbole,
            "coingecko_id": identifiant,
            "disponible": present,
            "motif": "" if present else "jeton absent de l'instantané CoinGecko",
            "prix_usd": _lire(identifiant, "prix_usd"),
            "variation_24h_pct": _lire(identifiant, "variation_24h_pct"),
            "variation_7j_pct": _lire(identifiant, "variation_7j_pct"),
            "variation_30j_pct": _lire(identifiant, "variation_30j_pct"),
            "capitalisation_usd": _lire(identifiant, "capitalisation_usd"),
        }
        if entree.get("note"):
            bloc["note"] = str(entree["note"])
        # Les jetons très jeunes n'ont aucun régime interprétable : leurs
        # variations se comptent en centaines de pour cent, et les seuils
        # calibrés sur BTC n'y ont aucun sens.
        if entree.get("jeune_et_volatil"):
            bloc["jeune_et_volatil"] = True
            bloc["avertissement"] = (
                "Jeton récent et très volatil : les repères de régime et les seuils "
                "calibrés sur BTC ou ETH ne s'appliquent pas ici."
            )
        positions.append(bloc)

    if absents:
        _LOG.warning("Jetons absents de l'instantané CoinGecko : %s", ", ".join(absents))

    disponibles = [p for p in positions if p["disponible"]]
    return {
        "disponible": bool(disponibles),
        "motif": "" if disponibles else "aucun jeton retrouvé dans l'instantané",
        "n_positions": len(positions),
        "n_disponibles": len(disponibles),
        "jetons_absents": absents,
        "positions": positions,
        "source": "CoinGecko",
    }


def analyser_positionnement(
    configuration: dict[str, Any],
    watchlist: list[dict[str, Any]],
    instantane: pd.DataFrame | None = None,
    funding: pd.Series | None = None,
    source_funding: str = "",
    open_interest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produit le bloc positionnement du rapport crypto.

    Args:
        configuration: bloc ``crypto_positioning`` de la configuration.
        watchlist: entrées ``crypto_watchlist``.
        instantane: instantané CoinGecko déjà obtenu.
        funding: historique de funding déjà obtenu, pour les tests.
        source_funding: origine de cet historique.
        open_interest: open interest déjà obtenu, pour les tests.

    Returns:
        Bloc sérialisable.
    """
    symbole = str(configuration.get("symbole_perp", "BTCUSDT"))
    jours = int(configuration.get("fenetre_percentile_funding_jours", 90))
    haut = float(configuration.get("percentile_extreme_haut", 90.0))
    bas = float(configuration.get("percentile_extreme_bas", 10.0))

    if funding is None:
        funding, source_funding = get_funding_history(symbole, jours=jours)
    if open_interest is None:
        open_interest = get_open_interest(symbole)

    return {
        "disponible": True,
        "symbole_perpetuel": symbole,
        "funding": analyser_funding(
            funding, source_funding or "aucune", percentile_haut=haut, percentile_bas=bas
        ),
        "open_interest": open_interest,
        "positions": suivre_positions(watchlist, instantane),
        "deblocages_tokens": get_deblocages_tokens(
            [str(e.get("symbol", "")) for e in watchlist]
        ),
    }
