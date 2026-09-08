"""Flux nets des ETF spot Bitcoin : tentative directe, puis approximation.

Pourquoi ce module existe
-------------------------
Les créations et rachats de parts des ETF spot sont la mesure la plus directe
de la demande institutionnelle occidentale pour le bitcoin. Ils entrent dans
la lecture de régime de :mod:`modules.crypto.regime`.

Ce qui a été essayé, et ce que ça a donné
-----------------------------------------
**Farside Investors** publie ces flux en dollars, par ETF et par jour. La
source avait été écartée sur un 403. Réinterrogée avec des en-têtes de
navigateur complets — User-Agent Chrome, ``Accept`` HTML,
``Accept-Language``, ``Upgrade-Insecure-Requests`` — elle **répond 200** et
renvoie le tableau réel. Le refus initial venait de l'empreinte de l'outil
d'appel, pas d'un blocage de la donnée.

C'est donc la source principale : des flux mesurés, pas estimés. Le tableau
est publié en millions de dollars et note les sorties entre parenthèses,
convention comptable que l'analyseur traduit en valeurs négatives. Les
quatre dernières lignes sont des agrégats — ``Total``, ``Average``,
``Maximum``, ``Minimum`` — et non des dates : elles sont écartées.

**Repli, si Farside redevient inaccessible : approximation par la variation
des actifs nets.** Pour chaque ETF spot majeur, ``yfinance`` expose
``totalAssets``. Entre deux observations, l'actif net bouge pour deux
raisons : le prix du bitcoin a changé, et des parts ont été créées ou
rachetées. En neutralisant la première, il reste la seconde :

    flux estimé = AUM_t − AUM_{t-1} × (P_t / P_{t-1})

Limites du repli, qui sont sérieuses
------------------------------------
Le repli est publié avec ``est_approximation: true`` et ne doit pas être lu
comme une mesure :

* ``totalAssets`` n'a **aucun historique** chez yfinance : c'est une valeur
  instantanée. Le module doit donc constituer lui-même sa série, dans un
  cache. À la première exécution, il n'y a pas de point antérieur et le
  bloc sort indisponible — c'est normal, pas une panne ;
* la fréquence réelle de rafraîchissement de ``totalAssets`` est celle du
  fournisseur de données, pas la nôtre : elle est souvent quotidienne mais
  pas garantie, et une valeur figée deux jours produirait un flux nul
  artificiel. L'âge du point précédent est donc publié ;
* les frais de gestion, l'écart entre prix de marché et valeur liquidative,
  et les arrondis du fournisseur se retrouvent dans le résidu. Sur une
  journée calme, le bruit peut dépasser le flux réel ;
* le prix retenu pour neutraliser l'effet de marché est celui du bitcoin au
  comptant, alors que chaque ETF valorise sur sa propre référence horaire.

Conclusion pratique sur le repli : le **signe** et l'**ordre de grandeur**
sur plusieurs jours ont un sens, le chiffre d'une journée isolée n'en a pas.
C'est précisément pourquoi Farside, quand il répond, est préféré.
"""

from __future__ import annotations

import io
import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Final

import requests

_LOG: Final = logging.getLogger(__name__)

#: Pages publiant les flux, par actif. Farside en tient une par sous-jacent,
#: de structure identique — vérifié le 8 septembre 2026, les deux répondent
#: 200 avec des en-têtes de navigateur.
URL_FARSIDE_PAR_ACTIF: Final[dict[str, str]] = {
    "btc": "https://farside.co.uk/btc/",
    "eth": "https://farside.co.uk/eth/",
}

#: Page par défaut, conservée pour compatibilité des appels existants.
URL_FARSIDE: Final = URL_FARSIDE_PAR_ACTIF["btc"]

#: Cache des actifs nets, versionné dans le dépôt : sans lui, aucune
#: variation n'est calculable puisque yfinance ne donne aucun historique.
CACHE_AUM: Final = Path(__file__).resolve().parents[1] / "config" / "etf_aum_cache.json"

#: Nombre d'instantanés conservés, pour que le fichier reste petit.
MAX_INSTANTANES: Final[int] = 90

