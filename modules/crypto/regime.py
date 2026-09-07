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

Flux des ETF spot : non alimenté
--------------------------------
Aucune source gratuite et fiable n'a été trouvée. Quatre pistes ont été
testées le 7 septembre 2026 et toutes ont échoué : CoinGlass (500),
SoSoValue (404), DefiLlama (400), Farside (403). Le bloc est donc publié
avec ``disponible: false`` et le détail des tentatives, plutôt que rempli
par une approximation.
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
# Flux ETF : non alimenté
# ---------------------------------------------------------------------------
def get_flux_etf() -> dict[str, Any]:
    """Signale l'absence de source gratuite pour les flux des ETF spot.

    Aucun appel réseau n'est fait : les quatre sources candidates ont été
    testées et écartées, et les réinterroger à chaque exécution coûterait du
    temps pour un échec connu d'avance. Le bloc conserve la liste des
    adresses testées afin que la vérification reste rejouable à la main.

    Returns:
        Bloc marqué indisponible, avec le détail des tentatives.
    """
    return {
        "disponible": False,
        "motif": (
            "aucune source gratuite et fiable de flux nets des ETF spot BTC. "
            "Quatre pistes testées le 7 septembre 2026, toutes en échec : "
            "CoinGlass (HTTP 500), SoSoValue (404), DefiLlama (400), Farside (403). "
            "Les agrégateurs qui publient ces flux les réservent à leurs offres payantes."
        ),
        "flux_net_usd": None,
        "sources_testees": [{"nom": n, "url": u} for n, u in SOURCES_ETF_TESTEES],
        "alimente_le_regime": False,
    }


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


def analyser_regime(
    configuration: dict[str, Any],
    mvrv_par_actif: dict[str, dict[str, Any]] | None = None,
    stablecoins: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produit le bloc régime du rapport crypto.

    Args:
        configuration: bloc ``crypto_regime`` de la configuration.
        mvrv_par_actif: MVRV déjà collectés, pour les tests.
        stablecoins: croissance déjà collectée, pour les tests.

    Returns:
        Bloc sérialisable, un régime par actif suivi.
    """
    actifs = [str(a).lower() for a in (configuration.get("actifs") or ["btc"])]
    seuils = dict(configuration.get("seuils_mvrv") or {})
    fenetre = int(configuration.get("fenetre_stablecoins_jours", 30))
    seuil_croissance = float(configuration.get("seuil_croissance_stablecoins_pct", 2.0))

    if mvrv_par_actif is None:
        mvrv_par_actif = {a: get_mvrv(a) for a in actifs}
    if stablecoins is None:
        stablecoins = get_croissance_stablecoins(fenetre_jours=fenetre)

    croissance = stablecoins.get("croissance_pct") if stablecoins.get("disponible") else None

    regimes: dict[str, Any] = {}
    for actif in actifs:
        mesure = dict(mvrv_par_actif.get(actif) or {})
        valeur = mesure.get("valeur") if mesure.get("disponible") else None
        bloc = classer_regime(
            valeur, croissance, seuils=seuils, seuil_croissance_pct=seuil_croissance
        )
        bloc["mvrv_source"] = mesure
        regimes[actif] = bloc

    disponibles = [a for a, b in regimes.items() if b.get("disponible")]
    return {
        "disponible": bool(disponibles),
        "motif": "" if disponibles else "aucun MVRV exploitable",
        "actifs": actifs,
        "regimes": regimes,
        "offre_stablecoins": stablecoins,
        "flux_etf": get_flux_etf(),
        "avertissement": (
            "Un régime décrit l'état courant du marché et la condition qui le "
            "rendrait caduc. Ce n'est ni une prévision de prix, ni une indication "
            "de ce qu'il conviendrait de faire."
        ),
    }
