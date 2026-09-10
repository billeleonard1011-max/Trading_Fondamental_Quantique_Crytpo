"""Le marché suit-il le bitcoin, ou l'argent tourne-t-il vers les alts ?

Trois mesures indépendantes, et pourquoi trois
----------------------------------------------
Aucune ne suffit seule, et c'est justement leur désaccord qui est informatif.
La synthèse ne tranche que lorsqu'elles pointent dans le même sens ; sinon
elle sort ``indetermine``, ce qui est une réponse et non un échec.

1. **Dominance du bitcoin** — sa part de la capitalisation totale. Elle monte
   quand le marché se replie sur l'actif le plus liquide.
2. **Ratio ETH/BTC** — l'indicateur historique de la rotation. Voir plus bas
   pourquoi il ne vaut plus ce qu'il valait.
3. **Largeur de marché** — la part des grandes capitalisations qui font mieux
   que le bitcoin. C'est la mesure la plus directe : la rotation, c'est par
   définition beaucoup d'actifs qui surperforment, pas un seul.

Ce que valent les fenêtres demandées, et ce qu'on obtient
---------------------------------------------------------
Les percentiles sont demandés sur deux ans. L'API gratuite de CoinGecko
plafonne son historique à **365 jours** — vérifié : ``days=730`` renvoie zéro
point. Les percentiles sont donc calculés sur la fenêtre réellement
disponible, et la sortie porte toujours ``fenetre_demandee_jours`` à côté de
``fenetre_effective_jours``. Un cache local accumule par ailleurs les
observations quotidiennes : la profondeur s'étend d'elle-même vers deux ans à
mesure que le système tourne.

La dominance, elle, n'est publiée qu'en valeur courante par l'API. Sa
tendance et son percentile viennent donc du cache seul, et restent
indisponibles tant qu'il n'est pas assez fourni. C'est une limite déclarée,
pas un trou masqué.

Le ratio ETH/BTC, et pourquoi on le publie en le nuançant
----------------------------------------------------------
Ce ratio a longtemps servi de signal avancé de la rotation : l'ether montait
d'abord, les alts suivaient. La relation s'est affaiblie depuis 2024-2025,
pour deux raisons structurelles qui n'ont pas disparu :

* une partie de la valeur autrefois captée par la couche de base d'Ethereum
  se loge désormais dans ses Layer 2, dont les jetons ne sont pas l'ether ;
* les flux des ETF spot ont divergé entre bitcoin et ether, ce qui déplace le
  ratio pour des raisons de collecte institutionnelle sans rapport avec
  l'appétit pour le risque des alts.

La sortie porte donc obligatoirement ``fiabilite_historique`` à
``reduite_depuis_2024``, avec l'explication. Ce n'est pas un commentaire de
code : c'est un champ du JSON, parce qu'un lecteur qui ignore cette réserve
lirait le ratio comme on le lisait en 2021.

Ce module décrit un état de marché. Il ne recommande rien.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
import requests

from dataio import crypto as crypto_io

_LOG: Final = logging.getLogger(__name__)

#: Cache des observations quotidiennes, versionné dans le dépôt : l'API ne
#: publie pas d'historique de dominance, il faut donc le constituer.
#:
#: Pourquoi ce fichier est versionné alors qu'il est généré
#: --------------------------------------------------------
#: Il n'est pas régénérable. Chaque exécution y **ajoute** l'observation du
#: jour ; l'API ne sait pas rendre les jours passés. Le supprimer du dépôt
#: ferait donc repartir de zéro l'historique de dominance, et ni le
#: percentile sur deux ans ni la tendance sur trente jours ne seraient plus
#: calculables avant des mois. C'est une donnée accumulée, pas un cache
#: d'accélération : la distinction décide de tout.
#:
#: Il vit sous ``config/`` pour des raisons historiques, à côté de fichiers
#: écrits à la main. Cette cohabitation prête à confusion — elle a fait
#: croire, lors d'un conflit de publication, que le fichier était édité
#: manuellement. Vérification faite sur son historique git : hormis le commit
#: qui l'a créé, il n'est écrit que par le workflow. Le conflit venait de
#: deux exécutions automatiques concurrentes, pas d'une main humaine.
#:
#: En cas de publication concurrente, il se fusionne par union des dates
#: (voir scripts/fusionner_sorties.py) : écraser une version perdrait
#: l'observation de l'autre exécution.
CACHE_ROTATION: Final = Path(__file__).resolve().parents[2] / "config" / "rotation_cache.json"

#: Deux ans d'observations conservées au maximum.
MAX_OBSERVATIONS: Final[int] = 760

#: Page publiant l'indice de saison des altcoins, pour recoupement.
URL_BLOCKCHAINCENTER: Final = "https://www.blockchaincenter.net/altcoin-season-index/"

#: Délai maximal, en secondes, accordé à un appel réseau.
TIMEOUT: Final[float] = float(os.environ.get("HTTP_TIMEOUT", "20"))

#: Fenêtres demandées, en jours.
FENETRE_PERCENTILE_JOURS: Final[int] = 730
FENETRE_TENDANCE_JOURS: Final[int] = 30

#: Horizon de comparaison de la largeur de marché. Quatre-vingt-dix jours est
#: la convention de l'indice de référence ; voir ``calculer_largeur_marche``
#: pour ce que l'API gratuite permet réellement.
HORIZON_LARGEUR_JOURS: Final[int] = 90

#: Nombre de capitalisations retenues pour la largeur de marché.
TAILLE_PANIER: Final[int] = 50

#: Part d'actifs surperformant au-delà de laquelle la largeur penche vers les
#: alts, et en deçà de laquelle elle penche vers le bitcoin. Le seuil de 75 %
#: est celui de l'indice de référence ; 25 % en est le symétrique.
SEUIL_LARGEUR_ALTS: Final[float] = 75.0
SEUIL_LARGEUR_BTC: Final[float] = 25.0

#: Jetons exclus du panier : ils ne mesurent pas l'appétit pour le risque.
#: Un stablecoin ne « sous-performe » pas le bitcoin, il est stable par
#: construction ; le compter fausserait la largeur vers le bas. Les jetons
#: emballés ou mis en jalonnement dupliquent un actif déjà présent.
_MOTIFS_EXCLUS: Final[tuple[str, ...]] = (
    "wrapped", "staked", "bridged", "peg", "usd", "eur", "tether", "dai",
)
_SYMBOLES_EXCLUS: Final[frozenset[str]] = frozenset(
    {
        "usdt", "usdc", "dai", "busd", "tusd", "usde", "fdusd", "pyusd",
        "usds", "usdd", "frax", "lusd", "gusd", "eurc", "eurs",
        "wbtc", "cbbtc", "weth", "steth", "wsteth", "reth", "wbeth",
        "beth", "meth", "ezeth", "weeth", "rseth", "solvbtc", "lbtc",
    }
)

#: États possibles de la synthèse.
DOMINANCE_BTC: Final = "dominance_btc"
ROTATION_ALTS: Final = "rotation_alts"
INDETERMINE: Final = "indetermine"

_ENTETES: Final[dict[str, str]] = {
    "Accept": "application/json",
    "User-Agent": "veille-marches/1.0",
}

__all__ = [
    "DOMINANCE_BTC",
    "ROTATION_ALTS",
    "INDETERMINE",
    "analyser_dominance",
    "analyser_ratio_eth_btc",
    "calculer_largeur_marche",
    "lire_indice_externe",
    "synthetiser",
    "analyser_rotation",
]


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------
def _percentile(valeur: float, echantillon: np.ndarray | pd.Series) -> float | None:
    """Rang d'une valeur dans une distribution, en pourcentage.

    Args:
        valeur: valeur à situer.
        echantillon: distribution de référence.

    Returns:
        Le rang, ou ``None`` si l'échantillon est trop court pour avoir un sens.
    """
    donnees = np.asarray(echantillon, dtype="float64")
    donnees = donnees[np.isfinite(donnees)]
    if donnees.size < 30:
        return None
    return float((donnees <= valeur).mean() * 100.0)


def _qualifier(variation: float, seuil: float) -> str:
    """Traduit une variation en mot.

    Args:
        variation: variation observée.
        seuil: amplitude en deçà de laquelle on parle de stabilité.

    Returns:
        ``en hausse``, ``en baisse`` ou ``stable``.
    """
    if variation > seuil:
        return "en hausse"
    if variation < -seuil:
        return "en baisse"
    return "stable"


def _lire_cache(chemin: Path) -> list[dict[str, Any]]:
    """Relit les observations quotidiennes accumulées.

    Args:
        chemin: fichier de cache.

    Returns:
        Observations triées par date, liste vide si le cache est absent.
    """
    try:
        contenu = json.loads(chemin.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("Cache de rotation illisible (%s) : %s", chemin, exc)
        return []
    observations = contenu.get("observations") if isinstance(contenu, dict) else None
    if not isinstance(observations, list):
        return []
    return sorted(
        (o for o in observations if isinstance(o, dict) and o.get("date")),
        key=lambda o: str(o["date"]),
    )


def _ecrire_cache(chemin: Path, observations: list[dict[str, Any]]) -> bool:
    """Enregistre les observations, en bornant la profondeur conservée.

    Args:
        chemin: fichier de cache.
        observations: observations à conserver.

    Returns:
        ``True`` si l'écriture a réussi.
    """
    contenu = {
        "_commentaire": (
            "Observations quotidiennes de dominance, accumulées par "
            "modules/crypto/rotation.py. L'API ne publie pas d'historique de "
            "dominance : sans ce cache, ni tendance ni percentile ne sont "
            "calculables. Régénéré automatiquement, ne pas éditer à la main."
        ),
        "max_observations": MAX_OBSERVATIONS,
        "observations": observations[-MAX_OBSERVATIONS:],
    }
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(
            json.dumps(contenu, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        _LOG.warning("Cache de rotation non écrit (%s) : %s", chemin, exc)
        return False
    return True


# ---------------------------------------------------------------------------
# 1. Dominance du bitcoin
# ---------------------------------------------------------------------------
def analyser_dominance(
    contexte: dict[str, Any] | None,
    historique: list[dict[str, Any]],
) -> dict[str, Any]:
    """Situe la dominance courante du bitcoin dans son histoire connue.

    Args:
        contexte: sortie de :func:`dataio.crypto.get_market_context`.
        historique: observations accumulées dans le cache.

    Returns:
        Bloc sérialisable. Tendance et percentile restent ``None`` tant que le
        cache n'est pas assez fourni, avec le motif correspondant.
    """
    if not contexte or not contexte.get("disponible"):
        return {
            "disponible": False,
            "motif": "contexte de marché CoinGecko indisponible",
            "valeur_pct": None,
        }

    courante = contexte.get("dominance_btc_pct")
    if courante is None:
        return {
            "disponible": False,
            "motif": "dominance absente du contexte de marché",
            "valeur_pct": None,
        }
    courante = float(courante)

    series = [
        float(o["dominance_btc_pct"])
        for o in historique
        if o.get("dominance_btc_pct") is not None
    ]

    variation: float | None = None
    tendance = ""
    if len(series) > FENETRE_TENDANCE_JOURS:
        precedente = series[-1 - FENETRE_TENDANCE_JOURS]
        variation = courante - precedente
        tendance = _qualifier(variation, 0.5)

    percentile = _percentile(courante, np.array(series)) if series else None

    return {
        "disponible": True,
        "motif": "",
        "valeur_pct": courante,
        "dominance_eth_pct": contexte.get("dominance_eth_pct"),
        "variation_30j_points": variation,
        "tendance": tendance,
        "percentile": percentile,
        "fenetre_demandee_jours": FENETRE_PERCENTILE_JOURS,
        "fenetre_effective_jours": len(series),
        "motif_fenetre": (
            ""
            if percentile is not None
            else (
                f"{len(series)} observation(s) en cache : l'API ne publie pas "
                "d'historique de dominance, la profondeur se constitue jour après "
                "jour et le percentile apparaîtra à partir de 30 observations."
            )
        ),
        "source": "CoinGecko /global + cache local",
        "lecture": (
            f"Dominance du bitcoin à {courante:.1f} %"
            + (f", {variation:+.1f} point(s) sur 30 jours" if variation is not None else "")
            + (f", {percentile:.0f}e percentile sur {len(series)} jours" if percentile is not None else "")
            + "."
        ),
    }


# ---------------------------------------------------------------------------
# 2. Ratio ETH/BTC
# ---------------------------------------------------------------------------
def analyser_ratio_eth_btc(
    prix_eth: pd.Series | None = None,
    prix_btc: pd.Series | None = None,
    jours: int = 365,
) -> dict[str, Any]:
    """Mesure le ratio ETH/BTC, sa tendance et son rang historique.

    Args:
        prix_eth: série de prix de l'ether. Chargée si absente.
        prix_btc: série de prix du bitcoin. Chargée si absente.
        jours: profondeur d'historique demandée.

    Returns:
        Bloc sérialisable. ``fiabilite_historique`` y figure toujours : la
        réserve sur ce ratio fait partie de la mesure, pas d'un commentaire.
    """
    reserve = {
        "fiabilite_historique": "reduite_depuis_2024",
        "explication_fiabilite": (
            "La relation entre ce ratio et la rotation vers les alts s'est "
            "affaiblie depuis 2024-2025. Une partie de la valeur autrefois captée "
            "par la couche de base d'Ethereum se loge désormais dans ses Layer 2, "
            "dont les jetons ne sont pas l'ether ; et les flux des ETF spot ont "
            "divergé entre bitcoin et ether, ce qui déplace le ratio pour des "
            "raisons de collecte institutionnelle sans rapport avec l'appétit pour "
            "le risque. Un ratio en hausse ne signale donc plus une rotation avec "
            "la fiabilité qu'il avait avant 2024."
        ),
    }

    if prix_eth is None:
        cadre = crypto_io.get_ohlc("ethereum", days=jours)
        prix_eth = cadre["close"] if not cadre.empty else None
    if prix_btc is None:
        cadre = crypto_io.get_ohlc("bitcoin", days=jours)
        prix_btc = cadre["close"] if not cadre.empty else None

    if prix_eth is None or prix_btc is None or prix_eth.empty or prix_btc.empty:
        return {
            **reserve,
            "disponible": False,
            "motif": "séries de prix ETH ou BTC indisponibles",
            "valeur": None,
        }

    cadre = pd.DataFrame({"eth": prix_eth, "btc": prix_btc}).dropna()
    cadre = cadre[cadre["btc"] > 0.0]
    if len(cadre) < 2:
        return {
            **reserve,
            "disponible": False,
            "motif": "moins de deux séances communes entre ETH et BTC",
            "valeur": None,
        }

    ratio = (cadre["eth"] / cadre["btc"]).sort_index()
    courant = float(ratio.iloc[-1])

    variation: float | None = None
    tendance = ""
    if len(ratio) > FENETRE_TENDANCE_JOURS:
        precedent = float(ratio.iloc[-1 - FENETRE_TENDANCE_JOURS])
        if precedent:
            variation = (courant / precedent - 1.0) * 100.0
            tendance = _qualifier(variation, 2.0)

    percentile = _percentile(courant, ratio)

    return {
        **reserve,
        "disponible": True,
        "motif": "",
        "valeur": courant,
        "variation_30j_pct": variation,
        "tendance": tendance,
        "percentile": percentile,
        "fenetre_demandee_jours": FENETRE_PERCENTILE_JOURS,
        "fenetre_effective_jours": len(ratio),
        "motif_fenetre": (
            ""
            if len(ratio) >= FENETRE_PERCENTILE_JOURS
            else (
                f"percentile calculé sur {len(ratio)} jours au lieu des "
                f"{FENETRE_PERCENTILE_JOURS} demandés : l'API gratuite de CoinGecko "
                "plafonne son historique à 365 jours"
            )
        ),
        "source": "CoinGecko (prix ETH et BTC)",
        "lecture": (
            f"Ratio ETH/BTC à {courant:.5f}"
            + (f", {variation:+.1f} % sur 30 jours" if variation is not None else "")
            + (f", {percentile:.0f}e percentile sur {len(ratio)} jours" if percentile is not None else "")
            + "."
        ),
    }


# ---------------------------------------------------------------------------
# 3. Largeur de marché
# ---------------------------------------------------------------------------
def _est_exclu(actif: dict[str, Any]) -> bool:
    """Dit si un actif doit être écarté du panier de largeur.

    Args:
        actif: entrée de ``/coins/markets``.

    Returns:
        ``True`` pour un stablecoin ou un jeton dupliquant un autre actif.
    """
    symbole = str(actif.get("symbol", "")).lower()
    if symbole in _SYMBOLES_EXCLUS:
        return True
    nom = f"{actif.get('id', '')} {actif.get('name', '')}".lower()
    return any(motif in nom for motif in _MOTIFS_EXCLUS)


def calculer_largeur_marche(
    marches: list[dict[str, Any]] | None = None,
    taille_panier: int = TAILLE_PANIER,
    horizon_jours: int = HORIZON_LARGEUR_JOURS,
) -> dict[str, Any]:
    """Part des grandes capitalisations qui font mieux que le bitcoin.

    Méthode, telle qu'appliquée
    ---------------------------
    1. Les ``taille_panier`` premières capitalisations sont demandées à
       CoinGecko, avec leurs variations sur plusieurs horizons.
    2. Les stablecoins et les jetons emballés ou mis en jalonnement sont
       écartés : un stablecoin ne sous-performe pas le bitcoin, il est stable
       par construction, et le compter tirerait la largeur vers le bas
       mécaniquement. Les jetons emballés dupliqueraient un actif déjà là.
    3. Le bitcoin lui-même sort du panier : il est la référence.
    4. On compte la part des actifs restants dont la variation dépasse celle
       du bitcoin sur le même horizon.

    Sur l'horizon, et pourquoi il peut différer de celui demandé : l'indice de
    référence raisonne à quatre-vingt-dix jours, mais l'API gratuite de
    CoinGecko ne renseigne pas ce champ — vérifié, il revient à ``null`` pour
    les cent actifs. Le module retient donc le plus long horizon réellement
    renseigné parmi ceux disponibles, et publie ``horizon_effectif_jours`` à
    côté de ``horizon_demande_jours``. La comparaison reste valide puisque
    tous les actifs sont mesurés sur le même horizon ; seule sa longueur
    diffère de la convention.

    Args:
        marches: réponse de ``/coins/markets`` déjà obtenue, pour les tests.
        taille_panier: nombre de capitalisations retenues.
        horizon_jours: horizon souhaité.

    Returns:
        Bloc sérialisable.
    """
    horizons_possibles = (90, 30, 14, 7)
    if marches is None:
        marches = crypto_io._appeler(
            "/coins/markets",
            {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": max(int(taille_panier) * 2, 50),
                "page": 1,
                "sparkline": "false",
                "price_change_percentage": ",".join(f"{h}d" for h in horizons_possibles),
            },
        )

    if not marches:
        return {
            "disponible": False,
            "motif": "CoinGecko n'a renvoyé aucune capitalisation",
            "part_surperformant_btc_pct": None,
        }

    # Le premier horizon effectivement renseigné pour le bitcoin fait foi.
    reference = next((a for a in marches if a.get("id") == "bitcoin"), None)
    if reference is None:
        return {
            "disponible": False,
            "motif": "bitcoin absent du panier : aucune référence de comparaison",
            "part_surperformant_btc_pct": None,
        }

    horizon_effectif: int | None = None
    for candidat in horizons_possibles:
        if candidat > int(horizon_jours):
            continue
        if reference.get(f"price_change_percentage_{candidat}d_in_currency") is not None:
            horizon_effectif = candidat
            break
    if horizon_effectif is None:
        for candidat in horizons_possibles:
            if reference.get(f"price_change_percentage_{candidat}d_in_currency") is not None:
                horizon_effectif = candidat
                break

    if horizon_effectif is None:
        return {
            "disponible": False,
            "motif": "aucune variation renseignée pour le bitcoin, quel que soit l'horizon",
            "part_surperformant_btc_pct": None,
        }

    champ = f"price_change_percentage_{horizon_effectif}d_in_currency"
    perf_btc = float(reference[champ])

    panier: list[dict[str, Any]] = []
    for actif in marches:
        if actif.get("id") == "bitcoin" or _est_exclu(actif):
            continue
        valeur = actif.get(champ)
        if valeur is None:
            continue
        panier.append(
            {
                "id": actif.get("id"),
                "symbole": str(actif.get("symbol", "")).upper(),
                "performance_pct": float(valeur),
            }
        )
        if len(panier) >= int(taille_panier):
            break

    if len(panier) < 10:
        return {
            "disponible": False,
            "motif": f"{len(panier)} actif(s) exploitable(s) après filtrage, 10 au minimum",
            "part_surperformant_btc_pct": None,
        }

    surperformants = [a for a in panier if a["performance_pct"] > perf_btc]
    part = len(surperformants) / len(panier) * 100.0

    return {
        "disponible": True,
        "motif": "",
        "part_surperformant_btc_pct": part,
        "n_actifs_panier": len(panier),
        "n_surperformants": len(surperformants),
        "performance_btc_pct": perf_btc,
        "horizon_demande_jours": int(horizon_jours),
        "horizon_effectif_jours": horizon_effectif,
        "motif_horizon": (
            ""
            if horizon_effectif == int(horizon_jours)
            else (
                f"horizon ramené à {horizon_effectif} jours : l'API gratuite de "
                f"CoinGecko ne renseigne pas la variation à {horizon_jours} jours. "
                "Tous les actifs restent comparés sur le même horizon."
            )
        ),
        "seuil_alts_pct": SEUIL_LARGEUR_ALTS,
        "seuil_btc_pct": SEUIL_LARGEUR_BTC,
        "exclusions": "stablecoins, jetons emballés ou mis en jalonnement, bitcoin lui-même",
        "source": "CoinGecko /coins/markets, calcul interne",
        "lecture": (
            f"{len(surperformants)} des {len(panier)} plus grandes capitalisations "
            f"({part:.0f} %) font mieux que le bitcoin sur {horizon_effectif} jours, "
            f"lequel varie de {perf_btc:+.1f} %."
        ),
    }


def lire_indice_externe(html: str | None = None) -> dict[str, Any]:
    """Relève l'indice public de saison des altcoins, pour recoupement.

    La valeur est extraite d'une page HTML : c'est fragile par nature, et le
    module le déclare. Elle ne sert qu'à recouper le calcul interne, jamais à
    le remplacer — deux mesures qui divergent sont une information, une mesure
    unique dont on ignore la méthode n'en est pas une.

    Args:
        html: page déjà obtenue, pour les tests hors ligne.

    Returns:
        Bloc sérialisable, indisponible si la page a changé de forme.
    """
    if html is None:
        try:
            reponse = requests.get(
                URL_BLOCKCHAINCENTER,
                timeout=TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0 (compatible; veille-marches/1.0)"},
            )
            reponse.raise_for_status()
            html = reponse.text
        except requests.RequestException as exc:
            return {
                "disponible": False,
                "motif": f"blockchaincenter injoignable ({type(exc).__name__})",
                "valeur": None,
                "source": URL_BLOCKCHAINCENTER,
            }

    # La page annonce « Altcoin Season ( 43 ) » ou « Bitcoin Season ( 12 ) »,
    # mais le balisage insère des commentaires entre le libellé et le nombre :
    # « Altcoin Season (<!-- -->43<!-- -->) ». On retire donc commentaires et
    # balises avant de chercher. La page affiche aussi des variantes « Month »
    # et « Year » ; seule « Season » porte sur quatre-vingt-dix jours, et
    # c'est celle que la recherche cible explicitement.
    texte = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    texte = re.sub(r"<[^>]+>", " ", texte)
    texte = re.sub(r"\s+", " ", texte)
    trouve = re.search(r"(Altcoin|Bitcoin)\s*Season\s*\(\s*(\d{1,3})\s*\)", texte)
    if not trouve:
        return {
            "disponible": False,
            "motif": (
                "valeur de l'indice introuvable dans la page : sa structure a "
                "probablement changé"
            ),
            "valeur": None,
            "source": URL_BLOCKCHAINCENTER,
        }

    return {
        "disponible": True,
        "motif": "",
        "valeur": int(trouve.group(2)),
        "qualification": trouve.group(1).lower(),
        "source": URL_BLOCKCHAINCENTER,
        "methode_annoncee": (
            "Part des 50 premières capitalisations ayant fait mieux que le bitcoin "
            "sur 90 jours ; au-delà de 75, la source parle de saison des altcoins."
        ),
        "fragilite": (
            "Valeur extraite d'une page HTML, sans API. Un changement de mise en "
            "page la rendrait indisponible : elle sert de recoupement, pas de "
            "source principale."
        ),
    }


# ---------------------------------------------------------------------------
# Synthèse
# ---------------------------------------------------------------------------
def synthetiser(
    dominance: dict[str, Any],
    ratio: dict[str, Any],
    largeur: dict[str, Any],
) -> dict[str, Any]:
    """Combine les trois mesures, sans trancher quand elles divergent.

    Chaque mesure vote pour un état ou s'abstient. La synthèse ne retient un
    état que si **toutes les mesures qui se prononcent** vont dans le même
    sens et qu'il y en a au moins deux. Sinon elle sort ``indetermine`` :
    un marché où la dominance monte pendant que la largeur s'élargit ne
    « penche » nulle part, et le dire est plus utile que de forcer un
    résultat par majorité.

    Args:
        dominance: bloc de dominance.
        ratio: bloc du ratio ETH/BTC.
        largeur: bloc de largeur de marché.

    Returns:
        Bloc de synthèse, avec la contribution détaillée de chaque mesure.
    """
    contributions: list[dict[str, Any]] = []

    # Dominance : elle monte, le marché se replie sur le bitcoin.
    vote_dominance: str | None = None
    if dominance.get("disponible") and dominance.get("variation_30j_points") is not None:
        variation = float(dominance["variation_30j_points"])
        if variation > 0.5:
            vote_dominance = DOMINANCE_BTC
        elif variation < -0.5:
            vote_dominance = ROTATION_ALTS
    contributions.append(
        {
            "mesure": "dominance_btc",
            "vote": vote_dominance,
            "abstention": vote_dominance is None,
            "valeur": dominance.get("valeur_pct"),
            "variation_30j_points": dominance.get("variation_30j_points"),
            "motif_abstention": (
                ""
                if vote_dominance
                else (
                    dominance.get("motif")
                    or dominance.get("motif_fenetre")
                    or "variation trop faible pour se prononcer (seuil 0,5 point)"
                )
            ),
        }
    )

    # Ratio ETH/BTC : il monte, l'ether prend la main sur le bitcoin.
    vote_ratio: str | None = None
    if ratio.get("disponible") and ratio.get("variation_30j_pct") is not None:
        variation = float(ratio["variation_30j_pct"])
        if variation > 2.0:
            vote_ratio = ROTATION_ALTS
        elif variation < -2.0:
            vote_ratio = DOMINANCE_BTC
    contributions.append(
        {
            "mesure": "ratio_eth_btc",
            "vote": vote_ratio,
            "abstention": vote_ratio is None,
            "valeur": ratio.get("valeur"),
            "variation_30j_pct": ratio.get("variation_30j_pct"),
            "fiabilite_historique": ratio.get("fiabilite_historique"),
            "poids_reduit": True,
            "motif_abstention": (
                ""
                if vote_ratio
                else (ratio.get("motif") or "variation trop faible pour se prononcer (seuil 2 %)")
            ),
        }
    )

    # Largeur : c'est la mesure la plus directe de la rotation.
    vote_largeur: str | None = None
    if largeur.get("disponible") and largeur.get("part_surperformant_btc_pct") is not None:
        part = float(largeur["part_surperformant_btc_pct"])
        if part >= SEUIL_LARGEUR_ALTS:
            vote_largeur = ROTATION_ALTS
        elif part <= SEUIL_LARGEUR_BTC:
            vote_largeur = DOMINANCE_BTC
    contributions.append(
        {
            "mesure": "largeur_marche",
            "vote": vote_largeur,
            "abstention": vote_largeur is None,
            "valeur": largeur.get("part_surperformant_btc_pct"),
            "horizon_effectif_jours": largeur.get("horizon_effectif_jours"),
            "motif_abstention": (
                ""
                if vote_largeur
                else (
                    largeur.get("motif")
                    or f"part comprise entre {SEUIL_LARGEUR_BTC:.0f} % et "
                    f"{SEUIL_LARGEUR_ALTS:.0f} % : aucun des deux régimes n'est net"
                )
            ),
        }
    )

    votes = [c["vote"] for c in contributions if c["vote"] is not None]
    distincts = set(votes)

    if len(votes) < 2:
        etat = INDETERMINE
        justification = (
            f"{len(votes)} mesure(s) se prononce(nt) sur trois : il en faut au moins "
            "deux concordantes pour conclure."
        )
    elif len(distincts) == 1:
        etat = votes[0]
        justification = (
            f"Les {len(votes)} mesures qui se prononcent vont toutes dans le même "
            f"sens : {etat.replace('_', ' ')}."
        )
    else:
        etat = INDETERMINE
        justification = (
            "Les mesures se contredisent : "
            + " ; ".join(
                f"{c['mesure']} penche vers {c['vote'].replace('_', ' ')}"
                for c in contributions
                if c["vote"]
            )
            + ". Aucun état d'ensemble ne se dégage."
        )

    # Condition d'invalidation : ce qui ferait basculer l'état constaté.
    if etat == ROTATION_ALTS:
        invalidation = {
            "variable": "largeur de marché",
            "seuil": SEUIL_LARGEUR_BTC,
            "condition": (
                f"L'état cesse d'être « rotation alts » si la part des grandes "
                f"capitalisations faisant mieux que le bitcoin retombe sous "
                f"{SEUIL_LARGEUR_ALTS:.0f} %, ou si la dominance du bitcoin repart "
                "à la hausse de plus de 0,5 point sur 30 jours."
            ),
        }
    elif etat == DOMINANCE_BTC:
        invalidation = {
            "variable": "largeur de marché",
            "seuil": SEUIL_LARGEUR_ALTS,
            "condition": (
                f"L'état cesse d'être « dominance btc » si la part des grandes "
                f"capitalisations faisant mieux que le bitcoin repasse au-dessus de "
                f"{SEUIL_LARGEUR_BTC:.0f} %, ou si la dominance recule de plus de "
                "0,5 point sur 30 jours."
            ),
        }
    else:
        invalidation = {
            "variable": "concordance des trois mesures",
            "seuil": 2,
            "condition": (
                "L'état cesse d'être « indeterminé » dès qu'au moins deux des trois "
                "mesures se prononcent dans le même sens."
            ),
        }

    return {
        "etat": etat,
        "justification": justification,
        "n_mesures_exprimees": len(votes),
        "n_mesures_total": len(contributions),
        "contributions": contributions,
        "invalidation": invalidation,
    }


def analyser_rotation(
    chemin_cache: Path | None = None,
    aujourd_hui: date | None = None,
    contexte: dict[str, Any] | None = None,
    marches: list[dict[str, Any]] | None = None,
    html_indice: str | None = None,
    prix_eth: pd.Series | None = None,
    prix_btc: pd.Series | None = None,
) -> dict[str, Any]:
    """Produit le bloc de rotation du rapport crypto.

    Args:
        chemin_cache: cache des observations. ``None`` retient
            :data:`CACHE_ROTATION`, résolu à l'appel.
        aujourd_hui: date de référence, pour les tests.
        contexte: contexte de marché déjà obtenu, pour les tests.
        marches: capitalisations déjà obtenues, pour les tests.
        html_indice: page d'indice déjà obtenue, pour les tests.
        prix_eth: série de prix de l'ether, pour les tests.
        prix_btc: série de prix du bitcoin, pour les tests.

    Returns:
        Bloc sérialisable.
    """
    chemin = chemin_cache or CACHE_ROTATION
    jour = aujourd_hui or datetime.now(timezone.utc).date()

    if contexte is None:
        contexte = crypto_io.get_market_context()

    historique = _lire_cache(chemin)

    # L'observation du jour est enregistrée avant lecture de la tendance :
    # c'est elle qui rendra la profondeur possible demain.
    if contexte and contexte.get("disponible") and contexte.get("dominance_btc_pct") is not None:
        conserves = [o for o in historique if str(o.get("date")) != str(jour)]
        conserves.append(
            {
                "date": str(jour),
                "dominance_btc_pct": float(contexte["dominance_btc_pct"]),
                "dominance_eth_pct": contexte.get("dominance_eth_pct"),
            }
        )
        historique = sorted(conserves, key=lambda o: str(o["date"]))
        _ecrire_cache(chemin, historique)

    dominance = analyser_dominance(contexte, historique)
    ratio = analyser_ratio_eth_btc(prix_eth=prix_eth, prix_btc=prix_btc)
    largeur = calculer_largeur_marche(marches=marches)
    indice_externe = lire_indice_externe(html=html_indice)

    # Recoupement : deux mesures de la même grandeur, obtenues autrement.
    ecart: float | None = None
    if largeur.get("disponible") and indice_externe.get("disponible"):
        ecart = float(largeur["part_surperformant_btc_pct"]) - float(indice_externe["valeur"])

    synthese = synthetiser(dominance, ratio, largeur)

    return {
        "disponible": any(b.get("disponible") for b in (dominance, ratio, largeur)),
        "horodatage_utc": datetime.now(timezone.utc).isoformat(),
        "dominance_btc": dominance,
        "ratio_eth_btc": ratio,
        "largeur_marche": largeur,
        "indice_externe_recoupement": {
            **indice_externe,
            "ecart_avec_calcul_interne_points": ecart,
            "commentaire_ecart": (
                ""
                if ecart is None
                else (
                    f"Le calcul interne donne {largeur['part_surperformant_btc_pct']:.0f} "
                    f"et la source publique {indice_externe['valeur']}, soit "
                    f"{ecart:+.0f} point(s) d'écart. Les deux ne mesurent pas "
                    "exactement la même chose : l'horizon et le panier diffèrent, et "
                    "l'écart est attendu plutôt qu'anormal."
                )
            ),
        },
        "synthese": synthese,
        "avertissement": (
            "Cet état décrit où va l'argent, pas où il devrait aller. Aucune de ces "
            "mesures ne constitue une indication d'achat ou de vente."
        ),
    }
