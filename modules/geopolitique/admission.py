"""Admission d'un article dans les fils : « est-ce que ça touche mon univers ? »

Pourquoi ce module est séparé du rattachement
---------------------------------------------
Les mots-clés des dossiers (``config/geopolitique_dossiers.yaml``) jouaient
deux rôles à la fois : décider si un article *entre* dans le fil, et décider
dans quel *onglet* le ranger. Les deux questions n'appellent pourtant pas la
même exigence. Le rattachement doit être strict — un dossier nommé ne vaut que
si ce qu'il contient lui correspond. L'admission doit être large — ce qui
concerne le portefeuille dépasse de loin les dossiers nommés.

Les confondre revenait à jeter tout ce qui compte sans rentrer dans une case.
Le cas mesuré : un séisme sous une région productrice de cuivre, collecté par
le flux USGS, était intégralement écarté parce que son titre
(« M 6.1 - 40 km W of Calama, Chile ») ne contient aucun mot-clé de dossier.
L'article n'était pas jugé peu important : il n'était pas jugé du tout.

Ce module ne répond donc qu'à la question de l'entrée. Le rattachement reste
dans :mod:`modules.geopolitique.feed`, à partir des dossiers.

Le classement remplace le rejet
-------------------------------
Chaque domaine déclare sa portée — sa distance aux actifs réellement suivis.
Un article admis sans lien mesurable avec l'un d'eux est rangé plus bas, jamais
écarté. Voir :data:`ORDRE_PORTEE`.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

_LOG: Final = logging.getLogger(__name__)

#: Racine du dépôt, déduite de l'emplacement de ce fichier.
RACINE: Final = Path(__file__).resolve().parents[2]

#: Vocabulaire d'admission, hors du code pour être relu et étendu sans le toucher.
FICHIER_UNIVERS: Final = RACINE / "config" / "univers_admission.yaml"

#: Portées, de la plus proche des actifs suivis à la plus lointaine.
#:
#: C'est l'échelle du classement demandé : un article admis par le seul
#: contexte géopolitique, sans actif ni canal nommé, passe derrière un article
#: qui nomme l'or. Il passe derrière, il ne disparaît pas.
ORDRE_PORTEE: Final[dict[str, int]] = {
    "actif_direct": 3,
    "influence": 2,
    "contexte": 1,
}

#: Portée attribuée à un domaine dont la valeur configurée est inconnue.
#:
#: Le repli est le rang le plus bas, jamais le plus haut : une erreur de
#: configuration ne doit pas promouvoir un article devant ceux qui nomment
#: vraiment un actif suivi.
PORTEE_DEFAUT: Final = "contexte"

#: Longueur minimale d'un terme ordinaire, si la configuration n'en dit rien.
LONGUEUR_MIN_TERME: Final[int] = 4

#: Magnitude sismique minimale par défaut (voir la configuration).
MAGNITUDE_SEISME_MIN: Final[float] = 6.0

#: Reconnaît la magnitude en tête des titres USGS : « M 6.1 - 40 km W of … ».
#:
#: Lu sur le texte BRUT et non normalisé : la normalisation remplace le point
#: décimal par une espace, ce qui transformerait 6.1 en « 6 1 ».
_MAGNITUDE: Final = re.compile(r"\bM\s*(\d{1,2}(?:[.,]\d+)?)\b")

__all__ = [
    "ORDRE_PORTEE",
    "Domaine",
    "Univers",
    "Admission",
    "normaliser",
    "magnitude_sismique",
    "charger_univers",
    "evaluer",
]


def normaliser(texte: str) -> str:
    """Réduit un texte à une forme comparable : minuscules, sans accent.

    La ponctuation devient une espace, ce qui aligne « l'or » et « l or »,
    « coup d'état » et « coup d etat ». Les termes de la configuration passent
    par la même fonction, donc les deux côtés de la comparaison sont traités
    à l'identique.

    Args:
        texte: texte d'origine.

    Returns:
        Texte normalisé, sans ponctuation ni accent.
    """
    decompose = unicodedata.normalize("NFKD", (texte or "").lower())
    sans_accent = "".join(c for c in decompose if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", sans_accent)).strip()


def magnitude_sismique(texte: str) -> float | None:
    """Extrait la magnitude d'un titre de séisme, si le texte en porte une.

    Args:
        texte: titre brut, non normalisé.

    Returns:
        La magnitude, ou ``None`` si le texte n'en annonce aucune.
    """
    trouve = _MAGNITUDE.search(texte or "")
    if not trouve:
        return None
    try:
        return float(trouve.group(1).replace(",", "."))
    except ValueError:
        return None


def _motif(terme: str, exact: bool, longueur_min: int) -> re.Pattern[str] | None:
    """Compile le motif de recherche d'un terme, ou dit qu'il est inutilisable.

    Deux règles, et une seule exclusion :

    * un terme de ``termes_exacts``, ou un terme écrit entièrement en
      majuscules — un sigle — est comparé au **mot entier**. « TIPS » ne doit
      pas se déclencher sur « tips for investors », ni « Mali » sur « Malibu » ;
    * tout autre terme est ancré sur un **début de mot**, comme partout dans le
      projet : « Iran » retrouve « Iranian », jamais « Tirana ».

    Un terme ordinaire plus court que ``longueur_min`` est écarté : c'est le
    sort de « or », dont les deux lettres sont la conjonction anglaise la plus
    courante.

    Args:
        terme: terme tel qu'écrit dans la configuration.
        exact: vrai si le terme vient de ``termes_exacts``.
        longueur_min: longueur minimale d'un terme ordinaire.

    Returns:
        Le motif compilé, ou ``None`` si le terme est écarté.
    """
    brut = str(terme or "").strip()
    normalise = normaliser(brut)
    if not normalise:
        return None
    sigle = brut.isupper()
    if exact or sigle:
        return re.compile(rf"\b{re.escape(normalise)}\b")
    if len(normalise) < longueur_min:
        return None
    return re.compile(rf"\b{re.escape(normalise)}")


@dataclass(frozen=True)
class Domaine:
    """Un pan de l'univers suivi, avec sa distance aux actifs détenus."""

    identifiant: str
    libelle: str
    portee: str
    #: Dossier auquel rattacher un article admis par ce domaine, s'il en existe
    #: un. Seul point de contact avec ``geopolitique_dossiers.yaml``, et il ne
    #: va que dans ce sens : l'admission peut nourrir un dossier, jamais
    #: l'inverse.
    dossier: str = ""
    #: N'admet que si le texte porte aussi une magnitude sismique suffisante.
    exige_seisme: bool = False
    motifs: tuple[re.Pattern[str], ...] = field(default=(), repr=False)

    @property
    def rang(self) -> int:
        """Rang numérique de la portée, pour le classement."""
        return ORDRE_PORTEE.get(self.portee, ORDRE_PORTEE[PORTEE_DEFAUT])


