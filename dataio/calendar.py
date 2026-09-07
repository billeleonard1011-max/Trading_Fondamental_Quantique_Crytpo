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

Le FOMC, lui, n'est pas une publication de données : FRED n'expose aucune
« release » correspondante. Ses dates viennent du calendrier officiel de la
Réserve fédérale, recopiées dans la configuration et à revérifier une fois
par an — la Fed précise que chaque date reste provisoire jusqu'à la réunion
qui la précède.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
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

__all__ = ["Echeance", "get_calendrier", "prochaines_echeances"]


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


def _echeances_fomc(
    bloc_fomc: dict[str, Any],
    fuseau: str,
    maintenant: datetime,
) -> list[Echeance]:
    """Construit les échéances du FOMC depuis la configuration.

    Args:
        bloc_fomc: bloc ``calendrier.fomc`` de la configuration.
        fuseau: fuseau de publication.
        maintenant: instant de référence, en UTC.

    Returns:
        Liste d'échéances futures.
    """
    heure = str(bloc_fomc.get("heure_locale", "14:00"))
    verifie_le = str(bloc_fomc.get("verifie_le", "date de vérification non renseignée"))
    echeances: list[Echeance] = []

    for brut in bloc_fomc.get("dates_decision", []):
        try:
            jour = datetime.strptime(str(brut), "%Y-%m-%d").date()
        except ValueError:
            _LOG.warning("Date FOMC « %s » illisible : ignorée.", brut)
            continue

        horodatage = _horodater(jour, heure, fuseau)
        if horodatage <= maintenant:
            continue
        echeances.append(
            Echeance(
                nom="Décision de politique monétaire (FOMC)",
                horodatage=horodatage,
                minutes_restantes=int((horodatage - maintenant).total_seconds() // 60),
                heure_conventionnelle=True,
                impact_or="fort",
                source=f"calendrier officiel de la Réserve fédérale, vérifié le {verifie_le}",
            )
        )

    if not echeances:
        _LOG.warning(
            "Aucune date FOMC future dans la configuration : la liste est épuisée, "
            "à recharger depuis federalreserve.gov/monetarypolicy/fomccalendars.htm."
        )
    return echeances


def prochaines_echeances(
    configuration: dict[str, Any],
    maintenant: datetime | None = None,
    limite: int = 6,
) -> list[Echeance]:
    """Assemble et trie les prochaines échéances macro.

    Args:
        configuration: bloc ``calendrier`` de ``config/gold.yaml``.
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
    echeances += _echeances_fomc(dict(configuration.get("fomc") or {}), fuseau, reference)

    echeances.sort(key=lambda e: e.horodatage)
    return echeances[: max(int(limite), 1)]


def get_calendrier(
    configuration: dict[str, Any],
    maintenant: datetime | None = None,
    limite: int = 6,
) -> dict[str, Any]:
    """Produit le bloc calendrier du rapport JSON.

    Args:
        configuration: bloc ``calendrier`` de ``config/gold.yaml``.
        maintenant: instant de référence, en UTC.
        limite: nombre maximal d'échéances renvoyées.

    Returns:
        Dictionnaire prêt à être sérialisé, avec la fenêtre de silence et un
        drapeau ``en_fenetre_de_silence`` qui dit s'il faut s'abstenir.
    """
    reference = maintenant or datetime.now(timezone.utc)
    echeances = prochaines_echeances(configuration, maintenant=reference, limite=limite)
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
        "echeances": [e.to_dict() for e in echeances],
    }