#: Lignes d'agrégat en bas du tableau Farside, à écarter : ce ne sont pas
#: des dates mais des totaux et des extrêmes de colonne.
LIGNES_AGREGAT: Final[frozenset[str]] = frozenset(
    {"total", "average", "maximum", "minimum"}
)

#: Le tableau Farside est libellé en millions de dollars.
MILLIONS: Final[float] = 1e6

#: ETF spot bitcoin suivis par le repli, du plus gros au plus petit.
ETF_SPOT_BTC: Final[tuple[str, ...]] = ("IBIT", "FBTC", "ARKB", "BITB", "HODL", "BRRR")

#: Le repli par actifs nets n'est câblé que pour le bitcoin : les ETF ETH
#: n'ont pas été vérifiés un à un chez yfinance. Farside couvrant les deux,
#: le repli ETH n'a pas lieu d'être improvisé.
ETF_SPOT_PAR_ACTIF: Final[dict[str, tuple[str, ...]]] = {"btc": ETF_SPOT_BTC}

#: Délai maximal, en secondes, accordé à un appel réseau.
TIMEOUT: Final[float] = float(os.environ.get("HTTP_TIMEOUT", "20"))

#: En-têtes d'un navigateur réel, pour la tentative Farside.
_ENTETES_NAVIGATEUR: Final[dict[str, str]] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}

__all__ = [
    "ETF_SPOT_BTC",
    "URL_FARSIDE_PAR_ACTIF",
    "tenter_farside",
    "lire_actifs_nets",
    "get_flux_etf",
    "get_flux_etf_btc",
]


# ---------------------------------------------------------------------------
# Tentative 1 : Farside
# ---------------------------------------------------------------------------
def _nombre_farside(brut: Any) -> float | None:
    """Interprète une cellule du tableau Farside.

    Les sorties de capitaux sont notées entre parenthèses — « (528.3) » vaut
    -528,3 —, convention comptable qu'aucun analyseur numérique ne comprend
    seul. Les séparateurs de milliers et le tiret des cellules vides sont
    également traités.

    Args:
        brut: contenu de la cellule.

    Returns:
        La valeur en millions de dollars, ou ``None`` si la cellule est vide.
    """
    texte = str(brut).strip()
    if not texte or texte.lower() in {"nan", "-", "–", ""}:
        return None

    negatif = texte.startswith("(") and texte.endswith(")")
    if negatif:
        texte = texte[1:-1]
    texte = texte.replace(",", "").replace("$", "").replace("%", "").strip()

    try:
        valeur = float(texte)
    except ValueError:
        return None
    return -valeur if negatif else valeur


def tenter_farside(
    url: str = URL_FARSIDE, html: str | None = None
) -> tuple[dict[str, Any] | None, str]:
    """Lit le tableau des flux quotidiens publié par Farside.

    Args:
        url: page à interroger.
        html: contenu déjà obtenu, pour les tests hors ligne.

    Returns:
        Couple ``(donnees, motif)``. ``donnees`` porte la dernière journée
        publiée et l'historique ; il vaut ``None`` en cas d'échec, le motif
        disant alors ce qui s'est passé.
    """
    if html is None:
        try:
            reponse = requests.get(url, timeout=TIMEOUT, headers=_ENTETES_NAVIGATEUR)
        except requests.RequestException as exc:
            return None, f"Farside injoignable ({type(exc).__name__})"
        if reponse.status_code != 200:
            return None, (
                f"Farside répond HTTP {reponse.status_code} malgré des en-têtes de "
                "navigateur complets"
            )
        html = reponse.text

    try:
        import pandas as pd

        tableaux = pd.read_html(io.StringIO(html))
    except (ImportError, ValueError) as exc:
        return None, f"tableau Farside non analysable ({type(exc).__name__})"

    if not tableaux:
        return None, "aucun tableau trouvé sur la page Farside"

    cadre = tableaux[0]
    # L'en-tête est sur deux niveaux : le nom de l'ETF est au second.
    cadre.columns = [
        str(c[1]) if isinstance(c, tuple) and len(c) > 1 else str(c) for c in cadre.columns
    ]
    if cadre.shape[1] < 3:
        return None, "tableau Farside de forme inattendue"

    colonne_date = cadre.columns[0]
    colonne_total = cadre.columns[-1]

    lignes: list[dict[str, Any]] = []
    for _, ligne in cadre.iterrows():
        libelle = str(ligne[colonne_date]).strip()
        if not libelle or libelle.lower() in LIGNES_AGREGAT:
            continue
        try:
            jour = datetime.strptime(libelle, "%d %b %Y").date()
        except ValueError:
            continue

        total = _nombre_farside(ligne[colonne_total])
        if total is None:
            continue
        par_etf = {
            str(nom): _nombre_farside(ligne[nom])
            for nom in cadre.columns[1:-1]
            if _nombre_farside(ligne[nom]) is not None
        }
        lignes.append(
            {
                "date": str(jour),
                "flux_net_usd": total * MILLIONS,
                "detail_par_etf_usd": {k: v * MILLIONS for k, v in par_etf.items()},
            }
        )

    if not lignes:
        return None, "aucune ligne de flux datée dans le tableau Farside"

    lignes.sort(key=lambda l: l["date"])
    return {"lignes": lignes, "n_jours": len(lignes)}, ""


