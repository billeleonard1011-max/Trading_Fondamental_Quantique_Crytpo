"""Fusionne les sorties générées avec celles déjà publiées sur la branche.

Pourquoi ce script existe
-------------------------
Deux workflows publient dans le même dépôt : le rapport or quotidien et les
fils d'actualité, ces derniers toutes les quinze à trente minutes. Quand ils
se chevauchent — et GDELT, qui peut prendre dix minutes à lui seul, rend le
chevauchement banal —, le second à pousser doit repartir de l'état publié
par le premier.

Deux natures de fichiers, deux résolutions
------------------------------------------
Elles ne se traitent pas de la même façon, et les confondre perd des données :

* **Les instantanés recalculés** (``*_latest.json``, ``reports/gold/*.json``)
  sont refaits intégralement à chaque exécution. La version la plus récente
  est toujours la bonne : elle écrase, sans fusion. Ce script ne les touche
  donc pas.

* **Les journaux en ajout seul** (``*_historique.jsonl``, et les historiques
  d'observations datées ``rotation_historique.json`` et
  ``etf_aum_historique.json``) *accumulent*. Y écraser la version
  publiée ferait disparaître les lignes ajoutées par l'exécution concurrente
  — pour les fils, cela veut dire des articles qui perdent leur trace de
  « déjà vu » et resurgissent en nouveauté quelques heures plus tard. Ces
  fichiers-là se fusionnent par union.

Exécution (depuis le dépôt) :
    python -m scripts.fusionner_sorties origin/main
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

_LOG: Final = logging.getLogger("scripts.fusionner_sorties")

#: Racine du dépôt, déduite de l'emplacement de ce fichier.
RACINE: Final = Path(__file__).resolve().parents[1]

#: Journaux en ajout seul : une ligne JSON par entrée, jamais réécrite.
FICHIERS_JSONL: Final[tuple[str, ...]] = (
    "reports/gold/historique_biais.jsonl",
    "reports/gold/geopolitique_dossiers_historique.jsonl",
    "reports/gold/geopolitique_classement_historique.jsonl",
    "reports/quantum/feed_historique.jsonl",
    "reports/crypto/feed_historique.jsonl",
    "reports/geopolitique/feed_historique.jsonl",
)

#: Historiques d'observations datées, accumulés par les modules : chemin,
#: clé de la liste d'observations, clé du plafond de profondeur.
#: Chaque entrée porte une date unique par observation ; c'est ce qui
#: permet la fusion par union (voir modules/crypto/rotation.py et
#: dataio/etf_flows.py).
FICHIERS_OBSERVATIONS: Final[tuple[tuple[str, str, str], ...]] = (
    ("reports/crypto/rotation_historique.json", "observations", "max_observations"),
    ("reports/crypto/etf_aum_historique.json", "instantanes", "max_instantanes"),
)

#: Conservé pour les appels existants : le premier historique d'observations.
FICHIER_OBSERVATIONS: Final = FICHIERS_OBSERVATIONS[0][0]

__all__ = ["fusionner_lignes", "fusionner_observations", "fusionner_tout"]


def fusionner_lignes(publiees: str, locales: str) -> str:
    """Fusionne deux journaux en ajout seul, sans doublon ni perte.

    L'ordre est celui de première apparition : les lignes déjà publiées
    d'abord, puis celles que cette exécution ajoute. Un journal en ajout seul
    se lit chronologiquement, et réordonner créerait un diff illisible à
    chaque exécution.

    Args:
        publiees: contenu de la version déjà sur la branche.
        locales: contenu produit par cette exécution.

    Returns:
        Le contenu fusionné, une ligne par entrée.
    """
    vues: set[str] = set()
    retenues: list[str] = []
    for contenu in (publiees, locales):
        for ligne in contenu.splitlines():
            propre = ligne.strip()
            if not propre or propre in vues:
                continue
            vues.add(propre)
            retenues.append(propre)
    return "\n".join(retenues) + ("\n" if retenues else "")


def fusionner_observations(
    publiees: str, locales: str, cle_liste: str = "observations", cle_plafond: str = "max_observations",
) -> str:
    """Fusionne deux historiques d'observations quotidiennes, une par date.

    À date identique, l'observation locale gagne : elle vient d'être mesurée,
    l'autre est au mieux du même jour. Le plafond de profondeur du fichier
    local est respecté — c'est lui qui porte le réglage courant.

    Args:
        publiees: contenu JSON déjà sur la branche.
        locales: contenu JSON produit par cette exécution.
        cle_liste: clé de la liste d'observations (``observations``,
            ``instantanes``).
        cle_plafond: clé du plafond de profondeur.

    Returns:
        Le contenu fusionné, sérialisé en JSON.

    Raises:
        ValueError: si le contenu local n'est pas un cache exploitable — un
            cache illisible ne doit pas être publié en silence.
    """
    def _charger(brut: str) -> dict[str, Any]:
        try:
            contenu = json.loads(brut)
        except (json.JSONDecodeError, TypeError):
            return {}
        return contenu if isinstance(contenu, dict) else {}

    base = _charger(publiees)
    local = _charger(locales)
    if not local:
        raise ValueError("historique d'observations local illisible : fusion refusée")

    par_date: dict[str, dict[str, Any]] = {}
    for source in (base, local):  # le local écrase à date égale
        for observation in source.get(cle_liste) or []:
            if isinstance(observation, dict) and observation.get("date"):
                par_date[str(observation["date"])] = observation

    plafond = int(local.get(cle_plafond) or len(par_date))
    observations = [par_date[d] for d in sorted(par_date)][-plafond:]

    fusionne = dict(local)
    fusionne[cle_liste] = observations
    return json.dumps(fusionne, ensure_ascii=False, indent=2) + "\n"


def _version_publiee(reference: str, chemin: str) -> str | None:
    """Lit un fichier tel qu'il est sur la branche distante.

    Args:
        reference: référence git, par exemple ``origin/main``.
        chemin: chemin du fichier, relatif à la racine du dépôt.

    Returns:
        Le contenu, ou ``None`` si le fichier n'existe pas encore là-bas.
    """
    resultat = subprocess.run(
        ["git", "show", f"{reference}:{chemin}"],
        capture_output=True, text=True, cwd=RACINE, check=False,
    )
    return resultat.stdout if resultat.returncode == 0 else None


def fusionner_tout(reference: str, racine: Path = RACINE) -> list[str]:
    """Fusionne tous les fichiers en ajout seul avec la version publiée.

    Args:
        reference: référence git de la branche distante.
        racine: racine du dépôt.

    Returns:
        Les chemins effectivement fusionnés.
    """
    fusionnes: list[str] = []

    for chemin in FICHIERS_JSONL:
        fichier = racine / chemin
        if not fichier.exists():
            continue
        publiee = _version_publiee(reference, chemin)
        if publiee is None:
            continue
        locale = fichier.read_text(encoding="utf-8")
        contenu = fusionner_lignes(publiee, locale)
        if contenu != locale:
            fichier.write_text(contenu, encoding="utf-8")
            fusionnes.append(chemin)

    for chemin, cle_liste, cle_plafond in FICHIERS_OBSERVATIONS:
        fichier = racine / chemin
        if not fichier.exists():
            continue
        publiee = _version_publiee(reference, chemin)
        if publiee is None:
            continue
        locale = fichier.read_text(encoding="utf-8")
        try:
            contenu = fusionner_observations(publiee, locale, cle_liste, cle_plafond)
        except ValueError as exc:
            _LOG.error("%s non fusionné : %s", chemin, exc)
            continue
        if contenu != locale:
            fichier.write_text(contenu, encoding="utf-8")
            fusionnes.append(chemin)

    return fusionnes


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée.

    Args:
        argv: arguments ; le premier est la référence distante.

    Returns:
        Code de sortie, toujours 0 : une fusion impossible ne doit pas faire
        échouer la publication, elle laisse simplement le fichier local tel
        quel.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = list(sys.argv[1:] if argv is None else argv)
    reference = arguments[0] if arguments else "origin/main"

    fusionnes = fusionner_tout(reference)
    if fusionnes:
        print(f"Journaux fusionnés avec {reference} : {', '.join(fusionnes)}")
    else:
        print(f"Aucun journal à fusionner avec {reference}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
