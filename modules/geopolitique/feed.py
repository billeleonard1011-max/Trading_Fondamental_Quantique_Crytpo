"""Fil d'actualité géopolitique : chaque news expliquée dès sa collecte.

Reprend le patron de :mod:`modules.quantum.feed`, avec une différence de
fond : la pertinence n'est pas « c'est un événement géopolitique important »,
mais « ce thème a un canal de transmission connu vers l'or ». Une actualité
internationale qui ne relève d'aucun des quatre thèmes de
:mod:`modules.gold.geopolitics` (tensions énergétiques, conflits majeurs,
sanctions, tensions sur les réserves de change) n'entre pas dans ce fil, même
si elle fait la une ailleurs — ce n'est pas ce fil qui juge la gravité d'un
événement, c'est :mod:`modules.gold.geopolitics` qui a déjà défini, thème par
thème, lequel a une prise mesurable sur l'or.

Les thèmes eux-mêmes ne sont donc pas redéfinis ici : ils viennent de
``config/gold.yaml`` (bloc ``geopolitique.themes``), et l'intensité de
couverture déjà calculée par le rapport or du jour est réutilisée telle
quelle plutôt que recalculée une seconde fois par un appel GDELT identique.

Format unifié
-------------
Structure de sortie identique à celle du fil quantique (``feed.CLES_ITEM``,
``feed.CATEGORIES``). Le champ ``tickers_ou_themes_lies`` porte ici des noms
de thèmes plutôt que des tickers — un usage que le nom du champ anticipait
déjà.

Ce que le filtre de pertinence retient
--------------------------------------
Un article entre dans le fil s'il cite un **mot-clé d'un dossier suivi**
(``config/geopolitique_dossiers.yaml``) ou une **expression de plusieurs
mots** d'un thème générique de ``config/gold.yaml``, dans son titre **ou**
son chapô. Il est alors rangé dans l'onglet du dossier reconnu, ou dans
« Autres » si seul un thème l'a retenu.

Le filtre d'origine n'acceptait que les expressions exactes des requêtes
GDELT, sur le seul titre : il n'a laissé passer qu'un item depuis la
création du fil, alors que ``NewsItem.resume`` était déjà rempli par
``fetch_rss`` et ne servait à rien. Mesuré sur une collecte réelle de 24 h :
3 items retenus par l'ancien filtre, 48 par le nouveau.
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
from modules import quota_llm
from modules.quantum import moves

_LOG: Final = logging.getLogger(__name__)

#: Racine du dépôt, déduite de l'emplacement de ce fichier.
RACINE: Final = Path(__file__).resolve().parents[2]

#: Dossier de publication du fil.
DOSSIER_FEED: Final = RACINE / "reports" / "geopolitique"

#: Historique de tout ce qui a déjà été traité, en JSON par lignes.
FICHIER_HISTORIQUE: Final = DOSSIER_FEED / "feed_historique.jsonl"

#: Fil courant, plus récent en premier.
FICHIER_LATEST: Final = DOSSIER_FEED / "feed_latest.json"

#: Catégories admises. Contrat partagé avec les fils quantique et crypto : les
#: trois valeurs doivent rester identiques dans les trois modules, vérifié
#: par test.
CATEGORIES: Final[tuple[str, ...]] = ("quantique", "crypto", "geopolitique")

#: Clés exactes d'un item du fil. Même contrat que le fil quantique.
CLES_ITEM: Final[tuple[str, ...]] = (
    "id",
    "categorie",
    "titre_affiche",
    "horodatage_utc",
    "source_nom",
    "url_source",
    "a_une_analyse_interne",
    "analyse_interne",
    #: Porte le nom d'affichage des dossiers auxquels l'item se rattache
    #: (config/geopolitique_dossiers.yaml), puis les thèmes génériques. C'est
    #: par ce champ — déjà au contrat, partagé avec le fil quantique — que le
    #: site range l'item dans l'onglet du bon dossier, ou dans « Autres »
    #: quand seul un thème générique l'a retenu.
    "tickers_ou_themes_lies",
    "nouveaute",
)

#: Longueur minimale d'un mot-clé d'un seul mot pour servir de filtre.
#:
#: Les mots-clés des dossiers sont des noms propres distinctifs (« Gaza »,
#: « Iran », « FOMC », les trois plus courts, à quatre lettres) ; en deçà, un
#: mot risquerait de se retrouver par accident dans un titre sans rapport.
LONGUEUR_MIN_MOT_CLE: Final[int] = 4

#: Nombre d'items conservés dans le fil courant.
MAX_ITEMS_FIL: Final[int] = 120

CONSIGNE_FEED: Final = """Tu expliques une actualité géopolitique à un lecteur qui suit l'or comme actif refuge.

