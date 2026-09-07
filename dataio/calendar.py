"""Calendrier des publications macroéconomiques qui déplacent l'or.

À quoi sert ce module
---------------------
Le compte à rebours avant la prochaine publication est l'une des
informations les plus utiles du système, et la seule qui dise **quand ne pas
trader**. Trente minutes avant un chiffre d'inflation, l'or ne suit plus
aucune logique fondamentale : les carnets se vident, les écarts s'élargissent
et le prix saute de plusieurs dollars sur la première ligne de la
publication. Une analyse fondamentale, même juste, n'a aucune prise sur ces
minutes-là.

Quatre publications sont suivies, parce qu'elles agissent sur les deux
moteurs du modèle de juste valeur — les taux réels et le dollar :

* **CPI** — l'inflation constatée, qui déplace les anticipations ;
* **Emploi (NFP)** — la vigueur de l'économie, donc la trajectoire des taux ;
* **PCE** — la mesure d'inflation que la Fed regarde réellement ;
* **FOMC** — la décision elle-même.

Sur l'heure de publication : point d'honnêteté
----------------------------------------------
FRED donne la **date** de publication, jamais l'**heure**. Les heures
utilisées ici sont les heures officielles d'usage, lues dans
``config/gold.yaml`` et exprimées en fuseau de New York : 08:30 pour les
statistiques, 14:00 pour la décision du FOMC. Chaque échéance porte donc un
drapeau ``heure_conventionnelle`` à ``True``. Un compte à rebours affiché à
la minute près alors que l'heure est une convention serait une fausse
précision, et c'est précisément le genre de détail sur lequel on perd de
l'argent.

Le FOMC : pourquoi FRED ne peut pas servir
------------------------------------------
FRED expose bien une « release » nommée **FOMC Press Release** (``rid=101``).
Elle a été vérifiée, et elle ne convient pas — le constat est reproduit ici
pour éviter qu'on refasse la vérification, ou pire, qu'on l'adopte de bonne
foi :

* elle ne contient que quatre séries, dont ``DFEDTARU`` et ``DFEDTARL``, les
  bornes du corridor des Fed funds ;
* ``DFEDTARU`` est une série **quotidienne 7 jours sur 7** : elle porte une
  observation le samedi et le dimanche, avec la valeur en vigueur ce jour-là ;
* les dates de publication de cette release sont donc les dates de mise à
  jour quotidienne de ces séries, **pas** les huit réunions annuelles du
  comité.

Interroger ``fred/release/dates`` avec ``release_id=101`` renverrait par
conséquent une date par jour. Un compte à rebours bâti dessus annoncerait une
réunion du FOMC pour demain, tous les jours de l'année : faux, et faux en
silence. C'est précisément le genre d'erreur que ce module doit empêcher.

Les dates viennent donc de la page officielle de la Réserve fédérale, lue par
:func:`fetch_fomc_calendar`, avec un cache versionné dans le dépôt comme
filet. La Fed précise que chaque date reste provisoire jusqu'à la réunion qui
la précède.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import requests

_LOG: Final = logging.getLogger(__name__)

#: Point d'accès des dates de publication d'une « release » FRED.
URL_RELEASE_DATES: Final = "https://api.stlouisfed.org/fred/release/dates"

#: Délai maximal, en secondes, accordé à un appel réseau.
TIMEOUT: Final[float] = float(os.environ.get("HTTP_TIMEOUT", "20"))

#: Fuseau de publication par défaut des statistiques américaines.
FUSEAU_DEFAUT: Final = "America/New_York"

#: Nombre d'échéances futures demandées à FRED par publication.
LIMITE_DATES: Final[int] = 40

#: Page officielle du calendrier des réunions du FOMC.
URL_FOMC: Final = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

#: Cache versionné des réunions collectées, relatif à la racine du dépôt.
CACHE_FOMC: Final = Path(__file__).resolve().parents[1] / "config" / "fomc_calendar_cache.json"

#: En deçà de ce nombre de réunions futures connues, l'alerte est levée.
SEUIL_ALERTE_REUNIONS: Final[int] = 2

#: Au-delà de cette ancienneté de collecte, le cache est jugé douteux et
#: mentionné dans le motif d'alerte.
CACHE_PERIME_JOURS: Final[int] = 120

#: Mois anglais tels qu'ils apparaissent sur la page de la Fed.
_MOIS: Final[dict[str, int]] = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    # La page abrège les mois des réunions à cheval : « Apr/May », « Jan/Feb ».
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sept": 9, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_ENTETES_FED: Final[dict[str, str]] = {
    "User-Agent": "Mozilla/5.0 (compatible; veille-marches/1.0)"
}

__all__ = [
    "Echeance",
    "ReunionFOMC",
    "fetch_fomc_calendar",
    "parser_calendrier_fomc",
    "charger_reunions_fomc",
    "get_calendrier",
    "prochaines_echeances",
]


@dataclass(slots=True, frozen=True)
class Echeance:
    """Une publication à venir.

    Attributes:
        nom: libellé lisible de la publication.
        horodatage: instant de publication, en UTC.
        minutes_restantes: minutes avant publication. Négatif si déjà passée.
        heure_conventionnelle: ``True`` quand l'heure vient de la convention
            et non de la source. C'est le cas de toutes les échéances ici.
        impact_or: intensité attendue de l'effet sur l'or.
        source: origine de la date.
    """

    nom: str
    horodatage: datetime
    minutes_restantes: int
    heure_conventionnelle: bool
    impact_or: str
    source: str

    @property
    def jours_restants(self) -> float:
        """Délai en jours, pratique pour l'affichage."""
        return self.minutes_restantes / 1440.0

    def to_dict(self) -> dict[str, Any]:
        """Sérialise l'échéance pour le rapport JSON."""
        return {
            "nom": self.nom,
            "horodatage_utc": self.horodatage.isoformat(),
            "date": str(self.horodatage.astimezone(ZoneInfo(FUSEAU_DEFAUT)).date()),
            "heure_locale_new_york": self.horodatage.astimezone(ZoneInfo(FUSEAU_DEFAUT)).strftime("%H:%M"),
            "minutes_restantes": self.minutes_restantes,
            "jours_restants": round(self.jours_restants, 2),
            "heure_conventionnelle": self.heure_conventionnelle,
            "impact_or": self.impact_or,
            "source": self.source,
        }


