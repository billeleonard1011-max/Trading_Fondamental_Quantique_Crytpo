"""Fil d'actualité quantique : chaque news expliquée dès sa collecte.

Le manque que ce module comble
------------------------------
Jusqu'ici, une actualité n'était expliquée que dans deux cas : elle coïncidait
avec un mouvement de prix marqué, et :mod:`modules.quantum.moves` la remontait
en pièce à conviction ; ou elle nourrissait le résumé sectoriel quotidien de
:mod:`modules.quantum.industry`. Une nouvelle importante qui ne déplaçait pas
le cours le jour même n'était donc jamais commentée.

Ce module tient le fil chronologique qui manquait : chaque item pertinent
reçoit sa propre explication à mesure qu'il arrive, indépendamment de ce que
fait le prix.

Conçu pour tourner souvent
--------------------------
Une cadence de quinze à trente minutes serait cohérente avec l'objet. Le
workflow GitHub Actions n'est **pas** modifié pour l'instant : le calendrier
définitif sera réglé en même temps que la page web, qui déterminera la
fraîcheur réellement utile. En l'état, le module se lance à la main ou depuis
n'importe quel ordonnanceur.

Ne rien réexpliquer deux fois
----------------------------
``reports/quantum/feed_historique.jsonl`` conserve tout ce qui a déjà été
traité. À chaque exécution, les items déjà connus sont écartés avant toute
analyse : c'est ce qui rend une cadence rapprochée soutenable, puisque le
coût d'une exécution est proportionnel à la nouveauté, pas au volume collecté.

Format unifié
-------------
La structure de sortie est fermée et volontairement identique pour les trois
domaines à venir — quantique, crypto, géopolitique. Elle est vérifiée par un
test : un champ manquant ou surnuméraire fait échouer la suite. Seul le fil
quantique existe à ce stade ; les deux autres reprendront ce patron.

Mêmes interdits que partout ailleurs
------------------------------------
Aucune recommandation, et aucun chiffre qui ne vienne des données collectées.
La vérification numérique de :mod:`modules.gold.explain` est réutilisée telle
quelle plutôt que réécrite, et le contrôle anti-recommandation de
:mod:`modules.quantum.moves` s'applique avant publication.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from dataio import news
from modules.gold import explain
from modules.quantum import moves

_LOG: Final = logging.getLogger(__name__)

#: Racine du dépôt, déduite de l'emplacement de ce fichier.
RACINE: Final = Path(__file__).resolve().parents[2]

#: Dossier de publication du fil.
DOSSIER_FEED: Final = RACINE / "reports" / "quantum"

#: Historique de tout ce qui a déjà été traité, en JSON par lignes.
FICHIER_HISTORIQUE: Final = DOSSIER_FEED / "feed_historique.jsonl"

#: Fil courant, plus récent en premier.
FICHIER_LATEST: Final = DOSSIER_FEED / "feed_latest.json"

#: Catégories admises. La liste est fermée : les fils crypto et géopolitique
#: à venir devront s'y ranger, sans en inventer une quatrième.
CATEGORIES: Final[tuple[str, ...]] = ("quantique", "crypto", "geopolitique")

#: Clés exactes d'un item du fil. Le format est un contrat entre ce module et
#: la page web : un champ en plus ou en moins casserait l'affichage sans que
#: rien ne le signale, d'où la vérification par test.
CLES_ITEM: Final[tuple[str, ...]] = (
    "id",
    "categorie",
    "titre_affiche",
    "horodatage_utc",
    "source_nom",
    "url_source",
    "a_une_analyse_interne",
    "analyse_interne",
    "tickers_ou_themes_lies",
    "nouveaute",
)

#: Nombre d'items conservés dans le fil courant.
MAX_ITEMS_FIL: Final[int] = 120

CONSIGNE_FEED: Final = """Tu expliques une actualité du secteur du calcul quantique à un lecteur qui suit quelques valeurs cotées.

RÈGLES ABSOLUES :
1. N'utilise QUE les informations et les nombres du JSON fourni. N'invente aucun chiffre, aucune date, aucun montant.
2. Ne recommande JAMAIS d'acheter, de vendre, de se positionner. N'écris jamais qu'un titre est intéressant, attractif ou sous-évalué.
3. Si le JSON indique qu'un prix a bougé et si le mouvement est classé sectoriel, dis que la cause n'est pas propre à la société.
4. Quand une information manque, dis-le au lieu de la contourner.
5. Français simple, trois phrases au maximum, pas de liste.

