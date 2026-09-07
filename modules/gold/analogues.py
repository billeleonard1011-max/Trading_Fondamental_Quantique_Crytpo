"""Précédents historiques : ce qui s'est passé les fois d'avant.

Pourquoi ce module existe
-------------------------
« Le z-score de juste valeur est à +1,8 » ne dit rien à personne. « Les
quatorze fois où l'or s'est trouvé dans cette configuration depuis 2010, il
a perdu 0,9 % en médiane sur vingt jours, et n'a monté que dans cinq cas sur
quatorze » est une information sur laquelle on peut travailler.

Le module cherche donc, dans l'histoire, les journées dont la configuration
ressemble le plus à celle du jour, puis regarde ce que l'or a fait ensuite.
Il ne prédit rien : il rapporte une distribution de résultats passés, avec
son étendue, ce qui permet de voir aussitôt si le résultat médian est
robuste ou s'il masque des cas violemment opposés.

Les deux garde-fous, et pourquoi ils ne sont pas négociables
-----------------------------------------------------------
1. **Séparation minimale entre précédents.** Les configurations de marché
   sont persistantes : si l'or est extrême un mardi, il l'est encore le
   mercredi et le jeudi. Sans filtre, les quinze « précédents » les plus
   proches seraient quinze jours consécutifs de la même semaine de 2013. On
   croirait disposer de quinze observations indépendantes alors qu'on en a
   une seule, et l'intervalle de confiance serait faux d'un facteur quatre.
   Le filtre impose un écart minimal entre deux précédents retenus.

2. **Nombre minimal de cas.** En dessous du seuil, le module refuse de
   conclure. Une médiane sur trois observations n'est pas une statistique,
   c'est une anecdote. Dire « je ne sais pas » est une réponse ; donner un
   chiffre auquel on ne croit pas n'en est pas une.

Un troisième garde-fou, moins visible, est appliqué silencieusement : les
dates trop récentes pour que leur rendement à vingt jours soit **entièrement
réalisé** sont exclues de la base. Sans cette exclusion, les précédents les
plus récents apporteraient des rendements tronqués, systématiquement biaisés
vers zéro.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final, Sequence

import numpy as np
import pandas as pd

_LOG: Final = logging.getLogger(__name__)

#: Variables décrivant une configuration de marché, dans l'ordre de lecture.
VARIABLES: Final[tuple[str, ...]] = (
    "z_fair_value",
    "percentile_cot",
    "regime_taux_reels",
    "stress_geopolitique",
)

#: Horizons de rendement, en séances.
HORIZONS: Final[tuple[int, ...]] = (1, 5, 20)

#: Nombre de précédents recherchés par défaut.
N_VOISINS: Final[int] = 15

#: Écart minimal entre deux précédents retenus, en jours calendaires.
SEPARATION_MIN_JOURS: Final[int] = 21

#: Nombre de cas sous lequel le module refuse de conclure.
MIN_CAS: Final[int] = 8

__all__ = [
    "Analogue",
    "construire_base",
    "find_analogues",
]


@dataclass(slots=True, frozen=True)
class Analogue:
    """Un précédent historique et ce qui a suivi.

    Attributes:
        date: date du précédent.
        distance: distance normalisée à la configuration du jour. Zéro
            signifierait une configuration identique.
        etat: valeurs des variables descriptives ce jour-là.
        rendements: rendement de l'or en pourcentage, par horizon.
        drawdown_max_pct: pire excursion sous le prix d'entrée sur le plus
            long horizon, en pourcentage. Toujours négatif ou nul.
    """

    date: pd.Timestamp
    distance: float
    etat: dict[str, float]
    rendements: dict[int, float]
    drawdown_max_pct: float | None

    def to_dict(self) -> dict[str, Any]:
        """Sérialise le précédent pour le rapport JSON."""
        return {
            "date": str(pd.Timestamp(self.date).date()),
            "distance": round(self.distance, 4),
            "etat": {k: round(v, 4) for k, v in self.etat.items()},
            "rendements_pct": {str(h): round(r, 3) for h, r in self.rendements.items()},
            "drawdown_max_pct": None if self.drawdown_max_pct is None else round(self.drawdown_max_pct, 3),
        }


# ---------------------------------------------------------------------------
# Construction de la base
# ---------------------------------------------------------------------------
def _percentile_glissant(serie: pd.Series, fenetre: int) -> pd.Series:
    """Rang glissant d'une série dans sa propre histoire récente.

    Le calcul est causal : le rang de la date ``t`` ne regarde que les
    observations jusqu'à ``t`` incluse.

    Args:
        serie: série à classer.
        fenetre: profondeur de la fenêtre glissante.

    Returns:
        Série des rangs, en pourcentage.
    """
    return (
        serie.rolling(window=fenetre, min_periods=max(20, fenetre // 4))
        .apply(lambda f: float((f <= f[-1]).mean() * 100.0), raw=True)
    )


def construire_base(
    historique_fair_value: pd.DataFrame,
    prix_or: pd.Series,
    cot: pd.Series | None = None,
    stress_geopolitique: pd.Series | None = None,
    debut: str = "2010-01-01",
    horizons: Sequence[int] = HORIZONS,
) -> tuple[pd.DataFrame, list[str]]:
    """Assemble la base des configurations passées.

    Args:
        historique_fair_value: sortie de
            :func:`modules.gold.fair_value.fair_value_history`, qui contient
            ``z_score`` et ``taux_reel``.
        prix_or: série du prix de l'or, pour les rendements futurs.
        cot: série du net *managed money*, hebdomadaire. Convertie en
            percentile glissant sur cinq ans puis reportée sur les jours
            ouvrés.
        stress_geopolitique: série d'intensité géopolitique. Presque toujours
            absente : GDELT ne se remonte pas jusqu'en 2010 par l'API
            publique. La variable est alors simplement retirée de la
            description, et son absence est signalée dans la valeur de retour.
        debut: début de l'historique retenu.
        horizons: horizons de rendement à calculer.

    Returns:
        Couple ``(base, variables_absentes)``. La base est indexée par date et
        contient les variables descriptives disponibles, les rendements futurs
        ``rendement_<h>j`` et ``drawdown_max_pct``.
    """
    if historique_fair_value is None or historique_fair_value.empty:
        _LOG.warning("Base des précédents non constructible : historique de juste valeur vide.")
        return pd.DataFrame(), list(VARIABLES)
    if prix_or is None or prix_or.dropna().empty:
        _LOG.warning("Base des précédents non constructible : prix de l'or absent.")
        return pd.DataFrame(), list(VARIABLES)

    base = pd.DataFrame(index=pd.DatetimeIndex(historique_fair_value.index).sort_values())
    base["z_fair_value"] = historique_fair_value["z_score"]

    # Le régime de taux réels est décrit par le *niveau* du taux réel : c'est
    # lui qui fixe le coût de portage de l'or, donc le contexte dans lequel
    # tout le reste se lit.
    if "taux_reel" in historique_fair_value.columns:
        base["regime_taux_reels"] = historique_fair_value["taux_reel"]

    absentes: list[str] = []

    if cot is not None and not cot.dropna().empty:
        percentile = _percentile_glissant(cot.dropna().sort_index(), fenetre=260)
        # Le COT est hebdomadaire et l'index quotidien : on reporte vers
        # l'avant, ce qui est légitime — le rapport de mardi reste la dernière
        # information connue jusqu'au suivant. Aucune valeur future n'est
        # utilisée.
        base["percentile_cot"] = percentile.reindex(
            base.index.union(percentile.index)
        ).ffill().reindex(base.index)
    else:
        absentes.append("percentile_cot")

    if stress_geopolitique is not None and not stress_geopolitique.dropna().empty:
        base["stress_geopolitique"] = (
            stress_geopolitique.dropna()
            .sort_index()
            .reindex(base.index.union(stress_geopolitique.index))
            .ffill()
            .reindex(base.index)
        )
    else:
        absentes.append("stress_geopolitique")

    # --- Rendements futurs -------------------------------------------------
    prix = prix_or.dropna().sort_index()
    prix = prix[~prix.index.duplicated(keep="last")]
    # Aligner le prix sur le calendrier de la base, sans remplissage arrière.
    prix_aligne = prix.reindex(base.index.union(prix.index)).ffill().reindex(base.index)

    horizon_max = max(int(h) for h in horizons)
    valeurs = prix_aligne.to_numpy(dtype="float64")

    for horizon in horizons:
        h = int(horizon)
        futur = np.full(valeurs.size, np.nan)
        if valeurs.size > h:
            futur[:-h] = valeurs[h:]
        base[f"rendement_{h}j"] = (futur / valeurs - 1.0) * 100.0

    # Pire excursion sous le prix d'entrée sur le plus long horizon. C'est ce
    # qu'un trader aurait effectivement subi entre l'entrée et la sortie, et
    # non l'écart entre deux extrêmes quelconques de la période.
    plus_bas = np.full(valeurs.size, np.nan)
    for i in range(valeurs.size - horizon_max):
        fenetre = valeurs[i + 1 : i + 1 + horizon_max]
        if fenetre.size:
            plus_bas[i] = float(np.nanmin(fenetre))
    base["drawdown_max_pct"] = np.minimum((plus_bas / valeurs - 1.0) * 100.0, 0.0)

    base = base.loc[base.index >= pd.Timestamp(debut)]

    # Les dates trop récentes n'ont pas de rendement à l'horizon le plus long :
    # les garder biaiserait la statistique vers zéro.
    base = base.dropna(subset=[f"rendement_{int(h)}j" for h in horizons])

    variables_presentes = [v for v in VARIABLES if v in base.columns]
    base = base.dropna(subset=variables_presentes)

    if absentes:
        _LOG.warning(
            "Base des précédents construite sans %s : variable(s) sans historique exploitable.",
            ", ".join(absentes),
        )
    _LOG.info(
        "Base des précédents : %d configuration(s) depuis %s, décrites par %s.",
        len(base),
        debut,
        ", ".join(variables_presentes),
    )
    return base, absentes


# ---------------------------------------------------------------------------
# Recherche des précédents
# ---------------------------------------------------------------------------
def find_analogues(
    current_state: dict[str, float],
    base: pd.DataFrame,
    n: int = N_VOISINS,
    separation_min_jours: int = SEPARATION_MIN_JOURS,
    min_cas: int = MIN_CAS,
    horizons: Sequence[int] = HORIZONS,
) -> dict[str, Any]:
    """Retrouve les configurations passées les plus proches de celle du jour.

    Les variables sont normalisées avant d'être comparées : sans cela, le
    percentile COT (de 0 à 100) écraserait le z-score de juste valeur (de -3
    à +3), et la distance ne mesurerait plus que le positionnement.

    Args:
        current_state: configuration du jour. Seules les clés également
            présentes dans la base sont utilisées.
        base: sortie de :func:`construire_base`.
        n: nombre de précédents recherchés.
        separation_min_jours: écart minimal entre deux précédents retenus.
        min_cas: nombre de cas sous lequel le module refuse de conclure.
        horizons: horizons de rendement à agréger.

    Returns:
        Dictionnaire avec ``disponible``, ``motif``, la liste des précédents
        et l'agrégation. ``disponible`` est ``False`` — et l'agrégation
        absente — dès que le nombre de cas retenus passe sous ``min_cas``.
    """
    refus = {
        "disponible": False,
        "n_cas": 0,
        "precedents": [],
        "agregation": {},
        "variables_utilisees": [],
    }

    if base is None or base.empty:
        return {**refus, "motif": "base des précédents vide"}

    communes = [v for v in VARIABLES if v in base.columns and v in current_state]
    communes = [v for v in communes if current_state.get(v) is not None]
    if not communes:
        return {
            **refus,
            "motif": (
                "aucune variable descriptive commune entre l'état du jour et la base "
                f"(base : {', '.join(c for c in base.columns if c in VARIABLES)})"
            ),
        }

    echantillon = base[communes].dropna()
    if len(echantillon) < min_cas:
        return {
            **refus,
            "motif": f"{len(echantillon)} configuration(s) dans la base, {min_cas} requises",
            "variables_utilisees": communes,
        }

    # --- Normalisation -----------------------------------------------------
    moyennes = echantillon.mean()
    ecarts = echantillon.std(ddof=0).replace(0.0, np.nan)
    if ecarts.isna().any():
        constantes = list(ecarts[ecarts.isna()].index)
        _LOG.warning("Variable(s) constante(s) écartée(s) de la distance : %s", ", ".join(constantes))
        communes = [c for c in communes if c not in constantes]
        if not communes:
            return {**refus, "motif": "toutes les variables descriptives sont constantes"}
        echantillon = echantillon[communes]
        moyennes, ecarts = echantillon.mean(), echantillon.std(ddof=0)

    normalise = (echantillon - moyennes) / ecarts
    cible = np.array(
        [(float(current_state[v]) - float(moyennes[v])) / float(ecarts[v]) for v in communes],
        dtype="float64",
    )

    distances = pd.Series(
        np.sqrt(((normalise.to_numpy(dtype="float64") - cible) ** 2).sum(axis=1)),
        index=echantillon.index,
        name="distance",
    ).sort_values()

    # --- Garde-fou 1 : séparation minimale ---------------------------------
    retenues: list[pd.Timestamp] = []
    separation = pd.Timedelta(days=int(separation_min_jours))
    for date_candidate in distances.index:
        horodatage = pd.Timestamp(date_candidate)
        if all(abs(horodatage - deja) >= separation for deja in retenues):
            retenues.append(horodatage)
        if len(retenues) >= int(n):
            break

    # --- Garde-fou 2 : nombre minimal de cas -------------------------------
    if len(retenues) < min_cas:
        _LOG.warning(
            "Précédents refusés : %d cas après filtre de séparation, %d requis.",
            len(retenues),
            min_cas,
        )
        return {
            **refus,
            "motif": (
                f"{len(retenues)} précédent(s) distincts après application de l'écart "
                f"minimal de {separation_min_jours} jours, {min_cas} requis. "
                "Configuration trop rare pour en tirer une statistique."
            ),
            "variables_utilisees": communes,
        }

    horizons_entiers = [int(h) for h in horizons]
    precedents = [
        Analogue(
            date=horodatage,
            distance=float(distances.loc[horodatage]),
            etat={v: float(base.loc[horodatage, v]) for v in communes},
            rendements={
                h: float(base.loc[horodatage, f"rendement_{h}j"])
                for h in horizons_entiers
                if f"rendement_{h}j" in base.columns
            },
            drawdown_max_pct=(
                float(base.loc[horodatage, "drawdown_max_pct"])
                if "drawdown_max_pct" in base.columns
                and pd.notna(base.loc[horodatage, "drawdown_max_pct"])
                else None
            ),
        )
        for horodatage in retenues
    ]
    precedents.sort(key=lambda a: a.distance)

    return {
        "disponible": True,
        "motif": "",
        "n_cas": len(precedents),
        "variables_utilisees": communes,
        "separation_min_jours": int(separation_min_jours),
        "min_cas": int(min_cas),
        "periode_base": {
            "debut": str(pd.Timestamp(base.index.min()).date()),
            "fin": str(pd.Timestamp(base.index.max()).date()),
            "n_configurations": int(len(base)),
        },
        "precedents": [a.to_dict() for a in precedents],
        "agregation": _agreger(precedents, horizons_entiers),
    }


def _agreger(precedents: list[Analogue], horizons: list[int]) -> dict[str, Any]:
    """Résume la distribution des rendements des précédents.

    L'étendue entre le meilleur et le pire cas est publiée au même rang que
    la médiane, et volontairement : une médiane de +0,4 % qui recouvre des
    cas allant de -9 % à +12 % ne dit pas la même chose qu'une médiane
    identique sur des cas serrés entre -1 % et +2 %.

    Args:
        precedents: précédents retenus.
        horizons: horizons à résumer.

    Returns:
        Dictionnaire par horizon, plus le drawdown médian.
    """
    resume: dict[str, Any] = {}

    for horizon in horizons:
        valeurs = np.array(
            [a.rendements[horizon] for a in precedents if horizon in a.rendements],
            dtype="float64",
        )
        valeurs = valeurs[np.isfinite(valeurs)]
        if valeurs.size == 0:
            resume[f"{horizon}j"] = {"disponible": False, "motif": "aucun rendement calculable"}
            continue

        resume[f"{horizon}j"] = {
            "disponible": True,
            "n_cas": int(valeurs.size),
            "rendement_median_pct": round(float(np.median(valeurs)), 3),
            "rendement_moyen_pct": round(float(valeurs.mean()), 3),
            "proportion_haussiers": round(float((valeurs > 0.0).mean()), 3),
            "pire_pct": round(float(valeurs.min()), 3),
            "meilleur_pct": round(float(valeurs.max()), 3),
            "etendue_pct": round(float(valeurs.max() - valeurs.min()), 3),
        }

    drawdowns = np.array(
        [a.drawdown_max_pct for a in precedents if a.drawdown_max_pct is not None],
        dtype="float64",
    )
    resume["drawdown"] = (
        {
            "disponible": True,
            "median_pct": round(float(np.median(drawdowns)), 3),
            "pire_pct": round(float(drawdowns.min()), 3),
        }
        if drawdowns.size
        else {"disponible": False, "motif": "aucun drawdown calculable"}
    )
    return resume