def _cle_api() -> str | None:
    """Lit la clé FRED dans l'environnement.

    Returns:
        La clé, ou ``None`` si elle est absente.
    """
    cle = os.environ.get("FRED_API_KEY", "").strip()
    if not cle:
        _LOG.warning("FRED_API_KEY absente : le calendrier des publications sera incomplet.")
        return None
    return cle


def _horodater(jour: date, heure_texte: str, fuseau: str) -> datetime:
    """Combine une date et une heure conventionnelle en instant UTC.

    Args:
        jour: date de publication.
        heure_texte: heure locale au format ``HH:MM``.
        fuseau: nom IANA du fuseau de publication.

    Returns:
        L'instant correspondant, converti en UTC.
    """
    try:
        heures, minutes = (int(p) for p in heure_texte.split(":", 1))
        moment = time(hour=heures, minute=minutes)
    except (ValueError, TypeError):
        _LOG.warning("Heure « %s » illisible : 08:30 retenu par défaut.", heure_texte)
        moment = time(hour=8, minute=30)

    # Le fuseau gère seul le passage à l'heure d'été : 08:30 à New York reste
    # 08:30 à New York toute l'année, même si l'écart avec UTC change.
    local = datetime.combine(jour, moment, tzinfo=ZoneInfo(fuseau))
    return local.astimezone(timezone.utc)


