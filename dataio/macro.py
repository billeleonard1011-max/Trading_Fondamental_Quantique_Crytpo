"""Client FRED (Federal Reserve Bank of St. Louis) et lecture du régime macro.

FRED est gratuit et documenté, mais exige une clé d'API. Elle est lue dans
la variable d'environnement ``FRED_API_KEY`` et n'apparaît jamais dans le
code ni dans les journaux.

Obtenir une clé : https://fredaccount.stlouisfed.org/apikeys
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

import numpy as np
import pandas as pd
import requests

_LOG: Final = logging.getLogger(__name__)

#: Point d'accès des observations de séries FRED.
URL_FRED: Final = "https://api.stlouisfed.org/fred/series/observations"

#: Délai maximal, en secondes, accordé à un appel réseau.
TIMEOUT: Final[float] = float(os.environ.get("HTTP_TIMEOUT", "20"))

#: Nombre d'observations dans une fenêtre glissante de deux ans ouvrés.
FENETRE_2_ANS: Final[int] = 504

#: Unités natives de certaines séries, nécessaires avant toute soustraction.
#: WALCL est publié en millions de dollars, RRPONTSYD en milliards.
FACTEUR_VERS_MILLIARDS: Final[dict[str, float]] = {
    "WALCL": 1e-3,       # millions -> milliards
    "RRPONTSYD": 1.0,    # déjà en milliards
    "WTREGEN": 1.0,      # déjà en milliards
}

__all__ = [
    "get_series",
    "get_many",
    "compute_macro_regime",
    "AxisReading",
    "MacroRegime",
]


def _cle_api() -> str | None:
    """Lit la clé FRED dans l'environnement.

    Returns:
        La clé, ou ``None`` si elle n'est pas définie.
    """
    cle = os.environ.get("FRED_API_KEY", "").strip()
    if not cle:
        _LOG.warning(
            "FRED_API_KEY absente de l'environnement : les séries macro seront vides."
        )
        return None
    return cle


def get_series(
    series_id: str, start: str = "2010-01-01", end: str | None = None
) -> pd.Series:
    """Récupère une série FRED.

    Les observations manquantes, que FRED encode par un point, sont écartées.

    Args:
        series_id: identifiant FRED (par exemple ``DGS10``).
        start: date de début au format ISO.
        end: date de fin au format ISO. Aujourd'hui par défaut.

    Returns:
        Série de flottants indexée par date et nommée ``series_id``. Vide en
        cas d'échec ou d'absence de clé.
    """
    vide = pd.Series(dtype="float64", name=series_id)
    cle = _cle_api()
    if cle is None:
        return vide

    parametres = {
        "series_id": series_id,
        "api_key": cle,
        "file_type": "json",
        "observation_start": start,
        "observation_end": end or date.today().strftime("%Y-%m-%d"),
    }
    try:
        reponse = requests.get(URL_FRED, params=parametres, timeout=TIMEOUT)
        reponse.raise_for_status()
        charge = reponse.json()
    except requests.RequestException as exc:
        _LOG.warning("FRED injoignable pour %s : %s", series_id, exc)
        return vide
    except ValueError as exc:
        _LOG.warning("Réponse FRED illisible pour %s : %s", series_id, exc)
        return vide

    observations = charge.get("observations", [])
    if not observations:
        _LOG.warning("FRED n'a renvoyé aucune observation pour %s.", series_id)
        return vide

    cadre = pd.DataFrame(observations)
    # FRED encode une valeur manquante par un point : to_numeric la neutralise.
    valeurs = pd.to_numeric(cadre["value"], errors="coerce")
    index = pd.to_datetime(cadre["date"], errors="coerce")

    serie = pd.Series(valeurs.to_numpy(), index=index, name=series_id, dtype="float64")
    serie = serie[serie.index.notna()].dropna().sort_index()
    serie.index.name = "date"
    _LOG.debug("%s : %d observations.", series_id, len(serie))
    return serie


def get_many(
    series_ids: list[str], start: str = "2010-01-01", end: str | None = None
) -> pd.DataFrame:
    """Récupère plusieurs séries FRED et les aligne sur un calendrier commun.

    Les fréquences diffèrent : ``DGS10`` est quotidienne, ``WALCL`` hebdomadaire,
    ``CPIAUCSL`` mensuelle. L'alignement se fait sur les jours ouvrés, complété
    vers l'avant (``ffill``) : chaque jour reprend la dernière valeur *publiée*,
    jamais une valeur future. C'est ce qui rend le DataFrame utilisable dans un
    backtest sans introduire de fuite d'information.

    Args:
        series_ids: identifiants FRED.
        start: date de début au format ISO.
        end: date de fin au format ISO.

    Returns:
        DataFrame aligné, une colonne par série. Vide si aucune série n'a été
        récupérée.
    """
    series: dict[str, pd.Series] = {}
    for identifiant in series_ids:
        serie = get_series(identifiant, start=start, end=end)
        if serie.empty:
            _LOG.warning("Série %s ignorée : vide.", identifiant)
            continue
        series[identifiant] = serie

    if not series:
        _LOG.warning("Aucune série macro récupérée.")
        return pd.DataFrame()

    cadre = pd.DataFrame(series).sort_index()
    calendrier = pd.bdate_range(cadre.index.min(), cadre.index.max(), name="date")
    # reindex + ffill : on ne remplit que vers l'avant, jamais vers l'arrière.
    return cadre.reindex(calendrier).ffill()


# ---------------------------------------------------------------------------
# Lecture qualitative du régime macro
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class AxisReading:
    """Lecture d'un axe du régime macro.

    Attributes:
        axe: nom de l'axe.
        valeur: valeur numérique la plus récente. ``None`` si indisponible.
        niveau: qualification courte, par exemple ``inversée`` ou ``tendu``.
        commentaire: phrase explicative destinée au rapport.
        disponible: ``False`` quand les séries nécessaires manquent.
    """

    axe: str
    valeur: float | None
    niveau: str
    commentaire: str
    disponible: bool = True


@dataclass(slots=True, frozen=True)
class MacroRegime:
    """Régime macro lu sur trois axes.

    Attributes:
        courbe_des_taux: pente 10 ans moins 2 ans.
        stress_credit: prime de risque haut rendement, en percentile.
        liquidite_nette: bilan de la Fed net des prises en pension.
        date_lecture: date de la dernière observation utilisée.
    """

    courbe_des_taux: AxisReading
    stress_credit: AxisReading
    liquidite_nette: AxisReading
    date_lecture: pd.Timestamp | None = None

    @property
    def axes(self) -> tuple[AxisReading, ...]:
        """Les trois axes, dans l'ordre de lecture."""
        return (self.courbe_des_taux, self.stress_credit, self.liquidite_nette)

    def to_dict(self) -> dict[str, Any]:
        """Sérialise la lecture pour un rapport ou un fichier JSON.

        Returns:
            Dictionnaire imbriqué, un bloc par axe.
        """
        return {
            "date_lecture": None if self.date_lecture is None else str(self.date_lecture.date()),
            "axes": {
                axe.axe: {
                    "valeur": axe.valeur,
                    "niveau": axe.niveau,
                    "commentaire": axe.commentaire,
                    "disponible": axe.disponible,
                }
                for axe in self.axes
            },
        }


