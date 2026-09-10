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

#: Nombre de séances par an et par mois, pour les variations en glissement.
#: Les séries mensuelles (CPIAUCSL, UNRATE) sont réindexées en jours ouvrés
#: par :func:`get_many` : un an s'y compte en séances, pas en douze points.
JOURS_OUVRES_PAR_AN: Final[int] = 252
JOURS_OUVRES_PAR_MOIS: Final[int] = 21

#: Groupes sectoriels de config/universe.yaml, repris ici pour que le calcul
#: de rotation cyclique/défensif n'impose pas de charger le fichier.
GROUPE_CYCLIQUES: Final[tuple[str, ...]] = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLY")
GROUPE_DEFENSIFS: Final[tuple[str, ...]] = ("XLP", "XLRE", "XLU", "XLV")

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
    "rediger_contexte_macro",
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
def _fr(valeur: float, decimales: int = 2, signe: bool = False) -> str:
    """Formate un nombre à la française : virgule décimale.

    Les textes du projet sont en français et lus tels quels sur le site :
    « 16,5 », pas « 16.5 ». Même convention que site/js/format.js et que les
    alertes du scanner en direct.

    Args:
        valeur: nombre à formater.
        decimales: nombre de décimales.
        signe: forcer le signe, même positif.

    Returns:
        Le nombre formaté.
    """
    texte = f"{valeur:+.{decimales}f}" if signe else f"{valeur:.{decimales}f}"
    return texte.replace(".", ",")


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
    """Régime macro lu sur sept axes.

    Les trois premiers décrivent les conditions financières, les trois
    suivants le contexte économique factuel, le dernier ce que les
    investisseurs en font.

    Attributes:
        courbe_des_taux: pente 10 ans moins 2 ans.
        stress_credit: prime de risque haut rendement, en percentile.
        liquidite_nette: bilan de la Fed net des prises en pension.
        inflation: glissement annuel de l'indice des prix.
        chomage: taux de chômage et son inflexion sur un an.
        petrole: variation du brut WTI sur un mois.
        appetit_risque: recherche de rendement ou de sécurité.
        date_lecture: date de la dernière observation utilisée.
    """

    courbe_des_taux: AxisReading
    stress_credit: AxisReading
    liquidite_nette: AxisReading
    inflation: AxisReading | None = None
    chomage: AxisReading | None = None
    petrole: AxisReading | None = None
    appetit_risque: AxisReading | None = None
    date_lecture: pd.Timestamp | None = None

    @property
    def axes(self) -> tuple[AxisReading, ...]:
        """Les axes renseignés, dans l'ordre de lecture."""
        tous = (
            self.courbe_des_taux, self.stress_credit, self.liquidite_nette,
            self.inflation, self.chomage, self.petrole, self.appetit_risque,
        )
        return tuple(axe for axe in tous if axe is not None)

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
            f"Courbe inversée de {_fr(pente, 2)} point : le marché anticipe un "
            "ralentissement et des baisses de taux. Signal récessif, mais précoce."
        )
    elif pente < 0.5:
        niveau = "plate"
        commentaire = (
            f"Courbe quasi plate ({_fr(pente, 2)} pt) : sortie d'inversion ou "
            "fin de cycle, sans direction affirmée."
        )
    else:
        niveau = "pentue"
        commentaire = (
            f"Courbe pentue ({_fr(pente, 2)} pt) : configuration de reprise ou de "
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
            f"Spread haut rendement à {_fr(courant, 2)} %, mais moins de 60 "
            "observations : le percentile ne veut rien dire.",
            disponible=False,
        )

    percentile = float((fenetre.to_numpy() <= courant).mean() * 100.0)
    if percentile >= 75.0:
        niveau = "tendu"
        commentaire = (
            f"Spread haut rendement à {_fr(courant, 2)} %, soit le {_fr(percentile, 0)}e "
            "percentile sur deux ans : le crédit se resserre, les actifs risqués "
            "sont vulnérables."
        )
    elif percentile <= 25.0:
        niveau = "calme"
        commentaire = (
            f"Spread haut rendement à {_fr(courant, 2)} %, soit le {_fr(percentile, 0)}e "
            "percentile sur deux ans : complaisance du crédit, peu de prime pour "
            "le risque."
        )
    else:
        niveau = "normal"
        commentaire = (
            f"Spread haut rendement à {_fr(courant, 2)} %, {_fr(percentile, 0)}e percentile "
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
            f"Liquidité nette de {_fr(courant, 0)} Md$, sans recul pour en lire la tendance.",
            disponible=False,
        )

    delta = courant - float(nette.iloc[-1 - horizon])
    delta_pct = delta / abs(float(nette.iloc[-1 - horizon])) * 100.0 if nette.iloc[-1 - horizon] else np.nan

    if delta > 0.0:
        niveau = "en expansion"
        commentaire = (
            f"Liquidité nette à {_fr(courant, 0)} Md$, en hausse de {delta:,.0f} Md$ "
            f"({delta_pct:+.1f} %) sur un trimestre : vent porteur pour les actifs risqués."
        )
    elif delta < 0.0:
        niveau = "en contraction"
        commentaire = (
            f"Liquidité nette à {_fr(courant, 0)} Md$, en baisse de {abs(delta):,.0f} Md$ "
            f"({delta_pct:+.1f} %) sur un trimestre : le drainage pèse sur les valorisations."
        )
    else:
        niveau = "stable"
        commentaire = f"Liquidité nette stable à {_fr(courant, 0)} Md$ sur un trimestre."
    return AxisReading("liquidite_nette", courant, niveau, commentaire)


