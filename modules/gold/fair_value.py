"""Modèle de juste valeur de l'or : taux réels et dollar.

Thèse du module
---------------
L'or ne rapporte aucun intérêt. Le détenir coûte donc le rendement réel
auquel on renonce : quand le taux réel monte, l'or devient cher à porter.
Il est par ailleurs coté en dollars : quand le dollar s'apprécie, la même
once vaut mécaniquement moins de dollars.

Ces deux variables expliquent l'essentiel des mouvements de l'or. Le module
ne cherche pas à prédire le prix, mais à mesurer **ce que ce modèle simple
n'explique pas** : le résidu. Ce résidu est la prime que le marché paie pour
autre chose — risque géopolitique, achats de banques centrales, défiance
envers le dollar.

C'est cette prime qui porte l'information exploitable. Quand elle est déjà
à deux écarts-types, une mauvaise nouvelle de plus ne fait presque rien
monter le prix, alors qu'une simple détente le fait beaucoup retomber :
l'asymétrie du risque s'est inversée sans que le prix ait bougé.

Spécification
-------------
    log(or) = a + b1 · taux_reel + b2 · log(dollar) + résidu

Le taux réel entre en niveau, parce qu'il peut être négatif et n'a donc pas
de logarithme. Le dollar entre en logarithme, parce que c'est un indice dont
seule la variation relative a un sens. Les coefficients se lisent :

* ``b1`` : variation en % de l'or pour 1 point de taux réel (attendu négatif) ;
* ``b2`` : variation en % de l'or pour 1 % de dollar (attendu négatif).

Causalité
---------
Toute estimation à la date ``t`` est faite sur les seules observations
allant jusqu'à ``t`` inclus. La troncature est appliquée dès la première
ligne de :func:`estimate_fair_value`, avant tout calcul. La propriété est
vérifiée mécaniquement par ``tests/test_fair_value.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np
import pandas as pd

_LOG: Final = logging.getLogger(__name__)

#: Nom des colonnes attendues par le modèle, après préparation.
COL_OR: Final = "or_usd"
COL_TAUX: Final = "taux_reel"
COL_DOLLAR: Final = "dollar"

#: Fenêtre glissante par défaut : trois ans de jours ouvrés.
FENETRE_DEFAUT: Final[int] = 756

#: Nombre minimal d'observations sous lequel la régression est refusée.
MIN_OBSERVATIONS: Final[int] = 250

#: R² sous lequel le modèle est déclaré non fiable.
SEUIL_R2_DEFAUT: Final[float] = 0.50

__all__ = [
    "FairValue",
    "preparer_donnees",
    "estimate_fair_value",
    "fair_value_history",
    "zscore",
]


# ---------------------------------------------------------------------------
# Outils numériques
# ---------------------------------------------------------------------------
def zscore(valeur: float, echantillon: np.ndarray | pd.Series) -> float:
    """Écart-type d'écart entre une valeur et la distribution d'un échantillon.

    L'écart-type est calculé sur l'échantillon complet (``ddof=0``) : c'est
    la dispersion observée de la fenêtre, pas l'estimation d'un paramètre de
    population.

    Args:
        valeur: la valeur à situer.
        echantillon: distribution de référence.

    Returns:
        Le z-score. ``0.0`` si l'échantillon est vide ou de dispersion nulle,
        car dans ce cas aucun écart n'est mesurable.
    """
    donnees = np.asarray(echantillon, dtype="float64")
    donnees = donnees[np.isfinite(donnees)]
    if donnees.size == 0:
        return 0.0

    ecart_type = float(donnees.std(ddof=0))
    if ecart_type <= 0.0 or not np.isfinite(ecart_type):
        # Une distribution constante ne permet aucune mise à l'échelle :
        # renvoyer l'infini ou une division par zéro serait pire que 0.
        return 0.0
    return float((valeur - donnees.mean()) / ecart_type)


def _ols(matrice: np.ndarray, cible: np.ndarray) -> tuple[np.ndarray, float, np.ndarray]:
    """Régression linéaire ordinaire par moindres carrés.

    ``numpy.linalg.lstsq`` est utilisé plutôt que l'inversion de la matrice
    normale : il passe par une décomposition en valeurs singulières, qui reste
    stable même quand les régresseurs sont fortement corrélés — ce qui est
    exactement le cas du taux réel et du dollar.

    Args:
        matrice: régresseurs, constante **incluse**, de forme (n, k).
        cible: variable expliquée, de forme (n,).

    Returns:
        Triplet ``(coefficients, r2, résidus)``.
    """
    coefficients, *_ = np.linalg.lstsq(matrice, cible, rcond=None)
    ajuste = matrice @ coefficients
    residus = cible - ajuste

    somme_residus = float(np.sum(residus**2))
    somme_totale = float(np.sum((cible - cible.mean()) ** 2))
    # Une cible constante donne une somme totale nulle : le R² n'est pas
    # défini, on le déclare nul plutôt que de diviser par zéro.
    r2 = 0.0 if somme_totale <= 0.0 else 1.0 - somme_residus / somme_totale
    return coefficients, r2, residus


# ---------------------------------------------------------------------------
# Résultat
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class FairValue:
    """Lecture de juste valeur à une date donnée.

    Attributes:
        date: date de l'estimation.
        prix_observe: prix de l'or effectivement coté.
        prix_theorique: prix impliqué par le modèle.
        ecart_usd: ``prix_observe - prix_theorique``.
        ecart_pct: écart rapporté au prix théorique, en pourcentage.
        z_score: résidu du jour rapporté à la dispersion des résidus de la
            fenêtre. C'est la mesure centrale du système.
        percentile_historique: rang de l'écart en pourcentage dans son
            historique depuis la date de début configurée. ``None`` si
            l'historique n'a pas été fourni.
        r2: pouvoir explicatif du modèle sur la fenêtre courante.
        fiable: ``False`` si le R² passe sous le seuil. Le z-score devient
            alors ininterprétable et le reste du système doit l'ignorer.
        seuil_r2: seuil appliqué, repris dans la sortie pour que le lecteur
            sache à quoi le R² a été comparé.
        n_observations: taille effective de la fenêtre d'estimation.
        coefficients: ``constante``, ``taux_reel``, ``dollar``.
        contributions: pour chaque variable, son effet sur le prix théorique.
        facteur_dominant: variable dont la contribution est la plus forte en
            valeur absolue.
        disponible: ``False`` quand l'estimation n'a pas pu être faite.
        motif: raison de l'indisponibilité, vide sinon.
    """

    date: pd.Timestamp | None
    prix_observe: float | None = None
    prix_theorique: float | None = None
    ecart_usd: float | None = None
    ecart_pct: float | None = None
    z_score: float | None = None
    percentile_historique: float | None = None
    r2: float | None = None
    fiable: bool = False
    seuil_r2: float = SEUIL_R2_DEFAUT
    n_observations: int = 0
    coefficients: dict[str, float] = field(default_factory=dict)
    contributions: dict[str, dict[str, float]] = field(default_factory=dict)
    facteur_dominant: str = ""
    disponible: bool = False
    motif: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Sérialise la lecture pour le rapport JSON."""
        return {
            "date": None if self.date is None else str(pd.Timestamp(self.date).date()),
            "disponible": self.disponible,
            "motif": self.motif,
            "prix_observe": self.prix_observe,
            "prix_theorique": self.prix_theorique,
            "ecart_usd": self.ecart_usd,
            "ecart_pct": self.ecart_pct,
            "z_score": self.z_score,
            "percentile_historique": self.percentile_historique,
            "r2": self.r2,
            "fiable": self.fiable,
            "seuil_r2": self.seuil_r2,
            "n_observations": self.n_observations,
            "coefficients": dict(self.coefficients),
            "contributions": {k: dict(v) for k, v in self.contributions.items()},
            "facteur_dominant": self.facteur_dominant,
            "lecture": self.lecture(),
        }

    def lecture(self) -> str:
        """Phrase de synthèse, destinée au rapport et au mode dégradé."""
        if not self.disponible:
            return f"Juste valeur non calculable : {self.motif}"
        if not self.fiable:
            return (
                f"Le modèle n'explique plus que {self.r2:.0%} des mouvements de l'or "
                f"(seuil {self.seuil_r2:.0%}) : l'écart de {self.ecart_pct:+.1f} % "
                "existe mais ne peut pas être interprété comme une prime de risque."
            )
        sens = "au-dessus" if (self.ecart_usd or 0.0) >= 0.0 else "en dessous"
        return (
            f"L'or cote {abs(self.ecart_usd or 0.0):,.0f} $ {sens} de sa juste valeur "
            f"({self.ecart_pct:+.1f} %), soit {self.z_score:+.2f} écart-type. "
            f"Le modèle explique {self.r2:.0%} des mouvements sur "
            f"{self.n_observations} séances."
        )