def _dates_release_fred(release_id: int, depuis: date) -> list[date]:
    """Interroge FRED pour les dates de publication d'une « release ».

    ``include_release_dates_with_no_data=true`` est indispensable : sans ce
    paramètre, FRED ne renvoie que les dates auxquelles des données existent
    déjà, donc uniquement le passé. C'est exactement l'inverse de ce qu'on
    cherche ici.

    Args:
        release_id: identifiant FRED de la publication.
        depuis: date à partir de laquelle chercher.

    Returns:
        Liste de dates triée, vide en cas d'échec.
    """
    cle = _cle_api()
    if cle is None:
        return []

    parametres = {
        "release_id": int(release_id),
        "api_key": cle,
        "file_type": "json",
        "realtime_start": depuis.strftime("%Y-%m-%d"),
        "realtime_end": "9999-12-31",
        "include_release_dates_with_no_data": "true",
        "sort_order": "asc",
        "limit": LIMITE_DATES,
    }
    try:
        reponse = requests.get(URL_RELEASE_DATES, params=parametres, timeout=TIMEOUT)
        reponse.raise_for_status()
        charge = reponse.json()
    except requests.RequestException as exc:
        _LOG.warning("FRED injoignable pour la publication %d : %s", release_id, exc)
        return []
    except ValueError as exc:
        _LOG.warning("Réponse FRED illisible pour la publication %d : %s", release_id, exc)
        return []

    jours: list[date] = []
    for entree in charge.get("release_dates", []):
        brut = entree.get("date")
        if not brut:
            continue
        try:
            jour = datetime.strptime(brut, "%Y-%m-%d").date()
        except ValueError:
            continue
        if jour >= depuis:
            jours.append(jour)

    if not jours:
        _LOG.warning("FRED n'a renvoyé aucune date future pour la publication %d.", release_id)
    return sorted(jours)