# ---------------------------------------------------------------------------
# Contexte macro factuel : inflation, chômage, pétrole
# ---------------------------------------------------------------------------
def _lire_inflation(df: pd.DataFrame) -> AxisReading:
    """Mesure l'inflation en glissement annuel à partir de ``CPIAUCSL``.

    L'indice des prix est un niveau, pas un taux : le publier tel quel
    (« 320,4 ») ne dit rien. La seule lecture utile est sa variation sur
    douze mois, celle que tout le monde appelle « l'inflation ».

    Args:
        df: DataFrame contenant idéalement ``CPIAUCSL``.

    Returns:
        Lecture de l'axe, valeur en pourcentage annuel.
    """
    if "CPIAUCSL" not in df.columns:
        return _indisponible("inflation", "CPIAUCSL")

    serie = df["CPIAUCSL"].dropna()
    # La série est mensuelle, réindexée en jours ouvrés par get_many : un an
    # se compte donc en jours ouvrés, pas en douze observations.
    if len(serie) < JOURS_OUVRES_PAR_AN + 1:
        return _indisponible("inflation", "CPIAUCSL (moins d'un an d'historique)")

    courant = float(serie.iloc[-1])
    il_y_a_un_an = float(serie.iloc[-1 - JOURS_OUVRES_PAR_AN])
    if il_y_a_un_an == 0.0:
        return _indisponible("inflation", "CPIAUCSL (indice nul il y a un an)")

    glissement = (courant / il_y_a_un_an - 1.0) * 100.0
    if glissement >= 3.0:
        niveau = "élevée"
    elif glissement <= 1.5:
        niveau = "faible"
    else:
        niveau = "proche de la cible"
    commentaire = (
        f"Inflation à {_fr(glissement, 1)} % sur un an (indice CPI à {_fr(courant, 1)}), "
        f"{niveau} au regard de la cible de 2 % de la Réserve fédérale."
    )
    return AxisReading("inflation", glissement, niveau, commentaire)


def _lire_chomage(df: pd.DataFrame) -> AxisReading:
    """Lit le taux de chômage ``UNRATE`` et son inflexion sur un an.

    Le niveau seul se lit mal : 4 % est bas dans l'absolu mais inquiétant
    s'il vient de 3,4 %. La variation sur un an est donc donnée avec lui.

    Args:
        df: DataFrame contenant idéalement ``UNRATE``.

    Returns:
        Lecture de l'axe, valeur en pourcentage.
    """
    if "UNRATE" not in df.columns:
        return _indisponible("chomage", "UNRATE")

    serie = df["UNRATE"].dropna()
    if serie.empty:
        return _indisponible("chomage", "UNRATE")

    courant = float(serie.iloc[-1])
    if len(serie) < JOURS_OUVRES_PAR_AN + 1:
        commentaire = (
            f"Chômage à {_fr(courant, 1)} %, sans recul d'un an pour en qualifier "
            "la tendance."
        )
        return AxisReading("chomage", courant, "sans tendance", commentaire)

    il_y_a_un_an = float(serie.iloc[-1 - JOURS_OUVRES_PAR_AN])
    ecart = courant - il_y_a_un_an
    if ecart >= 0.3:
        niveau = "en hausse"
        sens = f"en hausse de {_fr(ecart, 1, signe=True)} point sur un an, le marché du travail se détend"
    elif ecart <= -0.3:
        niveau = "en baisse"
        sens = f"en baisse de {_fr(ecart, 1, signe=True)} point sur un an, le marché du travail se tend"
    else:
        niveau = "stable"
        sens = f"stable ({_fr(ecart, 1, signe=True)} point sur un an)"
    commentaire = f"Chômage à {_fr(courant, 1)} %, {sens}."
    return AxisReading("chomage", courant, niveau, commentaire)