def _indisponible(axe: str, series_requises: str) -> AxisReading:
    """Construit une lecture d'axe marquée indisponible.

    Args:
        axe: nom de l'axe.
        series_requises: séries FRED manquantes, listées dans le commentaire.

    Returns:
        Lecture neutre signalant l'absence de données.
    """
    return AxisReading(
        axe=axe,
        valeur=None,
        niveau="indisponible",
        commentaire=f"Série(s) requise(s) absente(s) : {series_requises}.",
        disponible=False,
    )


def _derniere_valeur(df: pd.DataFrame, colonne: str) -> float | None:
    """Renvoie la dernière valeur définie d'une colonne.

    Args:
        df: DataFrame de séries macro.
        colonne: nom de la colonne.

    Returns:
        La valeur, ou ``None`` si la colonne est absente ou entièrement vide.
    """
    if colonne not in df.columns:
        return None
    serie = df[colonne].dropna()
    return None if serie.empty else float(serie.iloc[-1])


def _lire_courbe_des_taux(df: pd.DataFrame) -> AxisReading:
    """Qualifie la pente de la courbe des taux à partir de ``T10Y2Y``.

    Une pente négative — le 2 ans rémunère plus que le 10 ans — traduit une
    anticipation de baisse des taux directeurs, donc de ralentissement. Le
    signal est historiquement précoce : l'inversion précède la récession de
    plusieurs trimestres, elle ne la date pas.

    Args:
        df: DataFrame contenant idéalement ``T10Y2Y``.

    Returns:
        Lecture de l'axe.
    """
    pente = _derniere_valeur(df, "T10Y2Y")
    if pente is None:
        dgs10, dgs2 = _derniere_valeur(df, "DGS10"), _derniere_valeur(df, "DGS2")
        if dgs10 is None or dgs2 is None:
            return _indisponible("courbe_des_taux", "T10Y2Y (ou DGS10 et DGS2)")
        pente = dgs10 - dgs2

    # Tendance sur environ un trimestre, pour distinguer une inversion qui
    # s'aggrave d'une pentification en cours.
    tendance = ""
    if "T10Y2Y" in df.columns:
        serie = df["T10Y2Y"].dropna()
        if len(serie) > 63:
            delta = float(serie.iloc[-1] - serie.iloc[-64])
            sens = "se repentifie" if delta > 0.05 else ("s'aplatit" if delta < -0.05 else "est stable")
            tendance = f" La pente {sens} sur trois mois ({delta:+.2f} pt)."

    if pente < 0.0:
        niveau = "inversée"
        commentaire = (
            f"Courbe inversée de {pente:.2f} point : le marché anticipe un "
            "ralentissement et des baisses de taux. Signal récessif, mais précoce."
        )
    elif pente < 0.5:
        niveau = "plate"
        commentaire = (
            f"Courbe quasi plate ({pente:.2f} pt) : sortie d'inversion ou "
            "fin de cycle, sans direction affirmée."
        )
    else:
        niveau = "pentue"
        commentaire = (
            f"Courbe pentue ({pente:.2f} pt) : configuration de reprise ou de "
            "prime de terme élevée."
        )
    return AxisReading("courbe_des_taux", pente, niveau, commentaire + tendance)


