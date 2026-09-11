"""Fil d'actualité géopolitique : chaque news expliquée dès sa collecte.

Reprend le patron de :mod:`modules.quantum.feed`, avec une différence de
fond : l'entrée dans le fil et le rangement dans un onglet sont deux décisions
distinctes, prises par deux vocabulaires distincts.

Deux décisions, deux vocabulaires
---------------------------------
**Admission** — :mod:`modules.geopolitique.admission`, à partir de
``config/univers_admission.yaml``. Large : un article entre s'il touche
l'univers suivi, qu'il s'agisse d'un actif détenu (or, quantique, crypto),
d'un canal de transmission connu vers ces actifs (pétrole, dollar, taux,
inflation, banques centrales, actions et technologie, semi-conducteurs,
matières premières, banques et crédit, croissance) ou de la géopolitique au
sens large (conflits, sanctions, accords, élections, tensions commerciales).

**Rattachement** — les dossiers de ``config/geopolitique_dossiers.yaml`` et
les thèmes de ``config/gold.yaml``. Strict : un dossier nommé ne vaut que si
ce qu'il contient lui correspond. Un article admis qu'aucun dossier ne prend
va dans « Autres », qui est la destination **normale** de tout ce qui compte
sans rentrer dans une case nommée, et non plus une exception rare.

Jusqu'ici les mots-clés des dossiers assuraient les deux rôles, ce qui rendait
l'admission aussi étroite que le rangement. Le cas mesuré : un séisme sous une
région productrice de cuivre, collecté par le flux USGS, était intégralement
écarté parce que son titre (« M 6.1 - 40 km W of Calama, Chile ») ne contient
aucun mot-clé de dossier.

Le bruit est traité par le classement, pas par le rejet
-------------------------------------------------------
Chaque item porte sa ``portee`` — ``actif_direct``, ``influence`` ou
``contexte`` — c'est-à-dire la distance du domaine le plus proche des actifs
suivis. Un article admis sans lien mesurable avec l'un d'eux est rangé plus
bas, jamais jeté. Le site s'en sert pour ordonner l'onglet « Autres ».

Les thèmes ne sont pas redéfinis ici : ils viennent de ``config/gold.yaml``
(bloc ``geopolitique.themes``), et l'intensité de couverture déjà calculée par
le rapport or du jour est réutilisée telle quelle plutôt que recalculée une
seconde fois par un appel GDELT identique.

Format unifié
-------------
Structure de sortie identique à celle du fil quantique (``feed.CLES_ITEM``,
``feed.CATEGORIES``). Le champ ``tickers_ou_themes_lies`` porte ici des noms
de thèmes plutôt que des tickers — un usage que le nom du champ anticipait
déjà.

Ce que le lecteur voit dans ``tickers_ou_themes_lies``
------------------------------------------------------
Le nom d'affichage des dossiers reconnus, puis les thèmes génériques. Quand
ni l'un ni l'autre ne prend l'article, le champ porte les **domaines
d'admission** : c'est ce qui dit au lecteur par quoi l'article le concerne,
plutôt que de le laisser dans un onglet sans raison affichée.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from dataio import news
from modules.geopolitique import admission as admission_univers
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
    #: Distance du domaine d'admission le plus proche des actifs suivis :
    #: ``actif_direct``, ``influence`` ou ``contexte`` (voir
    #: :data:`modules.geopolitique.admission.ORDRE_PORTEE`). C'est le
    #: classement qui remplace le rejet : un article sans lien mesurable avec
    #: un actif suivi est rangé plus bas, pas jeté.
    "portee",
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

CONSIGNE_FEED: Final = """Tu expliques une actualité à un lecteur qui suit l'or comme actif refuge, et qui suit aussi le quantique et les crypto-actifs.

RÈGLES ABSOLUES :
1. N'utilise QUE les informations et les nombres du JSON fourni. N'invente aucun chiffre, aucune date, aucun montant.
2. Ne recommande JAMAIS d'acheter, de vendre, de se positionner sur l'or ou un autre actif.
3. Ne dis jamais qu'un actif va monter ou baisser : dis seulement par quel sujet l'actualité passe et si ce sujet est déjà bien couvert ou en accélération.
4. Quand une information manque, dis-le au lieu de la contourner.
5. Français simple, trois phrases au maximum, pas de liste.

STRUCTURE : ce qui s'est passé ; à quel sujet suivi cela se rattache, en reprenant EXACTEMENT les rattachements fournis ; ce que dit la mesure de couverture de ce sujet, si elle est disponible.

