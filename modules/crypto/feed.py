"""Fil d'actualité crypto : chaque news expliquée dès sa collecte.

Reprend exactement le patron de :mod:`modules.quantum.feed`, appliqué aux dix
jetons de ``crypto_watchlist`` plutôt qu'aux valeurs cotées du secteur
quantique — même collecte à trois canaux, même déduplication, même historique
en ajout seul, même format de sortie, mêmes interdits (aucune recommandation,
aucun chiffre qui ne vienne des données collectées).

Différence avec le fil quantique
---------------------------------
Le fil quantique croise chaque item avec la classification sectoriel /
spécifique de :mod:`modules.quantum.moves`, qui n'a pas d'équivalent côté
crypto : aucun module ne classe aujourd'hui un mouvement de jeton comme
propre à ce jeton ou commun au marché. Le gabarit se contente donc de citer
la variation sur 24 heures telle que publiée par
``reports/crypto/latest.json``, sans classification qu'aucune donnée ne
permettrait de vérifier.

Format unifié
-------------
Structure de sortie identique à celle du fil quantique (``feed.CLES_ITEM``,
``feed.CATEGORIES``) : la page web les consomme sans distinction de domaine.
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
DOSSIER_FEED: Final = RACINE / "reports" / "crypto"

#: Historique de tout ce qui a déjà été traité, en JSON par lignes.
FICHIER_HISTORIQUE: Final = DOSSIER_FEED / "feed_historique.jsonl"

#: Fil courant, plus récent en premier.
FICHIER_LATEST: Final = DOSSIER_FEED / "feed_latest.json"

#: Catégories admises. Contrat partagé avec les fils quantique et
#: géopolitique : les trois valeurs doivent rester identiques dans les trois
#: modules, vérifié par test.
CATEGORIES: Final[tuple[str, ...]] = ("quantique", "crypto", "geopolitique")

#: Clés exactes d'un item du fil. Même contrat que le fil quantique : un
#: champ en plus ou en moins casserait l'affichage sans que rien ne le
#: signale, d'où la vérification par test.
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

CONSIGNE_FEED: Final = """Tu expliques une actualité du secteur crypto à un lecteur qui suit quelques jetons.

RÈGLES ABSOLUES :
1. N'utilise QUE les informations et les nombres du JSON fourni. N'invente aucun chiffre, aucune date, aucun montant.
2. Ne recommande JAMAIS d'acheter, de vendre, de se positionner. N'écris jamais qu'un jeton est intéressant, attractif ou sous-évalué.
3. Quand une information manque, dis-le au lieu de la contourner.
4. Français simple, trois phrases au maximum, pas de liste.

STRUCTURE : ce qui s'est passé ; quel(s) jeton(s) suivi(s) sont concernés ; pourquoi cela compte pour qui les suit."""

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
                    continue
                identifiant = entree.get("id")
                if identifiant:
                    connus.add(str(identifiant))
    except FileNotFoundError:
        _LOG.info("Aucun historique de fil crypto : première exécution.")
    except OSError as exc:
        _LOG.warning("Historique du fil crypto illisible (%s) : %s", fichier, exc)
    return connus


# ---------------------------------------------------------------------------
# Collecte
# ---------------------------------------------------------------------------
def collecter(
    configuration: dict[str, Any],
    flux_rss: list[dict[str, Any]] | None = None,
    requetes_gdelt: list[dict[str, Any]] | None = None,
    articles_precollectes: list[Any] | None = None,
) -> list[news.NewsItem]:
    """Rassemble les actualités crypto, tous canaux confondus.

    Trois canaux : les flux RSS étiquetés crypto, une recherche par
    mots-clés pour chaque jeton suivi, et les requêtes GDELT déjà déclarées
    dans ``config/feeds.yaml`` sous l'étiquette crypto (réutilisées telles
    quelles plutôt que réécrites).

    Args:
        configuration: contenu de ``config/universe.yaml``.
        flux_rss: flux déclarés dans ``config/feeds.yaml``.
        requetes_gdelt: requêtes ``gdelt_queries`` de ``config/feeds.yaml``.
        articles_precollectes: articles déjà obtenus, pour les tests hors ligne.

    Returns:
        Articles dédupliqués, du plus récent au plus ancien.
    """
    if articles_precollectes is not None:
        return news.dedupe(articles_precollectes)

    reglages = dict(configuration.get("feed_crypto") or {})
    fenetre = int(reglages.get("fenetre_heures", 24))
    tags_voulus = {str(t).lower() for t in (reglages.get("tags_flux") or ["crypto"])}

    articles: list[news.NewsItem] = []

    # Canal 1 : flux RSS étiquetés crypto.
    retenus = [
        f for f in (flux_rss or [])
        if tags_voulus & {str(t).lower() for t in (f.get("tags") or [])}
    ]
    if retenus:
        articles.extend(news.fetch_rss(retenus, hours=fenetre))
    else:
        _LOG.info("Aucun flux RSS étiqueté %s.", " / ".join(sorted(tags_voulus)))

    # Canal 2 : recherche par mots-clés, jeton par jeton.
    for entree in configuration.get("crypto_watchlist") or []:
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
                tags=["crypto", str(entree.get("symbol", ""))],
            )
        )

    # Canal 3 : requêtes GDELT déjà configurées pour le domaine crypto.
    for requete_config in requetes_gdelt or []:
        tags = {str(t).lower() for t in (requete_config.get("tags") or [])}
        if "crypto" not in tags:
            continue
        query = str(requete_config.get("query", ""))
        if not query:
            continue
        articles.extend(
            news.fetch_gdelt(
                query,
                timespan=f"{fenetre}h",
                max_records=50,
                tags=["crypto", "secteur"],
            )
        )

    return news.dedupe(articles)