RÈGLES ABSOLUES :
1. N'utilise QUE les informations et les nombres du JSON fourni. N'invente aucun chiffre, aucune date, aucun montant.
2. Ne recommande JAMAIS d'acheter, de vendre, de se positionner sur l'or ou un autre actif.
3. Ne dis jamais que l'or va monter ou baisser : dis seulement par quel thème l'actualité passe et si ce thème est déjà bien couvert ou en accélération.
4. Quand une information manque, dis-le au lieu de la contourner.
5. Français simple, trois phrases au maximum, pas de liste.

STRUCTURE : ce qui s'est passé ; à quel thème à canal de transmission vers l'or cela se rattache ; ce que dit la mesure de couverture de ce thème, si elle est disponible."""

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
        _LOG.info("Aucun historique de fil géopolitique : première exécution.")
    except OSError as exc:
        _LOG.warning("Historique du fil géopolitique illisible (%s) : %s", fichier, exc)
    return connus


def _termes_theme(query: str) -> list[str]:
    """Extrait les termes de recherche d'une requête GDELT d'un thème.

    Les requêtes de ``config/gold.yaml`` ont toutes la forme
    ``("terme un" OR "terme deux" OR ...)`` : il suffit de retirer les
    parenthèses englobantes et de couper sur ``OR``.

    Args:
        query: requête GDELT du thème.

    Returns:
        Termes nus, sans guillemets.
    """
    sans_parens = query.strip()
    if sans_parens.startswith("(") and sans_parens.endswith(")"):
        sans_parens = sans_parens[1:-1]
    morceaux = re.split(r"\s+OR\s+", sans_parens)
    return [m.strip().strip('"') for m in morceaux if m.strip()]


# ---------------------------------------------------------------------------
# Collecte
# ---------------------------------------------------------------------------
def collecter(
    configuration: dict[str, Any],
    flux_rss: list[dict[str, Any]] | None = None,
    articles_precollectes: list[Any] | None = None,
    avec_gdelt: bool = True,
) -> list[news.NewsItem]:
    """Rassemble les actualités géopolitiques pertinentes pour l'or.

    Deux canaux : les flux RSS étiquetés géopolitique, et une requête GDELT
    par thème déjà configuré dans ``config/gold.yaml`` — les mêmes requêtes
    que celles utilisées par :func:`modules.gold.geopolitics.analyser_theme`,
    reprises telles quelles plutôt que redéfinies.

    Args:
        configuration: bloc ``geopolitique`` de ``config/gold.yaml``.
        flux_rss: flux déclarés dans ``config/feeds.yaml``.
        articles_precollectes: articles déjà obtenus, pour les tests hors ligne.
        avec_gdelt: ``False`` pour ne collecter que les flux RSS. GDELT est
            la partie lente et limitée en débit de la collecte ; la voie
            rapide (toutes les trente minutes) s'en passe, la voie lente
            l'interroge (voir .github/workflows/).

    Returns:
        Articles dédupliqués, du plus récent au plus ancien.
    """
    if articles_precollectes is not None:
        return news.dedupe(articles_precollectes)

    reglages = dict(configuration.get("feed") or {})
    fenetre = int(reglages.get("fenetre_heures", 24))
    tags_voulus = {str(t).lower() for t in (reglages.get("tags_flux") or ["geopolitique"])}

    articles: list[news.NewsItem] = []

    # Canal 1 : flux RSS étiquetés géopolitique.
    retenus = [
        f for f in (flux_rss or [])
        if tags_voulus & {str(t).lower() for t in (f.get("tags") or [])}
    ]
    if retenus:
        articles.extend(news.fetch_rss(retenus, hours=fenetre))
    else:
        _LOG.info("Aucun flux RSS étiqueté %s.", " / ".join(sorted(tags_voulus)))

    if not avec_gdelt:
        _LOG.info("Collecte sans GDELT : flux RSS seuls.")
        return news.dedupe(articles)

    # Canal 2 : une requête GDELT par thème à canal de transmission connu.
    for theme in configuration.get("themes") or []:
        query = str(theme.get("query", ""))
        if not query:
            continue
        articles.extend(
            news.fetch_gdelt(
                query,
                timespan=f"{fenetre}h",
                max_records=50,
                tags=["geopolitique", str(theme.get("nom", ""))],
            )
        )

    return news.dedupe(articles)


def _contient(texte: str, terme: str) -> bool:
    """Cherche un terme au début d'un mot du texte, les deux déjà normalisés.

    L'ancrage sur un début de mot (et non une sous-chaîne quelconque) évite
    les rencontres fortuites — « Iran » ne doit pas se déclencher sur
    « Tirana » — tout en gardant les formes dérivées, « Iran » retrouvant
    bien « Iranian ». Sans ancrage du tout, le filtre attraperait n'importe
    quel mot contenant les mêmes lettres.

    Args:
        texte: texte normalisé où chercher.
        terme: terme normalisé cherché.

    Returns:
        ``True`` si le terme ouvre un mot du texte.
    """
    if not terme or not texte:
        return False
    return re.search(rf"\b{re.escape(terme)}", texte) is not None


def _dossiers_lies(texte: str, dossiers: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Rattache un texte aux dossiers géopolitiques configurés.

    Args:
        texte: titre et chapô, déjà normalisés et concaténés.
        dossiers: entrées de ``config/geopolitique_dossiers.yaml``.

    Returns:
        ``[{"id", "nom_affiche"}]`` des dossiers cités, dans l'ordre de la
        configuration, sans doublon.
    """
    trouves: list[dict[str, str]] = []
    for dossier in dossiers:
        mots = [_normaliser(str(m)) for m in (dossier.get("mots_cles") or [])]
        mots = [m for m in mots if " " in m or len(m) >= LONGUEUR_MIN_MOT_CLE]
        if any(_contient(texte, m) for m in mots):
            trouves.append({
                "id": str(dossier.get("id", "")),
                "nom_affiche": str(dossier.get("nom_affiche", dossier.get("id", ""))),
            })
    return trouves


