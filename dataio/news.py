"""Veille d'actualité par deux canaux : flux RSS publics et GDELT.

Aucun contenu payant n'est récupéré. Les flux RSS sont ceux que les
éditeurs publient volontairement ; GDELT est un service public de
l'université de Georgetown, ouvert et sans clé.

Les deux canaux sont complémentaires :

* le **RSS** dit ce qui est publié, avec un titre et un résumé lisibles ;
* **GDELT** dit *combien* le monde parle d'un sujet, ce qui permet de
  mesurer une escalade géopolitique au lieu de la deviner à la lecture de
  quelques titres.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final, Iterable

import requests

_LOG: Final = logging.getLogger(__name__)

#: Point d'accès de l'API documentaire de GDELT.
URL_GDELT: Final = "https://api.gdeltproject.org/api/v2/doc/doc"

#: Délai maximal, en secondes, accordé à un appel réseau.
TIMEOUT: Final[float] = float(os.environ.get("HTTP_TIMEOUT", "20"))

#: Délai propre à GDELT, plus long : des lectures de plus de vingt secondes
#: ont été observées sur des requêtes de volume à 30 jours.
TIMEOUT_GDELT: Final[float] = float(os.environ.get("GDELT_TIMEOUT", "40"))

#: Dossier du cache GDELT. Un job GitHub Actions démarre sur un système de
#: fichiers vide : le cache ne survit donc pas d'une exécution à l'autre, il
#: ne sert qu'à mutualiser les appels *au sein d'une même exécution* — la
#: chaîne géopolitique de l'or, le fil quantique et les fils crypto et
#: géopolitique interrogent souvent des requêtes GDELT identiques ou très
#: proches dans la même minute.
CACHE_DIR: Final = Path(
    os.environ.get(
        "GDELT_CACHE_DIR", str(Path(__file__).resolve().parents[1] / ".cache" / "gdelt")
    )
)

#: Durée de vie du cache, en secondes. Assez courte pour ne jamais masquer un
#: vrai changement de couverture, assez longue pour couvrir la poignée de
#: minutes que dure une exécution du workflow.
CACHE_TTL_SECONDES: Final[float] = float(os.environ.get("GDELT_CACHE_TTL_SECONDES", "1800"))

#: Ratio de volume au-delà duquel une couverture est jugée anormale.
SEUIL_ALERTE_INTENSITE: Final[float] = 2.0

#: Plafond imposé par GDELT sur le nombre d'articles renvoyés.
MAX_RECORDS_GDELT: Final[int] = 250

#: Attente initiale, en secondes, après un refus pour dépassement de débit
#: (doublée à chaque tentative : 15, 30, 60 s). À 5 s de base, une requête
#: épuisait encore ses essais en moins d'une minute — observé en local le
#: 11 septembre 2026 ; le quota de GDELT se réarme visiblement plus lentement.
ATTENTE_429_SECONDES: Final[float] = float(os.environ.get("GDELT_ATTENTE_429", "15"))

#: Intervalle minimal entre deux appels GDELT, en secondes.
#:
#: GDELT demande « one request every 5 seconds » et le fait respecter par un
#: 429. Jusqu'ici on déclenchait le limiteur puis on encaissait le refus :
#: sur une exécution qui enchaîne six requêtes (trois dossiers × volume +
#: articles), un dossier au hasard perdait sa mesure — observé en production
#: sur Iran-États-Unis, et sur Israël et Russie lors d'autres exécutions.
#: Espacer volontairement les appels coûte quelques secondes et supprime la
#: cause au lieu de la rattraper. Ce n'est pas une variable de confort : la
#: baisser sous 5 secondes fait réapparaître les 429.
#:
#: Relevé de 5,5 à 8 secondes : à 5,5 s, des 429 persistaient (les
#: exécuteurs GitHub partagent leurs adresses, et le quota de GDELT est par
#: adresse), ainsi que des lectures qui expiraient. Le rapport n'est pas
#: pressé : deux minutes de plus contre des dossiers complets.
INTERVALLE_MIN_GDELT: float = float(os.environ.get("GDELT_INTERVALLE_MIN", "8"))

#: Instant du dernier appel GDELT réellement parti, pour l'espacement.
_dernier_appel_gdelt: float = 0.0

#: Temps total, en secondes, que ce processus accepte de passer à patienter
#: entre deux tentatives GDELT.
#:
#: Pourquoi un budget global, et pas seulement un nombre d'essais par requête
#: -------------------------------------------------------------------------
#: Le repli par requête est bon quand GDELT ne refuse que passagèrement : la
#: deuxième ou la troisième tentative passe. Il devient destructeur quand le
#: refus est systématique, ce qui est le cas sur les exécuteurs GitHub, dont
#: les adresses sont partagées et que GDELT limite durement. Mesuré sur les
#: exécutions CI du 11 septembre 2026 : 53 refus 429, douze requêtes
#: abandonnées après quatre tentatives, six abouties. Chaque abandon coûte
#: 15 + 30 + 60 secondes d'attente, soit vingt et une minutes de pure
#: temporisation — et le job a été tué au bout de ses 45 minutes, à la même
#: étape, trois exécutions de suite, sans jamais rien publier.
#:
#: Un rapport partiel qui paraît vaut mieux qu'un rapport complet qui
#: n'existe pas. Le budget rend cette règle mécanique : tant qu'il reste, une
#: requête a droit à toutes ses tentatives ; une fois épuisé, les suivantes
#: échouent à la première et le dossier concerné est publié comme
#: indisponible, avec son motif. C'est la dégradation gracieuse que le reste
#: du projet applique partout ailleurs.
#:
#: 300 secondes laissent passer environ trois séries complètes de tentatives,
#: assez pour absorber une limitation passagère, trop peu pour dépasser le
#: délai d'expiration du job.
BUDGET_ATTENTE_GDELT: float = float(os.environ.get("GDELT_BUDGET_ATTENTE", "300"))

#: Temps déjà passé à patienter dans ce processus, décompté du budget.
_attente_gdelt_consommee: float = 0.0

#: En-têtes communs aux appels sortants.
#:
#: Note sur la compression : plusieurs flux servis derrière Cloudflare
#: renvoient du Brotli (« Content-Encoding: br ») même quand la requête
#: n'annonce que gzip et deflate — comportement vérifié, et contraire à la
#: négociation attendue. Sans le paquet ``brotli`` installé, requests rend
#: alors les octets compressés tels quels : feedparser échoue sur « not
#: well-formed (invalid token) » et le flux passe pour mort alors qu'il
#: fonctionne. C'est pourquoi ``brotli`` figure dans requirements.txt comme
#: dépendance de plein droit, et non comme un agrément.
_ENTETES: Final[dict[str, str]] = {
    "User-Agent": "Mozilla/5.0 (compatible; veille-marches/1.0)"
}

__all__ = [
    "NewsItem",
    "fetch_rss",
    "fetch_gdelt",
    "construire_requete_gdelt",
    "gdelt_volume_journalier",
    "gdelt_intensity",
    "dedupe",
    "reinitialiser_budget_gdelt",
    "budget_gdelt_restant",
]


def reinitialiser_budget_gdelt() -> None:
    """Rend au processus la totalité de son budget d'attente GDELT.

    Un processus qui démarre part d'un budget neuf ; cette fonction existe
    pour les tests, qui s'exécutent tous dans le même processus et se
    légueraient sinon un budget déjà entamé.
    """
    global _attente_gdelt_consommee
    _attente_gdelt_consommee = 0.0


def budget_gdelt_restant() -> float:
    """Dit combien de secondes d'attente le processus peut encore s'offrir.

    Returns:
        Le solde, jamais négatif.
    """
    return max(BUDGET_ATTENTE_GDELT - _attente_gdelt_consommee, 0.0)


@dataclass(slots=True)
class NewsItem:
    """Un article, quelle que soit sa provenance.

    Attributes:
        titre: titre de l'article.
        url: adresse canonique.
        source: nom lisible de la source (``WSJ Markets``, ``GDELT``...).
        date: date de publication, en UTC. ``None`` si la source ne la donne pas.
        resume: chapeau ou description, nettoyé de son balisage.
        tags: étiquettes thématiques héritées de la configuration du flux.
        poids: fiabilité relative de la source, de 0 à 1. Sert à trancher les
            doublons et à pondérer une synthèse.
        ton: tonalité de l'article, de -100 à +100. Reste ``None`` ici :
            ni le RSS ni le mode ``artlist`` de GDELT ne fournissent cette
            information. Le champ est réservé à un module d'analyse ultérieur.
    """

    titre: str
    url: str
    source: str
    date: datetime | None = None
    resume: str = ""
    tags: list[str] = field(default_factory=list)
    poids: float = 0.5
    ton: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Sérialise l'article pour un rapport ou un fichier JSON.

        Returns:
            Dictionnaire aux types simples.
        """
        return {
            "titre": self.titre,
            "url": self.url,
            "source": self.source,
            "date": None if self.date is None else self.date.isoformat(),
            "resume": self.resume,
            "tags": list(self.tags),
            "poids": self.poids,
            "ton": self.ton,
        }