def _lire_stress_credit(df: pd.DataFrame) -> AxisReading:
    """Qualifie le stress du crédit via le percentile de ``BAMLH0A0HYM2``.

    Le niveau absolu du spread haut rendement se compare mal d'une décennie à
    l'autre. Son percentile sur deux ans répond à la seule question utile :
    le crédit est-il tendu *par rapport au régime récent* ?

    Args:
        df: DataFrame contenant idéalement ``BAMLH0A0HYM2``.

    Returns:
        Lecture de l'axe.
    """
    if "BAMLH0A0HYM2" not in df.columns:
        return _indisponible("stress_credit", "BAMLH0A0HYM2")

    serie = df["BAMLH0A0HYM2"].dropna()
    if serie.empty:
        return _indisponible("stress_credit", "BAMLH0A0HYM2")

    fenetre = serie.iloc[-FENETRE_2_ANS:]
    courant = float(fenetre.iloc[-1])
    if len(fenetre) < 60:
        return AxisReading(
            "stress_credit",
            courant,
            "historique insuffisant",
            f"Spread haut rendement à {courant:.2f} %, mais moins de 60 "
            "observations : le percentile ne veut rien dire.",
            disponible=False,
        )

    percentile = float((fenetre.to_numpy() <= courant).mean() * 100.0)
    if percentile >= 75.0:
        niveau = "tendu"
        commentaire = (
            f"Spread haut rendement à {courant:.2f} %, soit le {percentile:.0f}e "
            "percentile sur deux ans : le crédit se resserre, les actifs risqués "
            "sont vulnérables."
        )
    elif percentile <= 25.0:
        niveau = "calme"
        commentaire = (
            f"Spread haut rendement à {courant:.2f} %, soit le {percentile:.0f}e "
            "percentile sur deux ans : complaisance du crédit, peu de prime pour "
            "le risque."
        )
    else:
        niveau = "normal"
        commentaire = (
            f"Spread haut rendement à {courant:.2f} %, {percentile:.0f}e percentile "
            "sur deux ans : régime de crédit ordinaire."
        )
    return AxisReading("stress_credit", percentile, niveau, commentaire)


