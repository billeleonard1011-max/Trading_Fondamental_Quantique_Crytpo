"""Coûts d'exécution, dimensionnement en euros, stop et objectifs.

Le compte est libellé en euros, l'or cote en dollars : toute taille de
position passe donc par le taux EUR/USD du jour de l'entrée. L'oublier
fausse le risque de plusieurs pour cent, dans un sens qui varie avec le
taux — l'erreur ne se voit pas sur un trade, elle se voit sur la courbe.

Sur l'écart de cotation
-----------------------
La valeur par défaut n'est pas choisie au jugé : elle vient des ticks
Dukascopy eux-mêmes. Sur une journée complète de juin 2025, l'écart médian
entre demandé et offert ressort à **0,619 $** sur XAUUSD. C'est un écart
d'agrégateur interbancaire ; un courtier de société de financement affiche
généralement un peu plus large. La valeur retenue par défaut est donc
**0,30 $ de glissement** en sus, et l'écart reste configurable pour mesurer
la sensibilité de la stratégie à ce poste — beaucoup de stratégies rentables
sur le papier meurent exactement là.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Final

_LOG: Final = logging.getLogger(__name__)

#: Onces d'or par lot standard sur XAUUSD.
ONCES_PAR_LOT: Final[float] = 100.0

#: Pas de lot minimal chez la plupart des courtiers.
PAS_DE_LOT: Final[float] = 0.01

#: Écart de cotation par défaut, en dollars. Mesuré sur les ticks Dukascopy.
SPREAD_DEFAUT: Final[float] = 0.62

#: Glissement par défaut, en dollars, appliqué à l'entrée comme à la sortie.
SLIPPAGE_DEFAUT: Final[float] = 0.30

#: Marge du stop au-delà de l'order block, en dollars.
MARGE_STOP_DEFAUT: Final[float] = 1.00

#: Fourchette de perte visée au stop, en euros.
PERTE_MIN_EUR: Final[float] = 50.0
PERTE_MAX_EUR: Final[float] = 60.0

__all__ = [
    "ConfigExecution",
    "ONCES_PAR_LOT",
    "calculer_stop",
    "dimensionner",
    "appliquer_couts_entree",
    "appliquer_couts_sortie",
]


@dataclass(slots=True, frozen=True)
class ConfigExecution:
    """Paramètres de coût et de dimensionnement.

    Attributes:
        spread: écart de cotation, en dollars.
        slippage: glissement subi à l'entrée et à la sortie, en dollars.
        marge_stop: distance au-delà de l'order block pour le stop.
        perte_min_eur: borne basse de la perte visée au stop.
        perte_max_eur: borne haute.
        pas_de_lot: granularité de la taille de position.
        lot_min: taille minimale négociable.
    """

    spread: float = SPREAD_DEFAUT
    slippage: float = SLIPPAGE_DEFAUT
    marge_stop: float = MARGE_STOP_DEFAUT
    perte_min_eur: float = PERTE_MIN_EUR
    perte_max_eur: float = PERTE_MAX_EUR
    pas_de_lot: float = PAS_DE_LOT
    lot_min: float = PAS_DE_LOT


def calculer_stop(
    sens: str,
    ob_haut: float,
    ob_bas: float,
    meche_bougie2: float,
    marge: float = MARGE_STOP_DEFAUT,
) -> float:
    """Place le stop au-delà de l'order block.

    Le stop se pose à ``marge`` dollars au-delà de l'extrémité de la zone :
    sous le bas pour un achat, au-dessus du haut pour une vente.

    Exception prévue par la stratégie : si la mèche de la bougie 2 du motif
    dépasse déjà cette marge, le stop se place au-delà de cette mèche. La
    marge appliquée alors est la même que dans le cas nominal — l'énoncé dit
    « au-delà » sans préciser de combien, et reprendre la marge existante
    évite d'introduire un second paramètre non demandé.

    Args:
        sens: ``haussier`` pour un achat, ``baissier`` pour une vente.
        ob_haut: borne haute de la zone.
        ob_bas: borne basse.
        meche_bougie2: extrême de la bougie 2 du côté du stop.
        marge: distance nominale, en dollars.

    Returns:
        Le niveau de stop.
    """
    from backtest.ict import HAUSSIER

    if sens == HAUSSIER:
        nominal = ob_bas - marge
        # La mèche ne l'emporte que si elle descend plus bas que le nominal.
        return min(nominal, meche_bougie2 - marge)
    nominal = ob_haut + marge
    return max(nominal, meche_bougie2 + marge)


def dimensionner(
    distance_stop_usd: float,
    taux_eurusd: float,
    config: ConfigExecution | None = None,
) -> dict[str, Any]:
    """Choisit la taille pour que la perte au stop tombe dans la fourchette.

    Sur XAUUSD, un lot porte cent onces : un dollar de mouvement vaut donc
    cent dollars par lot. La perte en euros s'obtient en divisant par le taux
    EUR/USD du jour, qui exprime des dollars par euro.

    Quand aucun multiple du pas de lot ne place la perte entre les deux
    bornes, le trade est déclaré **non prenable** plutôt qu'arrondi : arrondir
    reviendrait à prendre un risque différent de celui que la stratégie
    prévoit, et à le faire silencieusement.

    Args:
        distance_stop_usd: distance entre entrée et stop, en dollars.
        taux_eurusd: dollars par euro à la date d'entrée.
        config: paramètres d'exécution.

    Returns:
        Dictionnaire avec ``prenable``, la taille retenue et la perte
        simulée. ``motif`` est renseigné quand le trade est écarté.
    """
    reglages = config or ConfigExecution()
    if distance_stop_usd <= 0.0:
        return {
            "prenable": False,
            "motif": "distance au stop nulle ou négative",
            "lots": 0.0,
            "perte_eur": None,
        }
    if taux_eurusd <= 0.0:
        return {
            "prenable": False,
            "motif": "taux EUR/USD indisponible : le risque en euros n'est pas calculable",
            "lots": 0.0,
            "perte_eur": None,
        }

    # Perte en euros d'un lot entier, puis taille visant le milieu de la
    # fourchette — le point le plus robuste aux arrondis.
    perte_par_lot_eur = distance_stop_usd * ONCES_PAR_LOT / taux_eurusd
    cible = (reglages.perte_min_eur + reglages.perte_max_eur) / 2.0
    lots_theoriques = cible / perte_par_lot_eur

    # Arrondi au pas inférieur puis essai du pas supérieur : l'un des deux
    # tombe dans la fourchette quand c'est possible.
    pas = reglages.pas_de_lot
    candidats = sorted(
        {
            round(math.floor(lots_theoriques / pas) * pas, 10),
            round(math.ceil(lots_theoriques / pas) * pas, 10),
        }
    )

    for lots in candidats:
        if lots < reglages.lot_min:
            continue
        perte = lots * perte_par_lot_eur
        if reglages.perte_min_eur <= perte <= reglages.perte_max_eur:
            return {
                "prenable": True,
                "motif": "",
                "lots": float(lots),
                "perte_eur": float(perte),
                "perte_par_lot_eur": float(perte_par_lot_eur),
                "distance_stop_usd": float(distance_stop_usd),
                "taux_eurusd": float(taux_eurusd),
            }

    perte_lot_min = reglages.lot_min * perte_par_lot_eur
    motif = (
        f"aucune taille multiple de {pas:g} lot ne place la perte entre "
        f"{reglages.perte_min_eur:.0f} € et {reglages.perte_max_eur:.0f} € "
        f"(distance {distance_stop_usd:.2f} $, perte au lot minimum "
        f"{perte_lot_min:.2f} €)"
    )
    return {
        "prenable": False,
        "motif": motif,
        "lots": 0.0,
        "perte_eur": None,
        "perte_par_lot_eur": float(perte_par_lot_eur),
        "distance_stop_usd": float(distance_stop_usd),
        "taux_eurusd": float(taux_eurusd),
    }


def appliquer_couts_entree(prix: float, sens: str, config: ConfigExecution | None = None) -> float:
    """Dégrade le prix d'entrée de l'écart et du glissement.

    Les bougies sont bâties sur le prix offert. Un achat se paie au prix
    demandé, donc au-dessus ; une vente subit le glissement dans l'autre
    sens. Dans les deux cas le coût joue contre la position, ce qui est le
    seul comportement honnête pour un backtest.

    Args:
        prix: prix théorique d'exécution.
        sens: ``haussier`` pour un achat, ``baissier`` pour une vente.
        config: paramètres d'exécution.

    Returns:
        Le prix effectivement obtenu.
    """
    from backtest.ict import HAUSSIER

    reglages = config or ConfigExecution()
    cout = reglages.spread + reglages.slippage
    return prix + cout if sens == HAUSSIER else prix - cout


def appliquer_couts_sortie(prix: float, sens: str, config: ConfigExecution | None = None) -> float:
    """Dégrade le prix de sortie du glissement.

    L'écart de cotation a déjà été payé à l'entrée : ne rester ici que le
    glissement évite de le compter deux fois.

    Args:
        prix: prix théorique de sortie.
        sens: sens de la position.
        config: paramètres d'exécution.

    Returns:
        Le prix effectivement obtenu.
    """
    from backtest.ict import HAUSSIER

    reglages = config or ConfigExecution()
    return prix - reglages.slippage if sens == HAUSSIER else prix + reglages.slippage