# ---------------------------------------------------------------------------
# Outils de texte
# ---------------------------------------------------------------------------
_BALISES = re.compile(r"<[^>]+>")
_ESPACES = re.compile(r"\s+")
_PONCTUATION = re.compile(r"[^\w\s]", flags=re.UNICODE)


def _nettoyer_html(texte: str | None) -> str:
    """Retire le balisage HTML et normalise les espaces.

    Args:
        texte: chaîne éventuellement balisée.

    Returns:
        Texte brut, sans balise ni espace superflu.
    """
    if not texte:
        return ""
    return _ESPACES.sub(" ", _BALISES.sub(" ", texte)).strip()


def _normaliser_titre(titre: str) -> str:
    """Réduit un titre à une forme comparable, pour la déduplication.

    Minuscules, accents retirés, ponctuation supprimée, espaces normalisés, et
    suppression du suffixe de source que beaucoup d'agrégateurs ajoutent
    (« ... - Reuters »).

    Args:
        titre: titre d'origine.

    Returns:
        Clé de comparaison.
    """
    sans_suffixe = re.sub(r"\s+[-–|]\s+[^-–|]{1,40}$", "", titre.strip())
    decompose = unicodedata.normalize("NFKD", sans_suffixe.lower())
    sans_accent = "".join(c for c in decompose if not unicodedata.combining(c))
    return _ESPACES.sub(" ", _PONCTUATION.sub(" ", sans_accent)).strip()