def _lire_liquidite_nette(df: pd.DataFrame) -> AxisReading:
    """Qualifie la liquidité nette : bilan de la Fed moins prises en pension.

    Calcul retenu : ``WALCL − RRPONTSYD``, converti en milliards de dollars.
    Attention aux unités : FRED publie ``WALCL`` en millions et ``RRPONTSYD``
    en milliards ; soustraire directement les deux séries donne un résultat
    faux d'un facteur mille.

    Limite connue : la mesure usuelle retranche aussi le compte du Trésor
    (``WTREGEN``). Il n'est pas inclus ici, conformément à la définition
    demandée. La série sert donc de tendance, pas de niveau absolu.

    Args:
        df: DataFrame contenant idéalement ``WALCL`` et ``RRPONTSYD``.

    Returns:
        Lecture de l'axe.
    """
    manquantes = [c for c in ("WALCL", "RRPONTSYD") if c not in df.columns]
    if manquantes:
        return _indisponible("liquidite_nette", ", ".join(manquantes))

    bilan = df["WALCL"] * FACTEUR_VERS_MILLIARDS["WALCL"]
    pensions = df["RRPONTSYD"] * FACTEUR_VERS_MILLIARDS["RRPONTSYD"]
    nette = (bilan - pensions).dropna()
    if nette.empty:
        return _indisponible("liquidite_nette", "WALCL, RRPONTSYD (aucune date commune)")

    courant = float(nette.iloc[-1])

    # Variation sur environ treize semaines ouvrées.
    horizon = min(63, len(nette) - 1)
    if horizon <= 0:
        return AxisReading(
            "liquidite_nette",
            courant,
            "historique insuffisant",
            f"Liquidité nette de {courant:,.0f} Md$, sans recul pour en lire la tendance.",
            disponible=False,
        )

    delta = courant - float(nette.iloc[-1 - horizon])
    delta_pct = delta / abs(float(nette.iloc[-1 - horizon])) * 100.0 if nette.iloc[-1 - horizon] else np.nan

    if delta > 0.0:
        niveau = "en expansion"
        commentaire = (
            f"Liquidité nette à {courant:,.0f} Md$, en hausse de {delta:,.0f} Md$ "
            f"({delta_pct:+.1f} %) sur un trimestre : vent porteur pour les actifs risqués."
        )
    elif delta < 0.0:
        niveau = "en contraction"
        commentaire = (
            f"Liquidité nette à {courant:,.0f} Md$, en baisse de {abs(delta):,.0f} Md$ "
            f"({delta_pct:+.1f} %) sur un trimestre : le drainage pèse sur les valorisations."
        )
    else:
        niveau = "stable"
        commentaire = f"Liquidité nette stable à {courant:,.0f} Md$ sur un trimestre."
    return AxisReading("liquidite_nette", courant, niveau, commentaire)


def compute_macro_regime(df: pd.DataFrame) -> MacroRegime:
    """Traduit les séries FRED en lecture qualitative sur trois axes.

    Les trois axes sont indépendants : un axe indisponible ne dégrade pas les
    autres, il est simplement marqué comme tel.

    Args:
        df: DataFrame issu de :func:`get_many`.

    Returns:
        Lecture du régime macro.
    """
    if df is None or df.empty:
        _LOG.warning("Aucune donnée macro : régime non calculable.")
        vide = "aucune série chargée"
        return MacroRegime(
            _indisponible("courbe_des_taux", vide),
            _indisponible("stress_credit", vide),
            _indisponible("liquidite_nette", vide),
        )

    derniere_date = df.index.max()
    return MacroRegime(
        courbe_des_taux=_lire_courbe_des_taux(df),
        stress_credit=_lire_stress_credit(df),
        liquidite_nette=_lire_liquidite_nette(df),
        date_lecture=pd.Timestamp(derniere_date) if derniere_date is not None else None,
    )
