"""Diagnostic des sources d'actualité.

Teste chaque flux RSS et chaque requête GDELT de ``config/feeds.yaml``, puis
classe chaque source dans l'un de trois états :

``OK``
    La source répond et contient des articles récents.
``FIGÉ``
    La source répond et contient des articles, mais aucun récent. Le flux
    existe toujours, il n'est plus alimenté — c'est le cas le plus pernicieux,
    parce qu'il ne provoque aucune erreur et vide silencieusement la veille.
``MORT``
    La source ne répond pas, renvoie une erreur, ou ne contient aucun article.

Usage :
    python -m scripts.check_feeds
    python -m scripts.check_feeds --heures 72 --intensite
    python -m scripts.check_feeds --json > diagnostic.json

Codes de sortie :
    0  toutes les sources sont saines
    1  au moins une source est figée
    2  au moins une source est morte

Ce script effectue des appels réseau. Il n'est pas lancé par les tests.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

import requests

from dataio.news import TIMEOUT, _ENTETES, fetch_gdelt, gdelt_intensity

_LOG: Final = logging.getLogger("check_feeds")

#: Chemin du fichier de configuration, relatif à la racine du dépôt.
CHEMIN_CONFIG: Final = Path(__file__).resolve().parent.parent / "config" / "feeds.yaml"

#: États possibles d'une source.
OK: Final = "OK"
FIGE: Final = "FIGÉ"
MORT: Final = "MORT"

#: Nombre de sources testées simultanément. Les appels sont bloqués par le
#: réseau, pas par le processeur : paralléliser réduit fortement l'attente.
PARALLELISME: Final = 6


@dataclass(slots=True)
class Diagnostic:
    """Résultat du test d'une source.

    Attributes:
        nom: nom lisible de la source.
        canal: ``rss`` ou ``gdelt``.
        statut: ``OK``, ``FIGÉ`` ou ``MORT``.
        nb_articles: nombre d'articles vus.
        dernier_article: date du plus récent, au format ISO.
        age_heures: âge du plus récent, en heures.
        detail: précision lisible sur l'état constaté.
    """

    nom: str
    canal: str
    statut: str
    nb_articles: int = 0
    dernier_article: str | None = None
    age_heures: float | None = None
    detail: str = ""


def _maintenant() -> datetime:
    """Instant courant en UTC."""
    return datetime.now(timezone.utc)


def charger_config(chemin: Path = CHEMIN_CONFIG) -> dict[str, Any]:
    """Charge ``config/feeds.yaml``.

    Args:
        chemin: chemin du fichier de configuration.

    Returns:
        Dictionnaire de configuration.

    Raises:
        SystemExit: si PyYAML manque ou si le fichier est absent ou illisible.
    """
    try:
        import yaml
    except ImportError:
        raise SystemExit(
            "PyYAML est requis. Installez les dépendances : pip install -r requirements.txt"
        )
    if not chemin.exists():
        raise SystemExit(f"Configuration introuvable : {chemin}")
    try:
        with chemin.open(encoding="utf-8") as fichier:
            return yaml.safe_load(fichier) or {}
    except yaml.YAMLError as exc:
        raise SystemExit(f"Configuration illisible ({chemin}) : {exc}")


# ---------------------------------------------------------------------------
# Test d'un flux RSS
# ---------------------------------------------------------------------------
def tester_flux_rss(flux: dict[str, Any], seuil_heures: int) -> Diagnostic:
    """Teste un flux RSS et détermine son état.

    Args:
        flux: description du flux (``name``, ``url``...).
        seuil_heures: âge maximal du dernier article pour être jugé frais.

    Returns:
        Diagnostic de la source.
    """
    nom = str(flux.get("name", "flux sans nom"))
    url = str(flux.get("url", "")).strip()

    if not url:
        return Diagnostic(nom, "rss", MORT, detail="URL absente de la configuration.")

    try:
        import feedparser
    except ImportError:
        return Diagnostic(nom, "rss", MORT, detail="feedparser n'est pas installé.")

    try:
        reponse = requests.get(url, timeout=TIMEOUT, headers=_ENTETES)
    except requests.Timeout:
        return Diagnostic(nom, "rss", MORT, detail=f"Délai dépassé ({TIMEOUT:.0f} s).")
    except requests.RequestException as exc:
        return Diagnostic(nom, "rss", MORT, detail=f"Connexion impossible : {exc}")

    if reponse.status_code != 200:
        return Diagnostic(
            nom, "rss", MORT, detail=f"Code HTTP {reponse.status_code}."
        )

    try:
        analyse = feedparser.parse(reponse.content)
    except Exception as exc:  # noqa: BLE001
        return Diagnostic(nom, "rss", MORT, detail=f"Contenu illisible : {exc}")

    entrees = list(getattr(analyse, "entries", []))
    if not entrees:
        motif = getattr(analyse, "bozo_exception", None)
        detail = f"Aucun article. {motif}" if motif else "Aucun article dans le flux."
        return Diagnostic(nom, "rss", MORT, detail=detail.strip())

    dates: list[datetime] = []
    for entree in entrees:
        for champ in ("published_parsed", "updated_parsed"):
            valeur = entree.get(champ)
            if valeur:
                try:
                    dates.append(datetime(*tuple(valeur)[:6], tzinfo=timezone.utc))
                except (TypeError, ValueError):
                    pass
                break

    if not dates:
        return Diagnostic(
            nom,
            "rss",
            FIGE,
            nb_articles=len(entrees),
            detail=(
                f"{len(entrees)} article(s), mais aucune date exploitable : "
                "fraîcheur invérifiable."
            ),
        )

    plus_recent = max(dates)
    age = (_maintenant() - plus_recent).total_seconds() / 3600.0

    if age > seuil_heures:
        return Diagnostic(
            nom,
            "rss",
            FIGE,
            nb_articles=len(entrees),
            dernier_article=plus_recent.isoformat(),
            age_heures=round(age, 1),
            detail=(
                f"{len(entrees)} article(s), le plus récent date de {age:.0f} h "
                f"(seuil {seuil_heures} h)."
            ),
        )

    return Diagnostic(
        nom,
        "rss",
        OK,
        nb_articles=len(entrees),
        dernier_article=plus_recent.isoformat(),
        age_heures=round(age, 1),
        detail=f"{len(entrees)} article(s), dernier il y a {age:.1f} h.",
    )


# ---------------------------------------------------------------------------
# Test d'une requête GDELT
# ---------------------------------------------------------------------------
def tester_requete_gdelt(
    requete: dict[str, Any], timespan: str, avec_intensite: bool
) -> Diagnostic:
    """Teste une requête GDELT et détermine son état.

    Une requête qui ne ramène rien sur plusieurs jours est soit mal écrite,
    soit trop étroite : dans les deux cas elle ne sert à rien.

    Args:
        requete: description (``name``, ``query``, ``tags``).
        timespan: fenêtre GDELT à interroger.
        avec_intensite: si vrai, calcule aussi le ratio de couverture 24 h
            contre 30 jours (un appel réseau supplémentaire par requête).

    Returns:
        Diagnostic de la source.
    """
    nom = str(requete.get("name", "requête sans nom"))
    texte = str(requete.get("query", "")).strip()

    if not texte:
        return Diagnostic(nom, "gdelt", MORT, detail="Requête absente de la configuration.")

    articles = fetch_gdelt(texte, timespan=timespan, max_records=50)
    if not articles:
        return Diagnostic(
            nom,
            "gdelt",
            MORT,
            detail=(
                f"Aucun article sur {timespan}. Requête trop étroite, mal formée, "
                "ou API indisponible."
            ),
        )

    dates = [a.date for a in articles if a.date is not None]
    detail = f"{len(articles)} article(s) sur {timespan}."
    age: float | None = None
    plus_recent_iso: str | None = None

    if dates:
        plus_recent = max(dates)
        plus_recent_iso = plus_recent.isoformat()
        age = round((_maintenant() - plus_recent).total_seconds() / 3600.0, 1)
        detail += f" Dernier il y a {age:.1f} h."

    if avec_intensite:
        mesure = gdelt_intensity(texte)
        if mesure["disponible"] and mesure["ratio"] is not None:
            marqueur = " [ALERTE]" if mesure["alerte"] else ""
            detail += f" Intensité 24 h : {mesure['ratio']:.1f}×{marqueur}."
        else:
            detail += " Intensité indisponible."

    # Une requête dont le dernier article remonte à plus de trois jours est
    # techniquement vivante mais inutile pour une veille quotidienne.
    if age is not None and age > 72.0:
        return Diagnostic(
            nom, "gdelt", FIGE, len(articles), plus_recent_iso, age,
            detail + " Rien de récent : sujet inactif ou requête trop étroite.",
        )

    return Diagnostic(nom, "gdelt", OK, len(articles), plus_recent_iso, age, detail)


# ---------------------------------------------------------------------------
# Restitution
# ---------------------------------------------------------------------------
_MARQUEURS: Final[dict[str, str]] = {OK: "[ OK ]", FIGE: "[FIGÉ]", MORT: "[MORT]"}


def afficher(diagnostics: list[Diagnostic]) -> None:
    """Affiche le rapport en clair sur la sortie standard.

    Args:
        diagnostics: résultats à présenter, dans l'ordre d'affichage.
    """
    largeur = max((len(d.nom) for d in diagnostics), default=20)
    canal_courant = ""

    for diagnostic in diagnostics:
        if diagnostic.canal != canal_courant:
            canal_courant = diagnostic.canal
            titre = "Flux RSS" if canal_courant == "rss" else "Requêtes GDELT"
            print(f"\n{titre}")
            print("-" * (largeur + 46))
        print(
            f"{_MARQUEURS[diagnostic.statut]} {diagnostic.nom:<{largeur}}  "
            f"{diagnostic.detail}"
        )


def _analyser_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande.

    Args:
        argv: arguments bruts. ``sys.argv`` par défaut.

    Returns:
        Arguments analysés.
    """
    analyseur = argparse.ArgumentParser(
        prog="python -m scripts.check_feeds",
        description="Vérifie l'état des flux RSS et des requêtes GDELT configurés.",
    )
    analyseur.add_argument(
        "--heures", type=int, default=48,
        help="Âge maximal du dernier article RSS pour qu'un flux soit jugé frais (défaut : 48).",
    )
    analyseur.add_argument(
        "--timespan", default="7d",
        help="Fenêtre interrogée sur GDELT (défaut : 7d).",
    )
    analyseur.add_argument(
        "--intensite", action="store_true",
        help="Calcule aussi le ratio de couverture 24 h contre 30 jours (plus lent).",
    )
    analyseur.add_argument(
        "--json", action="store_true",
        help="Écrit le rapport en JSON plutôt qu'en clair.",
    )
    analyseur.add_argument(
        "--config", type=Path, default=CHEMIN_CONFIG,
        help="Chemin du fichier de configuration des flux.",
    )
    analyseur.add_argument(
        "--rss-seulement", action="store_true",
        help="Ne teste que les flux RSS, sans appeler GDELT.",
    )
    return analyseur.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée du script.

    Args:
        argv: arguments de ligne de commande.

    Returns:
        Code de sortie : 0 si tout va bien, 1 si une source est figée,
        2 si une source est morte.
    """
    arguments = _analyser_arguments(argv)
    logging.basicConfig(
        level=logging.ERROR if arguments.json else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    config = charger_config(arguments.config)
    flux = config.get("feeds") or []
    requetes = [] if arguments.rss_seulement else (config.get("gdelt_queries") or [])

    if not flux and not requetes:
        print("Aucune source à tester dans la configuration.", file=sys.stderr)
        return 2

    diagnostics: list[Diagnostic] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=PARALLELISME) as executeur:
        taches_rss = {
            executeur.submit(tester_flux_rss, f, arguments.heures): i
            for i, f in enumerate(flux)
        }
        taches_gdelt = {
            executeur.submit(
                tester_requete_gdelt, r, arguments.timespan, arguments.intensite
            ): i
            for i, r in enumerate(requetes)
        }

        resultats_rss: dict[int, Diagnostic] = {}
        for tache, rang in taches_rss.items():
            try:
                resultats_rss[rang] = tache.result()
            except Exception as exc:  # noqa: BLE001 - un test ne doit pas tout arrêter
                nom = str(flux[rang].get("name", f"flux #{rang}"))
                resultats_rss[rang] = Diagnostic(
                    nom, "rss", MORT, detail=f"Erreur inattendue : {exc}"
                )

        resultats_gdelt: dict[int, Diagnostic] = {}
        for tache, rang in taches_gdelt.items():
            try:
                resultats_gdelt[rang] = tache.result()
            except Exception as exc:  # noqa: BLE001
                nom = str(requetes[rang].get("name", f"requête #{rang}"))
                resultats_gdelt[rang] = Diagnostic(
                    nom, "gdelt", MORT, detail=f"Erreur inattendue : {exc}"
                )

    # On restitue dans l'ordre du fichier de configuration, plus lisible que
    # l'ordre d'arrivée des réponses.
    diagnostics.extend(resultats_rss[i] for i in sorted(resultats_rss))
    diagnostics.extend(resultats_gdelt[i] for i in sorted(resultats_gdelt))

    morts = [d for d in diagnostics if d.statut == MORT]
    figes = [d for d in diagnostics if d.statut == FIGE]

    if arguments.json:
        print(
            json.dumps(
                {
                    "date_controle": _maintenant().isoformat(),
                    "total": len(diagnostics),
                    "ok": len(diagnostics) - len(morts) - len(figes),
                    "figes": len(figes),
                    "morts": len(morts),
                    "sources": [asdict(d) for d in diagnostics],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        afficher(diagnostics)
        print("\nRésumé")
        print("-" * 40)
        print(f"Sources testées : {len(diagnostics)}")
        print(f"  saines        : {len(diagnostics) - len(morts) - len(figes)}")
        print(f"  figées        : {len(figes)}")
        print(f"  mortes        : {len(morts)}")
        if morts:
            print("\nÀ corriger dans config/feeds.yaml :")
            for diagnostic in morts:
                print(f"  - {diagnostic.nom} : {diagnostic.detail}")

    if morts:
        return 2
    if figes:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