@dataclass(frozen=True)
class Univers:
    """Vocabulaire d'admission chargé, prêt à l'emploi."""

    domaines: tuple[Domaine, ...] = ()
    magnitude_seisme_min: float = MAGNITUDE_SEISME_MIN

    def __bool__(self) -> bool:
        """Vrai si l'univers porte au moins un domaine exploitable."""
        return bool(self.domaines)


@dataclass(frozen=True)
class Admission:
    """Verdict d'admission d'un article.

    Attributes:
        domaines: domaines reconnus, du plus proche des actifs au plus
            lointain.
        portee: portée du domaine le plus proche. C'est elle qui classe.
        dossiers: dossiers vers lesquels les domaines reconnus renvoient.
    """

    domaines: tuple[Domaine, ...]
    portee: str
    dossiers: tuple[str, ...]

    @property
    def rang(self) -> int:
        """Rang numérique de la portée retenue."""
        return ORDRE_PORTEE.get(self.portee, ORDRE_PORTEE[PORTEE_DEFAUT])

    @property
    def libelles(self) -> list[str]:
        """Libellés des domaines reconnus, sans doublon, dans l'ordre du rang."""
        vus: list[str] = []
        for domaine in self.domaines:
            if domaine.libelle not in vus:
                vus.append(domaine.libelle)
        return vus


