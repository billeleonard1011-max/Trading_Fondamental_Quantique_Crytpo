"""Client pour la base GDELT Events (activité par acteur).

Complète ``dataio.news`` (API DOC, requêtes par mots-clés) : DOC 2.0 n'a
aucun opérateur de filtrage par acteur — vérifié sur sa documentation
officielle, ses opérateurs sont ``domain:``, ``domainis:``, ``sourcecountry:``,
``sourcelang:``, ``theme:``, ``tone:``, ``toneabs:``, ``near:``, ``repeat:``
et les filtres d'image, rien sur les codes CAMEO d'acteurs (ISR, PSE, IRN...).

Ces codes ne sont disponibles que dans les exports bruts de la base Events,
en CSV zippé, publiés toutes les quinze minutes, sans clé ni compte — vérifié
par téléchargement réel d'un export (voir la vérification B1 du prompt
« sorties par paliers + géopolitique »).

:func:`recuperer_dernier_export` ne télécharge que le **dernier** export
publié : un instantané. Pour découvrir des sujets, c'est trop mince — un
export de quinze minutes contient une vingtaine d'événements de conflit
entre pays distincts, et le « sujet le plus actif » y est une paire à deux
événements. :func:`recuperer_exports_recents` agrège donc les exports des
dernières heures (URLs déterministes, une toutes les quinze minutes, sans
limite de débit : c'est un hébergement statique). La tendance de couverture
sur trente jours reste mesurée par ``dataio.news.gdelt_intensity()`` ; les
deux se complètent, ils ne se recouvrent pas.

Colonnes lues, sur les 61 du format GDELT 2.0 Events (indices 0-based,
vérifiés sur un export réel — voir le module d'appelant pour la source) :
Actor1CountryCode (7), Actor2CountryCode (17), EventCode (26),
GoldsteinScale (30), AvgTone (34), SOURCEURL (60).
"""

from __future__ import annotations

import csv
import io
import logging
import os
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Final

import requests

_LOG: Final = logging.getLogger(__name__)

#: Index des exports publiés, mis à jour toutes les quinze minutes.
URL_DERNIERE_MISE_A_JOUR: Final = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"

#: Gabarit d'un export daté : horodatage UTC arrondi au quart d'heure.
URL_EXPORT: Final = "http://data.gdeltproject.org/gdeltv2/{horodatage}.export.CSV.zip"

#: Profondeur par défaut de l'agrégation, en heures, et pas entre exports.
HEURES_EXPORTS_DEFAUT: Final[int] = 24
PAS_EXPORT_MINUTES: Final[int] = 15

#: Délai maximal, en secondes, accordé à un appel réseau.
TIMEOUT: Final[float] = float(os.environ.get("HTTP_TIMEOUT", "20"))

_ENTETES: Final[dict[str, str]] = {
    "User-Agent": "Mozilla/5.0 (compatible; veille-marches/1.0)"
}

_COL_ACTOR1_PAYS: Final[int] = 7
_COL_ACTOR2_PAYS: Final[int] = 17
_COL_CODE_EVENEMENT: Final[int] = 26
_COL_GOLDSTEIN: Final[int] = 30
_COL_TONALITE: Final[int] = 34
_COL_SOURCE_URL: Final[int] = 60
_NB_COLONNES_MIN: Final[int] = 61

__all__ = ["recuperer_dernier_export", "recuperer_exports_recents", "compter_evenements_par_acteurs", "compter_evenements_region"]