# ---------------------------------------------------------------------------
# Tentative 2# ---------------------------------------------------------------------------
# Tentative 2 : approximation par les actifs nets
# ---------------------------------------------------------------------------
def lire_actifs_nets(tickers: tuple[str, ...] = ETF_SPOT_BTC) -> dict[str, float]:
    """Relève l'actif net courant de chaque ETF.

    Args:
        tickers: symboles des ETF suivis.

    Returns:
        Actifs nets en dollars, par ticker. Les ETF illisibles sont absents.
    """
    try:
        import yfinance as yf
    except ImportError:
        _LOG.warning("yfinance absent : actifs nets des ETF non lisibles.")
        return {}

    actifs: dict[str, float] = {}
    for ticker in tickers:
        try:
            informations = yf.Ticker(ticker).get_info() or {}
        except Exception as exc:  # noqa: BLE001 - yfinance remonte des erreurs variées
            _LOG.warning("Actif net de %s illisible : %s", ticker, type(exc).__name__)
            continue
        valeur = informations.get("totalAssets") or informations.get("netAssets")
        if valeur is None:
            _LOG.warning("yfinance n'expose pas l'actif net de %s.", ticker)
            continue
        try:
            actifs[ticker] = float(valeur)
        except (TypeError, ValueError):
            _LOG.warning("Actif net de %s non numérique.", ticker)
    return actifs


