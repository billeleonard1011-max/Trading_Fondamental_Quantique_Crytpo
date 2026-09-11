"""Plafond quotidien d'analyses par modèle de langage, partagé par les fils.

Pourquoi ce module existe
-------------------------
Chaque fil d'actualité analyse au plus ``max_analyses_par_execution``
nouveautés (huit par défaut). Tant que les fils tournaient une fois par
jour, ce plafond par exécution *était* le plafond quotidien : vingt-quatre
analyses, quelques centimes par mois. En passant à une exécution toutes les
trente minutes, le même réglage autorise quarante-huit fois plus d'appels
— la dépense est multipliée par la cadence sans que personne ne l'ait
décidé.

Le plafond par exécution reste utile : il empêche une seule exécution de
partir en boucle sur une rafale d'actualités. Mais il ne borne rien sur la
journée. Ce module ajoute la borne manquante, commune aux trois fils.

Comment le compte survit d'une exécution à l'autre
--------------------------------------------------
Le compteur vit dans ``reports/quota_llm.json``, versionné comme les
journaux en ajout seul : un exécuteur GitHub est éphémère, un fichier non
publié repartirait de zéro à chaque exécution et ne plafonnerait rien. Il
est remis à zéro au changement de jour (UTC), sans purge à écrire.

Limite connue, assumée : si deux workflows écrivent le compteur en même
temps, la fusion retient le plus grand des deux compteurs du jour (voir
``scripts/fusionner_sorties.py``), ce qui peut sous-compter de quelques
analyses. C'est un garde-fou de dépense, pas une comptabilité : sous-compter
de trois analyses coûte un millième de dollar, et l'alternative — sérialiser
les workflows — ferait attendre la voie rapide derrière la voie lente.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Final

_LOG: Final = logging.getLogger(__name__)

#: Compteur du jour, versionné pour survivre aux exécuteurs éphémères.
FICHIER_QUOTA: Final = Path(__file__).resolve().parents[1] / "reports" / "quota_llm.json"

#: Plafond retenu par défaut, en analyses par jour, tous fils confondus.
#:
#: Cinq fois le volume d'avant (vingt-quatre par jour), pour une cadence
#: quarante-huit fois plus rapide : la capacité d'explication augmente
#: nettement, la dépense reste de l'ordre de l'euro par mois. Réglable par
#: ``explication.max_analyses_par_jour`` dans ``config/gold.yaml``.
MAX_ANALYSES_PAR_JOUR: Final[int] = 120

__all__ = ["FICHIER_QUOTA", "MAX_ANALYSES_PAR_JOUR", "lire", "restant", "consommer"]


def _jour_utc(jour: date | None = None) -> str:
    """Jour courant en UTC, au format ``AAAA-MM-JJ``."""
    return str(jour or datetime.now(timezone.utc).date())


def lire(chemin: Path | None = None, jour: date | None = None) -> dict[str, Any]:
    """Relit le compteur du jour.

    Un fichier absent, illisible ou daté d'un autre jour vaut un compteur à
    zéro : le plafond est journalier, il n'a rien à conserver d'hier.

    Args:
        chemin: fichier du compteur. ``None`` retient :data:`FICHIER_QUOTA`.
        jour: jour de référence. ``None`` prend aujourd'hui en UTC.

    Returns:
        ``{"jour": "AAAA-MM-JJ", "analyses": int}``.
    """
    aujourd_hui = _jour_utc(jour)
    fichier = chemin or FICHIER_QUOTA
    try:
        contenu = json.loads(fichier.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"jour": aujourd_hui, "analyses": 0}
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("Compteur d'analyses illisible (%s) : remis à zéro pour la journée. %s", fichier, exc)
        return {"jour": aujourd_hui, "analyses": 0}
    if not isinstance(contenu, dict) or str(contenu.get("jour")) != aujourd_hui:
        return {"jour": aujourd_hui, "analyses": 0}
    try:
        deja = max(int(contenu.get("analyses") or 0), 0)
    except (TypeError, ValueError):
        deja = 0
    return {"jour": aujourd_hui, "analyses": deja}


def restant(
    plafond: int = MAX_ANALYSES_PAR_JOUR,
    chemin: Path | None = None,
    jour: date | None = None,
) -> int:
    """Dit combien d'analyses la journée autorise encore.

    Args:
        plafond: nombre maximal d'analyses par jour. Zéro ou négatif coupe
            complètement la couche pédagogique — c'est une façon assumée de
            la désactiver sans toucher au code.
        chemin: fichier du compteur.
        jour: jour de référence.

    Returns:
        Le solde, jamais négatif.
    """
    if plafond <= 0:
        return 0
    return max(plafond - lire(chemin, jour)["analyses"], 0)


def consommer(
    n: int,
    chemin: Path | None = None,
    jour: date | None = None,
) -> int:
    """Ajoute ``n`` analyses au compteur du jour et l'écrit.

    Args:
        n: nombre d'analyses réellement effectuées. Zéro ou négatif n'écrit
            rien — une exécution sans analyse ne doit pas réécrire le
            fichier pour rien, ni créer un diff vide à publier.
        chemin: fichier du compteur.
        jour: jour de référence.

    Returns:
        Le total du jour après ajout.
    """
    if n <= 0:
        return lire(chemin, jour)["analyses"]
    fichier = chemin or FICHIER_QUOTA
    etat = lire(fichier, jour)
    etat["analyses"] += int(n)
    try:
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_text(
            json.dumps(
                {
                    "_commentaire": (
                        "Analyses par modèle de langage effectuées aujourd'hui, tous fils "
                        "confondus. Remis à zéro au changement de jour (UTC). Voir "
                        "modules/quota_llm.py et explication.max_analyses_par_jour."
                    ),
                    **etat,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        # Le compteur non écrit ne doit pas faire échouer un fil : au pire la
        # journée suivante repart d'un compteur plus bas que la réalité.
        _LOG.warning("Compteur d'analyses non écrit (%s) : %s", fichier, exc)
    return etat["analyses"]