def _lire_petrole(df: pd.DataFrame) -> AxisReading:
    """Mesure la variation du brut WTI sur un mois (``DCOILWTICO``).

    Args:
        df: DataFrame contenant idéalement ``DCOILWTICO``.

    Returns:
        Lecture de l'axe, valeur en pourcentage sur un mois.
    """
    if "DCOILWTICO" not in df.columns:
        return _indisponible("petrole", "DCOILWTICO")

    serie = df["DCOILWTICO"].dropna()
    if len(serie) <= JOURS_OUVRES_PAR_MOIS:
        return _indisponible("petrole", "DCOILWTICO (moins d'un mois d'historique)")

    courant = float(serie.iloc[-1])
    precedent = float(serie.iloc[-1 - JOURS_OUVRES_PAR_MOIS])
    if precedent == 0.0:
        return _indisponible("petrole", "DCOILWTICO (prix nul il y a un mois)")

    variation = (courant / precedent - 1.0) * 100.0
    if variation >= 5.0:
        niveau = "en hausse"
    elif variation <= -5.0:
        niveau = "en baisse"
    else:
        niveau = "stable"
    commentaire = (
        f"Pétrole WTI à {_fr(courant, 2)} $, {_fr(variation, 1, signe=True)} % sur un mois "
        f"({niveau})."
    )
    return AxisReading("petrole", variation, niveau, commentaire)


def _lire_appetit_risque(
    df: pd.DataFrame, prix_secteurs: pd.DataFrame | None = None
) -> AxisReading:
    """Qualifie l'appétit pour le risque : recherche de rendement ou d'abri.

    Trois signaux indépendants, moyennés seulement s'ils sont disponibles :

    * le **VIX** en percentile sur deux ans — le prix de l'assurance ;
    * le **spread haut rendement** en percentile sur deux ans — ce que le
      crédit facture pour prêter aux emprunteurs fragiles ;
    * la **force relative des cycliques contre les défensives** sur vingt
      séances, quand les prix sectoriels sont fournis — ce que les
      investisseurs achètent réellement, par opposition à ce qu'ils disent.

    Les trois disent la même chose sous trois angles : additionner leurs
    verdicts évite de conclure sur un seul indicateur, qui peut décrocher
    pour des raisons techniques (échéance d'options sur le VIX, émission
    massive sur le crédit).

    Args:
        df: DataFrame FRED contenant idéalement ``VIXCLS`` et ``BAMLH0A0HYM2``.
        prix_secteurs: clôtures des ETF sectoriels, une colonne par ticker.
            ``None`` si les prix n'ont pas été chargés — le signal de rotation
            est alors simplement absent, les deux autres restent lus.

    Returns:
        Lecture de l'axe. ``valeur`` est le score composite, de -1
        (aversion franche) à +1 (appétit franc).
    """
    signaux: list[tuple[str, float, str]] = []

    if "VIXCLS" in df.columns:
        vix = df["VIXCLS"].dropna()
        if len(vix) >= 60:
            fenetre = vix.iloc[-FENETRE_2_ANS:]
            courant = float(fenetre.iloc[-1])
            percentile = float((fenetre.to_numpy() <= courant).mean() * 100.0)
            # Un VIX haut = peur : le signal d'appétit est l'inverse du percentile.
            signaux.append((
                "vix",
                (50.0 - percentile) / 50.0,
                f"VIX à {_fr(courant, 1)}, {_fr(percentile, 0)}e percentile sur deux ans",
            ))

    if "BAMLH0A0HYM2" in df.columns:
        spread = df["BAMLH0A0HYM2"].dropna()
        if len(spread) >= 60:
            fenetre = spread.iloc[-FENETRE_2_ANS:]
            courant = float(fenetre.iloc[-1])
            percentile = float((fenetre.to_numpy() <= courant).mean() * 100.0)
            # Un spread haut = prudence : même inversion que pour le VIX.
            signaux.append((
                "credit",
                (50.0 - percentile) / 50.0,
                f"spread haut rendement à {_fr(courant, 2)} %, {_fr(percentile, 0)}e percentile sur deux ans",
            ))

    rotation = _force_relative_cycliques(prix_secteurs)
    if rotation is not None:
        ecart, description = rotation
        # ±5 points d'écart sur vingt séances valent un signal franc.
        signaux.append(("rotation", max(-1.0, min(1.0, ecart / 5.0)), description))

    if not signaux:
        return _indisponible("appetit_risque", "VIXCLS, BAMLH0A0HYM2, prix sectoriels")

    score = float(sum(s[1] for s in signaux) / len(signaux))
    if score >= 0.25:
        niveau = "risk-on"
        lecture = "les investisseurs cherchent le rendement"
    elif score <= -0.25:
        niveau = "risk-off"
        lecture = "les investisseurs cherchent la sécurité"
    else:
        niveau = "neutre"
        lecture = "ni recherche de rendement ni fuite vers la sécurité"

    details = " ; ".join(s[2] for s in signaux)
    commentaire = (
        f"Appétit pour le risque {niveau} ({lecture}), sur {len(signaux)} signal(aux) : "
        f"{details}."
    )
    return AxisReading("appetit_risque", score, niveau, commentaire)