def _lire_cache(chemin: Path) -> list[dict[str, Any]]:
    """Relit les instantanés d'actifs nets déjà enregistrés.

    Args:
        chemin: fichier de cache.

    Returns:
        Instantanés triés par date, liste vide si le cache est absent.
    """
    try:
        contenu = json.loads(chemin.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("Cache des actifs nets illisible (%s) : %s", chemin, exc)
        return []

    instantanes = contenu.get("instantanes") if isinstance(contenu, dict) else None
    if not isinstance(instantanes, list):
        return []
    return sorted(
        (i for i in instantanes if isinstance(i, dict) and i.get("date")),
        key=lambda i: str(i["date"]),
    )


def _ecrire_cache(chemin: Path, instantanes: list[dict[str, Any]]) -> bool:
    """Enregistre les instantanés, en bornant la profondeur conservée.

    Args:
        chemin: fichier de cache.
        instantanes: instantanés à conserver.

    Returns:
        ``True`` si l'écriture a réussi.
    """
    contenu = {
        "_commentaire": (
            "Instantanés d'actifs nets des ETF spot bitcoin, accumulés par "
            "dataio/etf_flows.py. yfinance ne donne aucun historique : sans ce "
            "cache, aucune variation n'est calculable. Régénéré automatiquement."
        ),
        "max_instantanes": MAX_INSTANTANES,
        "instantanes": instantanes[-MAX_INSTANTANES:],
    }
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(
            json.dumps(contenu, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        _LOG.warning("Cache des actifs nets non écrit (%s) : %s", chemin, exc)
        return False
    return True


def get_flux_etf(
    actif: str = "btc",
    prix_btc: float | None = None,
    chemin_cache: Path | None = None,
    aujourd_hui: date | None = None,
    actifs_courants: dict[str, float] | None = None,
    html_farside: str | None = None,
    essayer_farside: bool = True,
) -> dict[str, Any]:
    """Renvoie le flux net des ETF spot bitcoin, mesuré ou à défaut estimé.

    Deux voies, dans cet ordre : le tableau publié par Farside, qui donne des
    flux mesurés, puis l'approximation par la variation des actifs nets. La
    seconde ne sert qu'en cas d'échec de la première, et se déclare comme
    approximation.

    Args:
        actif: ``btc`` ou ``eth``. Farside publie une page par sous-jacent.
        prix_btc: prix du sous-jacent, nécessaire au seul repli.
        chemin_cache: cache des actifs nets. ``None`` retient
            :data:`CACHE_AUM`, résolu à l'appel.
        aujourd_hui: date de référence, pour les tests.
        actifs_courants: actifs nets déjà relevés, pour les tests hors ligne.
        html_farside: page déjà obtenue, pour les tests hors ligne.
        essayer_farside: ``False`` pour forcer le repli.

    Returns:
        Bloc sérialisable, avec ``methode`` disant laquelle des deux voies a
        servi.
    """
    identifiant = actif.strip().lower()
    chemin = chemin_cache or CACHE_AUM
    jour = aujourd_hui or datetime.now(timezone.utc).date()

    # --- Voie 1 : flux mesurés ------------------------------------------
    motif_farside = "tentative directe non effectuée"
    if essayer_farside:
        url = URL_FARSIDE_PAR_ACTIF.get(identifiant)
        if url is None:
            motif_farside = f"aucune page Farside connue pour l'actif « {identifiant} »"
            donnees = None
        else:
            donnees, motif_farside = tenter_farside(url=url, html=html_farside)
        if donnees and donnees.get("lignes"):
            lignes = donnees["lignes"]
            derniere = lignes[-1]
            age = (jour - datetime.strptime(derniere["date"], "%Y-%m-%d").date()).days
            cumul_5j = sum(float(l["flux_net_usd"]) for l in lignes[-5:])
            flux = float(derniere["flux_net_usd"])
            return {
                "disponible": True,
                "motif": "",
                "methode": "mesure_directe",
                "actif": identifiant,
                "source": f"Farside Investors ({identifiant.upper()}, flux quotidiens)",
                "est_approximation": False,
                "flux_net_usd": flux,
                "date_flux": derniere["date"],
                "age_jours": age,
                "cumul_5_seances_usd": cumul_5j,
                "n_seances_publiees": donnees["n_jours"],
                "detail_par_etf_usd": derniere["detail_par_etf_usd"],
                "historique": lignes,
                "lecture": (
                    f"Flux net de {flux / 1e6:+.1f} M$ le {derniere['date']}, "
                    f"et de {cumul_5j / 1e6:+.1f} M$ cumulés sur les cinq dernières "
                    f"séances publiées. Donnée vieille de {age} jour(s)."
                ),
            }
        _LOG.warning("Flux ETF : source directe indisponible. %s", motif_farside)

    # --- Voie 2 : approximation par les actifs nets ----------------------
    tickers_repli = ETF_SPOT_PAR_ACTIF.get(identifiant)
    if tickers_repli is None:
        return {
            "disponible": False,
            "actif": identifiant,
            "methode": "aucune",
            "est_approximation": False,
            "motif": (
                f"{motif_farside} ; et aucun repli par actifs nets n'est câblé pour "
                f"« {identifiant} » : les ETF correspondants n'ont pas été vérifiés"
            ),
            "flux_net_usd": None,
        }
    if actifs_courants is None:
        actifs_courants = lire_actifs_nets(tickers_repli)

    base = {
        "methode": "approximation_actifs_nets",
        "source": "variation des actifs nets (yfinance)",
        "est_approximation": True,
        "actif": identifiant,
        "tentative_directe": {"source": "Farside Investors", "motif": motif_farside},
        "etf_suivis": list(tickers_repli),
    }

    if not actifs_courants:
        return {
            **base,
            "disponible": False,
            "motif": f"aucun actif net lisible ; {motif_farside}",
            "flux_net_usd": None,
        }

    total_courant = float(sum(actifs_courants.values()))
    instantanes = _lire_cache(chemin)
    precedents = [i for i in instantanes if str(i.get("date")) < str(jour)]

    # L'instantané du jour est enregistré quoi qu'il arrive : c'est lui qui
    # rendra le calcul possible demain.
    conserves = [i for i in instantanes if str(i.get("date")) != str(jour)]
    conserves.append(
        {
            "date": str(jour),
            "actifs_nets_usd": actifs_courants,
            "total_usd": total_courant,
            "prix_btc_usd": prix_btc,
        }
    )
    _ecrire_cache(chemin, sorted(conserves, key=lambda i: str(i["date"])))

    if not precedents:
        return {
            **base,
            "disponible": False,
            "motif": (
                "aucun instantané antérieur en cache : la variation n'est calculable "
                "qu'à partir de la deuxième exécution. L'instantané du jour vient "
                "d'être enregistré."
            ),
            "flux_net_usd": None,
            "actif_net_total_usd": total_courant,
            "n_instantanes_en_cache": len(conserves),
        }

    precedent = precedents[-1]
    total_precedent = float(precedent.get("total_usd") or 0.0)
    prix_precedent = precedent.get("prix_btc_usd")
    age_jours = (jour - datetime.strptime(str(precedent["date"]), "%Y-%m-%d").date()).days

    if total_precedent <= 0.0:
        return {
            **base,
            "disponible": False,
            "motif": "actif net antérieur nul ou illisible",
            "flux_net_usd": None,
        }

    # Sans prix exploitable des deux côtés, on ne peut pas séparer le flux du
    # mouvement de marché : mieux vaut le dire que de présenter une variation
    # d'actif net comme un flux.
    if prix_btc is None or prix_precedent in (None, 0):
        return {
            **base,
            "disponible": False,
            "motif": (
                "prix du bitcoin indisponible sur l'une des deux dates : l'effet de "
                "marché ne peut pas être neutralisé, et la variation d'actif net "
                "seule ne constitue pas un flux"
            ),
            "flux_net_usd": None,
            "actif_net_total_usd": total_courant,
        }

    facteur = float(prix_btc) / float(prix_precedent)
    attendu_sans_flux = total_precedent * facteur
    flux = total_courant - attendu_sans_flux

    return {
        **base,
        "disponible": True,
        "motif": "",
        "flux_net_usd": flux,
        "actif_net_total_usd": total_courant,
        "actif_net_precedent_usd": total_precedent,
        "date_precedente": str(precedent["date"]),
        "age_point_precedent_jours": age_jours,
        "prix_btc_usd": float(prix_btc),
        "prix_btc_precedent_usd": float(prix_precedent),
        "variation_prix_btc_pct": (facteur - 1.0) * 100.0,
        "actif_net_attendu_sans_flux_usd": attendu_sans_flux,
        "detail_par_etf": dict(actifs_courants),
        "n_instantanes_en_cache": len(conserves),
        "limite": (
            "Approximation, pas une mesure. L'actif net publié par yfinance n'a pas "
            "de fréquence garantie, et les frais comme l'écart à la valeur "
            "liquidative se retrouvent dans le résidu. Le signe et l'ordre de "
            "grandeur sur plusieurs jours ont un sens ; le chiffre d'une journée "
            "isolée n'en a pas."
        ),
        "lecture": (
            f"Actif net cumulé de {total_courant / 1e9:.1f} Md$ sur {len(actifs_courants)} "
            f"ETF. Après neutralisation d'un mouvement de {(facteur - 1.0) * 100:+.1f} % "
            f"du bitcoin sur {age_jours} jour(s), le flux net estimé ressort à "
            f"{flux / 1e9:+.2f} Md$."
        ),
    }


def get_flux_etf_btc(**kwargs: Any) -> dict[str, Any]:
    """Raccourci historique pour :func:`get_flux_etf` sur le bitcoin.

    Args:
        **kwargs: arguments transmis tels quels.

    Returns:
        Le bloc de flux du bitcoin.
    """
    return get_flux_etf(actif="btc", **kwargs)