def recuperer_dernier_export(
    recuperer: Any = None,
) -> tuple[list[list[str]], str]:
    """Télécharge et décompresse le dernier export Events publié.

    Args:
        recuperer: implémentation de ``requests.get``, injectable pour les
            tests hors ligne. ``None`` utilise ``requests.get``.

    Returns:
        Couple ``(lignes, motif)``. En cas d'échec, à n'importe quelle étape
        (index injoignable, export injoignable, archive corrompue), ``lignes``
        est vide et ``motif`` explique pourquoi — jamais d'exception remontée
        à l'appelant.
    """
    get = recuperer or requests.get

    try:
        reponse_index = get(URL_DERNIERE_MISE_A_JOUR, timeout=TIMEOUT, headers=_ENTETES)
        reponse_index.raise_for_status()
    except requests.RequestException as exc:
        return [], f"index GDELT Events injoignable : {exc}"

    url_zip = ""
    for ligne in reponse_index.text.splitlines():
        morceaux = ligne.split()
        if len(morceaux) >= 3 and morceaux[2].endswith("export.CSV.zip"):
            url_zip = morceaux[2]
            break
    if not url_zip:
        return [], "index GDELT Events sans fichier d'événements (format inattendu)"

    try:
        reponse_zip = get(url_zip, timeout=TIMEOUT, headers=_ENTETES)
        reponse_zip.raise_for_status()
    except requests.RequestException as exc:
        return [], f"export GDELT Events injoignable ({url_zip}) : {exc}"

    try:
        contenu = reponse_zip.content
        with zipfile.ZipFile(io.BytesIO(contenu)) as archive:
            noms = archive.namelist()
            if not noms:
                return [], "archive GDELT Events vide"
            texte = archive.read(noms[0]).decode("utf-8", errors="replace")
    except zipfile.BadZipFile as exc:
        return [], f"archive GDELT Events corrompue : {exc}"

    lignes = list(csv.reader(io.StringIO(texte), delimiter="\t"))
    return lignes, ""


def _lire_archive(contenu: bytes) -> list[list[str]] | None:
    """Décompresse un export et le lit en lignes ; ``None`` si l'archive est corrompue."""
    try:
        with zipfile.ZipFile(io.BytesIO(contenu)) as archive:
            noms = archive.namelist()
            if not noms:
                return None
            texte = archive.read(noms[0]).decode("utf-8", errors="replace")
    except zipfile.BadZipFile:
        return None
    return list(csv.reader(io.StringIO(texte), delimiter="\t"))