def _maintenant() -> datetime:
    """Instant courant en UTC, avec fuseau explicite."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Canal 1 : flux RSS
# ---------------------------------------------------------------------------
def _date_entree(entree: Any) -> datetime | None:
    """Extrait la date de publication d'une entrée feedparser.

    Args:
        entree: entrée renvoyée par feedparser.

    Returns:
        Date en UTC, ou ``None`` si le flux n'en fournit aucune d'exploitable.
    """
    for champ in ("published_parsed", "updated_parsed", "created_parsed"):
        valeur = entree.get(champ) if hasattr(entree, "get") else getattr(entree, champ, None)
        if not valeur:
            continue
        try:
            # feedparser renvoie un time.struct_time exprimé en UTC.
            return datetime(*tuple(valeur)[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
    return None


def fetch_rss(feeds: list[dict[str, Any]], hours: int = 24) -> list[NewsItem]:
    """Récupère les articles récents d'une liste de flux RSS.

    Le téléchargement passe par ``requests`` afin d'imposer un ``timeout`` :
    ``feedparser.parse(url)`` ouvre sa propre connexion, sans délai maximal,
    et peut bloquer indéfiniment sur un serveur muet.

    Args:
        feeds: flux décrits comme dans ``config/feeds.yaml`` : ``name``,
            ``url``, ``tags``, ``weight``.
        hours: fenêtre de fraîcheur. Les articles plus anciens sont écartés.
            Ceux dont la date est inconnue sont conservés, faute de pouvoir
            trancher.

    Returns:
        Liste d'articles, triée du plus récent au plus ancien.
    """
    try:
        import feedparser
    except ImportError:
        _LOG.warning("feedparser n'est pas installé : canal RSS indisponible.")
        return []

    limite = _maintenant() - timedelta(hours=hours)
    articles: list[NewsItem] = []

    for flux in feeds:
        nom = str(flux.get("name", "flux sans nom"))
        url = str(flux.get("url", "")).strip()
        if not url:
            _LOG.warning("Flux « %s » ignoré : URL absente.", nom)
            continue

        tags = list(flux.get("tags") or [])
        poids = float(flux.get("weight", 0.5))

        try:
            reponse = requests.get(url, timeout=TIMEOUT, headers=_ENTETES)
            reponse.raise_for_status()
        except requests.RequestException as exc:
            _LOG.warning("Flux « %s » injoignable : %s", nom, exc)
            continue

        try:
            analyse = feedparser.parse(reponse.content)
        except Exception as exc:  # noqa: BLE001 - un flux mal formé ne doit rien casser
            _LOG.warning("Flux « %s » illisible : %s", nom, exc)
            continue

        if getattr(analyse, "bozo", 0) and not analyse.entries:
            _LOG.warning("Flux « %s » mal formé et vide : %s", nom, getattr(analyse, "bozo_exception", ""))
            continue

        retenus = 0
        for entree in analyse.entries:
            titre = _nettoyer_html(entree.get("title", ""))
            lien = (entree.get("link") or "").strip()
            if not titre or not lien:
                continue

            publie = _date_entree(entree)
            if publie is not None and publie < limite:
                continue

            articles.append(
                NewsItem(
                    titre=titre,
                    url=lien,
                    source=nom,
                    date=publie,
                    resume=_nettoyer_html(entree.get("summary") or entree.get("description"))[:600],
                    tags=tags,
                    poids=poids,
                )
            )
            retenus += 1

        _LOG.debug("Flux « %s » : %d article(s) dans la fenêtre de %d h.", nom, retenus, hours)

    articles.sort(key=lambda a: a.date or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    _LOG.info("RSS : %d article(s) sur %d flux.", len(articles), len(feeds))
    return articles


# ---------------------------------------------------------------------------
# Canal 2 : GDELT
# ---------------------------------------------------------------------------
def _cle_cache_gdelt(parametres: dict[str, Any]) -> str:
    """Calcule la clé de cache d'un jeu de paramètres GDELT.

    Args:
        parametres: paramètres de la requête ``_appel_gdelt``.

    Returns:
        Empreinte hexadécimale stable, indépendante de l'ordre des clés.
    """
    brut = json.dumps(parametres, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()


def _lire_cache_gdelt(cle: str) -> dict[str, Any] | None:
    """Relit une réponse GDELT mise en cache, si elle est encore fraîche.

    Une erreur de lecture ou un cache expiré vaut absence : l'appelant
    referra l'appel réseau, la dégradation est donc sans risque.

    Args:
        cle: clé calculée par :func:`_cle_cache_gdelt`.

    Returns:
        La charge JSON mise en cache, ou ``None``.
    """
    fichier = CACHE_DIR / f"{cle}.json"
    try:
        age = time.time() - fichier.stat().st_mtime
        if age > CACHE_TTL_SECONDES:
            return None
        return json.loads(fichier.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _ecrire_cache_gdelt(cle: str, charge: dict[str, Any]) -> None:
    """Enregistre une réponse GDELT dans le cache.

    Un échec d'écriture (dossier en lecture seule, disque plein) ne doit
    jamais interrompre l'appelant : seule la mutualisation est perdue.

    Args:
        cle: clé calculée par :func:`_cle_cache_gdelt`.
        charge: charge JSON à mettre en cache.
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / f"{cle}.json").write_text(
            json.dumps(charge, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        _LOG.debug("Cache GDELT non écrit (%s) : sans effet sur le résultat.", exc)


def _espacer_appels_gdelt() -> None:
    """Attend, si nécessaire, avant de laisser partir un appel GDELT.

    Garantit :data:`INTERVALLE_MIN_GDELT` secondes entre deux appels partis
    du processus, quel que soit l'appelant — les dossiers géopolitiques, les
    trois fils d'actualité et la chaîne de transmission passent tous par ici.
    Le cache court-circuite cette attente : un appel servi depuis le cache ne
    part pas sur le réseau, il n'a donc rien à espacer.
    """
    global _dernier_appel_gdelt
    if INTERVALLE_MIN_GDELT <= 0:
        return
    attente = INTERVALLE_MIN_GDELT - (time.monotonic() - _dernier_appel_gdelt)
    if attente > 0:
        _LOG.debug("Espacement GDELT : attente de %.1f s.", attente)
        time.sleep(attente)
    _dernier_appel_gdelt = time.monotonic()


def _appel_gdelt(parametres: dict[str, Any], essais: int = 4) -> dict[str, Any] | None:
    """Appelle l'API GDELT et renvoie la charge JSON.

    GDELT limite le débit sans l'annoncer et répond alors par un code 429 ;
    il lui arrive aussi de ne pas répondre à temps ou de renvoyer une erreur
    de serveur passagère. Ces trois cas sont réessayés avec une attente
    exponentielle : ils s'arrangent en patientant. Une erreur client autre
    qu'un 429 (requête invalide) ne l'est pas — elle ne s'arrangerait pas.

    Auparavant seul le 429 était réessayé : un délai dépassé faisait perdre
    un dossier géopolitique entier au premier coup, ce qui a été observé en
    production sur Russie - Ukraine malgré l'espacement des appels.

    Le repli est borné par un **budget d'attente commun à tout le processus**
    (:data:`BUDGET_ATTENTE_GDELT`). Réessayer indéfiniment requête par requête
    ne répare rien quand GDELT refuse systématiquement, et coûte alors plus
    que l'exécution n'a de temps : trois exécutions CI de suite ont été tuées
    à leur délai d'expiration sans rien publier. Une fois le budget épuisé,
    les requêtes suivantes échouent à la première tentative et leur dossier
    paraît comme indisponible, avec son motif.

    Un cache disque de courte durée (voir :data:`CACHE_TTL_SECONDES`) évite
    de répéter le même appel plusieurs fois dans la même exécution : la
    chaîne de transmission géopolitique de l'or et les fils quantique, crypto
    et géopolitique interrogent souvent des requêtes identiques ou très
    proches.

    Args:
        parametres: paramètres de requête.
        essais: nombre maximal de tentatives.

    Returns:
        Dictionnaire JSON, ou ``None`` en cas d'échec.
    """
    cle = _cle_cache_gdelt(parametres)
    en_cache = _lire_cache_gdelt(cle)
    if en_cache is not None:
        _LOG.debug("GDELT « %s » servi depuis le cache.", parametres.get("query", "")[:60])
        return en_cache

    global _attente_gdelt_consommee

    def _patienter(secondes: float, motif: str, tentative: int) -> bool:
        """Attend entre deux tentatives, si le budget du processus le permet.

        Args:
            secondes: attente demandée par le repli exponentiel.
            motif: ce que GDELT a répondu, pour le journal.
            tentative: numéro de la tentative qui vient d'échouer.

        Returns:
            ``True`` si l'attente a eu lieu et qu'une nouvelle tentative doit
            suivre, ``False`` si le budget est épuisé et qu'il faut renoncer.
        """
        global _attente_gdelt_consommee
        if _attente_gdelt_consommee + secondes > BUDGET_ATTENTE_GDELT:
            _LOG.warning(
                "Budget d'attente GDELT épuisé (%.0f s) : %s abandonné sans réessayer. "
                "Le dossier concerné sera publié comme indisponible plutôt que de "
                "faire dépasser l'exécution.",
                BUDGET_ATTENTE_GDELT, motif,
            )
            return False
        _attente_gdelt_consommee += secondes
        _LOG.info(
            "GDELT %s : nouvelle tentative dans %.0f s (%d/%d), budget d'attente %.0f/%.0f s.",
            motif, secondes, tentative, essais, _attente_gdelt_consommee, BUDGET_ATTENTE_GDELT,
        )
        time.sleep(secondes)
        return True

    reponse = None
    essais = max(int(essais), 1)
    for tentative in range(1, essais + 1):
        attente = ATTENTE_429_SECONDES * (2 ** (tentative - 1))
        try:
            _espacer_appels_gdelt()
            reponse = requests.get(URL_GDELT, params=parametres, timeout=TIMEOUT_GDELT, headers=_ENTETES)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if tentative < essais and _patienter(
                attente, f"n'a pas répondu à temps ({parametres.get('mode')})", tentative
            ):
                continue
            _LOG.warning("GDELT injoignable (%s) après %d tentative(s) : %s", parametres.get("mode"), tentative, exc)
            return None
        except requests.RequestException as exc:
            _LOG.warning("GDELT injoignable (%s) : %s", parametres.get("mode"), exc)
            return None

        passager = reponse.status_code == 429 or reponse.status_code >= 500
        if passager and tentative < essais and _patienter(
            attente, f"répond {reponse.status_code}", tentative
        ):
            continue
        try:
            reponse.raise_for_status()
        except requests.RequestException as exc:
            _LOG.warning("GDELT refuse la requête (%s) : %s", parametres.get("mode"), exc)
            return None
        break

    if reponse is None:
        return None

    # GDELT répond parfois en texte brut pour signaler une requête invalide.
    try:
        charge = reponse.json()
    except ValueError:
        extrait = reponse.text.strip()[:200]
        _LOG.warning("Réponse GDELT non JSON (%s) : %s", parametres.get("mode"), extrait)
        return None

    _ecrire_cache_gdelt(cle, charge)
    return charge


def _date_gdelt(valeur: str | None) -> datetime | None:
    """Interprète un horodatage GDELT du type ``20260907T113000Z``.

    Args:
        valeur: horodatage brut.

    Returns:
        Date en UTC, ou ``None`` si le format est inattendu.
    """
    if not valeur:
        return None
    for motif in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ", "%Y%m%d%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(valeur, motif).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def construire_requete_gdelt(termes: list[str]) -> str:
    """Assemble une liste de termes en une requête GDELT valide.

    Deux règles de la syntaxe GDELT, apprises de ses messages d'erreur et non
    de sa documentation :

    * un terme **entre guillemets** doit être assez long, sinon l'API répond
      « The specified phrase is too short ». Un mot isolé court comme ``IonQ``
      doit donc rester **sans** guillemets, tandis qu'une expression de
      plusieurs mots en a besoin pour être cherchée telle quelle ;
    * un mot contenant un tiret ou un point doit en revanche être mis entre
      guillemets malgré sa brièveté, faute de quoi l'API répond « One or more
      of your keywords contained an illegal character » — c'est le cas de
      ``D-Wave`` ;
    * les parenthèses ne sont admises qu'autour d'alternatives ``OR`` — d'où
      le message « Parentheses may only be used around OR'd statements ». On
      ne parenthèse donc jamais un ``AND``.

    Args:
        termes: mots-clés à combiner en alternative.

    Returns:
        La requête, vide si aucun terme exploitable n'est fourni.
    """
    morceaux: list[str] = []
    for terme in termes:
        propre = str(terme).strip()
        if not propre:
            continue
        # Trois cas, tous dictés par des refus observés de l'API :
        #   - expression de plusieurs mots : guillemets obligatoires ;
        #   - mot contenant un tiret ou un point : guillemets obligatoires
        #     aussi, GDELT répondant sinon « One or more of your keywords
        #     contained an illegal character », ce que « D-Wave » déclenche ;
        #   - mot simple : surtout pas de guillemets, un terme court entre
        #     guillemets étant rejeté comme « phrase too short ».
        a_besoin_de_guillemets = " " in propre or any(c in propre for c in "-.&/")
        morceaux.append(f'"{propre}"' if a_besoin_de_guillemets else propre)

    if not morceaux:
        return ""
    if len(morceaux) == 1:
        return morceaux[0]
    return "(" + " OR ".join(morceaux) + ")"


def fetch_gdelt(
    query: str,
    timespan: str = "24h",
    max_records: int = 100,
    poids: float = 0.4,
    tags: list[str] | None = None,
) -> list[NewsItem]:
    """Récupère la liste des articles GDELT correspondant à une requête.

    Aucune clé n'est requise.

    Args:
        query: requête GDELT. La syntaxe accepte les guillemets et les
            opérateurs ``AND`` / ``OR``.
        timespan: fenêtre temporelle (``24h``, ``7d``, ``1w``...).
        max_records: nombre d'articles souhaité, plafonné à 250 par GDELT.
        poids: fiabilité attribuée aux articles issus de ce canal.
        tags: étiquettes thématiques à propager.

    Returns:
        Liste d'articles, vide en cas d'échec.
    """
    if not query.strip():
        _LOG.warning("Requête GDELT vide : appel ignoré.")
        return []

    demande = min(max(int(max_records), 1), MAX_RECORDS_GDELT)
    if max_records > MAX_RECORDS_GDELT:
        _LOG.info("GDELT plafonne à %d articles : demande ramenée.", MAX_RECORDS_GDELT)

    charge = _appel_gdelt(
        {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": demande,
            "timespan": timespan,
            "sort": "datedesc",
        }
    )
    if charge is None:
        return []

    articles: list[NewsItem] = []
    for brut in charge.get("articles", []):
        titre = _nettoyer_html(brut.get("title", ""))
        lien = (brut.get("url") or "").strip()
        if not titre or not lien:
            continue
        articles.append(
            NewsItem(
                titre=titre,
                url=lien,
                source=f"GDELT/{brut.get('domain', 'inconnu')}",
                date=_date_gdelt(brut.get("seendate")),
                resume="",
                tags=list(tags or []),
                poids=poids,
            )
        )

    _LOG.info("GDELT « %s » (%s) : %d article(s).", query[:60], timespan, len(articles))
    return articles


def gdelt_volume_journalier(
    query: str, timespan: str = "30d"
) -> tuple[dict[str, float], str]:
    """Renvoie le volume d'articles GDELT agrégé par jour.

    Le comptage passe par le mode ``timelinevolraw``, qui donne le volume
    réel. Compter les entrées de ``artlist`` plafonnerait à 250 et rendrait
    deux sujets très couverts indistinguables.

    Args:
        query: requête GDELT.
        timespan: fenêtre temporelle (``7d``, ``30d``...).

    Returns:
        Couple ``(volumes, motif)``. ``volumes`` associe une date ``AAAA-MM-JJ``
        à un nombre d'articles ; il est vide en cas d'échec, et ``motif`` dit
        alors pourquoi.
    """
    charge = _appel_gdelt(
        {
            "query": query,
            "mode": "timelinevolraw",
            "format": "json",
            "timespan": timespan,
        }
    )
    if charge is None:
        return {}, "GDELT n'a pas répondu."

    series = charge.get("timeline") or []
    if not series:
        return {}, "Aucune série de volume renvoyée par GDELT."

    # timelinevolraw renvoie le compte d'articles du sujet et, séparément, le
    # total surveillé. Seule la première nous intéresse.
    choisie = next(
        (s for s in series if "total" not in str(s.get("series", "")).lower()),
        series[0],
    )
    points = choisie.get("data") or []
    if not points:
        return {}, "Série de volume vide."

    # Les intervalles varient selon la fenêtre demandée : on agrège par jour.
    par_jour: dict[str, float] = {}
    for point in points:
        horodatage = _date_gdelt(point.get("date"))
        if horodatage is None:
            continue
        try:
            valeur = float(point.get("value", 0.0))
        except (TypeError, ValueError):
            continue
        jour = horodatage.strftime("%Y-%m-%d")
        par_jour[jour] = par_jour.get(jour, 0.0) + valeur

    if not par_jour:
        return {}, "Aucun point de volume horodaté correctement."
    return par_jour, ""


def gdelt_intensity(
    query: str,
    seuil: float = SEUIL_ALERTE_INTENSITE,
    volumes: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Mesure l'intensité de couverture d'un sujet sur les dernières 24 heures.

    Le volume des dernières 24 heures est rapporté à la moyenne journalière
    des 30 derniers jours. Un ratio supérieur à ``seuil`` signale une
    escalade : le sujet occupe soudain deux fois plus de place que d'ordinaire.

    Le comptage passe par le mode ``timelinevolraw``, qui renvoie le volume
    réel d'articles. Compter les entrées de ``artlist`` donnerait un résultat
    faux dès que le sujet dépasse 250 articles, puisque la liste est plafonnée
    à ce nombre : deux sujets très couverts sembleraient également couverts.

    Args:
        query: requête GDELT.
        seuil: ratio au-delà duquel l'alerte est levée.
        volumes: volumes journaliers déjà obtenus par
            :func:`gdelt_volume_journalier`. Fournis, aucune requête n'est
            émise.

    Returns:
        Dictionnaire : ``query``, ``volume_24h``, ``moyenne_journaliere_30j``,
        ``ratio``, ``alerte``, ``jours_observes``, ``disponible``, ``commentaire``.
    """
    resultat: dict[str, Any] = {
        "query": query,
        "volume_24h": 0,
        "moyenne_journaliere_30j": 0.0,
        "ratio": None,
        "alerte": False,
        "jours_observes": 0,
        "disponible": False,
        "commentaire": "",
    }

    # Les volumes journaliers portent déjà toute l'information nécessaire.
    # Quand l'appelant les a chargés — c'est le cas de la chaîne géopolitique,
    # qui en a besoin pour la trajectoire —, les réutiliser évite une seconde
    # requête identique, que GDELT refuserait souvent pour dépassement de débit.
    if volumes is None:
        par_jour, motif = gdelt_volume_journalier(query, timespan="30d")
    else:
        par_jour, motif = volumes, ""
    if not par_jour:
        resultat["commentaire"] = motif
        return resultat

    if len(par_jour) < 2:
        resultat["commentaire"] = "Moins de deux jours de données : ratio non calculable."
        return resultat

    jours = sorted(par_jour)
    volume_24h = par_jour[jours[-1]]
    anterieurs = [par_jour[j] for j in jours[:-1]]
    moyenne = sum(anterieurs) / len(anterieurs)

    resultat.update(
        {
            "volume_24h": volume_24h,
            "moyenne_journaliere_30j": moyenne,
            "jours_observes": len(jours),
            "disponible": True,
        }
    )

    if moyenne <= 0.0:
        resultat["commentaire"] = (
            "Aucune couverture sur les 30 jours précédents : tout article récent "
            "constitue déjà une rupture."
        )
        resultat["alerte"] = volume_24h > 0
        return resultat

    ratio = volume_24h / moyenne
    resultat["ratio"] = ratio
    resultat["alerte"] = ratio >= seuil
    resultat["commentaire"] = (
        f"Couverture {ratio:.1f}× la normale sur 24 h ({volume_24h:.0f} articles "
        f"contre {moyenne:.0f} en moyenne) : escalade à surveiller."
        if resultat["alerte"]
        else f"Couverture {ratio:.1f}× la normale : rien d'anormal."
    )
    if resultat["alerte"]:
        _LOG.warning("Intensité GDELT anormale sur « %s » : ratio %.1f.", query[:60], ratio)
    return resultat


# ---------------------------------------------------------------------------
# Déduplication
# ---------------------------------------------------------------------------
def dedupe(items: Iterable[NewsItem]) -> list[NewsItem]:
    """Déduplique des articles sur leur titre normalisé.

    Une même dépêche circule sur plusieurs flux. En cas de doublon, on garde
    l'exemplaire venant de la source au poids le plus élevé, puis, à poids
    égal, le plus récent : autant lire l'information chez l'éditeur le plus
    fiable.

    Args:
        items: articles à dédupliquer.

    Returns:
        Liste dédupliquée, triée du plus récent au plus ancien.
    """
    meilleurs: dict[str, NewsItem] = {}
    doublons = 0

    for article in items:
        cle = _normaliser_titre(article.titre)
        if not cle:
            continue
        tenant = meilleurs.get(cle)
        if tenant is None:
            meilleurs[cle] = article
            continue

        doublons += 1
        if article.poids > tenant.poids:
            meilleurs[cle] = article
        elif article.poids == tenant.poids:
            date_a = article.date or datetime.min.replace(tzinfo=timezone.utc)
            date_t = tenant.date or datetime.min.replace(tzinfo=timezone.utc)
            if date_a > date_t:
                meilleurs[cle] = article

    resultat = sorted(
        meilleurs.values(),
        key=lambda a: a.date or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    if doublons:
        _LOG.info("Déduplication : %d doublon(s) écarté(s).", doublons)
    return resultat