Si les rattachements fournis ne sont pas des dossiers suivis mais des domaines de l'univers surveillé, dis-le ainsi : l'actualité touche ce domaine sans relever d'un dossier en cours."""

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
#: Normalisation partagée avec l'admission : les deux côtés de toute
#: comparaison — texte de l'article et terme de configuration — doivent passer
#: par la même fonction, sans quoi « l'or » et « l or » ne se rencontreraient
#: jamais. Définie dans :mod:`modules.geopolitique.admission`, qui ne dépend
#: de rien d'autre, pour qu'il n'en existe qu'une version.
_normaliser = admission_univers.normaliser


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

    # Canal 2 : une requête GDELT par thème mesuré (config/gold.yaml).
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


def _rattacher(
    titre: str,
    themes: list[dict[str, Any]],
    resume: str = "",
    dossiers: list[dict[str, Any]] | None = None,
    admission: admission_univers.Admission | None = None,
) -> tuple[list[str], list[str], bool]:
    """Range un article **déjà admis** : dans un dossier, ou dans « Autres ».

    Le rattachement ne décide plus de l'entrée — c'est
    :func:`modules.geopolitique.admission.evaluer` qui s'en charge, sur un
    vocabulaire bien plus large. Cette fonction ne répond qu'à « où le
    mettre ? », et a le droit d'être exigeante pour cela : un dossier nommé ne
    vaut que si ce qu'il contient lui correspond vraiment.

    Trois canaux de rangement, dans cet ordre :

    * **les mots-clés des dossiers configurés** — « Gaza », « Houthi »,
      « Federal Reserve », « tariffs »… L'item alimente l'onglet de ce
      dossier ;
    * **le dossier désigné par un domaine d'admission** (champ ``dossier`` de
      ``config/univers_admission.yaml``). C'est ce canal qui rattrape ce que
      les mots-clés d'un dossier ne savent pas voir : le titre d'un séisme ne
      nomme qu'un pays et une magnitude, jamais le cuivre qu'on y extrait ;
    * **les expressions de plusieurs mots des thèmes génériques** de
      ``gold.yaml`` (« military strike », « oil embargo »…).

    Quand aucun des trois ne prend l'article, le champ affiché reprend les
    **domaines d'admission** : l'item va dans « Autres », mais avec la raison
    de sa présence écrite en toutes lettres plutôt qu'un onglet muet.

    Args:
        titre: titre de l'article.
        themes: entrées ``geopolitique.themes`` de ``config/gold.yaml``.
        resume: chapô de l'article, tel que le remplit ``fetch_rss``.
        dossiers: entrées de ``config/geopolitique_dossiers.yaml``.
        admission: verdict d'admission de l'article, qui porte les domaines
            reconnus et les dossiers vers lesquels ils renvoient.

    Returns:
        Triplet ``(entités lisibles, identifiants de dossiers, sujet mesuré)``.
        Les entités servent à l'affichage et à l'explication ; les identifiants
        disent au site dans quel onglet ranger l'item ; le booléen dit si un
        dossier ou un thème — c'est-à-dire un sujet dont le projet mesure
        l'intensité et la chaîne de transmission — a pris l'article, ce qui
        pèse sur son rang.
    """
    texte = _normaliser(f"{titre} {resume}")
    rattaches = _dossiers_lies(texte, dossiers or [])
    identifiants = [d["id"] for d in rattaches]
    entites = [d["nom_affiche"] for d in rattaches]

    # Dossiers désignés par un domaine d'admission. Le sens est à sens unique
    # et voulu : l'admission peut nourrir un dossier, jamais l'inverse.
    par_identifiant = {str(d.get("id", "")): d for d in (dossiers or [])}
    for identifiant in (admission.dossiers if admission else ()):
        if identifiant in identifiants:
            continue
        dossier = par_identifiant.get(identifiant)
        if dossier is None:
            # Un domaine qui renvoie vers un dossier supprimé ne doit pas
            # faire disparaître l'article : il ira dans « Autres ».
            _LOG.warning(
                "Domaine d'admission renvoyant vers un dossier inconnu (%s) : ignoré.",
                identifiant,
            )
            continue
        identifiants.append(identifiant)
        entites.append(str(dossier.get("nom_affiche") or identifiant))

    for theme in themes:
        nom = str(theme.get("nom", ""))
        # Expressions de plusieurs mots seulement : un mot seul est trop
        # générique pour ce test plus grossier que celui de GDELT.
        termes = [
            _normaliser(t) for t in _termes_theme(str(theme.get("query", ""))) if " " in t
        ]
        if nom not in entites and any(_contient(texte, t) for t in termes):
            entites.append(nom)

    # Un dossier ou un thème l'a pris : l'article relève d'un sujet dont le
    # projet mesure déjà l'intensité et la chaîne de transmission vers l'or.
    sujet_mesure = bool(entites)

    if not entites and admission is not None:
        entites = list(admission.libelles)

    return entites, identifiants, sujet_mesure


