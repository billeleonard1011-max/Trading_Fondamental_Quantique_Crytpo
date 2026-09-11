"""Chaîne de transmission du risque géopolitique vers l'or.

Le problème que ce module résout
--------------------------------
« Les tensions au Moyen-Orient soutiennent l'or » est une phrase qu'on lit
tous les jours et qui n'aide jamais à décider. Elle ne dit ni de combien, ni
si c'est déjà arrivé dans les prix, ni par quel mécanisme.

Ce module remplace la phrase par une chaîne mesurée. Un choc géopolitique
n'atteint pas l'or directement : il passe par des relais observables, et
chaque relais peut être vérifié séparément.

    événement → pétrole → anticipations d'inflation → taux réels → or

La lecture est celle-ci : un événement fait monter le pétrole ; un pétrole
plus cher relève les anticipations d'inflation ; à taux nominal inchangé,
des anticipations plus hautes font *baisser* le taux réel ; et un taux réel
plus bas réduit le coût de portage de l'or, donc le soutient.

Quand la chaîne est complète, le mouvement de l'or a une explication
vérifiable. Quand elle est rompue — le pétrole monte mais rien ne suit —
c'est que le marché ne croit pas à l'événement, et la hausse de l'or, si
elle existe, repose sur la seule peur. Ces deux situations n'ont pas la même
durée de vie.

L'indicateur qui compte : ``deja_dans_les_prix``
------------------------------------------------
Un thème connu depuis trois semaines dont la prime de risque est déjà à deux
écarts-types n'est plus un catalyseur haussier. Il est devenu une source de
risque baissier : tout le monde a déjà acheté, et il suffit que la tension
retombe pour que la prime s'évapore. Croiser l'ancienneté du thème avec le
z-score de la juste valeur est la seule façon de distinguer une nouvelle qui
va faire monter l'or d'une nouvelle qui l'a déjà fait monter.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Final

import numpy as np
import pandas as pd
import yaml

from dataio import news
from dataio import gdelt_events
from modules.geopolitique import sujets
from modules.geopolitique.feed import identifiant_item
from modules.gold import rotation_geopolitique

_LOG: Final = logging.getLogger(__name__)

#: Horizon de variation de chaque maillon de la chaîne, en séances.
HORIZON_MAILLON: Final[int] = 5

#: Fenêtre d'observation de la trajectoire d'un thème, en jours.
FENETRE_TRAJECTOIRE: Final[int] = 7

#: Profondeur de la couverture demandée à GDELT DOC, en jours. L'API couvre
#: trois mois glissants : les demander en une seule requête donne à la
#: pertinence marché (modules/geopolitique/sujets.py) une soixantaine de
#: séances au lieu d'une vingtaine, sans appel supplémentaire. L'intensité
#: et la trajectoire, elles, restent lues sur les trente derniers jours.
JOURS_PERTINENCE: Final[int] = 90
JOURS_INTENSITE: Final[int] = 30

#: Ancienneté au-delà de laquelle un thème est considéré comme installé.
ANCIENNETE_INSTALLEE: Final[int] = 14

#: Z-score de prime au-delà duquel la prime est jugée déjà payée.
SEUIL_PRIME_PAYEE: Final[float] = 1.5

#: Séries FRED composant la chaîne de transmission.
SERIE_PETROLE: Final = "DCOILWTICO"
SERIE_INFLATION_ANTICIPEE: Final = "T10YIE"
SERIE_TAUX_REEL: Final = "DFII10"

__all__ = [
    "Theme",
    "analyser_theme",
    "chaine_de_transmission",
    "Dossier",
    "charger_dossiers_config",
    "mesurer_dossier",
    "charger_historique_dossiers",
    "publier_historique_dossiers",
    "analyser_dossiers",
]

#: Fichier de configuration des dossiers de conflits (partie B du prompt
#: « sorties par paliers + géopolitique ») : remplace l'ancienne organisation
#: par thèmes économiques abstraits par des conflits nommés et identifiables.
FICHIER_DOSSIERS_DEFAUT: Final = (
    Path(__file__).resolve().parents[2] / "config" / "geopolitique_dossiers.yaml"
)

#: Historique append-only des développements déjà signalés, par dossier —
#: même patron que modules.geopolitique.feed (et modules.quantum.feed avant
#: lui) : un identifiant stable par item, jamais resignalé comme nouveau.
FICHIER_HISTORIQUE_DOSSIERS: Final = (
    Path(__file__).resolve().parents[2] / "reports" / "gold" / "geopolitique_dossiers_historique.jsonl"
)

#: Nombre de développements récents conservés dans le rapport, par dossier.
MAX_DEVELOPPEMENTS_AFFICHES: Final = 5


@dataclass(slots=True, frozen=True)
class Theme:
    """Lecture d'un thème géopolitique.

    Attributes:
        nom: libellé du thème.
        intensite_ratio: couverture des 24 h rapportée à la moyenne 30 jours.
        volume_24h: nombre d'articles sur les dernières 24 heures.
        alerte: ``True`` quand l'intensité dépasse le seuil d'escalade.
        trajectoire: ``en accélération``, ``stable`` ou ``en essoufflement``.
        ratio_trajectoire: moyenne des 3 derniers jours sur les 4 précédents.
        anciennete_jours: nombre de jours consécutifs au-dessus de la normale.
        disponible: ``False`` si GDELT n'a rien renvoyé.
        motif: raison de l'indisponibilité, vide sinon.
    """

    nom: str
    intensite_ratio: float | None = None
    volume_24h: float | None = None
    alerte: bool = False
    trajectoire: str = ""
    ratio_trajectoire: float | None = None
    anciennete_jours: int | None = None
    disponible: bool = False
    motif: str = ""
    #: Couverture journalière sur trente jours, gardée pour la mesure de
    #: pertinence marché (modules/geopolitique/sujets.py) — pas sérialisée.
    volumes: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Sérialise le thème pour le rapport JSON."""
        return {
            "nom": self.nom,
            "disponible": self.disponible,
            "motif": self.motif,
            "intensite_ratio": self.intensite_ratio,
            "volume_24h": self.volume_24h,
            "alerte": self.alerte,
            "trajectoire": self.trajectoire,
            "ratio_trajectoire": self.ratio_trajectoire,
            "anciennete_jours": self.anciennete_jours,
        }


def _trajectoire(
    volumes: dict[str, float],
    fenetre: int = FENETRE_TRAJECTOIRE,
    seuil_acceleration: float = 1.15,
    seuil_essoufflement: float = 0.85,
) -> tuple[str, float | None]:
    """Qualifie l'évolution récente de la couverture d'un thème.

    La fenêtre est coupée en deux : les trois jours les plus récents contre
    les quatre qui précèdent. Comparer un jour au précédent serait trop
    bruité — le volume d'articles s'effondre le week-end, et une simple
    différence jour à jour confondrait le dimanche avec une désescalade.

    Args:
        volumes: volumes journaliers, indexés par date ``AAAA-MM-JJ``.
        fenetre: nombre de jours observés.
        seuil_acceleration: ratio au-delà duquel le thème accélère.
        seuil_essoufflement: ratio en deçà duquel il s'essouffle.

    Returns:
        Couple ``(qualification, ratio)``. Le ratio est ``None`` si la
        fenêtre est trop courte.
    """
    jours = sorted(volumes)[-int(fenetre):]
    if len(jours) < 5:
        return "", None

    recents = [volumes[j] for j in jours[-3:]]
    anterieurs = [volumes[j] for j in jours[:-3]]
    moyenne_anterieure = float(np.mean(anterieurs)) if anterieurs else 0.0
    moyenne_recente = float(np.mean(recents))

    if moyenne_anterieure <= 0.0:
        # Aucune couverture avant : tout volume récent est une rupture nette.
        return ("en accélération", None) if moyenne_recente > 0.0 else ("stable", None)

    ratio = moyenne_recente / moyenne_anterieure
    if ratio >= seuil_acceleration:
        return "en accélération", ratio
    if ratio <= seuil_essoufflement:
        return "en essoufflement", ratio
    return "stable", ratio


def _anciennete(volumes: dict[str, float]) -> int | None:
    """Compte depuis combien de jours le thème est au-dessus de sa normale.

    La normale est la médiane de la fenêtre observée. La médiane est
    préférée à la moyenne parce qu'un seul pic d'actualité suffirait à
    relever la moyenne au-dessus de tous les autres jours, et le thème
    paraîtrait alors n'avoir jamais été actif.

    Args:
        volumes: volumes journaliers, indexés par date ``AAAA-MM-JJ``.

    Returns:
        Nombre de jours consécutifs au-dessus de la médiane, en partant du
        plus récent. ``None`` si la série est trop courte.
    """
    jours = sorted(volumes)
    if len(jours) < 10:
        return None

    valeurs = [volumes[j] for j in jours]
    mediane = float(np.median(valeurs))
    if mediane <= 0.0:
        return None

    compte = 0
    for valeur in reversed(valeurs):
        if valeur > mediane:
            compte += 1
        else:
            break
    return compte