def _force_relative_cycliques(
    prix_secteurs: pd.DataFrame | None, fenetre: int = JOURS_OUVRES_PAR_MOIS
) -> tuple[float, str] | None:
    """Compare la performance des secteurs cycliques et défensifs.

    Args:
        prix_secteurs: clôtures, une colonne par ticker (voir
            ``config/universe.yaml``, bloc ``groupes``).
        fenetre: nombre de séances comparées.

    Returns:
        Couple ``(écart en points de pourcentage, description chiffrée)``, ou
        ``None`` si les deux groupes ne sont pas mesurables.
    """
    if prix_secteurs is None or prix_secteurs.empty:
        return None

    def _perf(tickers: list[str]) -> float | None:
        """Performance moyenne d'un groupe sur la fenêtre, en pourcentage."""
        performances = []
        for ticker in tickers:
            if ticker not in prix_secteurs.columns:
                continue
            serie = prix_secteurs[ticker].dropna()
            if len(serie) <= fenetre:
                continue
            debut = float(serie.iloc[-1 - fenetre])
            if debut == 0.0:
                continue
            performances.append((float(serie.iloc[-1]) / debut - 1.0) * 100.0)
        return sum(performances) / len(performances) if performances else None

    cycliques = _perf(list(GROUPE_CYCLIQUES))
    defensifs = _perf(list(GROUPE_DEFENSIFS))
    if cycliques is None or defensifs is None:
        return None

    ecart = cycliques - defensifs
    sens = "surperforment" if ecart >= 0 else "sous-performent"
    description = (
        f"les cycliques {sens} les défensives de {_fr(abs(ecart), 1)} point(s) "
        f"sur {fenetre} séances ({_fr(cycliques, 1, signe=True)} % contre {_fr(defensifs, 1, signe=True)} %)"
    )
    return ecart, description