def _portee(
    admission: admission_univers.Admission | None,
    sujet_mesure: bool,
) -> str:
    """Donne son rang à un article admis : à quelle distance des actifs suivis.

    Deux sources de rang, et c'est la plus proche qui l'emporte :

    * la **portée du domaine d'admission** le plus proche des actifs détenus ;
    * le fait qu'un **dossier ou un thème** ait pris l'article. Un dossier
      déclare son canal de transmission vers l'or, un thème de ``gold.yaml``
      n'y figure que parce qu'il en a un : dans les deux cas le lien est
      mesuré, donc au moins ``influence``.

    Args:
        admission: verdict d'admission, ou ``None`` si seul un dossier ou un
            thème a reconnu l'article.
        sujet_mesure: vrai si un dossier ou un thème l'a pris.

    Returns:
        ``actif_direct``, ``influence`` ou ``contexte``.
    """
    candidats = ["contexte"]
    if admission is not None:
        candidats.append(admission.portee)
    if sujet_mesure:
        candidats.append("influence")
    return max(candidats, key=lambda p: admission_univers.ORDRE_PORTEE.get(p, 0))


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

    # Le champ porte soit des dossiers et thèmes suivis, soit — quand aucun ne
    # prend l'article — les domaines de l'univers qui l'ont fait entrer. La
    # phrase doit valoir dans les deux cas, sans annoncer un « thème suivi pour
    # l'or » là où il n'y en a pas.
    if themes:
        phrases.append(f"Elle se rattache à : {', '.join(themes)}.")
    else:
        phrases.append("Aucun rattachement n'a pu être établi pour cette actualité.")

    mesure = donnees.get("theme_mesure")
    if mesure and mesure.get("disponible") and mesure.get("intensite_ratio") is not None:
        phrases.append(
            f"Ce sujet affiche une couverture de {mesure['intensite_ratio']:.1f}× sa moyenne "
            f"sur 30 jours, trajectoire {mesure.get('trajectoire') or 'non qualifiée'}."
        )
    else:
        phrases.append("Aucune mesure récente de l'intensité de ce sujet n'est disponible.")
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
    univers: admission_univers.Univers | None = None,
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
        univers: vocabulaire d'admission (``config/univers_admission.yaml``).
            ``None`` le charge une fois pour toute la boucle.
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

    # Chargé une seule fois : la compilation des quelque quatre cents motifs
    # n'a pas à être refaite à chaque article.
    if univers is None:
        univers = admission_univers.charger_univers()

    items: list[dict[str, Any]] = []
    ecartes_hors_univers = 0
    analyses_faites = 0
    analyses_llm = 0

    for article in articles:
        titre = str(getattr(article, "titre", "") or "")
        url = str(getattr(article, "url", "") or "")
        if not titre:
            continue

        resume = str(getattr(article, "resume", "") or "")

        # ADMISSION — la seule question posée ici est « est-ce que ça touche
        # l'univers suivi ? ». Un article qui n'en touche aucun pan n'a rien à
        # faire dans le fil ; tout le reste entre, quitte à être rangé bas.
        admission = admission_univers.evaluer(titre, resume, univers)

        # RATTACHEMENT — une fois admis, l'article est rangé. Sans dossier, il
        # va dans « Autres », ce qui est une destination et non un rejet.
        entites, _, sujet_mesure = _rattacher(titre, themes, resume, dossiers, admission)

        # L'admission est une UNION, jamais un remplacement : le vocabulaire
        # large d'un côté, tout ce qu'un dossier ou un thème reconnaît de
        # l'autre. Sans cette union, ajouter un dossier dont les mots-clés ne
        # figurent pas au vocabulaire — « Gaza », « Houthi » — ferait
        # disparaître son sujet du fil au lieu de l'y faire entrer. Par
        # construction, l'élargissement ne peut donc rien retirer.
        if admission is None and not entites:
            ecartes_hors_univers += 1
            continue

        if _est_exclu(titre, entites, exclusions, resume):
            _LOG.debug("Item écarté par la liste d'exclusions : %s", titre[:60])
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
                "portee": _portee(admission, sujet_mesure),
                "nouveaute": nouveaute,
            }
        )

    # Le compteur du jour n'est incrémenté qu'une fois les analyses faites :
    # une exécution interrompue avant ce point n'aura rien facturé, donc rien
    # à décompter.
    quota_llm.consommer(analyses_llm)

    items.sort(key=lambda i: i["horodatage_utc"], reverse=True)
    repartition = ", ".join(
        f"{portee} {sum(1 for i in items if i['portee'] == portee)}"
        for portee in sorted(admission_univers.ORDRE_PORTEE, key=lambda p: -admission_univers.ORDRE_PORTEE[p])
    )
    _LOG.info(
        "Fil géopolitique : %d item(s), dont %d nouveau(x) et %d analysé(s). "
        "Portée : %s. %d article(s) hors de l'univers suivi.",
        len(items),
        sum(1 for i in items if i["nouveaute"]),
        analyses_faites,
        repartition,
        ecartes_hors_univers,
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