def _entites_liees(titre: str, watchlist: list[dict[str, Any]]) -> list[str]:
    """Repère les jetons suivis cités dans un titre.

    Args:
        titre: titre de l'article.
        watchlist: entrées ``crypto_watchlist``.

    Returns:
        Symboles des jetons suivis, sans doublon.
    """
    normalise = _normaliser(titre)
    liees: list[str] = []
    for entree in watchlist:
        symbole = str(entree.get("symbol", "")).upper()
        cles = [_normaliser(t) for t in (entree.get("keywords") or []) if t]
        if any(cle and cle in normalise for cle in cles):
            liees.append(symbole)
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

    Args:
        donnees: contexte de l'item.

    Returns:
        L'explication, en français.
    """
    entites = donnees.get("entites_liees") or []
    phrases = [f"Actualité relayée par {donnees.get('source_nom', 'une source crypto')}."]

    if entites:
        phrases.append(f"Elle mentionne : {', '.join(entites)}.")
    else:
        phrases.append("Elle porte sur le secteur crypto sans citer de jeton suivi en particulier.")

    mouvement = donnees.get("mouvement_du_jour")
    if mouvement:
        phrases.append(
            f"Le cours de {mouvement['symbole']} varie de {mouvement['variation_24h_pct']} % "
            "sur les dernières 24 heures."
        )
    else:
        phrases.append(
            "Aucune variation de prix notable n'est publiée pour les jetons suivis mentionnés."
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
        "actualite_crypto",
        donnees,
        configuration=reglages,
        client=client,
        consigne=CONSIGNE_FEED,
    )
    if resultat.mode == "openai":
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
    variations_par_symbole: dict[str, dict[str, Any]] | None = None,
    configuration_explication: dict[str, Any] | None = None,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    """Transforme les articles collectés en items du fil.

    Args:
        configuration: contenu de ``config/universe.yaml``.
        articles: articles dédupliqués.
        identifiants_connus: identifiants déjà traités lors des exécutions
            précédentes.
        variations_par_symbole: variation sur 24 h par symbole, telle que
            publiée par ``reports/crypto/latest.json``.
        configuration_explication: réglages de la couche pédagogique.
        client: client OpenAI éventuel.

    Returns:
        Items au format unifié, du plus récent au plus ancien.
    """
    reglages = dict(configuration.get("feed_crypto") or {})
    exclusions = [str(e) for e in (reglages.get("exclusions") or [])]
    max_analyses = int(reglages.get("max_analyses_par_execution", 8))

    watchlist = list(configuration.get("crypto_watchlist") or [])
    variations = dict(variations_par_symbole or {})

    items: list[dict[str, Any]] = []
    analyses_faites = 0

    for article in articles:
        titre = str(getattr(article, "titre", "") or "")
        url = str(getattr(article, "url", "") or "")
        if not titre:
            continue

        entites = _entites_liees(titre, watchlist)
        if _est_exclu(titre, entites, exclusions):
            _LOG.debug("Item écarté par la liste d'exclusions : %s", titre[:60])
            continue

        # Un item sans lien avec un jeton suivi n'a pas sa place dans un fil
        # destiné à suivre ces jetons.
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
                (variations[s] for s in entites if s in variations), None
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
                "categorie": "crypto",
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
        "Fil crypto : %d item(s), dont %d nouveau(x) et %d analysé(s).",
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
        _LOG.error("Publication du fil crypto impossible : %s", exc)
        return None

    _LOG.info("Fil crypto publié : %s (%d item(s)).", chemin_latest, len(items))
    return chemin_latest, historique


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------
def _variations_depuis_le_rapport(chemin: Path) -> dict[str, dict[str, Any]]:
    """Relit les variations 24 h publiées par le module de positions crypto.

    Args:
        chemin: chemin de ``reports/crypto/latest.json``.

    Returns:
        Dictionnaire par symbole, vide si le rapport est absent ou illisible.
    """
    try:
        rapport = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    positions = (
        rapport.get("positionnement", {}).get("positions", {}).get("positions", [])
    )
    resultat: dict[str, dict[str, Any]] = {}
    for position in positions:
        symbole = position.get("symbole")
        if not symbole or not position.get("disponible"):
            continue
        resultat[str(symbole)] = {
            "symbole": symbole,
            "variation_24h_pct": position.get("variation_24h_pct"),
        }
    return resultat


def main(argv: list[str] | None = None) -> int:
    """Collecte, analyse et publie le fil crypto.

    Returns:
        0 si le fil est publié, 1 sinon.
    """
    import argparse

    import yaml

    analyseur = argparse.ArgumentParser(description="Fil d'actualité du secteur crypto.")
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
    requetes_gdelt = _charger(RACINE / "config" / "feeds.yaml").get("gdelt_queries") or []
    reglages_explication = dict(
        (_charger(RACINE / "config" / "gold.yaml").get("explication") or {})
    )
    if arguments.sans_analyse:
        reglages_explication["activee"] = False

    articles = collecter(configuration, flux_rss=flux, requetes_gdelt=requetes_gdelt)
    variations = _variations_depuis_le_rapport(RACINE / "reports" / "crypto" / "latest.json")
    items = construire_fil(
        configuration,
        articles,
        identifiants_connus=charger_historique(),
        variations_par_symbole=variations,
        configuration_explication=reglages_explication,
    )

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
    print(f"\nFil crypto : {len(items)} item(s), {len(nouveaux)} nouveau(x)")
    for item in items[:5]:
        marque = "NOUVEAU" if item["nouveaute"] else "connu  "
        print(f"  [{marque}] {item['titre_affiche'][:72]}")
        print(f"            {', '.join(item['tickers_ou_themes_lies'])}")
    print(f"Écrit dans {chemins[0]}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
