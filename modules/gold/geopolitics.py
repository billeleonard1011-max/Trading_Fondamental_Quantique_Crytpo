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

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

import numpy as np
import pandas as pd

from dataio import news

_LOG: Final = logging.getLogger(__name__)

#: Horizon de variation de chaque maillon de la chaîne, en séances.
HORIZON_MAILLON: Final[int] = 5

#: Fenêtre d'observation de la trajectoire d'un thème, en jours.
FENETRE_TRAJECTOIRE: Final[int] = 7

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
    "analyser_geopolitique",
]


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
    # Les volumes journaliers sont chargés d'abord, puis passés à
    # gdelt_intensity : intensité et trajectoire se calculent à partir de la
    # même série, en une seule requête au lieu de deux identiques.
    if volumes is None:
        volumes, motif_volumes = news.gdelt_volume_journalier(query, timespan="30d")
    else:
        motif_volumes = ""
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


def chaine_de_transmission(
    series_macro: pd.DataFrame | None,
    prix_or: pd.Series | None,
    intensite_max: float | None = None,
    horizon: int = HORIZON_MAILLON,
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

    Returns:
        Dictionnaire décrivant les cinq maillons et la cohérence d'ensemble.
    """
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
        if maillons[cle].get("disponible") and maillons[cle].get("variation") is not None
    ]
    conformes = [cle for cle, variation, sens in mesures if variation * sens > 0]

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
        manquants = [cle for cle, _, _ in mesures if cle not in conformes]
        commentaire = (
            f"Chaîne rompue : {len(conformes)}/{len(mesures)} maillon(s) conformes, "
            f"rupture sur {', '.join(manquants)}. Le marché ne relaie pas l'événement "
            "par les canaux habituels."
        )
        rompue = True

    return {
        "horizon_seances": horizon,
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
            f"Oui. Thème(s) installé(s) depuis plus de {anciennete_installee} jours "
            f"({noms}) et prime déjà à {z_score_prime:+.2f} écart-type. Une mauvaise "
            "nouvelle supplémentaire ferait peu monter le prix ; une détente le ferait "
            "beaucoup retomber. L'asymétrie joue contre l'acheteur."
        )
    elif prime_elevee:
        commentaire = (
            f"Partiellement. La prime est élevée ({z_score_prime:+.2f} écart-type) mais "
            "aucun thème n'est installé depuis assez longtemps pour l'expliquer : elle "
            "vient d'ailleurs, ou d'un événement trop récent pour être mesuré."
        )
    elif installes:
        commentaire = (
            f"Non. Des thèmes durent depuis plus de {anciennete_installee} jours, mais la "
            f"prime reste modérée ({z_score_prime:+.2f} écart-type) : le marché ne les "
            "valorise pas encore."
        )
    else:
        commentaire = (
            f"Non. Ni thème installé ni prime élevée ({z_score_prime:+.2f} écart-type) : "
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


def analyser_geopolitique(
    configuration: dict[str, Any],
    series_macro: pd.DataFrame | None = None,
    prix_or: pd.Series | None = None,
    z_score_prime: float | None = None,
    themes_precalcules: list[Theme] | None = None,
) -> dict[str, Any]:
    """Produit le bloc géopolitique du rapport.

    Args:
        configuration: bloc ``geopolitique`` de ``config/gold.yaml``.
        series_macro: séries FRED de la chaîne de transmission.
        prix_or: série du prix de l'or.
        z_score_prime: z-score du résidu de juste valeur.
        themes_precalcules: thèmes déjà mesurés. Fournis, aucun appel réseau
            n'est effectué.

    Returns:
        Dictionnaire prêt à être sérialisé.
    """
    fenetre = int(configuration.get("fenetre_trajectoire_jours", FENETRE_TRAJECTOIRE))
    seuil_acc = float(configuration.get("seuil_acceleration", 1.15))
    seuil_ess = float(configuration.get("seuil_essoufflement", 0.85))

    if themes_precalcules is not None:
        themes = list(themes_precalcules)
    else:
        themes = [
            analyser_theme(
                nom=str(bloc.get("nom", "thème sans nom")),
                query=str(bloc.get("query", "")),
                fenetre=fenetre,
                seuil_acceleration=seuil_acc,
                seuil_essoufflement=seuil_ess,
            )
            for bloc in configuration.get("themes", [])
        ]

    disponibles = [t for t in themes if t.disponible]
    intensites = [t.intensite_ratio for t in disponibles if t.intensite_ratio is not None]
    intensite_max = max(intensites) if intensites else None
    theme_dominant = ""
    if intensite_max is not None:
        theme_dominant = next(
            (t.nom for t in disponibles if t.intensite_ratio == intensite_max), ""
        )

    return {
        "disponible": bool(disponibles),
        "motif": "" if disponibles else "aucun thème mesurable (GDELT indisponible)",
        "horodatage_utc": datetime.now(timezone.utc).isoformat(),
        "source": "GDELT (volume de couverture) + FRED (maillons macro)",
        "n_themes_mesures": len(disponibles),
        "n_themes_configures": len(themes),
        "intensite_max": intensite_max,
        "theme_dominant": theme_dominant,
        "themes": [t.to_dict() for t in themes],
        "chaine_de_transmission": chaine_de_transmission(
            series_macro, prix_or, intensite_max=intensite_max
        ),
        "deja_dans_les_prix": _deja_dans_les_prix(themes, z_score_prime),
    }