def analyser_theme(
    nom: str,
    query: str,
    fenetre: int = FENETRE_TRAJECTOIRE,
    seuil_acceleration: float = 1.15,
    seuil_essoufflement: float = 0.85,
    volumes: dict[str, float] | None = None,
    intensite: dict[str, Any] | None = None,
) -> Theme:
    """Mesure l'intensité et la trajectoire d'un thème géopolitique.

    Args:
        nom: libellé du thème.
        query: requête GDELT associée.
        fenetre: nombre de jours de la trajectoire.
        seuil_acceleration: ratio d'accélération.
        seuil_essoufflement: ratio d'essoufflement.
        volumes: volumes journaliers déjà chargés. Fournis, ils évitent tout
            appel réseau — c'est ce qui rend le module testable hors ligne.
        intensite: sortie de ``news.gdelt_intensity`` déjà obtenue.

    Returns:
        La lecture du thème, éventuellement marquée indisponible.
    """
    # Les volumes journaliers sont chargés d'abord — sur trois mois, en une
    # seule requête —, puis passés à gdelt_intensity : intensité et
    # trajectoire se calculent sur les trente derniers jours de la même
    # série, la pertinence marché sur la série entière.
    if volumes is None:
        volumes, motif_volumes = news.gdelt_volume_journalier(query, timespan=f"{JOURS_PERTINENCE}d")
    else:
        motif_volumes = ""
    volumes_complets = dict(volumes or {})
    if len(volumes_complets) > JOURS_INTENSITE:
        volumes = {j: volumes_complets[j] for j in sorted(volumes_complets)[-JOURS_INTENSITE:]}
    if intensite is None:
        intensite = news.gdelt_intensity(query, volumes=volumes or None)

    if not intensite.get("disponible") and not volumes:
        return Theme(
            nom=nom,
            motif=intensite.get("commentaire") or motif_volumes or "GDELT indisponible",
        )

    qualification, ratio_trajectoire = _trajectoire(
        volumes, fenetre=fenetre,
        seuil_acceleration=seuil_acceleration,
        seuil_essoufflement=seuil_essoufflement,
    )

    return Theme(
        nom=nom,
        intensite_ratio=intensite.get("ratio"),
        volume_24h=intensite.get("volume_24h"),
        alerte=bool(intensite.get("alerte", False)),
        trajectoire=qualification,
        ratio_trajectoire=ratio_trajectoire,
        anciennete_jours=_anciennete(volumes),
        disponible=True,
        volumes=volumes_complets,
    )


# ---------------------------------------------------------------------------
# Chaîne de transmission
# ---------------------------------------------------------------------------
def _variation(serie: pd.Series | None, horizon: int, en_pourcentage: bool) -> dict[str, Any]:
    """Mesure la variation d'une série sur un horizon.

    Args:
        serie: série observée, triée par date.
        horizon: nombre d'observations de recul.
        en_pourcentage: ``True`` pour un prix, ``False`` pour un taux déjà
            exprimé en points de pourcentage. Une variation de taux se lit en
            points de base, jamais en pourcentage : passer de 0,10 % à 0,20 %
            n'est pas « une hausse de 100 % », c'est « dix points de base ».

    Returns:
        Dictionnaire décrivant le maillon, avec ``disponible`` et ``motif``.
    """
    if serie is None or serie.dropna().empty:
        return {"disponible": False, "motif": "série absente", "valeur": None, "variation": None}

    propre = serie.dropna().sort_index()
    if len(propre) <= horizon:
        return {
            "disponible": False,
            "motif": f"{len(propre)} observation(s), {horizon + 1} requises",
            "valeur": float(propre.iloc[-1]),
            "variation": None,
        }

    courant = float(propre.iloc[-1])
    precedent = float(propre.iloc[-1 - horizon])

    if en_pourcentage:
        variation = None if precedent == 0.0 else (courant / precedent - 1.0) * 100.0
        unite = "%"
    else:
        variation = (courant - precedent) * 100.0
        unite = "points de base"

    return {
        "disponible": True,
        "motif": "",
        "valeur": courant,
        "valeur_precedente": precedent,
        "variation": variation,
        "unite_variation": unite,
        "date": str(pd.Timestamp(propre.index[-1]).date()),
    }


#: Canaux de transmission vers l'or, par nature de dossier.
#:
#: ``petrole`` (conflits, régions) : événement → pétrole → anticipations
#: d'inflation → taux réels → or, chaque maillon avec son sens attendu.
#: ``taux`` (politique monétaire) : événement → taux réels → or ; cohérent
#: quand les deux bougent en sens contraire, quel que soit le sens.
#: ``risque`` (commerce, technologie) : événement → VIX → or ; cohérent
#: quand les deux montent ou baissent ensemble — la fuite vers la sécurité.
CANAUX: Final[dict[str, str]] = {"petrole": "pétrole", "taux": "taux réels", "risque": "aversion au risque"}
SERIE_VIX: Final = "VIXCLS"


def chaine_de_transmission(
    series_macro: pd.DataFrame | None,
    prix_or: pd.Series | None,
    intensite_max: float | None = None,
    horizon: int = HORIZON_MAILLON,
    canal: str = "petrole",
) -> dict[str, Any]:
    """Mesure chaque maillon entre l'événement et l'or.

    Aucun maillon n'est déduit d'un autre : chacun est mesuré sur ses propres
    données. Une chaîne où le pétrole monte mais où les anticipations
    d'inflation ne bougent pas est une information en soi, et le module doit
    pouvoir la montrer plutôt que la lisser.

    Args:
        series_macro: DataFrame FRED contenant idéalement ``DCOILWTICO``,
            ``T10YIE`` et ``DFII10``.
        prix_or: série du prix de l'or.
        intensite_max: intensité du thème le plus actif, premier maillon.
        horizon: nombre de séances de la variation.
        canal: ``petrole``, ``taux`` ou ``risque`` (voir :data:`CANAUX`).

    Returns:
        Dictionnaire décrivant les maillons et la cohérence d'ensemble.
    """
    if canal not in ("petrole", "taux", "risque"):
        canal = "petrole"
    cadre = series_macro if series_macro is not None else pd.DataFrame()

    def _colonne(nom: str) -> pd.Series | None:
        """Extrait une colonne du DataFrame macro si elle existe."""
        return cadre[nom] if nom in cadre.columns else None

    maillons = {
        "1_evenement": {
            "libelle": "Intensité de couverture du thème le plus actif",
            "disponible": intensite_max is not None,
            "motif": "" if intensite_max is not None else "aucun thème mesurable",
            "valeur": intensite_max,
            "unite_variation": "× la normale",
            "variation": None,
        },
        "2_petrole": {
            "libelle": f"Pétrole brut WTI ({SERIE_PETROLE})",
            **_variation(_colonne(SERIE_PETROLE), horizon, en_pourcentage=True),
        },
        "3_inflation_anticipee": {
            "libelle": f"Anticipations d'inflation 10 ans ({SERIE_INFLATION_ANTICIPEE})",
            **_variation(_colonne(SERIE_INFLATION_ANTICIPEE), horizon, en_pourcentage=False),
        },
        "4_taux_reels": {
            "libelle": f"Taux réel 10 ans ({SERIE_TAUX_REEL})",
            **_variation(_colonne(SERIE_TAUX_REEL), horizon, en_pourcentage=False),
        },
        "5_or": {
            "libelle": "Or au comptant",
            **_variation(prix_or, horizon, en_pourcentage=True),
        },
    }
    if canal == "taux":
        maillons = {k: maillons[k] for k in ("1_evenement", "4_taux_reels", "5_or")}
    elif canal == "risque":
        maillons = {
            "1_evenement": maillons["1_evenement"],
            "3_vix": {"libelle": "VIX (aversion au risque)", **_variation(_colonne(SERIE_VIX), horizon, en_pourcentage=True)},
            "5_or": maillons["5_or"],
        }

    # Cohérence : la chaîne « théorique » veut pétrole en hausse, anticipations
    # en hausse, taux réels en baisse, or en hausse. On compte les maillons
    # qui vont dans ce sens, sans jamais forcer le résultat.
    attendus = {
        "2_petrole": 1,
        "3_inflation_anticipee": 1,
        "4_taux_reels": -1,
        "5_or": 1,
    }
    mesures = [
        (cle, maillons[cle]["variation"], sens)
        for cle, sens in attendus.items()
        if cle in maillons and maillons[cle].get("disponible") and maillons[cle].get("variation") is not None
    ]
    conformes = [cle for cle, variation, sens in mesures if variation * sens > 0]

    # Les canaux « taux » et « risque » n'ont pas de sens absolu attendu : ce
    # qui compte est la relation entre les deux maillons mesurés. Taux réels
    # et or en sens contraire, VIX et or dans le même sens : cohérent.
    if canal in ("taux", "risque") and len(mesures) == 2:
        (_, v_amont, _), (_, v_or, _) = mesures
        coherent = (v_amont * v_or < 0) if canal == "taux" else (v_amont * v_or > 0)
        conformes = [cle for cle, _, _ in mesures] if coherent else [mesures[1][0]]

    if not mesures:
        commentaire = "Chaîne non mesurable : aucune série disponible."
        rompue = None
    elif len(conformes) == len(mesures):
        commentaire = (
            f"Chaîne complète sur {len(mesures)} maillon(s) mesuré(s) : le mouvement "
            "de l'or a une explication vérifiable en amont."
        )
        rompue = False
    else:
        # Chaque maillon en rupture est cité avec son libellé lisible et sa
        # variation mesurée — jamais une formulation du type « le marché ne
        # relaie pas l'événement par les canaux habituels » sans le chiffre
        # qui la justifierait, et jamais l'identifiant technique brut
        # (« 2_petrole ») à la place de son libellé.
        details_ruptures = [
            f"{maillons[cle]['libelle']} ({'+' if variation >= 0 else ''}"
            f"{variation:.2f} {maillons[cle].get('unite_variation', '')})"
            for cle, variation, _ in mesures
            if cle not in conformes
        ]
        commentaire = (
            f"Chaîne rompue : {len(conformes)}/{len(mesures)} maillon(s) conformes. "
            f"Ne suivent pas la direction attendue : {', '.join(details_ruptures)}."
        )
        rompue = True

    return {
        "horizon_seances": horizon,
        "canal": canal,
        "maillons": maillons,
        "n_maillons_mesures": len(mesures),
        "n_maillons_conformes": len(conformes),
        "chaine_rompue": rompue,
        "commentaire": commentaire,
    }