def _indisponible(date: pd.Timestamp | None, motif: str, seuil_r2: float) -> FairValue:
    """Construit un résultat marqué indisponible.

    Args:
        date: date visée, si elle est connue.
        motif: raison, reprise telle quelle dans la sortie JSON.
        seuil_r2: seuil configuré, conservé pour information.

    Returns:
        Lecture neutre.
    """
    _LOG.warning("Juste valeur indisponible : %s", motif)
    return FairValue(date=date, motif=motif, seuil_r2=seuil_r2)


# ---------------------------------------------------------------------------
# Préparation des données
# ---------------------------------------------------------------------------
def preparer_donnees(
    prix_or: pd.Series,
    taux_reel: pd.Series,
    dollar: pd.Series,
) -> pd.DataFrame:
    """Aligne les trois séries sur un calendrier commun.

    Les trois sources n'ont ni le même calendrier ni les mêmes jours fériés :
    l'or cote des jours où FRED ne publie pas, et inversement. L'alignement se
    fait par jointure interne sur les dates réellement communes, sans aucun
    remplissage : compléter vers l'avant une variable explicative reviendrait
    à régresser l'or du jour sur un dollar de la veille sans le dire.

    Args:
        prix_or: prix de l'once, indexé par date.
        taux_reel: taux réel 10 ans en pourcentage (``DFII10``).
        dollar: indice du dollar (``DTWEXBGS``).

    Returns:
        DataFrame trié, colonnes ``or_usd``, ``taux_reel``, ``dollar``. Vide si
        l'intersection des trois calendriers l'est.
    """
    vide = pd.DataFrame(columns=[COL_OR, COL_TAUX, COL_DOLLAR])
    if prix_or is None or taux_reel is None or dollar is None:
        return vide

    cadre = pd.DataFrame(
        {
            COL_OR: pd.to_numeric(prix_or, errors="coerce"),
            COL_TAUX: pd.to_numeric(taux_reel, errors="coerce"),
            COL_DOLLAR: pd.to_numeric(dollar, errors="coerce"),
        }
    ).dropna()

    if cadre.empty:
        return vide

    # Un prix ou un indice négatif ou nul rendrait le logarithme indéfini.
    cadre = cadre[(cadre[COL_OR] > 0.0) & (cadre[COL_DOLLAR] > 0.0)]
    cadre.index = pd.to_datetime(cadre.index)
    cadre = cadre[~cadre.index.duplicated(keep="last")].sort_index()
    cadre.index.name = "date"
    return cadre


