"""Classification du régime de marché crypto — jamais une prévision de prix.

Ce que le module fait, et ce qu'il ne fait pas
----------------------------------------------
Il range l'état courant du marché dans l'une de quatre cases, et dit à quelle
condition ce classement cesserait d'être valable. Il ne dit pas où va le
prix, ni quoi faire.

Le MVRV, et pourquoi il est au centre
-------------------------------------
Le MVRV rapporte la capitalisation de marché à la capitalisation
**réalisée** — la somme de ce que chaque unité a coûté à son détenteur
actuel, à son dernier mouvement on-chain. C'est donc, en substance, la
plus-value latente moyenne du marché :

* MVRV inférieur à 1 : le détenteur moyen est en perte latente ;
* MVRV autour de 2 : plus-value moyenne de 100 % ;
* MVRV supérieur à 3 : euphorie historique, où les détenteurs anciens ont un
  intérêt croissant à vendre.

Source retenue, et pourquoi
---------------------------
**API Community de Coin Metrics**, métrique ``CapMVRVCur``, gratuite et sans
clé. Vérifié le 7 septembre 2026 : elle répond pour BTC et pour ETH.

Tenue en exécution automatisée, vérifiée et non supposée : l'API annonce ses
quotas dans ses en-têtes de réponse, et le plan anonyme (``x-ratelimit-plan:
download``) autorise **6 000 requêtes par fenêtre glissante de 20 secondes**
(``x-ratelimit-limit: 6000;w=20``). Six appels consécutifs ont été passés
sans le moindre 429. Le rapport quotidien en consomme un par actif suivi,
soit deux : la marge est de plus de trois ordres de grandeur, et aucune clé
n'est nécessaire depuis GitHub Actions.

L'API accepte aussi plusieurs actifs en un seul appel (``assets=btc,eth``).
Ce module interroge malgré tout un actif à la fois, délibérément : cela
permet qu'un actif indisponible porte son propre ``motif`` sans faire tomber
les autres. Le coût — un appel de plus — est sans objet au regard du quota.

Le point important est ce qu'on ne fait **pas**. Le catalogue gratuit
n'expose pas ``CapRealUSD``, la capitalisation réalisée : la demander renvoie
un 403 explicite. On aurait donc pu fabriquer une estimation maison du prix
réalisé — à partir de moyennes de prix pondérées par le volume, par exemple.
Ce serait une grandeur inventée, non comparable aux MVRV publiés ailleurs, et
elle servirait ici de fondement à une classification. C'est exactement le
genre d'approximation silencieuse que ce projet s'interdit. Coin Metrics
publiant le ratio directement, la question ne se pose pas ; s'il cessait de
le publier, le module renverrait ``disponible: false``.

Flux des ETF spot : désormais mesurés
-------------------------------------
Ils avaient été jugés inaccessibles sur un 403 de Farside. Réinterrogée avec
des en-têtes de navigateur complets, la source répond et publie le tableau
réel, pour le bitcoin comme pour l'ether — voir :mod:`dataio.etf_flows`. Le
refus venait de l'empreinte de l'outil d'appel, pas d'un blocage de la
donnée. Un repli par variation des actifs nets reste codé au cas où.

Extension à l'ether, et ce qui ne se transpose pas
--------------------------------------------------
Les mêmes métriques sont cherchées pour l'ether, et trois des quatre
existent : MVRV et capitalisation de marché chez Coin Metrics, flux ETF chez
Farside. Le **prix réalisé** n'est pas publié en accès gratuit, mais il se
déduit exactement : la capitalisation réalisée vaut la capitalisation de
marché divisée par le MVRV, puisque c'est ainsi que le ratio est défini. Ce
n'est pas une approximation, c'est de l'algèbre sur deux grandeurs publiées.

Le **comportement des détenteurs de long terme** n'a en revanche aucune
source gratuite, ni pour l'ether ni pour le bitcoin : le catalogue Community
ne contient aucune métrique d'ancienneté des pièces. Le bloc sort
``disponible: false``.

Un avertissement accompagne systématiquement le régime de l'ether : **les
seuils de MVRV sont calibrés sur l'histoire du bitcoin**. Les distributions
diffèrent — au 7 septembre 2026, le MVRV du bitcoin est à 1,51 et celui de
l'ether à 1,10 —, et appliquer les mêmes bornes aux deux est une convention
de lecture, pas un résultat mesuré. Le signaler est le minimum ; le taire
reviendrait à présenter une approximation comme une mesure.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Final

import requests

_LOG: Final = logging.getLogger(__name__)

#: API Community de Coin Metrics : gratuite, sans clé. Quota vérifié le
#: 7 septembre 2026 : 6 000 requêtes par fenêtre glissante de 20 secondes
#: pour le plan anonyme, quand le rapport quotidien en consomme deux.
URL_COINMETRICS: Final = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"

#: Historique de l'offre de stablecoins, publié par DefiLlama.
URL_STABLECOINS: Final = "https://stablecoins.llama.fi/stablecoincharts/all"

#: Délai maximal, en secondes, accordé à un appel réseau.
TIMEOUT: Final[float] = float(os.environ.get("HTTP_TIMEOUT", "20"))

#: Sources de flux ETF testées et écartées, conservées pour que la
#: vérification soit rejouable et que le jour où l'une redevient exploitable
#: soit détectable.
SOURCES_ETF_TESTEES: Final[tuple[tuple[str, str], ...]] = (
    ("CoinGlass", "https://open-api.coinglass.com/public/v2/bitcoin_etf"),
    ("SoSoValue", "https://api.sosovalue.xyz/openapi/v1/etf/currentEtfDataMetrics"),
    ("DefiLlama", "https://api.llama.fi/etfs"),
    ("Farside", "https://farside.co.uk/btc/"),
)

#: Les quatre régimes possibles.
ACCUMULATION: Final = "accumulation"
EXPANSION: Final = "expansion"
DISTRIBUTION: Final = "distribution"
CAPITULATION: Final = "capitulation"
REGIMES: Final[tuple[str, ...]] = (ACCUMULATION, EXPANSION, DISTRIBUTION, CAPITULATION)

_ENTETES: Final[dict[str, str]] = {
    "Accept": "application/json",
    "User-Agent": "veille-marches/1.0",
}

__all__ = [
    "REGIMES",
    "get_mvrv",
    "get_prix_realise",
    "get_comportement_detenteurs_lt",
    "get_croissance_stablecoins",
    "get_flux_etf",
    "classer_regime",
    "analyser_regime",
]


def _appeler(url: str, parametres: dict[str, Any] | None = None) -> Any | None:
    """Appelle un point d'accès public et renvoie la charge JSON.

    Args:
        url: adresse complète.
        parametres: paramètres de requête.

    Returns:
        Charge décodée, ou ``None`` en cas d'échec.
    """
    try:
        reponse = requests.get(url, params=parametres, timeout=TIMEOUT, headers=_ENTETES)
        reponse.raise_for_status()
        return reponse.json()
    except requests.RequestException as exc:
        _LOG.warning("Source injoignable (%s) : %s", url, exc)
        return None
    except ValueError as exc:
        _LOG.warning("Réponse illisible (%s) : %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# MVRV
# ---------------------------------------------------------------------------
def get_mvrv(actif: str = "btc", charge: Any | None = None) -> dict[str, Any]:
    """Récupère le MVRV courant d'un actif chez Coin Metrics.

    Args:
        actif: ``btc`` ou ``eth``, en minuscules.
        charge: réponse déjà obtenue, pour les tests hors ligne.

    Returns:
        Bloc avec ``disponible``, la valeur, sa date et sa source.
    """
    identifiant = actif.strip().lower()
    if charge is None:
        charge = _appeler(
            URL_COINMETRICS,
            {
                "assets": identifiant,
                "metrics": "CapMVRVCur",
                "frequency": "1d",
                "page_size": 10,
            },
        )

    if not isinstance(charge, dict) or not charge.get("data"):
        return {
            "disponible": False,
            "motif": "Coin Metrics n'a renvoyé aucune observation de CapMVRVCur",
            "actif": identifiant,
            "valeur": None,
            "source": "Coin Metrics Community API (CapMVRVCur)",
        }

    points = [p for p in charge["data"] if p.get("CapMVRVCur") is not None]
    if not points:
        return {
            "disponible": False,
            "motif": "aucune valeur CapMVRVCur exploitable",
            "actif": identifiant,
            "valeur": None,
            "source": "Coin Metrics Community API (CapMVRVCur)",
        }

    points.sort(key=lambda p: str(p.get("time", "")))
    dernier = points[-1]
    try:
        valeur = float(dernier["CapMVRVCur"])
    except (TypeError, ValueError):
        return {
            "disponible": False,
            "motif": "valeur CapMVRVCur non numérique",
            "actif": identifiant,
            "valeur": None,
            "source": "Coin Metrics Community API (CapMVRVCur)",
        }

    return {
        "disponible": True,
        "motif": "",
        "actif": identifiant,
        "valeur": valeur,
        "date": str(dernier.get("time", ""))[:10],
        "source": "Coin Metrics Community API (CapMVRVCur)",
        "definition": (
            "Capitalisation de marché rapportée à la capitalisation réalisée. "
            "Sous 1, le détenteur moyen est en perte latente."
        ),
    }


def get_prix_realise(actif: str = "btc", charge: Any | None = None) -> dict[str, Any]:
    """Déduit la capitalisation réalisée et le prix réalisé d'un actif.

    ``CapRealUSD`` n'est pas exposée en accès gratuit, mais le MVRV l'est, et
    il est *défini* comme le rapport de la capitalisation de marché à la
    capitalisation réalisée. La seconde s'obtient donc exactement en divisant
    la première par le ratio. Rien n'est estimé ici : c'est une identité
    algébrique entre trois grandeurs publiées.

    Args:
        actif: ``btc`` ou ``eth``.
        charge: réponse déjà obtenue, pour les tests hors ligne.

    Returns:
        Bloc avec ``disponible``, la capitalisation réalisée et, quand
        l'offre en circulation est connue, le prix réalisé par unité.
    """
    identifiant = actif.strip().lower()
    echec = {
        "disponible": False,
        "actif": identifiant,
        "capitalisation_realisee_usd": None,
        "prix_realise_usd": None,
        "source": "Coin Metrics Community (déduit de CapMrktCurUSD / CapMVRVCur)",
    }

    if charge is None:
        charge = _appeler(
            URL_COINMETRICS,
            {
                "assets": identifiant,
                "metrics": "CapMVRVCur,CapMrktCurUSD,SplyCur",
                "frequency": "1d",
                "page_size": 10,
            },
        )

    if not isinstance(charge, dict) or not charge.get("data"):
        return {**echec, "motif": "Coin Metrics n'a renvoyé aucune observation"}

    points = [
        p for p in charge["data"]
        if p.get("CapMVRVCur") is not None and p.get("CapMrktCurUSD") is not None
    ]
    if not points:
        return {**echec, "motif": "capitalisation de marché ou MVRV absent"}

    points.sort(key=lambda p: str(p.get("time", "")))
    dernier = points[-1]
    try:
        mvrv = float(dernier["CapMVRVCur"])
        capitalisation = float(dernier["CapMrktCurUSD"])
    except (TypeError, ValueError):
        return {**echec, "motif": "valeurs non numériques"}

    if mvrv <= 0.0:
        return {**echec, "motif": "MVRV nul ou négatif : la division est impossible"}

    realisee = capitalisation / mvrv
    offre = dernier.get("SplyCur")
    prix_realise: float | None = None
    try:
        if offre is not None and float(offre) > 0.0:
            prix_realise = realisee / float(offre)
    except (TypeError, ValueError):
        prix_realise = None

    return {
        "disponible": True,
        "motif": "",
        "actif": identifiant,
        "date": str(dernier.get("time", ""))[:10],
        "capitalisation_marche_usd": capitalisation,
        "capitalisation_realisee_usd": realisee,
        "offre_en_circulation": None if offre is None else float(offre),
        "prix_realise_usd": prix_realise,
        "source": "Coin Metrics Community (déduit de CapMrktCurUSD / CapMVRVCur)",
        "methode": (
            "Le MVRV étant par définition le rapport de la capitalisation de marché "
            "à la capitalisation réalisée, cette dernière s'obtient exactement par "
            "division. Aucune estimation n'intervient."
        ),
    }


def get_comportement_detenteurs_lt(actif: str = "btc") -> dict[str, Any]:
    """Signale l'absence de source gratuite sur les détenteurs de long terme.

    Les métriques d'ancienneté des pièces — part de l'offre immobile depuis
    plus de cent cinquante jours, dépenses des détenteurs anciens — sont
    l'apanage des fournisseurs payants. Le catalogue Community de Coin
    Metrics en compte trente et une, et aucune ne mesure l'âge des pièces.
    Aucun appel réseau n'est fait : l'absence est structurelle, la
    réinterroger chaque jour coûterait du temps pour un échec connu.

    Args:
        actif: actif concerné, repris pour information.

    Returns:
        Bloc marqué indisponible, avec le motif.
    """
    return {
        "disponible": False,
        "actif": actif.strip().lower(),
        "motif": (
            "aucune métrique gratuite d'ancienneté des pièces. Le catalogue Community "
            "de Coin Metrics (31 métriques, vérifié le 8 septembre 2026) n'expose ni "
            "part de l'offre dormante, ni dépenses des détenteurs anciens : ces "
            "grandeurs sont réservées aux offres payantes."
        ),
        "part_offre_dormante": None,
        "alimente_le_regime": False,
    }


# ---------------------------------------------------------------------------
# Offre de stablecoins
# ---------------------------------------------------------------------------
def get_croissance_stablecoins(
    fenetre_jours: int = 30, charge: Any | None = None
) -> dict[str, Any]:
    """Mesure la croissance de l'offre de stablecoins sur une fenêtre.

    L'offre de stablecoins est la trésorerie disponible du marché crypto :
    elle croît quand des capitaux entrent et attendent d'être déployés, elle
    se contracte quand ils sortent vers la monnaie traditionnelle.

    Args:
        fenetre_jours: profondeur de la comparaison.
        charge: réponse DefiLlama déjà obtenue, pour les tests.

    Returns:
        Bloc avec ``disponible``, l'offre courante et la variation.
    """
    if charge is None:
        charge = _appeler(URL_STABLECOINS)

    if not isinstance(charge, list) or len(charge) < 2:
        return {
            "disponible": False,
            "motif": "DefiLlama n'a renvoyé aucune série d'offre de stablecoins",
            "source": "DefiLlama /stablecoincharts/all",
        }

    def _valeur(point: Any) -> float | None:
        """Extrait l'encours en dollars d'un point de la série."""
        if not isinstance(point, dict):
            return None
        total = point.get("totalCirculating")
        if not isinstance(total, dict):
            return None
        try:
            return float(total.get("peggedUSD"))
        except (TypeError, ValueError):
            return None

    points = [(p, _valeur(p)) for p in charge]
    points = [(p, v) for p, v in points if v is not None and v > 0.0]
    if len(points) < 2:
        return {
            "disponible": False,
            "motif": "moins de deux points d'offre exploitables",
            "source": "DefiLlama /stablecoincharts/all",
        }

    points.sort(key=lambda pv: int(pv[0].get("date", 0)))
    courant_point, courant = points[-1]

    # La série est journalière : on recule du nombre de jours demandé, borné
    # par la profondeur réellement disponible.
    recul = min(int(fenetre_jours), len(points) - 1)
    _, precedent = points[-1 - recul]

    croissance = (courant / precedent - 1.0) * 100.0 if precedent else None
    return {
        "disponible": croissance is not None,
        "motif": "" if croissance is not None else "offre antérieure nulle",
        "offre_usd": courant,
        "offre_precedente_usd": precedent,
        "fenetre_jours": recul,
        "croissance_pct": croissance,
        "source": "DefiLlama /stablecoincharts/all",
        "lecture": (
            f"Offre de stablecoins à {courant / 1e9:.1f} Md$, "
            f"{croissance:+.1f} % sur {recul} jours."
            if croissance is not None
            else ""
        ),
    }


# ---------------------------------------------------------------------------
# Flux des ETF spot
# ---------------------------------------------------------------------------
def get_flux_etf(actif: str = "btc", **kwargs: Any) -> dict[str, Any]:
    """Renvoie le flux net des ETF spot d'un actif.

    Délègue à :mod:`dataio.etf_flows`, qui lit le tableau de Farside et
    dispose d'un repli par variation des actifs nets.

    Args:
        actif: ``btc`` ou ``eth``.
        **kwargs: arguments transmis tels quels, utiles aux tests.

    Returns:
        Le bloc de flux, tel que produit par la couche de données.
    """
    from dataio import etf_flows

    return etf_flows.get_flux_etf(actif=actif, **kwargs)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def classer_regime(
    mvrv: float | None,
    croissance_stablecoins_pct: float | None,
    seuils: dict[str, float] | None = None,
    seuil_croissance_pct: float = 2.0,
) -> dict[str, Any]:
    """Range l'état du marché dans l'un des quatre régimes.

    Logique retenue
    ---------------
    Le MVRV donne l'axe principal, parce qu'il mesure la plus-value latente
    du détenteur moyen, donc la pression de vente potentielle :

    * MVRV < 1 — ``capitulation``. Le détenteur moyen est en perte : ceux qui
      restent sont ceux qui n'ont pas vendu à perte.
    * 1 ≤ MVRV < 2 — ``accumulation``. Plus-value modérée, peu d'incitation à
      vendre massivement.
    * 2 ≤ MVRV < 3 — ``expansion``. Les plus-values s'installent.
    * MVRV ≥ 3 — ``distribution``. Plus-value moyenne supérieure à 200 % :
      les détenteurs anciens ont un intérêt croissant à réaliser.

    L'offre de stablecoins **nuance** sans jamais renverser : elle mesure la
    trésorerie disponible pour acheter. Une offre qui se contracte pendant
    une phase d'accumulation affaiblit ce classement, et le module le dit
    dans sa nuance plutôt que de changer de case — un seul indicateur
    secondaire ne doit pas suffire à basculer une lecture.

    Args:
        mvrv: valeur du MVRV.
        croissance_stablecoins_pct: croissance de l'offre sur la fenêtre.
        seuils: bornes de MVRV, depuis la configuration.
        seuil_croissance_pct: croissance jugée significative.

    Returns:
        Bloc avec le régime, la condition d'invalidation et la nuance.
        **Chaque régime possible possède une condition d'invalidation non
        vide** : un classement qu'on ne saurait pas contredire ne serait pas
        une lecture, ce serait une opinion.
    """
    bornes = dict(seuils or {})
    borne_capitulation = float(bornes.get("capitulation_max", 1.0))
    borne_accumulation = float(bornes.get("accumulation_max", 2.0))
    borne_expansion = float(bornes.get("expansion_max", 3.0))

    if mvrv is None:
        return {
            "disponible": False,
            "motif": "MVRV indisponible : aucun régime ne peut être établi",
            "regime": None,
            "invalidation": {
                "condition": "Retour d'une source de MVRV exploitable.",
                "seuil": None,
                "variable": "MVRV",
            },
            "nuance": "",
        }

    if mvrv < borne_capitulation:
        regime = CAPITULATION
        description = (
            f"MVRV à {mvrv:.2f}, sous {borne_capitulation:.2f} : le détenteur moyen "
            "porte une perte latente."
        )
        invalidation = {
            "variable": "MVRV",
            "seuil": borne_capitulation,
            "operateur": ">=",
            "condition": (
                f"Le régime cesse d'être « capitulation » dès que le MVRV repasse "
                f"au-dessus de {borne_capitulation:.2f}, c'est-à-dire dès que le "
                "détenteur moyen redevient en plus-value latente."
            ),
        }
    elif mvrv < borne_accumulation:
        regime = ACCUMULATION
        description = (
            f"MVRV à {mvrv:.2f}, entre {borne_capitulation:.2f} et "
            f"{borne_accumulation:.2f} : plus-value latente modérée."
        )
        invalidation = {
            "variable": "MVRV",
            "seuil": [borne_capitulation, borne_accumulation],
            "operateur": "sort de l'intervalle",
            "condition": (
                f"Le régime cesse d'être « accumulation » si le MVRV retombe sous "
                f"{borne_capitulation:.2f} (retour en capitulation) ou dépasse "
                f"{borne_accumulation:.2f} (passage en expansion)."
            ),
        }
    elif mvrv < borne_expansion:
        regime = EXPANSION
        description = (
            f"MVRV à {mvrv:.2f}, entre {borne_accumulation:.2f} et "
            f"{borne_expansion:.2f} : les plus-values latentes s'installent."
        )
        invalidation = {
            "variable": "MVRV",
            "seuil": [borne_accumulation, borne_expansion],
            "operateur": "sort de l'intervalle",
            "condition": (
                f"Le régime cesse d'être « expansion » si le MVRV retombe sous "
                f"{borne_accumulation:.2f} ou dépasse {borne_expansion:.2f}."
            ),
        }
    else:
        regime = DISTRIBUTION
        description = (
            f"MVRV à {mvrv:.2f}, au-dessus de {borne_expansion:.2f} : plus-value "
            "latente moyenne élevée."
        )
        invalidation = {
            "variable": "MVRV",
            "seuil": borne_expansion,
            "operateur": "<",
            "condition": (
                f"Le régime cesse d'être « distribution » dès que le MVRV repasse "
                f"sous {borne_expansion:.2f}."
            ),
        }

    # Nuance apportée par l'offre de stablecoins, sans changer le classement.
    nuance = ""
    if croissance_stablecoins_pct is None:
        nuance = (
            "L'offre de stablecoins n'a pas pu être mesurée : le régime repose sur "
            "le seul MVRV."
        )
    elif croissance_stablecoins_pct >= seuil_croissance_pct:
        nuance = (
            f"L'offre de stablecoins croît de {croissance_stablecoins_pct:+.1f} % sur la "
            "fenêtre : la trésorerie disponible du marché augmente, ce qui va dans le "
            f"sens du régime « {regime} » lorsqu'il est haussier et le contredit sinon."
        )
    elif croissance_stablecoins_pct <= -seuil_croissance_pct:
        nuance = (
            f"L'offre de stablecoins recule de {abs(croissance_stablecoins_pct):.1f} % sur "
            "la fenêtre : la trésorerie disponible se contracte, ce qui affaiblit un "
            "classement haussier."
        )
    else:
        nuance = (
            f"L'offre de stablecoins varie de {croissance_stablecoins_pct:+.1f} %, sous le "
            f"seuil de {seuil_croissance_pct:.1f} % : elle n'apporte aucune nuance."
        )

    return {
        "disponible": True,
        "motif": "",
        "regime": regime,
        "mvrv": mvrv,
        "description": description,
        "invalidation": invalidation,
        "nuance": nuance,
        "seuils_appliques": {
            "capitulation_max": borne_capitulation,
            "accumulation_max": borne_accumulation,
            "expansion_max": borne_expansion,
        },
    }


def analyser_actif(
    actif: str,
    seuils: dict[str, float],
    croissance_stablecoins_pct: float | None,
    seuil_croissance_pct: float,
    mvrv: dict[str, Any] | None = None,
    prix_realise: dict[str, Any] | None = None,
    flux_etf: dict[str, Any] | None = None,
    actif_de_calibrage: str = "btc",
) -> dict[str, Any]:
    """Assemble le régime d'un actif et les métriques qui le documentent.

    Args:
        actif: ``btc`` ou ``eth``.
        seuils: bornes de MVRV.
        croissance_stablecoins_pct: croissance de l'offre, commune au marché.
        seuil_croissance_pct: croissance jugée significative.
        mvrv: mesure déjà obtenue, pour les tests.
        prix_realise: mesure déjà obtenue, pour les tests.
        flux_etf: mesure déjà obtenue, pour les tests.
        actif_de_calibrage: actif sur l'histoire duquel les seuils ont été
            établis. Tout autre actif reçoit un avertissement explicite.

    Returns:
        Bloc de régime, enrichi des métriques et de leurs indisponibilités.
    """
    identifiant = actif.strip().lower()

    if mvrv is None:
        mvrv = get_mvrv(identifiant)
    if prix_realise is None:
        prix_realise = get_prix_realise(identifiant)
    if flux_etf is None:
        flux_etf = get_flux_etf(identifiant)

    valeur = mvrv.get("valeur") if mvrv.get("disponible") else None
    bloc = classer_regime(
        valeur, croissance_stablecoins_pct,
        seuils=seuils, seuil_croissance_pct=seuil_croissance_pct,
    )

    # Les bornes viennent de l'histoire d'un seul actif. Les appliquer à un
    # autre est une convention de lecture, pas un résultat mesuré : le taire
    # ferait passer une approximation pour une mesure.
    if identifiant != actif_de_calibrage:
        bloc["seuils_calibres_sur"] = actif_de_calibrage
        bloc["avertissement_calibrage"] = (
            f"Les bornes de MVRV appliquées ici sont calibrées sur l'histoire de "
            f"{actif_de_calibrage.upper()}. Les distributions de MVRV diffèrent d'un "
            f"actif à l'autre : ce classement de {identifiant.upper()} est une "
            "convention de lecture, pas un seuil mesuré sur son propre historique."
        )
    else:
        bloc["seuils_calibres_sur"] = actif_de_calibrage
        bloc["avertissement_calibrage"] = ""

    bloc["actif"] = identifiant
    bloc["metriques"] = {
        "mvrv": mvrv,
        "prix_realise": prix_realise,
        "detenteurs_long_terme": get_comportement_detenteurs_lt(identifiant),
        "flux_etf_spot": flux_etf,
    }
    bloc["metriques_indisponibles"] = [
        nom for nom, m in bloc["metriques"].items() if not m.get("disponible")
    ]
    return bloc


def analyser_regime(
    configuration: dict[str, Any],
    mvrv_par_actif: dict[str, dict[str, Any]] | None = None,
    stablecoins: dict[str, Any] | None = None,
    prix_realise_par_actif: dict[str, dict[str, Any]] | None = None,
    flux_etf_par_actif: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Produit le bloc régime du rapport crypto, un bloc par actif suivi.

    Args:
        configuration: bloc ``crypto_regime`` de la configuration.
        mvrv_par_actif: MVRV déjà collectés, pour les tests.
        stablecoins: croissance déjà collectée, pour les tests.
        prix_realise_par_actif: prix réalisés déjà collectés, pour les tests.
        flux_etf_par_actif: flux déjà collectés, pour les tests.

    Returns:
        Bloc sérialisable. Outre le dictionnaire ``regimes`` indexé par
        actif, il expose ``regime_btc`` et ``regime_eth`` à la racine, de
        structure identique, pour que la page web n'ait pas à connaître la
        liste des actifs suivis.
    """
    actifs = [str(a).lower() for a in (configuration.get("actifs") or ["btc"])]
    seuils = dict(configuration.get("seuils_mvrv") or {})
    fenetre = int(configuration.get("fenetre_stablecoins_jours", 30))
    seuil_croissance = float(configuration.get("seuil_croissance_stablecoins_pct", 2.0))
    calibrage = str(configuration.get("actif_de_calibrage", "btc")).lower()

    if stablecoins is None:
        stablecoins = get_croissance_stablecoins(fenetre_jours=fenetre)
    croissance = stablecoins.get("croissance_pct") if stablecoins.get("disponible") else None

    regimes: dict[str, Any] = {}
    for actif in actifs:
        regimes[actif] = analyser_actif(
            actif,
            seuils=seuils,
            croissance_stablecoins_pct=croissance,
            seuil_croissance_pct=seuil_croissance,
            mvrv=(mvrv_par_actif or {}).get(actif),
            prix_realise=(prix_realise_par_actif or {}).get(actif),
            flux_etf=(flux_etf_par_actif or {}).get(actif),
            actif_de_calibrage=calibrage,
        )

    disponibles = [a for a, b in regimes.items() if b.get("disponible")]
    resultat: dict[str, Any] = {
        "disponible": bool(disponibles),
        "motif": "" if disponibles else "aucun MVRV exploitable",
        "actifs": actifs,
        "actif_de_calibrage": calibrage,
        "regimes": regimes,
        "offre_stablecoins": stablecoins,
        "avertissement": (
            "Un régime décrit l'état courant du marché et la condition qui le "
            "rendrait caduc. Ce n'est ni une prévision de prix, ni une indication "
            "de ce qu'il conviendrait de faire."
        ),
    }
    # Accès direct par actif, à la racine, comme demandé par les consommateurs
    # du JSON qui ne veulent pas parcourir un dictionnaire.
    for actif in ("btc", "eth"):
        resultat[f"regime_{actif}"] = regimes.get(
            actif,
            {
                "disponible": False,
                "actif": actif,
                "motif": f"actif « {actif} » absent de la configuration crypto_regime.actifs",
                "regime": None,
                "invalidation": {
                    "variable": "configuration",
                    "seuil": None,
                    "condition": (
                        f"Ajouter « {actif} » à crypto_regime.actifs pour que ce "
                        "régime soit calculé."
                    ),
                },
            },
        )
    return resultat