# ---------------------------------------------------------------------------
# Déjà dans les prix
# ---------------------------------------------------------------------------
def _deja_dans_les_prix(
    themes: list[Theme],
    z_score_prime: float | None,
    seuil_prime: float = SEUIL_PRIME_PAYEE,
    anciennete_installee: int = ANCIENNETE_INSTALLEE,
) -> dict[str, Any]:
    """Croise l'ancienneté des thèmes avec la prime déjà payée.

    Args:
        themes: thèmes mesurés.
        z_score_prime: z-score du résidu de juste valeur. ``None`` si le
            modèle n'est pas disponible ou pas fiable.
        seuil_prime: z-score au-delà duquel la prime est jugée déjà payée.
        anciennete_installee: ancienneté au-delà de laquelle un thème est
            considéré comme installé.

    Returns:
        Dictionnaire avec ``valeur`` (booléen ou ``None``) et son explication.
    """
    if z_score_prime is None:
        return {
            "valeur": None,
            "disponible": False,
            "motif": "z-score de juste valeur indisponible ou non fiable",
            "commentaire": (
                "Sans mesure de la prime, impossible de dire si le risque "
                "géopolitique est déjà payé."
            ),
        }

    actifs = [t for t in themes if t.disponible and t.anciennete_jours is not None]
    installes = [t for t in actifs if t.anciennete_jours >= anciennete_installee]
    anciennete_max = max((t.anciennete_jours or 0 for t in actifs), default=None)

    prime_elevee = z_score_prime >= seuil_prime
    deja_paye = bool(installes) and prime_elevee

    if deja_paye:
        noms = ", ".join(t.nom for t in installes)
        commentaire = (
            f"Oui. {noms} installé(s) depuis plus de {anciennete_installee} jours "
            f"et prime déjà à {z_score_prime:+.2f} écart-type. Une mauvaise "
            "nouvelle supplémentaire ferait peu monter le prix ; une détente le ferait "
            "beaucoup retomber. L'asymétrie joue contre l'acheteur."
        )
    elif prime_elevee:
        commentaire = (
            f"Partiellement. La prime est élevée ({z_score_prime:+.2f} écart-type) mais "
            "ce dossier n'est pas installé depuis assez longtemps pour l'expliquer : elle "
            "vient d'ailleurs, ou d'un événement trop récent pour être mesuré."
        )
    elif installes:
        commentaire = (
            f"Non. Ce dossier dure depuis plus de {anciennete_installee} jours, mais la "
            f"prime reste modérée ({z_score_prime:+.2f} écart-type) : le marché ne le "
            "valorise pas encore."
        )
    else:
        commentaire = (
            f"Non. Ni dossier installé ni prime élevée ({z_score_prime:+.2f} écart-type) : "
            "le risque géopolitique n'est pas le moteur du moment."
        )

    return {
        "valeur": deja_paye,
        "disponible": True,
        "motif": "",
        "z_score_prime": z_score_prime,
        "seuil_prime": seuil_prime,
        "anciennete_max_jours": anciennete_max,
        "themes_installes": [t.nom for t in installes],
        "commentaire": commentaire,
    }



# ---------------------------------------------------------------------------
# Dossiers de conflits nommés (partie B — remplace l'organisation par thèmes)
# ---------------------------------------------------------------------------
#
# L'ancien découpage par thèmes économiques abstraits (tensions énergétiques,
# conflits majeurs, sanctions, réserves de change) ne disait jamais
# concrètement ce qui se passait dans le monde : un ratio d'intensité sans
# nom de conflit. Cette section le remplace par des dossiers nommés et
# configurables (config/geopolitique_dossiers.yaml), chacun porteur de son
# propre narratif, de sa propre chaîne de transmission vers l'or, et d'un état
# qui persiste d'une exécution à l'autre au lieu d'être recalculé isolément.
#
# Les briques déjà présentes plus haut (Theme, analyser_theme,
# chaine_de_transmission, _deja_dans_les_prix) sont réutilisées telles
# quelles : elles ne savaient déjà rien d'un « thème » économique en
# particulier, seulement d'un nom et d'une requête GDELT — un dossier de
# conflit leur convient tout autant.


#: Nombre de nouvelles mesures accordées à un dossier que GDELT n'a pas servi,
#: et pause avant la première (doublée à chaque fois).
#:
#: Ramené de deux à une. Chaque remesure coûte deux requêtes GDELT par dossier
#: concerné, et ces requêtes-là sont précisément celles que GDELT refuse quand
#: il limite. Elles étaient indispensables tant qu'un échec faisait perdre le
#: dossier ; depuis que la dernière mesure connue est reprise et datée (voir
#: :mod:`modules.gold.rotation_geopolitique`), perdre la mesure du jour ne fait
#: plus perdre le dossier, et insister deux fois de plus ne rachète qu'une
#: fraîcheur d'un jour contre un tiers du budget de requêtes.
REESSAIS_DOSSIER: int = 1
ATTENTE_REESSAI_DOSSIER_SECONDES: float = 20.0