def charger_univers(chemin: Path | None = None) -> Univers:
    """Lit le vocabulaire d'admission.

    Un fichier absent ou illisible rend un univers vide plutôt que d'échouer :
    la dégradation se lit alors dans le fil — plus rien n'est admis — et le
    journal le dit. Un fil vide est un symptôme visible ; une exception au
    milieu d'une collecte ne l'est pas.

    Args:
        chemin: fichier de configuration. ``None`` retient
            :data:`FICHIER_UNIVERS`.

    Returns:
        L'univers chargé, éventuellement vide.
    """
    import yaml

    fichier = chemin or FICHIER_UNIVERS
    try:
        contenu = yaml.safe_load(fichier.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        _LOG.error("Vocabulaire d'admission illisible (%s) : aucun article ne sera admis. %s", fichier, exc)
        return Univers()

    reglages = dict(contenu.get("reglages") or {})
    try:
        longueur_min = max(int(reglages.get("longueur_min_terme", LONGUEUR_MIN_TERME)), 1)
    except (TypeError, ValueError):
        longueur_min = LONGUEUR_MIN_TERME
    try:
        magnitude_min = float(reglages.get("magnitude_seisme_min", MAGNITUDE_SEISME_MIN))
    except (TypeError, ValueError):
        magnitude_min = MAGNITUDE_SEISME_MIN

    domaines: list[Domaine] = []
    for entree in contenu.get("domaines") or []:
        if not isinstance(entree, dict):
            continue
        identifiant = str(entree.get("id") or "").strip()
        libelle = str(entree.get("libelle") or identifiant).strip()
        if not identifiant or not libelle:
            continue
        portee = str(entree.get("portee") or PORTEE_DEFAUT).strip()
        if portee not in ORDRE_PORTEE:
            _LOG.warning(
                "Portée inconnue « %s » pour le domaine %s : rangé en %s.",
                portee, identifiant, PORTEE_DEFAUT,
            )
            portee = PORTEE_DEFAUT
        motifs = [
            motif
            for terme, exact in (
                [(t, False) for t in (entree.get("termes") or [])]
                + [(t, True) for t in (entree.get("termes_exacts") or [])]
            )
            if (motif := _motif(str(terme), exact, longueur_min)) is not None
        ]
        if not motifs:
            _LOG.warning("Domaine %s sans terme exploitable : ignoré.", identifiant)
            continue
        domaines.append(
            Domaine(
                identifiant=identifiant,
                libelle=libelle,
                portee=portee,
                dossier=str(entree.get("dossier") or "").strip(),
                exige_seisme=bool(entree.get("exige_seisme")),
                motifs=tuple(motifs),
            )
        )

    if not domaines:
        _LOG.error("Vocabulaire d'admission vide (%s) : aucun article ne sera admis.", fichier)
    return Univers(domaines=tuple(domaines), magnitude_seisme_min=magnitude_min)


def evaluer(titre: str, resume: str = "", univers: Univers | None = None) -> Admission | None:
    """Dit si un article entre, et à quelle distance des actifs suivis.

    La recherche porte sur le titre **et** le chapô, comme le rattachement.

    Args:
        titre: titre de l'article, tel que collecté.
        resume: chapô de l'article.
        univers: vocabulaire chargé. ``None`` le charge, ce qui relit le
            fichier — à éviter dans une boucle.

    Returns:
        L'admission, ou ``None`` si l'article ne touche aucun domaine suivi.
    """
    vocabulaire = univers if univers is not None else charger_univers()
    if not vocabulaire:
        return None

    brut = f"{titre} {resume}"
    texte = normaliser(brut)
    if not texte:
        return None
    # Calculée une fois pour tout l'article : le motif est le même pour tous
    # les domaines qui l'exigent.
    magnitude = magnitude_sismique(brut)
    assez_fort = magnitude is not None and magnitude >= vocabulaire.magnitude_seisme_min

    reconnus: list[Domaine] = []
    for domaine in vocabulaire.domaines:
        if domaine.exige_seisme and not assez_fort:
            continue
        if any(motif.search(texte) for motif in domaine.motifs):
            reconnus.append(domaine)

    if not reconnus:
        return None

    # Le domaine le plus proche des actifs suivis donne la portée, et les
    # libellés sont présentés dans cet ordre : le lecteur voit d'abord par quoi
    # l'article le concerne le plus directement.
    reconnus.sort(key=lambda d: -d.rang)
    dossiers: list[str] = []
    for domaine in reconnus:
        if domaine.dossier and domaine.dossier not in dossiers:
            dossiers.append(domaine.dossier)
    return Admission(
        domaines=tuple(reconnus),
        portee=reconnus[0].portee,
        dossiers=tuple(dossiers),
    )