def recuperer_exports_recents(
    heures: int = HEURES_EXPORTS_DEFAUT,
    recuperer: Any = None,
    maintenant: datetime | None = None,
) -> tuple[list[list[str]], dict[str, Any]]:
    """Agrège les exports Events des dernières heures.

    Les exports sont publiés toutes les quinze minutes à une URL déduite de
    l'horodatage : aucun index à télécharger. Un export manquant (retard de
    publication, trou) est simplement ignoré et compté ; l'agrégation n'a
    pas besoin d'être complète pour être utile, mais le rapport dit combien
    d'exports ont réellement été lus.

    Args:
        heures: profondeur de l'agrégation.
        recuperer: implémentation de ``requests.get``, injectable pour les
            tests hors ligne.
        maintenant: instant de référence UTC, pour les tests.

    Returns:
        ``(lignes, meta)`` — toutes les lignes des exports lus, et
        ``meta = {"n_exports_lus", "n_exports_attendus", "heures", "motif"}``.
        ``lignes`` est vide et ``motif`` explique pourquoi si rien n'a pu
        être lu — jamais d'exception.
    """
    get = recuperer or requests.get
    ref = (maintenant or datetime.now(timezone.utc)).astimezone(timezone.utc)
    # Le dernier export disponible date d'un quart d'heure révolu, et sa
    # publication prend quelques minutes : on part du quart d'heure précédent.
    arrondi = ref.replace(second=0, microsecond=0, minute=(ref.minute // PAS_EXPORT_MINUTES) * PAS_EXPORT_MINUTES)
    arrondi -= timedelta(minutes=PAS_EXPORT_MINUTES)
    n_attendus = max(int(heures * 60 / PAS_EXPORT_MINUTES), 1)

    lignes: list[list[str]] = []
    lus = 0
    derniere_erreur = ""
    for k in range(n_attendus):
        instant = arrondi - timedelta(minutes=PAS_EXPORT_MINUTES * k)
        url = URL_EXPORT.format(horodatage=instant.strftime("%Y%m%d%H%M%S"))
        try:
            reponse = get(url, timeout=TIMEOUT, headers=_ENTETES)
            reponse.raise_for_status()
        except requests.RequestException as exc:
            derniere_erreur = f"{url} : {exc}"
            continue
        contenu = _lire_archive(reponse.content)
        if contenu is None:
            derniere_erreur = f"{url} : archive corrompue"
            continue
        lignes.extend(contenu)
        lus += 1

    meta = {"n_exports_lus": lus, "n_exports_attendus": n_attendus, "heures": heures, "motif": ""}
    if not lus:
        meta["motif"] = f"aucun export GDELT Events lisible sur {heures} h ({derniere_erreur or 'aucune réponse'})"
    elif lus < n_attendus:
        _LOG.info("GDELT Events : %d export(s) lu(s) sur %d attendus (%s).", lus, n_attendus, derniere_erreur)
    return lignes, meta


def compter_evenements_par_acteurs(
    lignes: list[list[str]], acteurs: list[str]
) -> dict[str, Any]:
    """Compte les événements **bilatéraux** entre les acteurs suivis.

    Exige les deux acteurs à la fois (dans un ordre ou l'autre), pas l'un ou
    l'autre : ne demander qu'un seul acteur présent ferait remonter, pour
    « Russie-Ukraine », un événement entre la Russie et le Viêt Nam sans
    aucun rapport avec ce conflit — repéré sur un export réel pendant la
    vérification. La contrepartie est un compte plus petit, mais qui mesure
    vraiment l'activité entre les deux parties nommées du dossier.

    Args:
        lignes: lignes brutes d'un export Events (voir
            :func:`recuperer_dernier_export`).
        acteurs: exactement deux codes CAMEO, les deux parties du dossier
            (ex. ``["ISR", "PSE"]``).

    Returns:
        ``{"n_evenements": int, "exemple": dict | None}``. ``exemple`` donne
        le code d'événement CAMEO, l'échelle de Goldstein (impact théorique
        sur la stabilité, de -10 à +10), la tonalité moyenne et la source du
        premier événement trouvé — jamais inventé, ``None`` si aucun.
    """
    acteurs_voulus = set(acteurs)
    correspondants = [
        ligne for ligne in lignes
        if len(ligne) >= _NB_COLONNES_MIN
        and {ligne[_COL_ACTOR1_PAYS], ligne[_COL_ACTOR2_PAYS]} >= acteurs_voulus
    ]

    if not correspondants:
        return {"n_evenements": 0, "exemple": None}

    premiere = correspondants[0]

    def _flottant(valeur: str) -> float | None:
        try:
            return float(valeur)
        except (TypeError, ValueError):
            return None

    return {
        "n_evenements": len(correspondants),
        "exemple": {
            "code_evenement": premiere[_COL_CODE_EVENEMENT],
            "goldstein": _flottant(premiere[_COL_GOLDSTEIN]),
            "tonalite": _flottant(premiere[_COL_TONALITE]),
            "source_url": premiere[_COL_SOURCE_URL],
        },
    }


def compter_evenements_region(lignes: list[list[str]], pays: list[str]) -> dict[str, Any]:
    """Compte les événements dont les **deux** acteurs sont dans une région.

    Sert aux dossiers régionaux (« Moyen-Orient ») : un événement entre le
    Yémen et l'Arabie saoudite n'appartient à aucun dossier bilatéral, mais
    il appartient à la région — et c'est là qu'une attaque qui fait monter
    le pétrole doit se rattacher, pas dans « Autres ».

    Args:
        lignes: lignes brutes d'un export Events.
        pays: codes CAMEO des pays de la région.

    Returns:
        Même forme que :func:`compter_evenements_par_acteurs`.
    """
    voulus = set(pays)
    correspondants = [
        ligne for ligne in lignes
        if len(ligne) >= _NB_COLONNES_MIN
        and ligne[_COL_ACTOR1_PAYS] in voulus and ligne[_COL_ACTOR2_PAYS] in voulus
        and ligne[_COL_ACTOR1_PAYS] != ligne[_COL_ACTOR2_PAYS]
    ]
    if not correspondants:
        return {"n_evenements": 0, "exemple": None}

    premiere = correspondants[0]

    def _flottant(valeur: str) -> float | None:
        try:
            return float(valeur)
        except (TypeError, ValueError):
            return None

    return {
        "n_evenements": len(correspondants),
        "exemple": {
            "code_evenement": premiere[_COL_CODE_EVENEMENT],
            "goldstein": _flottant(premiere[_COL_GOLDSTEIN]),
            "tonalite": _flottant(premiere[_COL_TONALITE]),
            "source_url": premiere[_COL_SOURCE_URL],
        },
    }