@dataclass(slots=True, frozen=True)
class Dossier:
    """Lecture complète d'un dossier de conflit.

    Attributes:
        id: identifiant stable (voir config/geopolitique_dossiers.yaml) —
            ne change jamais une fois publié, l'historique en dépend.
        nom_affiche: libellé lisible.
        theme: mesure d'intensité et de trajectoire de couverture (réutilise
            :class:`Theme`, alimentée par la même mécanique GDELT DOC).
        n_evenements_bilateraux: nombre d'événements GDELT Events impliquant
            les deux acteurs du dossier, dans le tout dernier export publié
            (un instantané de l'activité récente, pas une tendance — la
            tendance est déjà mesurée par ``theme``). ``None`` si l'export
            Events était indisponible.
        motif_events: motif d'indisponibilité de l'export Events, vide sinon.
        exemple_evenement: un événement bilatéral représentatif, si trouvé.
        developpements_recents: derniers articles nommés et sourcés relevant
            de ce dossier.
        nouveaux_developpements: sous-ensemble de ``developpements_recents``
            pas encore signalé lors d'une exécution précédente.
        etat_actuel: résumé en langage clair, ce qui a changé inclus.
        chaine_de_transmission: impact chiffré sur l'or, maillon par maillon,
            propre à ce dossier (son intensité en est le premier maillon).
        deja_dans_les_prix: la prime de ce dossier semble-t-elle déjà payée
            par le marché (résidu de juste valeur croisé à son ancienneté).
        invalidation: ce qui remettrait en cause cette lecture.
    """

    id: str
    nom_affiche: str
    theme: Theme
    mots_cles: list[str] = field(default_factory=list)
    n_evenements_bilateraux: int | None = None
    motif_events: str = ""
    exemple_evenement: dict[str, Any] | None = None
    developpements_recents: list[dict[str, Any]] = field(default_factory=list)
    nouveaux_developpements: list[dict[str, Any]] = field(default_factory=list)
    etat_actuel: str = ""
    chaine_transmission: dict[str, Any] = field(default_factory=dict)
    deja_dans_les_prix: dict[str, Any] = field(default_factory=dict)
    invalidation: str = ""
    #: ``conflit`` (bilatéral), ``regional`` ou ``thematique``.
    type: str = "conflit"
    #: Codes CAMEO des deux parties (bilatéral) ou des pays de la région :
    #: gardés pour rattacher les paires découvertes même quand les dossiers
    #: sont fournis déjà mesurés.
    acteurs: list[str] = field(default_factory=list)
    pays: list[str] = field(default_factory=list)
    #: Canal de transmission vers l'or (voir :data:`CANAUX`).
    canal: str = "petrole"
    #: Imposé par la configuration : suivi quoi que mesure le système.
    epingle: bool = False
    #: Coïncidence entre pics de couverture et mouvements de marché
    #: (modules/geopolitique/sujets.py::pertinence_marche).
    pertinence: dict[str, Any] = field(default_factory=dict)
    #: Statut et rang dans le classement des sujets.
    classement: dict[str, Any] = field(default_factory=dict)
    #: Paires d'acteurs découvertes dans l'export Events et rattachées à ce
    #: dossier (régional surtout).
    sujets_rattaches: list[dict[str, Any]] = field(default_factory=list)
    #: Vrai quand la mesure publiée n'est pas celle du jour mais la dernière
    #: connue, reprise faute d'avoir pu interroger GDELT (rotation, ou refus).
    reprise: bool = False
    #: Jour où GDELT a réellement servi cette mesure, au format ``AAAA-MM-JJ``.
    #: Propagé tel quel d'une reprise à l'autre : sans cela, une reprise de
    #: reprise paraîtrait fraîche.
    mesure_du: str = ""
    #: Âge de la mesure en jours. ``0`` pour une mesure du jour, ``None``
    #: quand aucune mesure n'est disponible.
    age_mesure_jours: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Sérialise le dossier pour le rapport JSON."""
        return {
            "id": self.id,
            "nom_affiche": self.nom_affiche,
            # Publiés pour que le site puisse répartir les événements
            # géopolitiques qui ne relèvent d'aucun dossier configuré vers
            # l'onglet « Autres » (partie B4) — sans redéfinir les mots-clés
            # une seconde fois côté site.
            "mots_cles": self.mots_cles,
            "disponible": self.theme.disponible,
            "motif": self.theme.motif,
            "intensite_ratio": self.theme.intensite_ratio,
            "volume_24h": self.theme.volume_24h,
            "trajectoire": self.theme.trajectoire,
            "anciennete_jours": self.theme.anciennete_jours,
            "n_evenements_bilateraux": self.n_evenements_bilateraux,
            "motif_events": self.motif_events,
            "exemple_evenement": self.exemple_evenement,
            "developpements_recents": self.developpements_recents,
            "n_nouveaux_developpements": len(self.nouveaux_developpements),
            "nouveaux_developpements": self.nouveaux_developpements,
            "etat_actuel": self.etat_actuel,
            "chaine_de_transmission": self.chaine_transmission,
            "deja_dans_les_prix": self.deja_dans_les_prix,
            "invalidation": self.invalidation,
            "type": self.type,
            "canal": self.canal,
            "epingle": self.epingle,
            "pertinence": self.pertinence,
            "classement": self.classement,
            "sujets_rattaches": self.sujets_rattaches,
            # Fraîcheur de la mesure. Même convention que le positionnement
            # COT, publié avec son `age_jours` : un chiffre daté vaut mieux
            # qu'un « indisponible », à condition que la date paraisse.
            "reprise": self.reprise,
            "mesure_du": self.mesure_du,
            "age_mesure_jours": self.age_mesure_jours,
        }


def charger_dossiers_config(chemin: Path | None = None) -> list[dict[str, Any]]:
    """Charge la liste des dossiers configurés.

    Args:
        chemin: fichier YAML. ``None`` retient :data:`FICHIER_DOSSIERS_DEFAUT`.

    Returns:
        Liste de dossiers bruts, vide si le fichier manque ou est malformé —
        dégradation, jamais d'exception.
    """
    fichier = chemin or FICHIER_DOSSIERS_DEFAUT
    try:
        with fichier.open("r", encoding="utf-8") as flux:
            contenu = yaml.safe_load(flux) or {}
    except (OSError, yaml.YAMLError) as exc:
        _LOG.error("Configuration des dossiers géopolitiques illisible (%s) : %s", fichier, exc)
        return []
    return list(contenu.get("dossiers") or [])


def charger_reglages_decouverte(chemin: Path | None = None) -> dict[str, Any]:
    """Charge le bloc ``decouverte`` de la configuration (vide si absent)."""
    fichier = chemin or FICHIER_DOSSIERS_DEFAUT
    try:
        with fichier.open("r", encoding="utf-8") as flux:
            contenu = yaml.safe_load(flux) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return dict(contenu.get("decouverte") or {})


def _etat_actuel_dossier(
    nom: str, theme: Theme, nouveaux: list[dict[str, Any]], developpements: list[dict[str, Any]]
) -> str:
    """Rédige l'état actuel d'un dossier, ce qui a changé inclus.

    Chaque phrase s'appuie sur un chiffre mesuré — jamais une formulation
    vague du type « le marché ne relaie pas l'événement par les canaux
    habituels » sans rien pour l'étayer.

    Args:
        nom: libellé du dossier.
        theme: mesure d'intensité et de trajectoire.
        nouveaux: développements pas encore signalés.
        developpements: tous les développements récents retenus.

    Returns:
        Le résumé, en français.
    """
    if not theme.disponible:
        return f"Aucune mesure disponible pour {nom} : {theme.motif or 'GDELT indisponible'}."

    phrases = [
        f"Couverture à {theme.intensite_ratio:.1f}× sa moyenne sur 30 jours, "
        f"trajectoire {theme.trajectoire or 'non qualifiée'}."
    ]
    if theme.anciennete_jours:
        phrases.append(
            f"Au-dessus de sa normale depuis {theme.anciennete_jours} jour(s) consécutif(s)."
        )
    if nouveaux:
        plus_recent = nouveaux[0]
        suite = f" et {len(nouveaux) - 1} autre(s)" if len(nouveaux) > 1 else ""
        phrases.append(
            f"{len(nouveaux)} développement(s) nouveau(x) depuis la dernière vérification, "
            f"dont « {plus_recent['titre']} » ({plus_recent['source']}){suite}."
        )
    elif developpements:
        phrases.append("Rien de nouveau depuis la dernière vérification.")
    else:
        phrases.append("Aucun article récent trouvé sur ce dossier.")
    return " ".join(phrases)


def _invalidation_dossier(theme: Theme, chaine: dict[str, Any]) -> str:
    """Dit ce qui remettrait en cause la lecture d'un dossier.

    Args:
        theme: mesure d'intensité et de trajectoire.
        chaine: sortie de :func:`chaine_de_transmission` pour ce dossier.

    Returns:
        Le motif d'invalidation, en français, appuyé sur les chiffres mesurés.
    """
    if not theme.disponible:
        return "Sans mesure de couverture, aucune invalidation ne peut être formulée."

    morceaux = [
        f"Cette lecture s'appuie sur une couverture de {theme.intensite_ratio:.1f}× la normale : "
        "un retour sous 1,0× (couverture redevenue normale) la viderait de sa justification."
    ]
    if chaine.get("chaine_rompue") is False:
        morceaux.append(
            "La chaîne de transmission est cohérente sur "
            f"{chaine.get('n_maillons_conformes')}/{chaine.get('n_maillons_mesures')} maillon(s) mesuré(s) : "
            "un maillon qui cesse de suivre (pétrole ou taux réels, notamment) romprait cette cohérence."
        )
    elif chaine.get("chaine_rompue") is True:
        morceaux.append(
            "La chaîne de transmission est déjà rompue "
            f"({chaine.get('n_maillons_conformes')}/{chaine.get('n_maillons_mesures')} maillon(s) conformes) : "
            "elle redeviendrait significative si les maillons manquants se remettaient à suivre."
        )
    return " ".join(morceaux)


def _reprendre(
    cfg: dict[str, Any],
    connue: rotation_geopolitique.Mesure | None,
    mesurer: Callable[..., Dossier],
) -> Dossier:
    """Publie un dossier hors du lot : sa dernière mesure, ou rien, mais daté.

    Trois cas, et aucun n'appelle le réseau :

    * **mesure connue et encore valable** — elle est republiée telle quelle,
      avec son âge. C'est le principe déjà appliqué au positionnement COT,
      publié avec quatre jours de retard et son ``age_jours`` : un chiffre
      daté vaut mieux qu'un « indisponible » ;
    * **mesure trop ancienne** — au-delà d'une semaine, l'intensité ne dit
      plus rien du jour. Le dossier est publié indisponible, et le motif
      donne la date et l'âge plutôt qu'un vague « GDELT indisponible » ;
    * **aucune mesure** — démarrage à froid. Le motif dit que le tour du
      dossier n'est pas encore venu, ce qui se résout en un cycle.

    Args:
        cfg: entrée de ``config/geopolitique_dossiers.yaml``.
        connue: dernière mesure enregistrée, ou ``None``.
        mesurer: fonction de mesure, appelée sans réseau grâce aux volumes.

    Returns:
        Le dossier, marqué ``reprise`` quand un chiffre est republié.
    """
    if connue is None:
        return mesurer(cfg, volumes={}, intensite={
            "disponible": False,
            "commentaire": "pas encore mesuré : son tour vient dans la rotation des dossiers",
        })

    age = rotation_geopolitique.age_jours(connue)
    if rotation_geopolitique.est_perimee(connue):
        return mesurer(cfg, volumes={}, intensite={
            "disponible": False,
            "commentaire": (
                f"dernière mesure du {connue.mesure_du} ({age} jours) : trop ancienne "
                f"pour dire quoi que ce soit de la couverture du jour"
            ),
        })

    dossier = mesurer(cfg, volumes=connue.volumes)
    return replace(
        dossier,
        reprise=True,
        mesure_du=str(connue.mesure_du),
        age_mesure_jours=age,
    )


def mesurer_dossier(
    dossier_cfg: dict[str, Any],
    identifiants_connus: set[str] | None = None,
    fenetre: int = FENETRE_TRAJECTOIRE,
    seuil_acceleration: float = 1.15,
    seuil_essoufflement: float = 0.85,
    series_macro: pd.DataFrame | None = None,
    prix_or: pd.Series | None = None,
    z_score_prime: float | None = None,
    volumes: dict[str, float] | None = None,
    intensite: dict[str, Any] | None = None,
    articles: list[news.NewsItem] | None = None,
    lignes_events: list[list[str]] | None = None,
    motif_events: str = "",
) -> Dossier:
    """Mesure un dossier de conflit : intensité, narratif, nouveautés, impact.

    Args:
        dossier_cfg: entrée de ``config/geopolitique_dossiers.yaml`` (``id``,
            ``nom_affiche``, ``acteurs_gdelt``, ``mots_cles``).
        identifiants_connus: identifiants déjà signalés pour CE dossier (voir
            :func:`charger_historique_dossiers`) — sert à isoler les
            nouveautés plutôt que de réexpliquer ce qui n'a pas changé.
        fenetre, seuil_acceleration, seuil_essoufflement: réglages de
            trajectoire, mêmes noms que :func:`analyser_theme`.
        series_macro: séries FRED de la chaîne de transmission.
        prix_or: série du prix de l'or.
        z_score_prime: z-score du résidu de juste valeur.
        volumes, intensite: mesures GDELT DOC déjà obtenues, injectables
            pour les tests hors ligne (voir :func:`analyser_theme`).
        articles: articles GDELT DOC déjà collectés pour ce dossier,
            injectables pour les tests hors ligne. ``None`` déclenche un
            appel réseau réel.
        lignes_events: export GDELT Events déjà téléchargé (voir
            :mod:`dataio.gdelt_events`), injectable pour les tests hors ligne.
        motif_events: motif d'indisponibilité de l'export Events, s'il y a lieu.

    Returns:
        Le dossier mesuré.
    """
    ident = str(dossier_cfg.get("id", ""))
    nom = str(dossier_cfg.get("nom_affiche", ident or "dossier sans nom"))
    mots_cles = [str(m) for m in dossier_cfg.get("mots_cles") or []]
    acteurs = [str(a) for a in dossier_cfg.get("acteurs_gdelt") or []]
    pays = [str(p) for p in dossier_cfg.get("pays") or []]
    type_dossier = str(dossier_cfg.get("type") or ("regional" if pays else "conflit"))
    canal = str(dossier_cfg.get("canal") or "petrole")
    epingle = bool(dossier_cfg.get("epingle", False))
    connus = identifiants_connus or set()

    query = news.construire_requete_gdelt(mots_cles) if mots_cles else ""
    if query:
        theme = analyser_theme(
            nom=nom, query=query, fenetre=fenetre,
            seuil_acceleration=seuil_acceleration, seuil_essoufflement=seuil_essoufflement,
            volumes=volumes, intensite=intensite,
        )
    else:
        theme = Theme(nom=nom, motif="aucun mot-clé configuré pour ce dossier")

    # Activité bilatérale GDELT Events : un instantané, pas une tendance —
    # voir dataio/gdelt_events.py pour pourquoi seul le dernier export compte.
    n_evenements: int | None = None
    exemple_evenement: dict[str, Any] | None = None
    if len(acteurs) == 2 or pays:
        if lignes_events is not None:
            resultat_events = (
                gdelt_events.compter_evenements_par_acteurs(lignes_events, acteurs)
                if len(acteurs) == 2 else gdelt_events.compter_evenements_region(lignes_events, pays)
            )
            n_evenements = resultat_events["n_evenements"]
            exemple_evenement = resultat_events["exemple"]
        elif not motif_events:
            motif_events = "export GDELT Events non fourni"
    elif not motif_events:
        motif_events = "dossier thématique : pas d'activité par acteur"

    # Narratif : des articles réels, nommés et sourcés — pas seulement un
    # ratio d'intensité déconnecté de tout événement concret.
    if articles is None:
        articles = news.fetch_gdelt(query, timespan="7d", max_records=10, tags=[ident]) if query else []
    developpements = [
        {
            "id": identifiant_item(a.titre, a.url),
            "titre": a.titre,
            "url": a.url,
            "source": a.source,
            "horodatage_utc": a.date.isoformat() if a.date else None,
        }
        for a in articles[:MAX_DEVELOPPEMENTS_AFFICHES]
    ]
    nouveaux = [d for d in developpements if d["id"] not in connus]

    chaine = chaine_de_transmission(series_macro, prix_or, intensite_max=theme.intensite_ratio, canal=canal)
    deja_paye = _deja_dans_les_prix([theme], z_score_prime)

    return Dossier(
        id=ident,
        nom_affiche=nom,
        theme=theme,
        mots_cles=mots_cles,
        n_evenements_bilateraux=n_evenements,
        motif_events=motif_events,
        exemple_evenement=exemple_evenement,
        developpements_recents=developpements,
        nouveaux_developpements=nouveaux,
        etat_actuel=_etat_actuel_dossier(nom, theme, nouveaux, developpements),
        chaine_transmission=chaine,
        deja_dans_les_prix=deja_paye,
        invalidation=_invalidation_dossier(theme, chaine),
        type=type_dossier,
        acteurs=acteurs,
        pays=pays,
        canal=canal,
        epingle=epingle,
    )


def charger_historique_dossiers(chemin: Path | None = None) -> dict[str, set[str]]:
    """Relit les identifiants déjà signalés, par dossier.

    Même patron que ``modules.geopolitique.feed.charger_historique`` (et
    ``modules.quantum.feed`` avant lui) : un identifiant stable par item,
    jamais resignalé comme nouveau. Partitionné par dossier ici, puisque
    plusieurs dossiers partagent un seul fichier d'historique.

    Args:
        chemin: fichier JSONL. ``None`` retient :data:`FICHIER_HISTORIQUE_DOSSIERS`.

    Returns:
        Un ensemble d'identifiants connus, par identifiant de dossier. Vide
        si le fichier n'existe pas encore (première exécution).
    """
    fichier = chemin or FICHIER_HISTORIQUE_DOSSIERS
    connus: dict[str, set[str]] = {}
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
                dossier_id = entree.get("dossier")
                identifiant = entree.get("id")
                if dossier_id and identifiant:
                    connus.setdefault(str(dossier_id), set()).add(str(identifiant))
    except FileNotFoundError:
        _LOG.info("Aucun historique de dossiers géopolitiques : première exécution.")
    except OSError as exc:
        _LOG.warning("Historique des dossiers géopolitiques illisible (%s) : %s", fichier, exc)
    return connus


def publier_historique_dossiers(dossiers: list[Dossier], chemin: Path | None = None) -> None:
    """Ajoute au fichier d'historique les développements vus pour la première fois.

    Étape d'écriture séparée de :func:`mesurer_dossier` (fonction pure) :
    même principe que ``modules.geopolitique.feed.publier_fil``, qui sépare
    la construction du fil de l'écriture de son historique.

    Args:
        dossiers: dossiers mesurés (voir :func:`analyser_dossiers`).
        chemin: fichier JSONL. ``None`` retient :data:`FICHIER_HISTORIQUE_DOSSIERS`.
    """
    fichier = chemin or FICHIER_HISTORIQUE_DOSSIERS
    horodatage = datetime.now(timezone.utc).isoformat()
    lignes = [
        json.dumps(
            {"id": item["id"], "dossier": dossier.id, "titre": item["titre"], "horodatage_utc": horodatage},
            ensure_ascii=False,
        )
        for dossier in dossiers
        for item in dossier.nouveaux_developpements
    ]
    if not lignes:
        return
    try:
        fichier.parent.mkdir(parents=True, exist_ok=True)
        with fichier.open("a", encoding="utf-8") as flux:
            flux.write("\n".join(lignes) + "\n")
    except OSError as exc:
        _LOG.warning("Historique des dossiers géopolitiques non écrit (%s) : %s", fichier, exc)


#: Historique du classement des sujets : une ligne par sujet et par jour, en
#: ajout seul. Même mécanique que les journaux sous reports/crypto/ : jamais
#: réécrit, fusionné par union en cas de publication concurrente (voir
#: scripts/fusionner_sorties.py). C'est lui qui permet de dire qu'un sujet
#: est inerte depuis trois semaines plutôt que sur la photo du jour.
FICHIER_HISTORIQUE_CLASSEMENT: Final = (
    Path(__file__).resolve().parents[2] / "reports" / "gold" / "geopolitique_classement_historique.jsonl"
)

#: Fenêtres du résumé d'historique, en jours de classement.
JOURS_RESUME_HISTORIQUE: Final[int] = 30
JOURS_TENDANCE_SCORE: Final[int] = 7

#: Niveau ordinal d'un statut, pour dire « promu » ou « rétrogradé ». Un
#: sujet épinglé garde son statut quoi qu'il arrive : c'est sa lecture de
#: pertinence qui bouge, et c'est elle qu'on compare.
_NIVEAU_LECTURE: Final[dict[str, int]] = {"réagit": 3, "neutre": 2, "inerte": 1}


def cle_sujet(entree: dict[str, Any]) -> str:
    """Clé stable d'un sujet dans l'historique : l'identifiant du dossier, sinon son nom."""
    return str(entree.get("id") or f"sujet:{entree.get('nom', '')}")


