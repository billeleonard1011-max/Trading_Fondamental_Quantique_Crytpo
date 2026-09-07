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

import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Iterable

import requests

_LOG: Final = logging.getLogger(__name__)

#: Point d'accès de l'API documentaire de GDELT.
URL_GDELT: Final = "https://api.gdeltproject.org/api/v2/doc/doc"

#: Délai maximal, en secondes, accordé à un appel réseau.
TIMEOUT: Final[float] = float(os.environ.get("HTTP_TIMEOUT", "20"))

#: Ratio de volume au-delà duquel une couverture est jugée anormale.
SEUIL_ALERTE_INTENSITE: Final[float] = 2.0

#: Plafond imposé par GDELT sur le nombre d'articles renvoyés.
MAX_RECORDS_GDELT: Final[int] = 250

_ENTETES: Final[dict[str, str]] = {
    "User-Agent": "Mozilla/5.0 (compatible; veille-marches/1.0)"
}

__all__ = [
    "NewsItem",
    "fetch_rss",
    "fetch_gdelt",
    "gdelt_intensity",
    "dedupe",
]


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
def _appel_gdelt(parametres: dict[str, Any]) -> dict[str, Any] | None:
    """Appelle l'API GDELT et renvoie la charge JSON.

    Args:
        parametres: paramètres de requête.

    Returns:
        Dictionnaire JSON, ou ``None`` en cas d'échec.
    """
    try:
        reponse = requests.get(URL_GDELT, params=parametres, timeout=TIMEOUT, headers=_ENTETES)
        reponse.raise_for_status()
    except requests.RequestException as exc:
        _LOG.warning("GDELT injoignable (%s) : %s", parametres.get("mode"), exc)
        return None

    # GDELT répond parfois en texte brut pour signaler une requête invalide.
    try:
        return reponse.json()
    except ValueError:
        extrait = reponse.text.strip()[:200]
        _LOG.warning("Réponse GDELT non JSON (%s) : %s", parametres.get("mode"), extrait)
        return None


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


def gdelt_intensity(query: str, seuil: float = SEUIL_ALERTE_INTENSITE) -> dict[str, Any]:
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

    charge = _appel_gdelt(
        {
            "query": query,
            "mode": "timelinevolraw",
            "format": "json",
            "timespan": "30d",
        }
    )
    if charge is None:
        resultat["commentaire"] = "GDELT n'a pas répondu."
        return resultat

    series = charge.get("timeline") or []
    if not series:
        resultat["commentaire"] = "Aucune série de volume renvoyée par GDELT."
        return resultat

    # timelinevolraw renvoie le compte d'articles du sujet et, séparément, le
    # total surveillé. Seule la première nous intéresse.
    choisie = next(
        (s for s in series if "total" not in str(s.get("series", "")).lower()),
        series[0],
    )
    points = choisie.get("data") or []
    if not points:
        resultat["commentaire"] = "Série de volume vide."
        return resultat

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
        par_jour[horodatage.strftime("%Y-%m-%d")] = par_jour.get(
            horodatage.strftime("%Y-%m-%d"), 0.0
        ) + valeur

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