def _entites_liees(
    titre: str,
    themes: list[dict[str, Any]],
    resume: str = "",
    dossiers: list[dict[str, Any]] | None = None,
) -> tuple[list[str], list[str]]:
    """Dit à quoi se rattache un article : dossier suivi, ou thème générique.

    Deux canaux de reconnaissance, dans cet ordre :

    * **les mots-clés des dossiers configurés** — « Gaza », « Houthi »,
      « Federal Reserve », « tariffs »… Ce sont des noms propres et des
      expressions du domaine, assez distinctifs pour servir de filtre.
      L'item alimente alors l'onglet de ce dossier ;
    * **les expressions de plusieurs mots des thèmes génériques** de
      ``gold.yaml`` (« military strike », « oil embargo »…), pour ce qui ne
      relève d'aucun dossier suivi mais garde un canal de transmission connu
      vers l'or. L'item va dans « Autres ».

    La recherche porte sur le **titre et le chapô**. Le filtre d'origine ne
    lisait que le titre, et n'acceptait que les expressions exactes des
    requêtes GDELT : en pratique il ne laissait passer qu'un seul item depuis
    la création du fil, alors que ``NewsItem.resume`` était déjà rempli par
    ``fetch_rss`` et ne servait à rien.

    Le garde-fou qui avait motivé ce durcissement reste en place, par
    construction : le mot nu « invasion », qui convient à GDELT — il croise
    le mot avec tout l'article — mais qui avait fait remonter une brève de
    fait divers (« home invasion ») et un débat migratoire, n'est un mot-clé
    d'aucun dossier, et les thèmes génériques n'acceptent toujours que des
    expressions de plusieurs mots.

    Args:
        titre: titre de l'article.
        themes: entrées ``geopolitique.themes`` de ``config/gold.yaml``.
        resume: chapô de l'article, tel que le remplit ``fetch_rss``.
        dossiers: entrées de ``config/geopolitique_dossiers.yaml``.

    Returns:
        Couple ``(entités lisibles, identifiants de dossiers)``. Les entités
        servent à l'affichage et à l'explication ; les identifiants disent au
        site dans quel onglet ranger l'item.
    """
    texte = _normaliser(f"{titre} {resume}")
    rattaches = _dossiers_lies(texte, dossiers or [])
    entites = [d["nom_affiche"] for d in rattaches]

    for theme in themes:
        nom = str(theme.get("nom", ""))
        # Expressions de plusieurs mots seulement : un mot seul est trop
        # générique pour ce test plus grossier que celui de GDELT.
        termes = [
            _normaliser(t) for t in _termes_theme(str(theme.get("query", ""))) if " " in t
        ]
        if nom not in entites and any(_contient(texte, t) for t in termes):
            entites.append(nom)

    return entites, [d["id"] for d in rattaches]