def _echeances_fred(
    publications: list[dict[str, Any]],
    fuseau: str,
    maintenant: datetime,
) -> list[Echeance]:
    """Construit les échéances des publications statistiques.

    Args:
        publications: blocs ``calendrier.publications`` de la configuration.
        fuseau: fuseau de publication.
        maintenant: instant de référence, en UTC.

    Returns:
        Liste d'échéances futures.
    """
    echeances: list[Echeance] = []
    for bloc in publications:
        nom = str(bloc.get("nom", "publication sans nom"))
        release_id = bloc.get("release_id")
        if release_id is None:
            _LOG.warning("Publication « %s » sans release_id : ignorée.", nom)
            continue

        heure = str(bloc.get("heure_locale", "08:30"))
        impact = str(bloc.get("impact_or", "inconnu"))

        for jour in _dates_release_fred(int(release_id), depuis=maintenant.date()):
            horodatage = _horodater(jour, heure, fuseau)
            if horodatage <= maintenant:
                # La date du jour est renvoyée par FRED même après publication :
                # on écarte ce qui est déjà tombé.
                continue
            echeances.append(
                Echeance(
                    nom=nom,
                    horodatage=horodatage,
                    minutes_restantes=int((horodatage - maintenant).total_seconds() // 60),
                    heure_conventionnelle=True,
                    impact_or=impact,
                    source=f"FRED release {release_id}",
                )
            )
    return echeances


# ---------------------------------------------------------------------------
# Calendrier du FOMC : collecte, analyse, cache
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class ReunionFOMC:
    """Une réunion du comité de politique monétaire.

    Attributes:
        date_decision: jour de l'annonce. Les réunions durent deux jours et la
            décision tombe le second : c'est cette date qui compte pour un
            trader, pas celle d'ouverture.
        avec_projections: ``True`` quand la réunion s'accompagne des
            projections économiques trimestrielles. Ces réunions déplacent
            davantage les taux réels, donc l'or.
        libelle: intitulé d'origine, conservé pour vérification humaine.
    """

    date_decision: date
    avec_projections: bool
    libelle: str

    def to_dict(self) -> dict[str, Any]:
        """Sérialise la réunion pour le cache JSON."""
        return {
            "date_decision": self.date_decision.strftime("%Y-%m-%d"),
            "avec_projections": self.avec_projections,
            "libelle": self.libelle,
        }

    @classmethod
    def from_dict(cls, brut: dict[str, Any]) -> "ReunionFOMC | None":
        """Relit une réunion depuis le cache.

        Args:
            brut: entrée du fichier de cache.

        Returns:
            La réunion, ou ``None`` si l'entrée est illisible — une ligne
            corrompue ne doit pas faire perdre tout le cache.
        """
        try:
            jour = datetime.strptime(str(brut["date_decision"]), "%Y-%m-%d").date()
        except (KeyError, TypeError, ValueError):
            return None
        return cls(
            date_decision=jour,
            avec_projections=bool(brut.get("avec_projections", False)),
            libelle=str(brut.get("libelle", "")),
        )


def parser_calendrier_fomc(html: str) -> list[ReunionFOMC]:
    """Extrait les réunions du HTML de la page de la Réserve fédérale.

    La page groupe les réunions par année dans des panneaux, et décrit chacune
    par un mois et une plage de jours. Quatre particularités, toutes observées
    sur la page réelle et toutes traitées ici :

    * ``17-18*`` — l'astérisque signale les projections économiques ;
    * ``Apr/May`` avec ``30-1`` — la réunion est à cheval sur deux mois, et la
      décision tombe le second jour, donc dans le **second** mois ;
    * ``Dec/Jan`` — le cas limite du changement d'année, où le second jour
      appartient à l'année suivante ;
    * ``22 (notation vote)`` — un vote par notation, qui n'est pas une réunion
      de politique monétaire et ne donne lieu à aucune décision de taux. Ces
      entrées sont écartées : les compter produirait un compte à rebours vers
      un événement qui ne déplace pas le marché.

    L'analyse se fait par expression régulière sur les classes CSS
    ``fomc-meeting__month`` et ``fomc-meeting__date``, plus stables que la
    structure d'imbrication des div, qui change au gré des refontes.

    Args:
        html: contenu de la page.

    Returns:
        Réunions triées par date, dédupliquées. Liste vide si la structure
        n'est pas reconnue — ce qui doit alors déclencher l'alerte, pas un
        repli silencieux.
    """
    if not html or "fomc-meeting" not in html:
        _LOG.warning("Page FOMC : structure non reconnue, aucun bloc « fomc-meeting ».")
        return []

    reunions: list[ReunionFOMC] = []
    panneaux = re.findall(
        r'<h4><a id="\d+">(\d{4})\s+FOMC\s+Meetings</a></h4>(.*?)'
        r'(?=<h4><a id="\d+">\d{4}\s+FOMC\s+Meetings</a></h4>|\Z)',
        html,
        re.S,
    )
    if not panneaux:
        _LOG.warning("Page FOMC : aucun panneau annuel « AAAA FOMC Meetings » trouvé.")
        return []

    for annee_texte, bloc_annee in panneaux:
        try:
            annee = int(annee_texte)
        except ValueError:
            continue

        mois_bruts = re.findall(
            r'fomc-meeting__month[^>]*>(.*?)</div>', bloc_annee, re.S
        )
        jours_bruts = re.findall(
            r'fomc-meeting__date[^>]*>(.*?)</div>', bloc_annee, re.S
        )
        if len(mois_bruts) != len(jours_bruts):
            _LOG.warning(
                "Page FOMC %d : %d mois pour %d dates, panneau ignoré.",
                annee, len(mois_bruts), len(jours_bruts),
            )
            continue

        for mois_brut, jour_brut in zip(mois_bruts, jours_bruts):
            reunion = _reunion_depuis_cellules(annee, mois_brut, jour_brut)
            if reunion is not None:
                reunions.append(reunion)

    uniques = {r.date_decision: r for r in reunions}
    resultat = sorted(uniques.values(), key=lambda r: r.date_decision)
    _LOG.info("Page FOMC : %d réunion(s) extraite(s).", len(resultat))
    return resultat


def _texte(html: str) -> str:
    """Retire le balisage d'une cellule et normalise les espaces.

    Args:
        html: fragment HTML.

    Returns:
        Texte brut.
    """
    sans_balise = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"[\s\u00a0]+", " ", sans_balise).strip()


