"""Motifs ICT : order blocks, jambes et FVG.

Ce module ne contient que de la détection de motifs sur des bougies déjà
closes. Il ne place aucun ordre et ne connaît ni compte ni risque : tout ce
qui touche à l'exécution vit dans :mod:`backtest.execution`.

Aucune règle n'est inventée ici. Là où l'énoncé de la stratégie laisse une
zone d'ombre, le choix retenu est signalé par un commentaire commençant par
« CHOIX D'INTERPRÉTATION » et repris dans la sortie du backtest, pour que
l'utilisateur tranche lui-même plutôt que de découvrir une convention
implicite dans les résultats.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import pandas as pd

_LOG: Final = logging.getLogger(__name__)

#: Sens d'un motif.
HAUSSIER: Final = "haussier"
BAISSIER: Final = "baissier"

#: Nombre de bougies exigées de chaque côté pour valider un point de
#: retournement. Une valeur basse repère des swings ténus et découpe des
#: jambes courtes ; une valeur haute ne retient que les retournements francs
#: et allonge les jambes. Le paramètre est exposé partout parce qu'il décide
#: du découpage de la jambe, donc de la fenêtre où chercher le FVG.
SENSIBILITE_SWING: Final[int] = 4

#: Zones d'ombre de l'énoncé, tranchées ici faute de règle explicite. Elles
#: sont republiées telles quelles par le backtest.
CHOIX_INTERPRETATION: Final[tuple[dict[str, str], ...]] = (
    {
        "sujet": "origine de la jambe menant à l'order block",
        "manque": (
            "L'énoncé parle de « l'extrême d'origine » de la jambe sans dire "
            "comment le situer."
        ),
        "choix": (
            "Le dernier point de retournement — dernier swing haut ou bas — sur "
            "l'unité de l'order block, avant le mouvement qui va chercher la zone. "
            "La jambe est donc le seul dernier segment directionnel qui atteint "
            "l'order block, et non tout l'historique depuis sa formation. La "
            "sensibilité du swing est configurable, à quatre bougies de chaque "
            "côté par défaut."
        ),
    },
    {
        "sujet": "clôture « au-delà » du FVG",
        "manque": "L'énoncé ne dit pas de quelle borne du FVG il s'agit.",
        "choix": (
            "La borne opposée au sens du trade : pour un achat, la clôture doit "
            "dépasser le haut du FVG baissier ; pour une vente, passer sous le bas "
            "du FVG haussier."
        ),
    },
    {
        "sujet": "stop au-delà de la mèche de la bougie 2",
        "manque": (
            "L'énoncé dit « au-delà de cette mèche » sans préciser de combien."
        ),
        "choix": "La même marge que dans le cas nominal, appliquée au-delà de la mèche.",
    },
)

__all__ = [
    "OrderBlock",
    "FairValueGap",
    "SENSIBILITE_SWING",
    "detecter_swings",
    "origine_de_jambe",
    "HAUSSIER",
    "BAISSIER",
    "CHOIX_INTERPRETATION",
    "detecter_order_blocks",
    "detecter_fvg",
]


# ---------------------------------------------------------------------------
# Order blocks
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class OrderBlock:
    """Une zone d'order block issue du motif en trois bougies.

    Attributes:
        unite: unité de temps où le motif a été trouvé.
        sens: ``haussier`` (zone d'achat) ou ``baissier`` (zone de vente).
        haut: borne haute de la zone, mèches comprises.
        bas: borne basse de la zone.
        ouverture_bougie1: horodatage d'ouverture de la bougie de zone.
        fin_motif: horodatage de clôture de la troisième bougie. La zone
            n'existe pour le moteur qu'à partir de cet instant.
        mecthe_bougie2: extrême de la bougie 2 du côté du stop, qui peut
            l'emporter sur la marge nominale.
        mitige: ``True`` une fois que le prix a touché la zone.
        horodatage_mitigation: instant du premier contact.
    """

    unite: str
    sens: str
    haut: float
    bas: float
    ouverture_bougie1: pd.Timestamp
    fin_motif: pd.Timestamp
    meche_bougie2: float
    mitige: bool = False
    horodatage_mitigation: pd.Timestamp | None = None

    def contient(self, haut: float, bas: float) -> bool:
        """Dit si une bougie touche la zone.

        Args:
            haut: plus haut de la bougie testée.
            bas: plus bas de la bougie testée.

        Returns:
            ``True`` si les deux fourchettes se recoupent.
        """
        return not (bas > self.haut or haut < self.bas)

    def to_dict(self) -> dict[str, Any]:
        """Sérialise la zone pour le journal des trades."""
        return {
            "unite": self.unite,
            "sens": self.sens,
            "haut": self.haut,
            "bas": self.bas,
            "ouverture_bougie1": str(self.ouverture_bougie1),
            "fin_motif": str(self.fin_motif),
        }


def detecter_order_blocks(cadre: pd.DataFrame, unite: str) -> list[OrderBlock]:
    """Cherche le motif d'order block en trois bougies consécutives.

    Le motif, tel qu'il est défini et sans condition supplémentaire :

    * **bougie 1** — la zone, fourchette complète mèches comprises ;
    * **bougie 2** — de sens opposé à la première, et clôturant au-delà de
      son extrême : sous son plus bas si la bougie 1 est haussière, au-dessus
      de son plus haut si elle est baissière ;
    * **bougie 3** — sens indifférent, mais son extrême ne doit pas revenir
      toucher celui de la bougie 1. L'inégalité est **stricte** : une bougie 3
      qui touche exactement l'extrême invalide le motif, puisque c'est ce
      contact qui refermerait l'écart laissé ouvert.

    Une bougie 1 haussière donne un order block **baissier**, zone de vente ;
    une bougie 1 baissière donne un order block **haussier**.

    Args:
        cadre: bougies OHLC de l'unité, closes, indexées par ouverture.
        unite: nom de l'unité, repris dans les zones produites.

    Returns:
        Zones trouvées, dans l'ordre chronologique de formation.
    """
    if cadre is None or len(cadre) < 3:
        return []

    ouverture = cadre["open"].to_numpy(dtype="float64")
    haut = cadre["high"].to_numpy(dtype="float64")
    bas = cadre["low"].to_numpy(dtype="float64")
    cloture = cadre["close"].to_numpy(dtype="float64")
    index = cadre.index

    from backtest.data import DUREES

    duree = DUREES.get(unite, pd.Timedelta(0))
    zones: list[OrderBlock] = []

    for i in range(len(cadre) - 2):
        b1_haussiere = cloture[i] > ouverture[i]
        b1_baissiere = cloture[i] < ouverture[i]
        if not (b1_haussiere or b1_baissiere):
            # Une bougie 1 sans corps n'a pas de sens : le motif exige une
            # bougie 2 « de sens opposé », ce qui n'aurait alors aucun sens.
            continue

        j, k = i + 1, i + 2
        b2_haussiere = cloture[j] > ouverture[j]
        b2_baissiere = cloture[j] < ouverture[j]

        if b1_haussiere:
            # Order block baissier : la bougie 2 doit descendre sous le bas
            # de la bougie 1, et la bougie 3 ne doit pas y remonter.
            if not (b2_baissiere and cloture[j] < bas[i]):
                continue
            if haut[k] >= bas[i]:
                continue
            sens = BAISSIER
            meche = haut[j]
        else:
            # Order block haussier : symétrique.
            if not (b2_haussiere and cloture[j] > haut[i]):
                continue
            if bas[k] <= haut[i]:
                continue
            sens = HAUSSIER
            meche = bas[j]

        zones.append(
            OrderBlock(
                unite=unite,
                sens=sens,
                haut=float(haut[i]),
                bas=float(bas[i]),
                ouverture_bougie1=index[i],
                # La zone n'est connue qu'une fois la troisième bougie close.
                fin_motif=index[k] + duree,
                meche_bougie2=float(meche),
            )
        )

    _LOG.debug("%s : %d order block(s) détecté(s).", unite, len(zones))
    return zones


# ---------------------------------------------------------------------------
# Points de retournement
# ---------------------------------------------------------------------------
def detecter_swings(
    cadre: pd.DataFrame, sensibilite: int = SENSIBILITE_SWING
) -> tuple[list[int], list[int]]:
    """Repère les points de retournement d'une série de bougies.

    Un sommet est une bougie dont le plus haut dépasse **strictement** celui
    des ``sensibilite`` bougies de chaque côté. La comparaison stricte évite
    qu'un palier de plusieurs bougies au même niveau produise autant de
    sommets, ce qui découperait la jambe au mauvais endroit.

    Point de causalité, décisif ici : un retournement à la position ``i``
    n'est **confirmé** qu'à la position ``i + sensibilite``, puisqu'il faut
    voir les bougies de droite pour savoir que c'en était un. L'appelant doit
    donc n'utiliser que des swings dont la confirmation est déjà survenue —
    c'est ce que fait :func:`origine_de_jambe`. Rendre ici les positions
    brutes sans cette précaution serait la porte ouverte au look-ahead.

    Args:
        cadre: bougies OHLC, dans l'ordre chronologique.
        sensibilite: nombre de bougies exigées de chaque côté.

    Returns:
        Couple ``(positions des sommets, positions des creux)``.
    """
    k = max(int(sensibilite), 1)
    if cadre is None or len(cadre) < 2 * k + 1:
        return [], []

    haut = cadre["high"].to_numpy(dtype="float64")
    bas = cadre["low"].to_numpy(dtype="float64")
    sommets: list[int] = []
    creux: list[int] = []

    for i in range(k, len(cadre) - k):
        gauche_h, droite_h = haut[i - k : i], haut[i + 1 : i + 1 + k]
        if haut[i] > gauche_h.max() and haut[i] > droite_h.max():
            sommets.append(i)
        gauche_b, droite_b = bas[i - k : i], bas[i + 1 : i + 1 + k]
        if bas[i] < gauche_b.min() and bas[i] < droite_b.min():
            creux.append(i)

    return sommets, creux


def origine_de_jambe(
    cadre: pd.DataFrame,
    sens_ob: str,
    sensibilite: int = SENSIBILITE_SWING,
) -> int | None:
    """Situe le départ de la jambe qui va chercher l'order block.

    La jambe est le **dernier segment directionnel** menant à la zone : elle
    part du dernier point de retournement précédant ce mouvement, et non de
    l'extrême le plus lointain de tout l'historique.

    Le sens du retournement cherché découle du sens de la zone. Un order
    block baissier est une zone de vente, que le prix vient chercher **par le
    bas** : la jambe est donc haussière et part du dernier creux. Un order
    block haussier est l'inverse.

    Seuls les retournements **déjà confirmés** sont retenus : un swing à la
    position ``i`` exige ``sensibilite`` bougies à sa droite, il n'est donc
    connu qu'à ``i + sensibilite``. Les swings trop récents pour être
    confirmés sont écartés, faute de quoi le moteur lirait l'avenir.

    Args:
        cadre: bougies de l'unité de l'order block, closes, la dernière étant
            celle du contact avec la zone.
        sens_ob: sens de l'order block.
        sensibilite: nombre de bougies exigées de chaque côté.

    Returns:
        Position du départ de la jambe, ou ``None`` si aucun retournement
        confirmé ne précède le contact.
    """
    k = max(int(sensibilite), 1)
    sommets, creux = detecter_swings(cadre, k)

    # Zone de vente atteinte par le bas : la jambe monte, elle part d'un creux.
    candidats = creux if sens_ob == BAISSIER else sommets
    if not candidats:
        return None

    derniere = len(cadre) - 1
    # Un swing en position i n'est connu qu'en i + k : au-delà, il n'existe
    # pas encore pour qui observe la dernière bougie.
    confirmes = [i for i in candidats if i + k <= derniere]
    if not confirmes:
        return None
    return confirmes[-1]


# ---------------------------------------------------------------------------
# Fair value gaps
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class FairValueGap:
    """Un écart de valeur laissé par trois bougies.

    Attributes:
        unite: unité de temps où l'écart a été trouvé.
        sens: ``haussier`` si l'écart est laissé par une poussée vers le
            haut, ``baissier`` sinon.
        haut: borne haute de l'écart.
        bas: borne basse de l'écart.
        fin_motif: instant à partir duquel l'écart est connu.
    """

    unite: str
    sens: str
    haut: float
    bas: float
    fin_motif: pd.Timestamp

    def to_dict(self) -> dict[str, Any]:
        """Sérialise l'écart pour le journal des trades."""
        return {
            "unite": self.unite,
            "sens": self.sens,
            "haut": self.haut,
            "bas": self.bas,
            "fin_motif": str(self.fin_motif),
        }


def detecter_fvg(cadre: pd.DataFrame, unite: str, sens: str | None = None) -> list[FairValueGap]:
    """Cherche les écarts de valeur en trois bougies.

    Un écart haussier existe quand le plus haut de la première bougie reste
    sous le plus bas de la troisième : la deuxième a franchi la distance sans
    que le prix y revienne. L'écart baissier est le symétrique.

    Args:
        cadre: bougies OHLC de l'unité, closes.
        unite: nom de l'unité.
        sens: ne garder que les écarts de ce sens. Tous si ``None``.

    Returns:
        Écarts trouvés, dans l'ordre chronologique.
    """
    if cadre is None or len(cadre) < 3:
        return []

    haut = cadre["high"].to_numpy(dtype="float64")
    bas = cadre["low"].to_numpy(dtype="float64")
    index = cadre.index

    from backtest.data import DUREES

    duree = DUREES.get(unite, pd.Timedelta(0))
    ecarts: list[FairValueGap] = []

    for i in range(len(cadre) - 2):
        k = i + 2
        if haut[i] < bas[k]:
            trouve = FairValueGap(unite, HAUSSIER, float(bas[k]), float(haut[i]), index[k] + duree)
        elif bas[i] > haut[k]:
            trouve = FairValueGap(unite, BAISSIER, float(bas[i]), float(haut[k]), index[k] + duree)
        else:
            continue
        if sens is None or trouve.sens == sens:
            ecarts.append(trouve)

    return ecarts