def _est_exclu(titre: str, entites: list[str], exclusions: list[str], resume: str = "") -> bool:
    """Dit si un item porte un motif qu'on ne veut pas voir paraître.

    Le chapô est lu comme le titre : une exclusion qui ne porterait que sur
    le titre laisserait passer ce qu'elle vise dès que le mot se trouve dans
    le corps du chapô — d'autant que la reconnaissance, elle, lit désormais
    les deux.

    Args:
        titre: titre de l'article.
        entites: thèmes et dossiers détectés.
        exclusions: motifs interdits de publication.
        resume: chapô de l'article.

    Returns:
        ``True`` si l'item doit être écarté.
    """
    normalise = _normaliser(f"{titre} {resume}")
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
    themes = donnees.get("entites_liees") or []
    phrases = [f"Actualité relayée par {donnees.get('source_nom', 'une source d’actualité')}."]

    if themes:
        phrases.append(f"Elle relève du thème suivi pour l'or : {', '.join(themes)}.")
    else:
        phrases.append("Elle ne relève d'aucun thème à canal de transmission connu vers l'or.")

    mesure = donnees.get("theme_mesure")
    if mesure and mesure.get("disponible") and mesure.get("intensite_ratio") is not None:
        phrases.append(
            f"Ce thème affiche une couverture de {mesure['intensite_ratio']:.1f}× sa moyenne "
            f"sur 30 jours, trajectoire {mesure.get('trajectoire') or 'non qualifiée'}."
        )
    else:
        phrases.append("Aucune mesure récente de l'intensité de ce thème n'est disponible.")
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
        "actualite_geopolitique",
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
    mesures_par_theme: dict[str, dict[str, Any]] | None = None,
    configuration_explication: dict[str, Any] | None = None,
    client: Any | None = None,
    dossiers: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Transforme les articles collectés en items du fil.

    Args:
        configuration: bloc ``geopolitique`` de ``config/gold.yaml``.
        articles: articles dédupliqués.
        identifiants_connus: identifiants déjà traités lors des exécutions
            précédentes.
        mesures_par_theme: intensité de couverture par thème, telle que déjà
            calculée par le rapport or du jour (``geopolitique.themes``).
        dossiers: dossiers de ``config/geopolitique_dossiers.yaml``. ``None``
            les charge ; une liste vide n'attache l'item à aucun dossier et
            le laisse aux seuls thèmes génériques.
        configuration_explication: réglages de la couche pédagogique.
        client: client OpenAI éventuel.

    Returns:
        Items au format unifié, du plus récent au plus ancien.
    """
    reglages = dict(configuration.get("feed") or {})
    exclusions = [str(e) for e in (reglages.get("exclusions") or [])]
    max_analyses = int(reglages.get("max_analyses_par_execution", 8))
    # Plafond quotidien, commun aux trois fils : le plafond par exécution ne
    # borne plus rien dès que le workflow tourne toutes les trente minutes
    # (voir modules/quota_llm.py).
    reglages_llm = dict(configuration_explication or {})
    plafond_jour = int(reglages_llm.get("max_analyses_par_jour", quota_llm.MAX_ANALYSES_PAR_JOUR))
    budget_jour = quota_llm.restant(plafond_jour)
    if budget_jour < max_analyses:
        _LOG.info(
            "Plafond quotidien d'analyses : %d restante(s) sur %d pour aujourd'hui.",
            budget_jour, plafond_jour,
        )
    max_analyses = min(max_analyses, budget_jour)

    themes = list(configuration.get("themes") or [])
    mesures = dict(mesures_par_theme or {})

    # Import différé : modules.gold.geopolitics importe déjà identifiant_item
    # de ce module ; l'importer ici au niveau du module ferait un cycle.
    if dossiers is None:
        try:
            from modules.gold.geopolitics import charger_dossiers_config

            dossiers = charger_dossiers_config()
        except (ImportError, OSError) as exc:
            _LOG.warning("Dossiers géopolitiques illisibles (%s) : rattachement dégradé.", exc)
            dossiers = []
    dossiers = list(dossiers)

    items: list[dict[str, Any]] = []
    analyses_faites = 0
    analyses_llm = 0

    for article in articles:
        titre = str(getattr(article, "titre", "") or "")
        url = str(getattr(article, "url", "") or "")
        if not titre:
            continue

        resume = str(getattr(article, "resume", "") or "")
        entites, _ = _entites_liees(titre, themes, resume, dossiers)
        if _est_exclu(titre, entites, exclusions, resume):
            _LOG.debug("Item écarté par la liste d'exclusions : %s", titre[:60])
            continue

        # Un item qui ne relève d'aucun thème à canal de transmission connu
        # vers l'or n'a pas sa place dans ce fil, même s'il fait la une
        # ailleurs.
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
            mesure = next((mesures[t] for t in entites if t in mesures), None)
            contexte = {
                "titre": titre,
                "source_nom": str(getattr(article, "source", "") or ""),
                "entites_liees": entites,
                "horodatage_utc": horodatage,
                "theme_mesure": mesure,
            }
            analyse, par_modele = _analyser_item(contexte, configuration_explication, client)
            # Deux compteurs, et ils ne mesurent pas la même chose :
            # ``analyses_faites`` borne le nombre de tentatives de cette
            # exécution ; ``analyses_llm`` ne compte que les appels
            # réellement facturés. Un repli sur le gabarit déterministe —
            # clé absente, couche désactivée, réponse rejetée par le
            # garde-fou — ne coûte rien et ne doit rien consommer du budget
            # quotidien.
            analyses_faites += 1
            analyses_llm += int(bool(par_modele))

        items.append(
            {
                "id": identifiant,
                "categorie": "geopolitique",
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

    # Le compteur du jour n'est incrémenté qu'une fois les analyses faites :
    # une exécution interrompue avant ce point n'aura rien facturé, donc rien
    # à décompter.
    quota_llm.consommer(analyses_llm)

    items.sort(key=lambda i: i["horodatage_utc"], reverse=True)
    _LOG.info(
        "Fil géopolitique : %d item(s), dont %d nouveau(x) et %d analysé(s).",
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
        _LOG.error("Publication du fil géopolitique impossible : %s", exc)
        return None

    _LOG.info("Fil géopolitique publié : %s (%d item(s)).", chemin_latest, len(items))
    return chemin_latest, historique


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------
def _mesures_depuis_le_rapport(chemin: Path) -> dict[str, dict[str, Any]]:
    """Relit l'intensité par thème déjà calculée par le rapport or du jour.

    Réutiliser cette mesure évite un second appel GDELT identique à celui
    que :mod:`modules.gold.geopolitics` a déjà fait dans la même exécution.

    Args:
        chemin: chemin de ``reports/gold/latest.json``.

    Returns:
        Dictionnaire par nom de thème, vide si le rapport est absent,
        illisible, ou si le bloc géopolitique n'est pas disponible.
    """
    try:
        rapport = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    themes = rapport.get("geopolitique", {}).get("themes", [])
    return {
        str(t["nom"]): t
        for t in themes
        if isinstance(t, dict) and t.get("nom") and t.get("disponible")
    }


def main(argv: list[str] | None = None) -> int:
    """Collecte, analyse et publie le fil géopolitique.

    Returns:
        0 si le fil est publié, 1 sinon.
    """
    import argparse

    import yaml

    analyseur = argparse.ArgumentParser(description="Fil d'actualité géopolitique lié à l'or.")
    analyseur.add_argument("--verbeux", action="store_true", help="journalisation détaillée.")
    analyseur.add_argument(
        "--sans-analyse",
        action="store_true",
        help="collecte et publie sans produire d'explication.",
    )
    analyseur.add_argument(
        "--sans-gdelt",
        action="store_true",
        help="ne collecte que les flux RSS : rapide et sans limite de débit.",
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

    configuration_or = _charger(RACINE / "config" / "gold.yaml")
    configuration = dict(configuration_or.get("geopolitique") or {})
    flux = _charger(RACINE / "config" / "feeds.yaml").get("feeds") or []
    reglages_explication = dict(configuration_or.get("explication") or {})
    if arguments.sans_analyse:
        reglages_explication["activee"] = False

    articles = collecter(configuration, flux_rss=flux, avec_gdelt=not arguments.sans_gdelt)
    mesures = _mesures_depuis_le_rapport(RACINE / "reports" / "gold" / "latest.json")
    items = construire_fil(
        configuration,
        articles,
        identifiants_connus=charger_historique(),
        mesures_par_theme=mesures,
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
    print(f"\nFil géopolitique : {len(items)} item(s), {len(nouveaux)} nouveau(x)")
    for item in items[:5]:
        marque = "NOUVEAU" if item["nouveaute"] else "connu  "
        print(f"  [{marque}] {item['titre_affiche'][:72]}")
        print(f"            {', '.join(item['tickers_ou_themes_lies'])}")
    print(f"Écrit dans {chemins[0]}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
