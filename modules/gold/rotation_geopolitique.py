"""Rotation des dossiers géopolitiques, et reprise de la dernière mesure connue.

Pourquoi ce module existe
-------------------------
Mesurer les huit dossiers à chaque exécution demande seize requêtes GDELT, et
jusqu'à quarante-huit quand les remesures se déclenchent. GDELT limite
durement les exécuteurs GitHub, dont les adresses sont partagées : sur
l'exécution CI du 11 septembre 2026, soixante-quatre requêtes ont produit
cinquante-neuf refus et cinq réponses, pour vingt-deux minutes de phase
géopolitique. Trois exécutions de suite avaient auparavant été tuées à leur
délai de quarante-cinq minutes sans rien publier.

Deux mécanismes, qui se complètent
-----------------------------------
**Rotation** — chaque exécution ne mesure qu'un lot de dossiers, les moins
récemment mesurés d'abord. Moins de requêtes, donc moins de limitation, donc
plus de réponses par requête envoyée.

**Reprise** — un dossier hors du lot, ou dont la mesure du jour échoue, reprend
sa dernière série de volumes connue, et le rapport affiche l'âge de cette
mesure. Un chiffre d'hier clairement daté vaut mieux qu'un « indisponible » :
c'est le principe que le projet applique déjà au positionnement COT, publié
avec quatre jours de retard et son ``age_jours``.

L'ordre « le moins récemment mesuré d'abord » se répare tout seul : un dossier
dont la mesure échoue reste en tête de file et repasse à l'exécution suivante,
au lieu d'attendre un cycle entier.

Ce qui est stocké, et pourquoi c'est la série de volumes
--------------------------------------------------------
Pas le ratio d'intensité, mais la **série journalière** dont il est tiré.
Réinjectée dans :func:`modules.gold.geopolitics.mesurer_dossier`, elle permet
de recalculer la trajectoire, l'ancienneté et la pertinence marché exactement
comme le jour de la mesure, sans un seul appel réseau — le paramètre
``volumes`` existait déjà pour rendre le module testable hors ligne.

Pourquoi ce fichier n'est pas fusionné par ``scripts/fusionner_sorties``
------------------------------------------------------------------------
Contrairement aux journaux en ajout seul, il n'a qu'un seul écrivain : le
workflow ``daily.yml``, dont le groupe de concurrence ``rapport-quotidien``
interdit déjà deux exécutions simultanées. Il est simplement repris tel quel
par le ``git add reports/`` de la publication. Si un second workflow venait à
l'écrire, il faudrait une fusion retenant, par dossier, la date la plus
récente — et ce commentaire serait alors à corriger.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Final

_LOG: Final = logging.getLogger(__name__)

#: Racine du dépôt, déduite de l'emplacement de ce fichier.
RACINE: Final = Path(__file__).resolve().parents[2]

#: Dernière mesure connue de chaque dossier, versionnée pour survivre aux
#: exécuteurs éphémères — c'est tout l'intérêt d'une reprise.
FICHIER_MESURES: Final = RACINE / "reports" / "gold" / "geopolitique_dernieres_mesures.json"

#: Nombre de dossiers mesurés par exécution.
#:
#: Quatre sur huit, soit un cycle de deux exécutions. Le calcul, à 21,0 s par
#: requête mesurées sur l'exécution CI du 11 septembre 2026 :
#:
#:   * pire cas, toutes les mesures échouent et déclenchent leur remesure :
#:     4 dossiers × 2 requêtes × 2 tentatives + 16 requêtes hors dossiers
#:     = 32 requêtes, soit 11,2 min de phase et 20,2 min de job ;
#:   * cas normal, les mesures aboutissent du premier coup :
#:     4 × 2 + 16 = 24 requêtes, soit 8,4 min de phase et 17,4 min de job.
#:
#: Contre 31,4 min observées en mesurant les huit. Un lot de trois ferait
#: gagner 1,4 min de plus mais porterait le cycle à trois exécutions, donc le
#: délai maximum à cinq jours calendaires au lieu de quatre : le rapport ne
#: tourne que du lundi au vendredi, et un cycle qui traverse un week-end coûte
#: deux jours de plus.
TAILLE_LOT: Final[int] = 4

#: Âge au-delà duquel une mesure reprise cesse d'être publiée.
#:
#: L'intensité rapporte le volume des dernières 24 h à la moyenne des trente
#: jours précédents. Passé une semaine, elle ne dit plus rien de la situation
#: du jour, et l'afficher datée ne suffirait plus à la rendre honnête : le
#: dossier est alors publié comme indisponible, avec son motif.
PEREMPTION_JOURS: Final[int] = 7

__all__ = [
    "FICHIER_MESURES",
    "TAILLE_LOT",
    "PEREMPTION_JOURS",
    "Mesure",
    "lire",
    "choisir_lot",
    "age_jours",
    "est_perimee",
    "enregistrer",
]


@dataclass(frozen=True)
class Mesure:
    """Dernière mesure GDELT connue d'un dossier.

    Attributes:
        dossier: identifiant du dossier.
        mesure_du: jour où la série a été réellement obtenue de GDELT. C'est
            cette date qui est propagée, jamais celle de la reprise — sans
            quoi une reprise de reprise paraîtrait fraîche.
        volumes: série journalière ``{"AAAA-MM-JJ": volume}``.
    """

    dossier: str
    mesure_du: date
    volumes: dict[str, float]


def _aujourd_hui(jour: date | None = None) -> date:
    """Jour de référence, en UTC comme le reste des horodatages du projet."""
    return jour or datetime.now(timezone.utc).date()


def lire(chemin: Path | None = None) -> dict[str, Mesure]:
    """Relit la dernière mesure connue de chaque dossier.

    Un fichier absent ou illisible rend un dictionnaire vide : le premier
    cycle mesurera alors les dossiers dans l'ordre de la configuration, ce qui
    est le bon comportement au démarrage à froid.

    Args:
        chemin: fichier des mesures. ``None`` retient :data:`FICHIER_MESURES`.

    Returns:
        Mesures par identifiant de dossier.
    """
    fichier = chemin or FICHIER_MESURES
    try:
        contenu = json.loads(fichier.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _LOG.info("Aucune mesure géopolitique antérieure : démarrage à froid de la rotation.")
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("Mesures géopolitiques illisibles (%s) : rotation repartie de zéro. %s", fichier, exc)
        return {}

    mesures: dict[str, Mesure] = {}
    for identifiant, entree in (contenu.get("dossiers") or {}).items():
        if not isinstance(entree, dict):
            continue
        try:
            jour = date.fromisoformat(str(entree.get("mesure_du")))
        except (TypeError, ValueError):
            continue
        volumes = {
            str(j): float(v)
            for j, v in (entree.get("volumes") or {}).items()
            if isinstance(v, (int, float))
        }
        if not volumes:
            continue
        mesures[str(identifiant)] = Mesure(str(identifiant), jour, volumes)
    return mesures


def age_jours(mesure: Mesure | None, jour: date | None = None) -> int | None:
    """Donne l'âge d'une mesure, en jours.

    Args:
        mesure: mesure reprise, ou ``None``.
        jour: jour de référence. ``None`` prend aujourd'hui en UTC.

    Returns:
        L'âge en jours, ``None`` si aucune mesure n'est connue.
    """
    if mesure is None:
        return None
    return max((_aujourd_hui(jour) - mesure.mesure_du).days, 0)


def est_perimee(mesure: Mesure | None, jour: date | None = None) -> bool:
    """Dit si une mesure est trop vieille pour être encore publiée.

    Args:
        mesure: mesure reprise, ou ``None``.
        jour: jour de référence.

    Returns:
        ``True`` si la mesure manque ou dépasse :data:`PEREMPTION_JOURS`.
    """
    age = age_jours(mesure, jour)
    return age is None or age > PEREMPTION_JOURS


def choisir_lot(
    identifiants: list[str],
    mesures: dict[str, Mesure],
    taille: int = TAILLE_LOT,
    jour: date | None = None,
) -> list[str]:
    """Choisit les dossiers à mesurer cette fois-ci : les plus anciens d'abord.

    Un dossier jamais mesuré passe avant tous les autres. À ancienneté égale,
    l'ordre de la configuration tranche, ce qui rend la rotation reproductible
    plutôt que dépendante de l'ordre d'un dictionnaire.

    Cette règle se répare d'elle-même : un dossier dont la mesure échoue n'est
    pas enregistré, reste donc le plus ancien, et repasse à l'exécution
    suivante au lieu d'attendre un cycle entier.

    Args:
        identifiants: dossiers configurés, dans l'ordre du fichier.
        mesures: dernières mesures connues.
        taille: nombre de dossiers mesurés par exécution. Zéro ou négatif ne
            mesure rien — façon assumée de couper GDELT sans toucher au code.
        jour: jour de référence.

    Returns:
        Les identifiants à mesurer, dans l'ordre de la configuration.
    """
    if taille <= 0:
        return []
    reference = _aujourd_hui(jour)
    rangs = {identifiant: rang for rang, identifiant in enumerate(identifiants)}

    def _cle(identifiant: str) -> tuple[int, int]:
        """Trie par ancienneté décroissante, puis par ordre de configuration."""
        mesure = mesures.get(identifiant)
        # Jamais mesuré : rang d'ancienneté maximal, donc prioritaire.
        anciennete = (reference - mesure.mesure_du).days if mesure else 10**6
        return (-anciennete, rangs[identifiant])

    retenus = set(sorted(identifiants, key=_cle)[:taille])
    return [i for i in identifiants if i in retenus]


def enregistrer(
    mesures: dict[str, Mesure],
    chemin: Path | None = None,
    jour: date | None = None,
) -> bool:
    """Écrit les mesures connues, pour que la prochaine exécution les reprenne.

    Args:
        mesures: mesures à conserver, par identifiant.
        chemin: fichier cible.
        jour: jour de référence, écrit à titre documentaire.

    Returns:
        ``True`` si le fichier a été écrit.
    """
    fichier = chemin or FICHIER_MESURES
    contenu = {
        "_commentaire": (
            "Dernière série de volumes GDELT connue par dossier géopolitique. "
            "Sert à la rotation (quels dossiers mesurer) et à la reprise (quel "
            "chiffre publier quand la mesure du jour manque). Voir "
            "modules/gold/rotation_geopolitique.py. `mesure_du` est le jour où "
            "GDELT a réellement répondu, jamais celui d'une reprise."
        ),
        "ecrit_le": str(_aujourd_hui(jour)),
        "dossiers": {
            identifiant: {
                "mesure_du": str(mesure.mesure_du),
                "volumes": mesure.volumes,
            }
            for identifiant, mesure in sorted(mesures.items())
        },
    }
    try:
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_text(
            json.dumps(contenu, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError) as exc:
        # Ne pas faire échouer le rapport : au pire la prochaine exécution
        # remesurera au lieu de reprendre.
        _LOG.warning("Mesures géopolitiques non écrites (%s) : %s", fichier, exc)
        return False
    return True


def depuis_dossiers(dossiers: list[Any], jour: date | None = None) -> dict[str, Mesure]:
    """Extrait les mesures réellement obtenues d'une liste de dossiers mesurés.

    Seuls les dossiers dont GDELT a servi la série de volumes sont retenus :
    un dossier repris n'a rien de neuf à enregistrer, et l'enregistrer
    rajeunirait sa date au point de le sortir indéfiniment de la rotation.

    Args:
        dossiers: objets ``Dossier`` de :mod:`modules.gold.geopolitics`.
        jour: jour de la mesure.

    Returns:
        Mesures par identifiant, pour les seuls dossiers fraîchement mesurés.
    """
    reference = _aujourd_hui(jour)
    obtenues: dict[str, Mesure] = {}
    for dossier in dossiers:
        if getattr(dossier, "reprise", False):
            continue
        volumes = dict(getattr(getattr(dossier, "theme", None), "volumes", {}) or {})
        if not volumes:
            continue
        obtenues[str(dossier.id)] = Mesure(str(dossier.id), reference, volumes)
    return obtenues