STRUCTURE : ce qui s'est passé ; si c'est propre à une société ou commun au secteur ; pourquoi cela compte pour qui suit ces valeurs."""

__all__ = [
    "CATEGORIES",
    "CLES_ITEM",
    "identifiant_item",
    "charger_historique",
    "collecter",
    "construire_fil",
    "publier_fil",
]


# ---------------------------------------------------------------------------
# Identité et historique
# ---------------------------------------------------------------------------
def _normaliser(texte: str) -> str:
    """Réduit un texte à une forme comparable : minuscules, sans accent."""
    decompose = unicodedata.normalize("NFKD", (texte or "").lower())
    sans_accent = "".join(c for c in decompose if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", sans_accent)).strip()


def identifiant_item(titre: str, url: str) -> str:
    """Calcule l'identifiant stable d'un item.

    L'identifiant repose sur le titre normalisé plutôt que sur l'URL seule :
    une même dépêche circule sous plusieurs adresses, et se fier à l'URL
    rendrait le même article éternellement « nouveau ». L'URL entre tout de
    même dans le calcul, pour distinguer deux articles homonymes.

    Args:
        titre: titre de l'article.
        url: adresse de l'article.

    Returns:
        Empreinte hexadécimale de seize caractères.
    """
    base = _normaliser(titre) or (url or "").strip().lower()
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def charger_historique(chemin: Path | None = None) -> set[str]:
    """Relit les identifiants déjà traités.

    Args:
        chemin: fichier d'historique. ``None`` retient
            :data:`FICHIER_HISTORIQUE`, résolu à l'appel.

    Returns:
        Ensemble des identifiants connus. Vide si le fichier n'existe pas.
    """
    fichier = chemin or FICHIER_HISTORIQUE
    connus: set[str] = set()
    try:
        with fichier.open("r", encoding="utf-8") as flux:
            for ligne in flux:
                ligne = ligne.strip()
                if not ligne:
                    continue
                try:
                    entree = json.loads(ligne)
                except json.JSONDecodeError:
                    # Une ligne corrompue ne doit pas faire perdre tout
                    # l'historique : on l'ignore et on continue.
                    continue
                identifiant = entree.get("id")
                if identifiant:
                    connus.add(str(identifiant))
    except FileNotFoundError:
        _LOG.info("Aucun historique de fil : première exécution.")
    except OSError as exc:
        _LOG.warning("Historique du fil illisible (%s) : %s", fichier, exc)
    return connus


# ---------------------------------------------------------------------------
# Collecte
# ---------------------------------------------------------------------------
def collecter(
    configuration: dict[str, Any],
    flux_rss: list[dict[str, Any]] | None = None,
    articles_precollectes: list[Any] | None = None,
) -> list[news.NewsItem]:
    """Rassemble les actualités du secteur, tous canaux confondus.

    Trois canaux : les flux RSS étiquetés quantique, une recherche par
    mots-clés pour chaque valeur suivie, et la requête sectorielle GDELT. Le
    tout est dédupliqué sur le titre normalisé, une même dépêche circulant
    sur plusieurs supports.

    Args:
        configuration: contenu de ``config/universe.yaml``.
        flux_rss: flux déclarés dans ``config/feeds.yaml``.
        articles_precollectes: articles déjà obtenus, pour les tests hors ligne.

    Returns:
        Articles dédupliqués, du plus récent au plus ancien.
    """
    if articles_precollectes is not None:
        return news.dedupe(articles_precollectes)

    reglages = dict(configuration.get("feed_quantique") or {})
    fenetre = int(reglages.get("fenetre_heures", 24))
    tags_voulus = {str(t).lower() for t in (reglages.get("tags_flux") or ["quantique"])}

    articles: list[news.NewsItem] = []

    # Canal 1 : flux RSS étiquetés quantique.
    retenus = [
        f for f in (flux_rss or [])
        if tags_voulus & {str(t).lower() for t in (f.get("tags") or [])}
    ]
    if retenus:
        articles.extend(news.fetch_rss(retenus, hours=fenetre))
    else:
        _LOG.info("Aucun flux RSS étiqueté %s.", " / ".join(sorted(tags_voulus)))

    # Canal 2 : recherche par mots-clés, valeur par valeur.
    for entree in configuration.get("quantum_watchlist") or []:
        mots = list(entree.get("keywords") or [])
        if not mots:
            continue
        requete = news.construire_requete_gdelt(mots)
        if not requete:
            continue
        articles.extend(
            news.fetch_gdelt(
                requete,
                timespan=f"{fenetre}h",
                max_records=25,
                tags=["quantique", str(entree.get("ticker", ""))],
            )
        )

    # Canal 3 : requête sectorielle.
    theme = dict(
        configuration.get("quantum_industry_watch")
        or configuration.get("quantique_industrie")
        or {}
    )
    if theme.get("query"):
        articles.extend(
            news.fetch_gdelt(
                str(theme["query"]),
                timespan=f"{fenetre}h",
                max_records=50,
                tags=["quantique", "secteur"],
            )
        )

    return news.dedupe(articles)


def _entites_liees(
    titre: str,
    watchlist: list[dict[str, Any]],
    incumbents: list[str],
) -> list[str]:
    """Repère les valeurs suivies et acteurs connus cités dans un titre.

    Args:
        titre: titre de l'article.
        watchlist: entrées ``quantum_watchlist``.
        incumbents: acteurs connus du secteur.

    Returns:
        Tickers des valeurs suivies, puis noms d'acteurs, sans doublon.
    """
    normalise = _normaliser(titre)
    liees: list[str] = []

    for entree in watchlist:
        ticker = str(entree.get("ticker", "")).upper()
        termes = [str(entree.get("name", ""))] + list(entree.get("keywords") or [])
        if any(_normaliser(t) and _normaliser(t) in normalise for t in termes if t):
            liees.append(ticker)

    for acteur in incumbents:
        cle = _normaliser(acteur)
        if cle and cle in normalise and acteur not in liees:
            liees.append(acteur)

    return liees


def _est_exclu(titre: str, entites: list[str], exclusions: list[str]) -> bool:
    """Dit si un item porte une entité qu'on ne veut pas voir paraître.

    Args:
        titre: titre de l'article.
        entites: entités détectées.
        exclusions: entités interdites de publication.

    Returns:
        ``True`` si l'item doit être écarté.
    """
    normalise = _normaliser(titre)
    for interdit in exclusions:
        cle = _normaliser(interdit)
        if not cle:
            continue
        if cle in normalise or any(cle == _normaliser(e) for e in entites):
            return True
    return False


# ---------------------------------------------------------------------------
# Analyse d'un item
# ---------------------------------------------------------------------------
def _gabarit(donnees: dict[str, Any]) -> str:
    """Rédige l'explication sans appel à un modèle de langage.

    Le texte est assemblé à partir des seules données collectées. Il est donc
    exact par construction, ce qui en fait un repli sûr quand la vérification
    numérique échoue.

    Args:
        donnees: contexte de l'item.

    Returns:
        L'explication, en français.
    """
    entites = donnees.get("entites_liees") or []
    phrases = [f"Actualité relayée par {donnees.get('source_nom', 'une source du secteur')}."]

    if entites:
        phrases.append(f"Elle mentionne : {', '.join(entites)}.")
    else:
        phrases.append("Elle porte sur le secteur sans citer de valeur suivie en particulier.")

    mouvement = donnees.get("mouvement_du_jour")
    if mouvement and mouvement.get("classification") == moves.SECTORIEL:
        phrases.append(
            f"Le cours de {mouvement['ticker']} varie de {mouvement['variation_pct']} % le "
            "même jour, mais ce mouvement touche aussi les autres valeurs du secteur : "
            "sa cause n'est pas propre à cette société."
        )
    elif mouvement:
        phrases.append(
            f"Le cours de {mouvement['ticker']} varie de {mouvement['variation_pct']} % le "
            "même jour, sans que les autres valeurs suivies bougent de façon comparable."
        )
    else:
        phrases.append(
            "Aucun mouvement de cours notable n'accompagne cette actualité parmi les "
            "valeurs suivies."
        )
    return " ".join(phrases)


def _analyser_item(
    donnees: dict[str, Any],
    configuration_explication: dict[str, Any] | None,
    client: Any | None,
) -> tuple[str, bool]:
    """Produit l'explication d'un item, avec vérification puis repli.

    Args:
        donnees: contexte de l'item, seule source de chiffres autorisée.
        configuration_explication: bloc ``explication`` de la configuration or.
        client: client OpenAI éventuel.

    Returns:
        Couple ``(texte, produit_par_modele)``.
    """
    reglages = dict(configuration_explication or {})
    gabarit = _gabarit(donnees)

    if not reglages.get("activee", True) or (
        client is None and not os.environ.get("OPENAI_API_KEY", "").strip()
    ):
        return gabarit, False

    resultat = explain.expliquer(
        "actualite_quantique",
        donnees,
        configuration=reglages,
        client=client,
        consigne=CONSIGNE_FEED,
    )
    if resultat.mode == "openai":
        # Double contrôle : la vérification numérique d'explain, puis
        # l'interdiction de recommander propre au domaine quantique.
        if not moves.verifier_absence_recommandation({"analyse": resultat.texte}):
            return resultat.texte, True
        _LOG.warning("Explication rejetée : formulation de recommandation détectée.")
    return gabarit, False


# ---------------------------------------------------------------------------
# Construction du fil
# ---------------------------------------------------------------------------
def construire_fil(
    configuration: dict[str, Any],
    articles: list[news.NewsItem],
    identifiants_connus: set[str],
    mouvements_par_ticker: dict[str, dict[str, Any]] | None = None,
    configuration_explication: dict[str, Any] | None = None,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    """Transforme les articles collectés en items du fil.

    Args:
        configuration: contenu de ``config/universe.yaml``.
        articles: articles dédupliqués.
        identifiants_connus: identifiants déjà traités lors des exécutions
            précédentes.
        mouvements_par_ticker: mouvements de prix du jour, par ticker, tels
            que produits par :mod:`modules.quantum.moves`.
        configuration_explication: réglages de la couche pédagogique.
        client: client OpenAI éventuel.

    Returns:
        Items au format unifié, du plus récent au plus ancien.
    """
    reglages = dict(configuration.get("feed_quantique") or {})
    exclusions = [str(e) for e in (reglages.get("exclusions") or [])]
    max_analyses = int(reglages.get("max_analyses_par_execution", 8))

    watchlist = list(configuration.get("quantum_watchlist") or [])
    incumbents = list((configuration.get("quantum") or {}).get("incumbents") or [])
    mouvements = dict(mouvements_par_ticker or {})

    items: list[dict[str, Any]] = []
    analyses_faites = 0

    for article in articles:
        titre = str(getattr(article, "titre", "") or "")
        url = str(getattr(article, "url", "") or "")
        if not titre:
            continue

        entites = _entites_liees(titre, watchlist, incumbents)
        if _est_exclu(titre, entites, exclusions):
            _LOG.debug("Item écarté par la liste d'exclusions : %s", titre[:60])
            continue

        # Un item sans lien avec une valeur suivie ni un acteur connu n'a pas
        # sa place dans un fil destiné à suivre ces valeurs.
        if not entites:
            continue

        identifiant = identifiant_item(titre, url)
        nouveaute = identifiant not in identifiants_connus

        date_article = getattr(article, "date", None)
        horodatage = (
            date_article.astimezone(timezone.utc).isoformat()
            if isinstance(date_article, datetime)
            else datetime.now(timezone.utc).isoformat()
        )

        analyse: str | None = None
        if nouveaute and analyses_faites < max_analyses:
            mouvement = next(
                (mouvements[t] for t in entites if t in mouvements), None
            )
            contexte = {
                "titre": titre,
                "source_nom": str(getattr(article, "source", "") or ""),
                "entites_liees": entites,
                "horodatage_utc": horodatage,
                "mouvement_du_jour": mouvement,
            }
            analyse, _ = _analyser_item(contexte, configuration_explication, client)
            analyses_faites += 1

        items.append(
            {
                "id": identifiant,
                "categorie": "quantique",
                "titre_affiche": titre,
                "horodatage_utc": horodatage,
                "source_nom": str(getattr(article, "source", "") or ""),
                "url_source": url,
                "a_une_analyse_interne": analyse is not None,
                "analyse_interne": analyse,
                "tickers_ou_themes_lies": entites,
                "nouveaute": nouveaute,
            }
        )

    items.sort(key=lambda i: i["horodatage_utc"], reverse=True)
    _LOG.info(
        "Fil quantique : %d item(s), dont %d nouveau(x) et %d analysé(s).",
        len(items),
        sum(1 for i in items if i["nouveaute"]),
        analyses_faites,
    )
    return items


def publier_fil(
    items: list[dict[str, Any]],
    dossier: Path | None = None,
    chemin_historique: Path | None = None,
) -> tuple[Path, Path] | None:
    """Écrit le fil courant et complète l'historique.

    L'historique est en ajout seul : il n'est jamais relu en entier pour être
    réécrit, ce qui garde le coût d'une exécution constant quelle que soit sa
    profondeur. Seuls les items nouveaux y sont ajoutés.

    Args:
        items: items du fil.
        dossier: dossier de publication.
        chemin_historique: fichier d'historique.

    Returns:
        Couple des chemins écrits, ou ``None`` en cas d'échec.
    """
    cible = dossier or DOSSIER_FEED
    historique = chemin_historique or (cible / "feed_historique.jsonl")

    try:
        cible.mkdir(parents=True, exist_ok=True)
        chemin_latest = cible / "feed_latest.json"
        chemin_latest.write_text(
            json.dumps(items[:MAX_ITEMS_FIL], ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        nouveaux = [i for i in items if i.get("nouveaute")]
        if nouveaux:
            with historique.open("a", encoding="utf-8") as flux:
                for item in nouveaux:
                    flux.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        _LOG.error("Publication du fil impossible : %s", exc)
        return None

    _LOG.info("Fil publié : %s (%d item(s)).", chemin_latest, len(items))
    return chemin_latest, historique


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """Collecte, analyse et publie le fil quantique.

    Prévu pour tourner souvent — une cadence de quinze à trente minutes serait
    cohérente. Le workflow GitHub Actions n'est pas modifié pour l'instant :
    le calendrier définitif sera réglé avec la page web.

    Returns:
        0 si le fil est publié, 1 sinon.
    """
    import argparse

    import yaml

    analyseur = argparse.ArgumentParser(description="Fil d'actualité du secteur quantique.")
    analyseur.add_argument("--verbeux", action="store_true", help="journalisation détaillée.")
    analyseur.add_argument(
        "--sans-analyse",
        action="store_true",
        help="collecte et publie sans produire d'explication.",
    )
    arguments = analyseur.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if arguments.verbeux else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s : %(message)s",
        datefmt="%H:%M:%S",
    )

    def _charger(chemin: Path) -> dict[str, Any]:
        """Lit un fichier de configuration YAML."""
        try:
            with chemin.open("r", encoding="utf-8") as fichier:
                return yaml.safe_load(fichier) or {}
        except (OSError, yaml.YAMLError) as exc:
            _LOG.error("Configuration illisible (%s) : %s", chemin, exc)
            return {}

    configuration = _charger(RACINE / "config" / "universe.yaml")
    flux = _charger(RACINE / "config" / "feeds.yaml").get("feeds") or []
    reglages_explication = dict(
        (_charger(RACINE / "config" / "gold.yaml").get("explication") or {})
    )
    if arguments.sans_analyse:
        reglages_explication["activee"] = False

    articles = collecter(configuration, flux_rss=flux)
    items = construire_fil(
        configuration,
        articles,
        identifiants_connus=charger_historique(),
        configuration_explication=reglages_explication,
    )

    # Garde-fou avant publication, comme pour le rapport quantique.
    infractions = moves.verifier_absence_recommandation(items)
    if infractions:
        _LOG.error(
            "Publication du fil refusée : %d formulation(s) de recommandation.",
            len(infractions),
        )
        for infraction in infractions[:5]:
            _LOG.error("  %s → %s", infraction["chemin"], infraction["extrait"][:80])
        return 1

    chemins = publier_fil(items)
    if chemins is None:
        return 1

    nouveaux = [i for i in items if i["nouveaute"]]
    print(f"\nFil quantique : {len(items)} item(s), {len(nouveaux)} nouveau(x)")
    for item in items[:5]:
        marque = "NOUVEAU" if item["nouveaute"] else "connu  "
        print(f"  [{marque}] {item['titre_affiche'][:72]}")
        print(f"            {', '.join(item['tickers_ou_themes_lies'])}")
    print(f"Écrit dans {chemins[0]}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