def _reunion_depuis_cellules(annee: int, mois_brut: str, jour_brut: str) -> ReunionFOMC | None:
    """Construit une réunion à partir des cellules mois et jours.

    Args:
        annee: année du panneau.
        mois_brut: contenu de la cellule mois (``April``, ``Apr/May``...).
        jour_brut: contenu de la cellule jours (``28-29``, ``17-18*``...).

    Returns:
        La réunion, ou ``None`` si l'entrée n'est pas une décision de taux.
    """
    mois_texte = _texte(mois_brut)
    jour_texte = _texte(jour_brut)
    if not mois_texte or not jour_texte:
        return None

    avec_projections = "*" in jour_texte
    # Seuls les chiffres et le tiret décrivent la date ; l'astérisque est un
    # marqueur, et tout autre texte signale un événement d'une autre nature.
    noyau = jour_texte.replace("*", "").strip()
    if not re.fullmatch(r"\d{1,2}(?:\s*-\s*\d{1,2})?", noyau):
        _LOG.debug(
            "Entrée FOMC ignorée (pas une décision de taux) : %s %s", mois_texte, jour_texte
        )
        return None

    jours = [int(j) for j in re.findall(r"\d{1,2}", noyau)]
    if not jours:
        return None
    # La décision tombe le dernier jour de la réunion.
    jour_decision = jours[-1]

    mois_noms = [m.strip().lower() for m in mois_texte.split("/") if m.strip()]
    mois_numeros = [_MOIS[m] for m in mois_noms if m in _MOIS]
    if not mois_numeros:
        _LOG.warning("Mois FOMC non reconnu : %r", mois_texte)
        return None

    # Réunion à cheval : le dernier jour appartient au second mois cité.
    mois_decision = mois_numeros[-1]
    annee_decision = annee
    # « Dec/Jan » franchit l'année : le second mois est celui de l'an suivant.
    if len(mois_numeros) > 1 and mois_numeros[-1] < mois_numeros[0]:
        annee_decision = annee + 1

    try:
        return ReunionFOMC(
            date_decision=date(annee_decision, mois_decision, jour_decision),
            avec_projections=avec_projections,
            libelle=f"{mois_texte} {jour_texte}",
        )
    except ValueError as exc:
        _LOG.warning("Date FOMC invalide (%s %s) : %s", mois_texte, jour_texte, exc)
        return None


def fetch_fomc_calendar(url: str = URL_FOMC) -> tuple[list[ReunionFOMC], str]:
    """Collecte les réunions du FOMC sur le site de la Réserve fédérale.

    Args:
        url: page à lire.

    Returns:
        Couple ``(reunions, motif)``. La liste est vide en cas d'échec, et le
        motif dit ce qui s'est passé — page injoignable ou structure non
        reconnue. Les deux se traitent pareil en aval, mais pas au diagnostic.
    """
    try:
        reponse = requests.get(url, timeout=TIMEOUT, headers=_ENTETES_FED)
        reponse.raise_for_status()
    except requests.RequestException as exc:
        motif = f"page FOMC injoignable ({type(exc).__name__})"
        _LOG.warning("Calendrier FOMC : %s", motif)
        return [], motif

    reunions = parser_calendrier_fomc(reponse.text)
    if not reunions:
        motif = "page FOMC lue mais structure non reconnue (refonte du site ?)"
        _LOG.warning("Calendrier FOMC : %s", motif)
        return [], motif
    return reunions, ""