def compute_macro_regime(
    df: pd.DataFrame, prix_secteurs: pd.DataFrame | None = None
) -> MacroRegime:
    """Traduit les séries FRED en lecture qualitative sur sept axes.

    Les axes sont indépendants : un axe indisponible ne dégrade pas les
    autres, il est simplement marqué comme tel.

    Args:
        df: DataFrame issu de :func:`get_many`.
        prix_secteurs: clôtures des ETF sectoriels, une colonne par ticker.
            Facultatif : sans elles, l'appétit pour le risque se lit sur le
            VIX et le crédit seuls, sans la rotation cyclique/défensif.

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
            inflation=_indisponible("inflation", vide),
            chomage=_indisponible("chomage", vide),
            petrole=_indisponible("petrole", vide),
            appetit_risque=_lire_appetit_risque(pd.DataFrame(), prix_secteurs),
        )

    derniere_date = df.index.max()
    return MacroRegime(
        courbe_des_taux=_lire_courbe_des_taux(df),
        stress_credit=_lire_stress_credit(df),
        liquidite_nette=_lire_liquidite_nette(df),
        inflation=_lire_inflation(df),
        chomage=_lire_chomage(df),
        petrole=_lire_petrole(df),
        appetit_risque=_lire_appetit_risque(df, prix_secteurs),
        date_lecture=pd.Timestamp(derniere_date) if derniere_date is not None else None,
    )


# ---------------------------------------------------------------------------
# Narratif : le contexte macro en prose
# ---------------------------------------------------------------------------
def _date_serie(df: pd.DataFrame | None, colonne: str) -> str | None:
    """Date de la dernière observation réelle d'une série.

    Args:
        df: DataFrame des séries.
        colonne: nom de la série.

    Returns:
        Date au format ``AAAA-MM-JJ``, ou ``None`` si la série est absente.
    """
    if df is None or df.empty or colonne not in df.columns:
        return None
    serie = df[colonne].dropna()
    if serie.empty:
        return None
    return str(pd.Timestamp(serie.index[-1]).date())


def rediger_contexte_macro(
    regime: MacroRegime, df: pd.DataFrame | None = None
) -> dict[str, Any]:
    """Rédige le contexte macro en prose, chaque phrase adossée à un chiffre.

    Règle de rédaction, la même que partout dans le projet : une affirmation
    sans chiffre daté derrière n'est pas publiée. Un axe indisponible n'est
    donc pas contourné par une formule vague — il est nommé, avec son motif,
    ou simplement absent du texte.

    Args:
        regime: lecture produite par :func:`compute_macro_regime`.
        df: DataFrame des séries, pour dater chaque donnée citée. Facultatif :
            sans lui, les dates par série sont absentes plutôt qu'inventées.

    Returns:
        Dictionnaire sérialisable : ``texte`` (la prose), ``invalidation``,
        ``axes_indisponibles`` et ``dates_series``.
    """
    phrases: list[str] = []
    indisponibles: list[dict[str, str]] = []

    def _retenir(axe: AxisReading | None) -> None:
        """Ajoute le commentaire d'un axe, ou consigne son indisponibilité."""
        if axe is None:
            return
        if axe.disponible and axe.commentaire:
            phrases.append(axe.commentaire)
        else:
            indisponibles.append({"axe": axe.axe, "motif": axe.commentaire})

    # 1. Le contexte factuel : ce que l'économie fait.
    for axe in (regime.inflation, regime.chomage, regime.petrole):
        _retenir(axe)

    # 2. Les conditions financières : ce que les marchés facturent.
    for axe in (regime.courbe_des_taux, regime.stress_credit, regime.liquidite_nette):
        _retenir(axe)

    # 3. Ce que les investisseurs en font.
    _retenir(regime.appetit_risque)

    dates = {
        cle: _date_serie(df, cle)
        for cle in ("CPIAUCSL", "UNRATE", "DCOILWTICO", "VIXCLS", "BAMLH0A0HYM2", "T10Y2Y")
    }
    dates = {cle: valeur for cle, valeur in dates.items() if valeur is not None}

    if not phrases:
        return {
            "disponible": False,
            "motif": "aucun axe macro mesurable : toutes les séries manquent",
            "texte": "",
            "invalidation": "",
            "axes_indisponibles": indisponibles,
            "dates_series": dates,
            "date_lecture": None if regime.date_lecture is None else str(regime.date_lecture.date()),
        }

    return {
        "disponible": True,
        "motif": "",
        "texte": " ".join(phrases),
        "invalidation": _invalidation_macro(regime),
        "axes_indisponibles": indisponibles,
        "dates_series": dates,
        "date_lecture": None if regime.date_lecture is None else str(regime.date_lecture.date()),
    }


def _invalidation_macro(regime: MacroRegime) -> str:
    """Dit ce qui remettrait en cause la lecture macro.

    Chaque condition est exprimée en franchissement d'un seuil chiffré à
    partir de la valeur mesurée : « ce qui invaliderait » n'a de sens que si
    l'on sait à partir de quand.

    Args:
        regime: lecture du régime macro.

    Returns:
        Le motif d'invalidation, en français.
    """
    conditions: list[str] = []

    appetit = regime.appetit_risque
    if appetit is not None and appetit.disponible and appetit.valeur is not None:
        conditions.append(
            f"un basculement du score d'appétit pour le risque (actuellement "
            f"{_fr(appetit.valeur, 2, signe=True)}) au-delà de ±0,25 renverserait la lecture "
            f"« {appetit.niveau} »"
        )

    inflation = regime.inflation
    if inflation is not None and inflation.disponible and inflation.valeur is not None:
        conditions.append(
            f"une inflation repassant le seuil de 3 % (actuellement "
            f"{_fr(inflation.valeur, 1)} %) changerait la contrainte qui pèse sur la Fed"
        )

    credit = regime.stress_credit
    if credit is not None and credit.disponible and credit.valeur is not None:
        conditions.append(
            f"un spread haut rendement franchissant le 75e percentile "
            f"(actuellement au {_fr(credit.valeur, 0)}e) ferait passer le crédit en régime tendu"
        )

    if not conditions:
        return (
            "Aucune condition d'invalidation chiffrable : les axes nécessaires "
            "sont indisponibles."
        )
    return "Cette lecture serait invalidée si : " + " ; ".join(conditions) + "."