# ---------------------------------------------------------------------------
# Estimation
# ---------------------------------------------------------------------------
def estimate_fair_value(
    donnees: pd.DataFrame,
    date: pd.Timestamp | str | None = None,
    fenetre: int = FENETRE_DEFAUT,
    min_observations: int = MIN_OBSERVATIONS,
    seuil_r2: float = SEUIL_R2_DEFAUT,
    ecarts_historiques: pd.Series | None = None,
) -> FairValue:
    """Estime la juste valeur de l'or à une date, sans regarder au-delà.

    La première opération est la troncature à ``date`` : tout ce qui suit ne
    voit strictement rien du futur. C'est la seule garantie qui compte, et
    elle est vérifiée par un test dédié.

    Args:
        donnees: sortie de :func:`preparer_donnees`.
        date: date d'estimation. Dernière date disponible par défaut.
        fenetre: nombre d'observations de la fenêtre glissante.
        min_observations: taille minimale acceptée.
        seuil_r2: R² sous lequel le modèle est déclaré non fiable.
        ecarts_historiques: série des écarts en pourcentage déjà calculés,
            pour situer l'écart du jour en percentile. Elle est elle-même
            tronquée à ``date`` avant usage.

    Returns:
        Lecture de juste valeur, éventuellement marquée indisponible.
    """
    if donnees is None or donnees.empty:
        return _indisponible(None, "aucune donnée alignée entre or, taux réel et dollar", seuil_r2)

    manquantes = [c for c in (COL_OR, COL_TAUX, COL_DOLLAR) if c not in donnees.columns]
    if manquantes:
        return _indisponible(None, f"colonnes absentes : {', '.join(manquantes)}", seuil_r2)

    # --- Troncature : rien au-delà de la date d'estimation. ----------------
    cible_date = pd.Timestamp(date) if date is not None else pd.Timestamp(donnees.index.max())
    echantillon = donnees.loc[donnees.index <= cible_date]
    if echantillon.empty:
        return _indisponible(cible_date, f"aucune observation au {cible_date.date()} ou avant", seuil_r2)

    date_effective = pd.Timestamp(echantillon.index.max())
    fenetre_effective = echantillon.iloc[-int(fenetre):]
    n = len(fenetre_effective)
    if n < min_observations:
        return _indisponible(
            date_effective,
            f"{n} observation(s) dans la fenêtre, {min_observations} requises",
            seuil_r2,
        )

    # --- Régression --------------------------------------------------------
    log_or = np.log(fenetre_effective[COL_OR].to_numpy(dtype="float64"))
    taux = fenetre_effective[COL_TAUX].to_numpy(dtype="float64")
    log_dollar = np.log(fenetre_effective[COL_DOLLAR].to_numpy(dtype="float64"))

    matrice = np.column_stack([np.ones(n), taux, log_dollar])
    try:
        coefficients, r2, residus = _ols(matrice, log_or)
    except np.linalg.LinAlgError as exc:
        return _indisponible(date_effective, f"régression non convergente : {exc}", seuil_r2)

    if not np.all(np.isfinite(coefficients)):
        return _indisponible(date_effective, "coefficients non finis (régresseurs colinéaires)", seuil_r2)

    constante, b_taux, b_dollar = (float(c) for c in coefficients)

    prix_observe = float(fenetre_effective[COL_OR].iloc[-1])
    log_theorique = float(matrice[-1] @ coefficients)
    prix_theorique = float(np.exp(log_theorique))
    ecart_usd = prix_observe - prix_theorique
    ecart_pct = ecart_usd / prix_theorique * 100.0

    # Le z-score porte sur le résidu, pas sur l'écart en dollars : c'est le
    # résidu qui est centré par construction, donc directement comparable à
    # la dispersion de la fenêtre.
    z = zscore(float(residus[-1]), residus)

    # --- Contributions -----------------------------------------------------
    # Avec une constante, la moyenne des valeurs ajustées égale la moyenne de
    # log(or) sur la fenêtre. En écrivant chaque variable en écart à sa
    # moyenne, le log du prix théorique se décompose exactement en :
    #     log_theorique = moyenne_fenetre + contribution_taux + contribution_dollar
    # Chaque contribution se lit donc « ce que cette variable ajoute ou retire
    # par rapport à un jour moyen de la fenêtre ».
    moyenne_log = float(log_or.mean())
    prix_moyen = float(np.exp(moyenne_log))
    contribution_taux = b_taux * (taux[-1] - float(taux.mean()))
    contribution_dollar = b_dollar * (log_dollar[-1] - float(log_dollar.mean()))

    def _bloc(contribution: float, coefficient: float, valeur: float) -> dict[str, float]:
        """Met une contribution en forme lisible.

        L'effet en dollars est calculé par rapport au prix moyen de la fenêtre.
        Les contributions s'additionnent exactement en logarithme, seulement
        approximativement en dollars : c'est une conséquence de l'exponentielle,
        pas une erreur de calcul.
        """
        # Conversion explicite en flottant Python : les scalaires numpy ne
        # sont pas tous sérialisables en JSON — np.float64 hérite de float,
        # mais np.int64 n'hérite pas de int. Ne pas dépendre de ce détail.
        return {
            "coefficient": float(coefficient),
            "valeur_courante": float(valeur),
            "contribution_log": float(contribution),
            "effet_pct": (float(np.exp(contribution)) - 1.0) * 100.0,
            "effet_usd": prix_moyen * (float(np.exp(contribution)) - 1.0),
        }

    contributions = {
        "taux_reel": _bloc(contribution_taux, b_taux, float(taux[-1])),
        "dollar": _bloc(contribution_dollar, b_dollar, float(fenetre_effective[COL_DOLLAR].iloc[-1])),
    }
    dominant = max(contributions, key=lambda k: abs(contributions[k]["contribution_log"]))

    # --- Percentile historique de l'écart ----------------------------------
    percentile: float | None = None
    if ecarts_historiques is not None and not ecarts_historiques.empty:
        passe = ecarts_historiques.loc[ecarts_historiques.index <= date_effective].dropna()
        if len(passe) >= 60:
            percentile = float((passe.to_numpy() <= ecart_pct).mean() * 100.0)
        else:
            _LOG.debug(
                "Percentile historique ignoré : %d point(s), 60 requis.", len(passe)
            )

    fiable = bool(r2 >= seuil_r2)
    if not fiable:
        _LOG.warning(
            "Modèle de juste valeur peu explicatif au %s : R² = %.2f < %.2f.",
            date_effective.date(),
            r2,
            seuil_r2,
        )

    return FairValue(
        date=date_effective,
        prix_observe=prix_observe,
        prix_theorique=prix_theorique,
        ecart_usd=ecart_usd,
        ecart_pct=ecart_pct,
        z_score=z,
        percentile_historique=percentile,
        r2=float(r2),
        fiable=fiable,
        seuil_r2=seuil_r2,
        n_observations=n,
        coefficients={
            "constante": constante,
            "taux_reel": b_taux,
            "dollar": b_dollar,
        },
        contributions=contributions,
        facteur_dominant=dominant,
        disponible=True,
    )