def ecrire_cache_fomc(reunions: list[ReunionFOMC], chemin: Path | None = None) -> bool:
    """Enregistre les réunions collectées dans le cache versionné.

    Args:
        reunions: réunions à conserver.
        chemin: fichier de cache. ``None`` retient :data:`CACHE_FOMC`, résolu
            à l'appel et non à l'import, de sorte que le chemin reste
            substituable.

    Returns:
        ``True`` si l'écriture a réussi.
    """
    chemin = chemin or CACHE_FOMC
    contenu = {
        "_commentaire": (
            "Cache du calendrier FOMC, régénéré automatiquement par "
            "dataio/calendar.py à chaque collecte réussie. Ne pas éditer à la "
            "main : toute modification sera écrasée à la prochaine exécution."
        ),
        "source": URL_FOMC,
        "derniere_collecte_reussie": date.today().strftime("%Y-%m-%d"),
        "n_reunions": len(reunions),
        "reunions": [r.to_dict() for r in reunions],
    }
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(
            json.dumps(contenu, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        _LOG.warning("Cache FOMC non écrit (%s) : %s", chemin, exc)
        return False
    _LOG.info("Cache FOMC mis à jour : %d réunion(s) dans %s.", len(reunions), chemin)
    return True


def lire_cache_fomc(chemin: Path | None = None) -> tuple[list[ReunionFOMC], date | None, str]:
    """Relit le cache des réunions.

    Args:
        chemin: fichier de cache. ``None`` retient :data:`CACHE_FOMC`.

    Returns:
        Triplet ``(reunions, date_de_collecte, motif)``.
    """
    chemin = chemin or CACHE_FOMC
    try:
        contenu = json.loads(chemin.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], None, "aucun cache FOMC dans le dépôt"
    except (OSError, json.JSONDecodeError) as exc:
        return [], None, f"cache FOMC illisible ({type(exc).__name__})"

    reunions = [
        r for r in (ReunionFOMC.from_dict(e) for e in contenu.get("reunions", []))
        if r is not None
    ]
    collecte: date | None = None
    brut = contenu.get("derniere_collecte_reussie")
    if brut:
        try:
            collecte = datetime.strptime(str(brut), "%Y-%m-%d").date()
        except ValueError:
            _LOG.warning("Date de collecte du cache FOMC illisible : %r", brut)

    if not reunions:
        return [], collecte, "cache FOMC présent mais vide"
    return sorted(reunions, key=lambda r: r.date_decision), collecte, ""


def charger_reunions_fomc(
    chemin_cache: Path | None = None,
    autoriser_reseau: bool = True,
    url: str = URL_FOMC,
) -> tuple[list[ReunionFOMC], dict[str, Any]]:
    """Obtient les réunions du FOMC : réseau d'abord, cache en repli.

    Une collecte réussie rafraîchit le cache ; un échec s'appuie dessus. Le
    diagnostic renvoyé dit toujours laquelle des deux voies a servi et depuis
    combien de temps la source n'a pas été jointe, de sorte qu'un calendrier
    figé ne puisse jamais passer pour un calendrier à jour.

    Args:
        chemin_cache: fichier de cache. ``None`` retient :data:`CACHE_FOMC`.
        autoriser_reseau: mettre à ``False`` pour n'utiliser que le cache.
        url: page à lire.

    Returns:
        Couple ``(reunions, diagnostic)``.
    """
    chemin_cache = chemin_cache or CACHE_FOMC
    reunions_cache, collecte_cache, motif_cache = lire_cache_fomc(chemin_cache)

    if autoriser_reseau:
        reunions, motif_reseau = fetch_fomc_calendar(url)
        if reunions:
            ecrire_cache_fomc(reunions, chemin_cache)
            return reunions, {
                "source": "federalreserve.gov",
                "url": url,
                "collecte_reussie": True,
                "derniere_collecte_reussie": date.today().strftime("%Y-%m-%d"),
                "jours_depuis_collecte": 0,
                "motif": "",
            }
    else:
        motif_reseau = "collecte réseau désactivée par l'appelant"

    # Repli sur le cache.
    anciennete = (
        (date.today() - collecte_cache).days if collecte_cache is not None else None
    )
    diagnostic = {
        "source": "cache local" if reunions_cache else "aucune",
        "url": url,
        "collecte_reussie": False,
        "derniere_collecte_reussie": (
            None if collecte_cache is None else collecte_cache.strftime("%Y-%m-%d")
        ),
        "jours_depuis_collecte": anciennete,
        "motif": motif_reseau if reunions_cache else f"{motif_reseau} ; {motif_cache}",
    }
    if reunions_cache:
        _LOG.warning(
            "Calendrier FOMC : repli sur le cache (%s), collecte du %s.",
            motif_reseau,
            diagnostic["derniere_collecte_reussie"] or "date inconnue",
        )
    else:
        _LOG.error(
            "Calendrier FOMC indisponible : %s. Aucune réunion connue.",
            diagnostic["motif"],
        )
    return reunions_cache, diagnostic


def _echeances_fomc(
    reunions: list[ReunionFOMC],
    bloc_fomc: dict[str, Any],
    fuseau: str,
    maintenant: datetime,
) -> list[Echeance]:
    """Transforme les réunions collectées en échéances datées.

    Args:
        reunions: réunions issues de :func:`charger_reunions_fomc`.
        bloc_fomc: bloc ``calendrier.fomc`` de la configuration, pour l'heure.
        fuseau: fuseau de publication.
        maintenant: instant de référence, en UTC.

    Returns:
        Liste d'échéances futures.
    """
    heure = str(bloc_fomc.get("heure_locale", "14:00"))
    echeances: list[Echeance] = []

    for reunion in reunions:
        horodatage = _horodater(reunion.date_decision, heure, fuseau)
        if horodatage <= maintenant:
            continue
        precision = " avec projections économiques" if reunion.avec_projections else ""
        echeances.append(
            Echeance(
                nom=f"Décision de politique monétaire (FOMC){precision}",
                horodatage=horodatage,
                minutes_restantes=int((horodatage - maintenant).total_seconds() // 60),
                heure_conventionnelle=True,
                impact_or="fort",
                source="calendrier officiel de la Réserve fédérale",
            )
        )
    return echeances


def _diagnostic_fomc(
    reunions: list[ReunionFOMC],
    diagnostic_collecte: dict[str, Any],
    maintenant: datetime,
    seuil: int = SEUIL_ALERTE_REUNIONS,
) -> dict[str, Any]:
    """Évalue si le calendrier FOMC est encore digne de confiance.

    C'est le filet de sécurité du module. Un calendrier de réunions se périme
    en silence : les dates connues restent parfaitement valides jusqu'au jour
    où il n'en reste plus, et le compte à rebours cesse alors d'exister sans
    qu'aucune erreur ne soit levée. L'alerte transforme cette panne muette en
    panne visible.

    Args:
        reunions: réunions connues, toutes dates confondues.
        diagnostic_collecte: diagnostic renvoyé par
            :func:`charger_reunions_fomc`.
        maintenant: instant de référence, en UTC.
        seuil: nombre de réunions futures sous lequel l'alerte est levée.

    Returns:
        Bloc de diagnostic, prêt à être joint au JSON.
    """
    aujourd_hui = maintenant.date()
    futures = [r for r in reunions if r.date_decision >= aujourd_hui]
    derniere = max((r.date_decision for r in reunions), default=None)
    anciennete = diagnostic_collecte.get("jours_depuis_collecte")

    collecte_reussie = bool(diagnostic_collecte.get("collecte_reussie", False))
    cache_perime = (
        not collecte_reussie
        and anciennete is not None
        and anciennete >= CACHE_PERIME_JOURS
    )

    # Deux conditions lèvent l'alerte, et une seule ne suffirait pas :
    # l'épuisement des réunions futures, et un cache si ancien que le
    # calendrier a pu être révisé entre-temps sans qu'on le sache — auquel cas
    # un nombre confortable de réunions restantes inspire une fausse confiance.
    alerte = len(futures) < seuil or cache_perime

    motifs: list[str] = []
    if alerte:
        if not reunions:
            motifs.append(
                "aucune réunion connue : ni la page de la Réserve fédérale ni le "
                "cache local n'ont pu être lus"
            )
        elif len(futures) < seuil:
            motifs.append(
                f"moins de {seuil} réunions futures connues ({len(futures)}) : le "
                "calendrier FOMC est à renouveler"
            )
        if cache_perime:
            motifs.append(
                f"calendrier FOMC à vérifier, source indisponible depuis "
                f"{anciennete} jours"
            )
        elif not collecte_reussie:
            motifs.append(
                "source indisponible depuis "
                + (f"{anciennete} jour(s)" if anciennete is not None else "une date inconnue")
            )
        if diagnostic_collecte.get("motif"):
            motifs.append(str(diagnostic_collecte["motif"]))

    # Hors alerte, « motif » reste vide : un texte affiché à côté d'un drapeau
    # à false se lirait comme un avertissement alors que tout va bien. Le
    # recours au cache reste lisible dans « source » et « collecte_reussie ».

    return {
        "source": diagnostic_collecte.get("source", "inconnue"),
        "url": diagnostic_collecte.get("url", URL_FOMC),
        "collecte_reussie": collecte_reussie,
        "derniere_collecte_reussie": diagnostic_collecte.get("derniere_collecte_reussie"),
        "jours_depuis_collecte": anciennete,
        "n_reunions_connues": len(reunions),
        "n_reunions_a_venir_connues": len(futures),
        "horizon_couvert_jusquau": None if derniere is None else derniere.strftime("%Y-%m-%d"),
        "seuil_alerte": seuil,
        "alerte_renouvellement": alerte,
        "motif": " ; ".join(motifs),
    }


def prochaines_echeances(
    configuration: dict[str, Any],
    reunions_fomc: list[ReunionFOMC],
    maintenant: datetime | None = None,
    limite: int = 6,
) -> list[Echeance]:
    """Assemble et trie les prochaines échéances macro.

    Args:
        configuration: bloc ``calendrier`` de ``config/gold.yaml``.
        reunions_fomc: réunions issues de :func:`charger_reunions_fomc`.
        maintenant: instant de référence, en UTC. Utile aux tests.
        limite: nombre maximal d'échéances renvoyées.

    Returns:
        Échéances triées de la plus proche à la plus lointaine.
    """
    reference = maintenant or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    fuseau = str(configuration.get("fuseau_publication", FUSEAU_DEFAUT))
    echeances = _echeances_fred(
        list(configuration.get("publications") or []), fuseau, reference
    )
    echeances += _echeances_fomc(
        reunions_fomc, dict(configuration.get("fomc") or {}), fuseau, reference
    )

    echeances.sort(key=lambda e: e.horodatage)
    return echeances[: max(int(limite), 1)]


def get_calendrier(
    configuration: dict[str, Any],
    maintenant: datetime | None = None,
    limite: int = 6,
    chemin_cache: Path | None = None,
    autoriser_reseau: bool = True,
) -> dict[str, Any]:
    """Produit le bloc calendrier du rapport JSON.

    Args:
        configuration: bloc ``calendrier`` de ``config/gold.yaml``.
        maintenant: instant de référence, en UTC.
        limite: nombre maximal d'échéances renvoyées.
        chemin_cache: cache des réunions du FOMC. ``None`` retient
            :data:`CACHE_FOMC`.
        autoriser_reseau: ``False`` pour n'utiliser que le cache.

    Returns:
        Dictionnaire prêt à être sérialisé. Il porte la fenêtre de silence, le
        drapeau ``en_fenetre_de_silence`` qui dit s'il faut s'abstenir, et le
        bloc ``fomc`` dont ``alerte_renouvellement`` signale un calendrier à
        renouveler. Cette alerte est remontée jusqu'à ``meta`` par
        :mod:`modules.gold.run` : enterrée ici, elle ne servirait à rien.
    """
    reference = maintenant or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    reunions, diagnostic_collecte = charger_reunions_fomc(
        chemin_cache=chemin_cache, autoriser_reseau=autoriser_reseau
    )
    diagnostic_fomc = _diagnostic_fomc(reunions, diagnostic_collecte, reference)

    echeances = prochaines_echeances(
        configuration, reunions, maintenant=reference, limite=limite
    )
    silence = int(configuration.get("fenetre_silence_minutes", 120))

    prochaine = echeances[0] if echeances else None
    en_silence = prochaine is not None and prochaine.minutes_restantes <= silence

    if en_silence and prochaine is not None:
        commentaire = (
            f"{prochaine.nom} dans {prochaine.minutes_restantes} minutes : "
            "fenêtre de silence, l'analyse fondamentale n'a aucune prise sur ces minutes."
        )
    elif prochaine is not None:
        commentaire = (
            f"Prochaine échéance : {prochaine.nom} dans "
            f"{prochaine.minutes_restantes // 60} heure(s)."
        )
    else:
        commentaire = "Aucune échéance connue : calendrier indisponible ou épuisé."

    if diagnostic_fomc["alerte_renouvellement"]:
        _LOG.error(
            "Calendrier FOMC à renouveler : %s", diagnostic_fomc["motif"] or "motif non précisé"
        )

    return {
        "disponible": bool(echeances),
        "horodatage_calcul_utc": reference.astimezone(timezone.utc).isoformat(),
        "fenetre_silence_minutes": silence,
        "en_fenetre_de_silence": en_silence,
        "commentaire": commentaire,
        "avertissement_heures": (
            "FRED ne publie que la date, jamais l'heure. Les heures affichées sont "
            "les heures officielles d'usage (fuseau de New York) : elles sont "
            "conventionnelles, pas garanties."
        ),
        # Champs repris à la racine du bloc pour que la page web n'ait pas à
        # descendre dans « fomc » pour savoir si le compte à rebours est fiable.
        "horizon_couvert_jusquau": diagnostic_fomc["horizon_couvert_jusquau"],
        "n_reunions_a_venir_connues": diagnostic_fomc["n_reunions_a_venir_connues"],
        "alerte_renouvellement": diagnostic_fomc["alerte_renouvellement"],
        "motif": diagnostic_fomc["motif"],
        "fomc": diagnostic_fomc,
        "echeances": [e.to_dict() for e in echeances],
    }