def _niveau(statut: str, lecture: str | None) -> int:
    if statut == "actif":
        return 3
    if statut == "candidat":
        return 0
    return _NIVEAU_LECTURE.get(lecture or "", 0 if statut == "epingle" else 1)


def charger_historique_classement(chemin: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Relit l'historique du classement, par sujet, une entrée par jour.

    Deux lignes pour le même jour (exécution relancée à la main) : la
    dernière écrite l'emporte — c'est la mesure la plus fraîche.

    Args:
        chemin: fichier JSONL. ``None`` retient :data:`FICHIER_HISTORIQUE_CLASSEMENT`.

    Returns:
        Entrées triées par date, par clé de sujet. Vide si le fichier
        n'existe pas encore (première exécution) ou est illisible.
    """
    fichier = chemin or FICHIER_HISTORIQUE_CLASSEMENT
    par_sujet: dict[str, dict[str, dict[str, Any]]] = {}
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
                cle, date = entree.get("cle"), entree.get("date")
                if cle and date:
                    par_sujet.setdefault(str(cle), {})[str(date)] = entree
    except FileNotFoundError:
        _LOG.info("Aucun historique de classement : première exécution.")
    except OSError as exc:
        _LOG.warning("Historique du classement illisible (%s) : %s", fichier, exc)
    return {cle: [jours[d] for d in sorted(jours)] for cle, jours in par_sujet.items()}


def resumer_historique_sujet(
    entrees: list[dict[str, Any]], statut: str, lecture: str | None, score: float | None,
    date: str | None = None,
) -> dict[str, Any]:
    """Situe le classement du jour dans l'historique d'un sujet.

    Args:
        entrees: lignes passées de ce sujet (voir
            :func:`charger_historique_classement`), la journée en cours
            exclue ou incluse — elle est écartée si sa date est ``date``.
        statut, lecture, score: classement du jour.
        date: date du jour, ``AAAA-MM-JJ``.

    Returns:
        ``jours_observes`` (jours de classement passés), ``changement``
        (``nouveau`` / ``promu`` / ``rétrogradé`` / ``stable``),
        ``statut_precedent``, ``lecture_precedente``, ``date_precedente``,
        ``jours_consecutifs_statut`` (journée en cours comprise),
        ``inerte_depuis_jours`` (idem, 0 si le sujet n'est pas inerte),
        ``score_moyen_30j`` et ``tendance_score`` (moyenne des sept derniers
        jours contre les sept précédents). Chaque nombre est citable.
    """
    passes = [e for e in entrees if date is None or e.get("date") != date]
    resume: dict[str, Any] = {
        "jours_observes": len(passes), "changement": "nouveau",
        "statut_precedent": None, "lecture_precedente": None, "date_precedente": None,
        "jours_consecutifs_statut": 1,
        "inerte_depuis_jours": 1 if lecture == "inerte" else 0,
        "score_moyen_30j": None, "tendance_score": "",
    }
    if not passes:
        return resume

    dernier = passes[-1]
    resume.update({
        "statut_precedent": dernier.get("statut"), "lecture_precedente": dernier.get("lecture"),
        "date_precedente": dernier.get("date"),
    })
    avant = _niveau(str(dernier.get("statut", "")), dernier.get("lecture"))
    apres = _niveau(statut, lecture)
    resume["changement"] = "promu" if apres > avant else "rétrogradé" if apres < avant else "stable"

    # Séries consécutives, en remontant depuis hier.
    consecutifs = 1
    for e in reversed(passes):
        if e.get("statut") == statut and (statut not in ("veille", "epingle") or e.get("lecture") == lecture):
            consecutifs += 1
        else:
            break
    resume["jours_consecutifs_statut"] = consecutifs
    if lecture == "inerte":
        inerte = 1
        for e in reversed(passes):
            if e.get("lecture") == "inerte":
                inerte += 1
            else:
                break
        resume["inerte_depuis_jours"] = inerte

    scores = [float(e["score"]) for e in passes[-JOURS_RESUME_HISTORIQUE:] if e.get("score") is not None]
    if score is not None:
        scores.append(float(score))
    if scores:
        resume["score_moyen_30j"] = round(sum(scores) / len(scores), 2)
    if len(scores) >= 2 * JOURS_TENDANCE_SCORE:
        recents = scores[-JOURS_TENDANCE_SCORE:]
        precedents = scores[-2 * JOURS_TENDANCE_SCORE:-JOURS_TENDANCE_SCORE]
        m_r, m_p = sum(recents) / len(recents), sum(precedents) / len(precedents)
        resume["tendance_score"] = "en hausse" if m_r > m_p * 1.1 else "en baisse" if m_r < m_p * 0.9 else "stable"
    return resume


def publier_historique_classement(
    classement: list[dict[str, Any]], date: str, chemin: Path | None = None,
) -> int:
    """Ajoute au fichier d'historique le classement du jour, un sujet par ligne.

    Ajout seul, idempotent dans la journée : un sujet déjà consigné à cette
    date n'est pas réécrit — relancer le rapport le même jour n'invente pas
    une deuxième observation.

    Args:
        classement: bloc ``classement`` de :func:`analyser_dossiers`.
        date: date du rapport, ``AAAA-MM-JJ``.
        chemin: fichier JSONL. ``None`` retient :data:`FICHIER_HISTORIQUE_CLASSEMENT`.

    Returns:
        Nombre de lignes écrites.
    """
    fichier = chemin or FICHIER_HISTORIQUE_CLASSEMENT
    deja = {
        cle for cle, entrees in charger_historique_classement(fichier).items()
        if any(e.get("date") == date for e in entrees)
    }
    horodatage = datetime.now(timezone.utc).isoformat()
    lignes: list[str] = []
    for c in classement:
        cle = cle_sujet(c)
        if cle in deja:
            continue
        p = c.get("pertinence") or {}
        lignes.append(json.dumps({
            "date": date, "cle": cle, "nom": c.get("nom"), "origine": c.get("origine"), "type": c.get("type"),
            "statut": c.get("statut"), "rang": c.get("rang"),
            "score": p.get("score"), "lecture": p.get("lecture") or None, "fiabilite": p.get("fiabilite"),
            "n_observations": p.get("n_observations"), "n_pics": p.get("n_pics"),
            "intensite_ratio": (round(float(c["intensite_ratio"]), 3) if c.get("intensite_ratio") is not None else None),
            "horodatage_utc": horodatage,
        }, ensure_ascii=False))
    if not lignes:
        return 0
    try:
        fichier.parent.mkdir(parents=True, exist_ok=True)
        with fichier.open("a", encoding="utf-8") as flux:
            flux.write("\n".join(lignes) + "\n")
    except OSError as exc:
        _LOG.warning("Historique du classement non écrit (%s) : %s", fichier, exc)
        return 0
    return len(lignes)


def _mesurer_sujet_libre(
    nom: str, requete: str, marches: pd.DataFrame | None, min_obs: int,
    mesurer: bool, fenetre: int,
) -> dict[str, Any]:
    """Mesure couverture, trajectoire et pertinence d'un sujet sans dossier.

    Une requête vide (code pays inconnu) ou ``mesurer=False`` donne un sujet
    listé mais non mesuré, avec son motif.
    """
    if not requete:
        return {"disponible": False, "motif": "aucune requête composable pour ce sujet",
                "intensite_ratio": None, "trajectoire": "", "pertinence": sujets.pertinence_marche(None, marches)}
    if not mesurer:
        return {"disponible": False, "motif": "mesure GDELT non demandée",
                "intensite_ratio": None, "trajectoire": "", "pertinence": sujets.pertinence_marche(None, marches)}
    theme = analyser_theme(nom=nom, query=requete, fenetre=fenetre)
    return {
        "disponible": theme.disponible, "motif": theme.motif,
        "intensite_ratio": theme.intensite_ratio, "trajectoire": theme.trajectoire,
        "volume_24h": theme.volume_24h,
        "pertinence": sujets.pertinence_marche(theme.volumes, marches, min_observations=min_obs),
    }


def _articles_sujet(requete: str, mesurer: bool, maximum: int = 5) -> list[dict[str, Any]]:
    """Quelques articles nommés et sourcés pour un sujet du filet « Autres »."""
    if not requete or not mesurer:
        return []
    articles = news.fetch_gdelt(requete, timespan="48h", max_records=maximum, tags=["autres"])
    return [
        {"id": identifiant_item(a.titre, a.url), "titre": a.titre, "url": a.url, "source": a.source,
         "horodatage_utc": a.date.isoformat() if a.date else None}
        for a in articles[:maximum]
    ]


def analyser_dossiers(
    dossiers_configures: list[dict[str, Any]] | None = None,
    series_macro: pd.DataFrame | None = None,
    prix_or: pd.Series | None = None,
    z_score_prime: float | None = None,
    fenetre: int = FENETRE_TRAJECTOIRE,
    seuil_acceleration: float = 1.15,
    seuil_essoufflement: float = 0.85,
    identifiants_connus: dict[str, set[str]] | None = None,
    dossiers_precalcules: list[Dossier] | None = None,
    lignes_events: list[list[str]] | None = None,
    motif_events: str | None = None,
    themes_generiques: list[dict[str, Any]] | None = None,
    reglages_decouverte: dict[str, Any] | None = None,
    marches: pd.DataFrame | None = None,
    mesurer_candidats: bool = True,
    historique_classement: dict[str, list[dict[str, Any]]] | None = None,
    date_rapport: str | None = None,
) -> dict[str, Any]:
    """Produit le bloc géopolitique du rapport, dossier de conflit par dossier.

    Args:
        dossiers_configures: dossiers bruts (voir
            :func:`charger_dossiers_config`). ``None`` charge le fichier par
            défaut.
        series_macro: séries FRED de la chaîne de transmission.
        prix_or: série du prix de l'or.
        z_score_prime: z-score du résidu de juste valeur.
        fenetre, seuil_acceleration, seuil_essoufflement: réglages de
            trajectoire.
        identifiants_connus: historique déjà chargé (voir
            :func:`charger_historique_dossiers`). ``None`` le recharge depuis
            le fichier par défaut.
        dossiers_precalcules: dossiers déjà mesurés. Fournis, aucun appel
            réseau n'est effectué (utilisé par les tests hors ligne).
        lignes_events: export GDELT Events déjà téléchargé, injectable pour
            les tests. ``None`` déclenche un appel réseau réel, partagé par
            tous les dossiers (un seul export sert à tous).
        motif_events: motif d'indisponibilité de l'export Events, s'il y a
            lieu (évite un second appel réseau quand l'échec est déjà connu).

        themes_generiques: thèmes GDELT génériques (``gold.yaml``,
            ``geopolitique.themes``) : seconde source de candidats, et
            matière du filet « Autres ».
        reglages_decouverte: bloc ``decouverte`` de la configuration
            (``max_candidats``, ``max_candidats_mesures``,
            ``min_observations``).
        marches: mouvements journaliers des actifs suivis (voir
            :func:`modules.geopolitique.sujets.marches_journaliers`).
            ``None`` les dérive de ``series_macro`` et ``prix_or``.
        mesurer_candidats: ``False`` pour n'effectuer aucun appel GDELT DOC
            pour les candidats et thèmes (tests hors ligne) — ils sont
            alors listés sans mesure, jamais inventés.
        historique_classement: sortie de :func:`charger_historique_classement`,
            pour situer chaque sujet dans le temps (promu, rétrogradé, inerte
            depuis N jours). ``None`` : aucun historique, tout est « nouveau ».
        date_rapport: date du jour, ``AAAA-MM-JJ``, pour écarter une ligne
            d'historique déjà écrite aujourd'hui.

    Returns:
        Dictionnaire prêt à être sérialisé, avec en plus ``classement``
        (tous les sujets, du plus pertinent au moins), ``candidats`` (paires
        découvertes dans l'export Events) et ``autres`` (sujets significatifs
        hors de tout dossier). L'écriture de l'historique n'est PAS faite ici
        (voir :func:`publier_historique_dossiers`) : cette fonction reste
        pure, ce qui la rend testable sans toucher au disque.
    """
    reglages = dict(reglages_decouverte or {})
    min_obs = int(reglages.get("min_observations", sujets.MIN_OBSERVATIONS))
    if marches is None:
        marches = sujets.marches_journaliers(series_macro, prix_or)
    events_meta: dict[str, Any] = {
        "n_exports_lus": None, "n_exports_attendus": None,
        "heures": int(reglages.get("heures_events", gdelt_events.HEURES_EXPORTS_DEFAUT)), "motif": "",
    }

    if dossiers_precalcules is not None:
        dossiers = list(dossiers_precalcules)
        configs = [
            {"id": d.id, "nom_affiche": d.nom_affiche, "acteurs_gdelt": d.acteurs, "pays": d.pays, "mots_cles": d.mots_cles}
            for d in dossiers
        ]
    else:
        configs = (
            dossiers_configures if dossiers_configures is not None else charger_dossiers_config()
        )
        connus = identifiants_connus if identifiants_connus is not None else charger_historique_dossiers()

        if lignes_events is None and motif_events is None:
            lignes_events, events_meta = gdelt_events.recuperer_exports_recents(
                heures=int(reglages.get("heures_events", gdelt_events.HEURES_EXPORTS_DEFAUT)),
            )
            motif_events = events_meta.get("motif", "")
        motif_events = motif_events or ""
        if motif_events:
            _LOG.warning(
                "GDELT Events indisponible : %s. Signal par acteur dégradé pour tous les dossiers.",
                motif_events,
            )

        def _mesurer(
            cfg: dict[str, Any],
            volumes: dict[str, float] | None = None,
            intensite: dict[str, Any] | None = None,
        ) -> Dossier:
            """Mesure un dossier, ou le reconstitue sans toucher au réseau.

            ``volumes`` fourni, aucun appel réseau n'est émis : la trajectoire,
            l'ancienneté et la pertinence marché se recalculent sur la série
            reprise, exactement comme le jour où elle a été obtenue. Une série
            vide accompagnée d'une ``intensite`` indisponible produit un
            dossier non mesuré, toujours sans réseau.
            """
            return mesurer_dossier(
                cfg, connus.get(str(cfg.get("id", "")), set()),
                fenetre=fenetre, seuil_acceleration=seuil_acceleration,
                seuil_essoufflement=seuil_essoufflement,
                series_macro=series_macro, prix_or=prix_or, z_score_prime=z_score_prime,
                lignes_events=lignes_events, motif_events=motif_events,
                volumes=volumes, intensite=intensite,
                # Une reprise ne va pas rechercher d'articles : ce serait le
                # second appel réseau que la rotation cherche justement à
                # éviter.
                articles=[] if volumes is not None else None,
            )

        # --- Rotation : qui est mesuré cette fois-ci -------------------------
        #
        # Mesurer les huit dossiers à chaque exécution demandait jusqu'à
        # quarante-huit requêtes GDELT, dont GDELT refusait la quasi-totalité
        # depuis un exécuteur GitHub. Un lot des moins récemment mesurés suffit
        # à tenir chaque dossier à jour, et divise la pression par deux.
        mesures_connues = rotation_geopolitique.lire()
        lot = set(rotation_geopolitique.choisir_lot(
            [str(cfg.get("id", "")) for cfg in configs],
            mesures_connues,
            taille=int(reglages.get("dossiers_par_execution", rotation_geopolitique.TAILLE_LOT)),
        ))
        if lot:
            _LOG.info(
                "Rotation des dossiers : %d mesuré(s) cette fois-ci (%s).",
                len(lot), ", ".join(sorted(lot)),
            )

        dossiers: list[Dossier] = []
        for cfg in configs:
            identifiant = str(cfg.get("id", ""))
            connue = mesures_connues.get(identifiant)
            if identifiant in lot:
                dossiers.append(_mesurer(cfg))
                continue
            # Hors du lot : reprise de la dernière mesure connue, si elle a
            # encore un sens. Une série de plus d'une semaine ne dit plus rien
            # du jour, et la publier datée ne suffirait pas à la rendre juste.
            dossiers.append(_reprendre(cfg, connue, _mesurer))

        def _a_remesurer(d: Dossier) -> bool:
            """Dit si un dossier mérite une nouvelle tentative réseau.

            Remesuré : GDELT muet, ou intensité obtenue sans la série de
            volumes (la requête longue a échoué, la courte a réussi) — sans
            cette série, ni trajectoire ni pertinence marché. Un dossier
            repris n'est jamais remesuré : il est hors du lot par choix.
            """
            if d.reprise:
                return False
            return bool(
                (not d.theme.disponible and "GDELT" in (d.theme.motif or ""))
                or (d.theme.disponible and not d.theme.volumes)
            )

        # Un dossier que GDELT n'a pas servi est remesuré après une pause
        # croissante, plutôt que marqué indisponible au premier refus : le
        # rapport n'est pas pressé, et un dossier manquant coûte plus qu'une
        # minute d'attente. Seuls les échecs GDELT sont réessayés — un dossier
        # sans mot-clé configuré ne s'arrangera pas en patientant.
        for k in range(REESSAIS_DOSSIER):
            a_reessayer = [i for i, d in enumerate(dossiers) if _a_remesurer(d)]
            if not a_reessayer:
                break
            pause = ATTENTE_REESSAI_DOSSIER_SECONDES * (2 ** k)
            _LOG.warning(
                "GDELT muet pour %s : nouvelle mesure dans %.0f s (%d/%d).",
                ", ".join(dossiers[i].nom_affiche for i in a_reessayer), pause, k + 1, REESSAIS_DOSSIER,
            )
            time.sleep(pause)
            for i in a_reessayer:
                dossiers[i] = _mesurer(configs[i])

        # --- Reprise de secours : le lot a été tenté, GDELT n'a pas répondu --
        #
        # Un dossier du lot dont la mesure échoue malgré sa remesure reprend sa
        # dernière série connue, plutôt que de paraître indisponible. C'est ce
        # qui rend la remesure moins précieuse, et donc REESSAIS_DOSSIER plus
        # petit : perdre la mesure du jour ne fait plus perdre le dossier.
        for i, dossier in enumerate(dossiers):
            if dossier.reprise or dossier.theme.volumes:
                continue
            connue = mesures_connues.get(str(configs[i].get("id", "")))
            if connue is None or rotation_geopolitique.est_perimee(connue):
                continue
            _LOG.info(
                "Mesure du jour manquante pour %s : reprise de celle du %s.",
                dossier.nom_affiche, connue.mesure_du,
            )
            dossiers[i] = _reprendre(configs[i], connue, _mesurer)

        # La rotation ne retient que les séries fraîchement obtenues : une
        # reprise enregistrée rajeunirait sa date et sortirait le dossier de la
        # file d'attente pour de bon.
        obtenues = rotation_geopolitique.depuis_dossiers(dossiers)
        if obtenues:
            rotation_geopolitique.enregistrer({**mesures_connues, **obtenues})

    # --- Pertinence marché de chaque dossier ---------------------------------
    dossiers = [
        replace(d, pertinence=sujets.pertinence_marche(d.theme.volumes, marches, min_observations=min_obs))
        for d in dossiers
    ]

    # --- Candidats : paires d'acteurs de l'export, thèmes génériques ---------
    candidats = sujets.candidats_evenements(
        lignes_events or [], configs, max_candidats=int(reglages.get("max_candidats", 5)),
    )
    par_id = {d.id: i for i, d in enumerate(dossiers)}
    for c in candidats:
        r = c.get("rattachement")
        if r and r["id"] in par_id:
            i = par_id[r["id"]]
            dossiers[i] = replace(dossiers[i], sujets_rattaches=dossiers[i].sujets_rattaches + [
                {k: c[k] for k in ("paire", "libelle", "n_evenements", "mentions", "part", "goldstein_moyen")}
            ])
    hors_dossier = [c for c in candidats if not c.get("rattachement")]

    max_mesures = int(reglages.get("max_candidats_mesures", 3))
    for c in hors_dossier[:max_mesures]:
        c.update(_mesurer_sujet_libre(c["libelle"], c["requete"], marches, min_obs, mesurer_candidats, fenetre))
    for c in hors_dossier[max_mesures:]:
        c.update({"disponible": False, "motif": "au-delà du nombre de candidats mesurés par exécution",
                  "intensite_ratio": None, "pertinence": sujets.pertinence_marche(None, marches)})

    themes_mesures: list[dict[str, Any]] = []
    for t in themes_generiques or []:
        nom, query = str(t.get("nom", "")), str(t.get("query", ""))
        if not nom or not query:
            continue
        bloc = {"type": "theme", "libelle": nom, "requete": query}
        bloc.update(_mesurer_sujet_libre(nom, query, marches, min_obs, mesurer_candidats, fenetre))
        themes_mesures.append(bloc)

    # --- Classement de tous les sujets ---------------------------------------
    entrees = [
        {"nom": d.nom_affiche, "id": d.id, "type": d.type, "origine": "dossier", "epingle": d.epingle,
         "intensite_ratio": d.theme.intensite_ratio, "trajectoire": d.theme.trajectoire,
         "disponible": d.theme.disponible, "pertinence": d.pertinence}
        for d in dossiers
    ] + [
        {"nom": c["libelle"], "id": "", "type": "paire", "origine": "decouverte", "epingle": False,
         "intensite_ratio": c.get("intensite_ratio"), "trajectoire": c.get("trajectoire", ""),
         "disponible": bool(c.get("disponible")), "pertinence": c.get("pertinence") or {}}
        for c in hors_dossier
    ] + [
        {"nom": t["libelle"], "id": "", "type": "theme", "origine": "theme_generique", "epingle": False,
         "intensite_ratio": t.get("intensite_ratio"), "trajectoire": t.get("trajectoire", ""),
         "disponible": bool(t.get("disponible")), "pertinence": t.get("pertinence") or {}}
        for t in themes_mesures
    ]
    classement = sujets.classer(entrees)
    historique = historique_classement or {}
    for e in classement:
        e["cle"] = cle_sujet(e)
        p = e.get("pertinence") or {}
        e["historique"] = resumer_historique_sujet(
            historique.get(e["cle"], []), e["statut"], p.get("lecture") or None, p.get("score"), date_rapport,
        )
    statuts = {e["id"]: e for e in classement if e.get("id")}
    dossiers = [
        replace(d, classement={
            **{k: statuts[d.id][k] for k in ("statut", "rang", "donnees_suffisantes")},
            "historique": statuts[d.id]["historique"],
        })
        if d.id in statuts else d
        for d in dossiers
    ]

    # --- Filet « Autres » : significatif et hors de tout dossier --------------
    autres: list[dict[str, Any]] = []
    for sujet in hors_dossier + themes_mesures:
        retenu, critere = sujets.est_significatif(sujet)
        if retenu:
            autres.append({k: v for k, v in sujet.items() if k != "pertinence"} | {
                "critere": critere, "pertinence": sujet.get("pertinence") or {},
                "articles": _articles_sujet(sujet.get("requete", ""), mesurer_candidats),
            })

    disponibles = [d for d in dossiers if d.theme.disponible]
    intensites = [d.theme.intensite_ratio for d in disponibles if d.theme.intensite_ratio is not None]
    intensite_max = max(intensites) if intensites else None
    dossier_dominant = ""
    if intensite_max is not None:
        dossier_dominant = next(
            (d.id for d in disponibles if d.theme.intensite_ratio == intensite_max), ""
        )

    return {
        "disponible": bool(disponibles),
        "motif": "" if disponibles else "aucun dossier mesurable (GDELT indisponible)",
        "horodatage_utc": datetime.now(timezone.utc).isoformat(),
        "source": (
            "GDELT (DOC : narratif et couverture ; Events : activité bilatérale par acteur) "
            "+ FRED (maillons macro)"
        ),
        "n_dossiers_mesures": len(disponibles),
        "n_dossiers_configures": len(dossiers),
        "intensite_max": intensite_max,
        "dossier_dominant": dossier_dominant,
        "dossiers": [d.to_dict() for d in dossiers],
        "classement": classement,
        "candidats": candidats,
        "autres": autres,
        "events_meta": events_meta,
        # Seuils de lecture de la pertinence, publiés pour que la prose puisse
        # les citer (le vérificateur numérique n'accepte que les nombres du rapport).
        "criteres_pertinence": {"reagit": sujets.SEUIL_REAGIT, "inerte": sujets.SEUIL_INERTE,
                                "min_observations": min_obs, "min_pics": sujets.MIN_PICS},
        "criteres_autres": {
            "intensite_min": sujets.SEUIL_INTENSITE_SIGNIFICATIVE,
            "evenements_min_paire": sujets.MIN_EVENEMENTS_PAIRE,
            "part_min_paire": sujets.PART_MIN_PAIRE,
            "min_observations": min_obs,
        },
        # Objets Dossier, pour l'appelant qui voudrait publier l'historique
        # (voir publier_historique_dossiers) sans refaire la mesure. Même
        # convention que backtest/run.py::executer_variantes avec "_trades" :
        # une clé préfixée d'un tiret bas, à retirer avant toute
        # sérialisation JSON — elle n'a rien à faire dans le rapport publié.
        "_dossiers_objets": dossiers,
    }