def fair_value_history(
    donnees: pd.DataFrame,
    fenetre: int = FENETRE_DEFAUT,
    min_observations: int = MIN_OBSERVATIONS,
    pas: int = 1,
) -> pd.DataFrame:
    """Rejoue le modèle jour après jour sur tout l'historique.

    Sert à deux choses : situer l'écart du jour en percentile depuis 2010, et
    fournir à :mod:`modules.gold.analogues` la description des configurations
    passées. Chaque ligne est estimée **comme elle l'aurait été ce jour-là**,
    sur la seule fenêtre qui la précède.

    Le coût est celui d'une régression de taille ``fenetre × 3`` par date : sur
    quinze ans d'historique quotidien, quelques dixièmes de seconde.

    Args:
        donnees: sortie de :func:`preparer_donnees`.
        fenetre: fenêtre glissante d'estimation.
        min_observations: taille minimale de fenêtre.
        pas: n'estimer qu'une date sur ``pas``, pour accélérer un usage
            exploratoire. Laisser à 1 en production.

    Returns:
        DataFrame indexé par date, colonnes ``prix_observe``,
        ``prix_theorique``, ``ecart_usd``, ``ecart_pct``, ``z_score``, ``r2``,
        ``taux_reel``, ``dollar``. Vide si l'historique est trop court.
    """
    if donnees is None or donnees.empty or len(donnees) < min_observations:
        _LOG.warning(
            "Historique de juste valeur non calculable : %d observation(s).",
            0 if donnees is None else len(donnees),
        )
        return pd.DataFrame()

    log_or_total = np.log(donnees[COL_OR].to_numpy(dtype="float64"))
    taux_total = donnees[COL_TAUX].to_numpy(dtype="float64")
    log_dollar_total = np.log(donnees[COL_DOLLAR].to_numpy(dtype="float64"))
    prix_total = donnees[COL_OR].to_numpy(dtype="float64")
    dollar_total = donnees[COL_DOLLAR].to_numpy(dtype="float64")

    lignes: list[dict[str, Any]] = []
    dates: list[pd.Timestamp] = []

    for i in range(min_observations - 1, len(donnees), max(int(pas), 1)):
        debut = max(0, i + 1 - int(fenetre))
        # Bornes explicites : la tranche s'arrête à i inclus, jamais au-delà.
        y = log_or_total[debut : i + 1]
        x_taux = taux_total[debut : i + 1]
        x_dollar = log_dollar_total[debut : i + 1]
        n = y.size
        if n < min_observations:
            continue

        matrice = np.column_stack([np.ones(n), x_taux, x_dollar])
        try:
            coefficients, r2, residus = _ols(matrice, y)
        except np.linalg.LinAlgError:
            continue
        if not np.all(np.isfinite(coefficients)):
            continue

        prix_theorique = float(np.exp(matrice[-1] @ coefficients))
        prix_observe = float(prix_total[i])
        ecart_usd = prix_observe - prix_theorique

        lignes.append(
            {
                "prix_observe": prix_observe,
                "prix_theorique": prix_theorique,
                "ecart_usd": ecart_usd,
                "ecart_pct": ecart_usd / prix_theorique * 100.0,
                "z_score": zscore(float(residus[-1]), residus),
                "r2": float(r2),
                "taux_reel": float(taux_total[i]),
                "dollar": float(dollar_total[i]),
            }
        )
        dates.append(pd.Timestamp(donnees.index[i]))

    if not lignes:
        return pd.DataFrame()

    historique = pd.DataFrame(lignes, index=pd.DatetimeIndex(dates, name="date"))
    _LOG.info("Historique de juste valeur : %d date(s) estimée(s).", len(historique))
    return historique
