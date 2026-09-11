"""Motifs ICT : order blocks, jambes, FVG et niveaux de liquidité (sweep).

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

#: Nombre de bougies exigées de chaque côté d'un pivot pour en faire un
#: niveau de liquidité (setup sweep). Distinct de la sensibilité des swings
#: de jambe : les deux se testent séparément (3, 4, 5).
SENSIBILITE_PIVOT: Final[int] = 4

#: Côtés d'un niveau de liquidité : un ancien plus haut porte des stops
#: au-dessus (balayé par le haut, vendu à la clôture en dessous) ; un ancien
#: plus bas, l'inverse.
COTE_HAUT: Final = "haut"
COTE_BAS: Final = "bas"

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
    # --- Setup sweep -------------------------------------------------------
    {
        "sujet": "sweep : unité de la bougie qui « clôture de l'autre côté »",
        "manque": "L'énoncé dit « une bougie clôture » sans dire de quelle unité.",
        "choix": (
            "Une bougie de l'unité du niveau balayé (M15 pour un niveau M15). La "
            "traversée, elle, est lue sur M1 : une mèche au-delà suffit, et "
            "l'extrême du sweep est le plus bas (ou plus haut) atteint depuis la "
            "première minute au-delà du niveau jusqu'à la clôture qui le reprend."
        ),
    },
    {
        "sujet": "sweep : fenêtre de recherche du FVG",
        "manque": "L'énoncé reprend la confirmation du setup OB sans dire d'où part la recherche.",
        "choix": (
            "Du début du sweep (première bougie M1 au-delà du niveau) jusqu'à "
            "l'instant courant, en M5 puis M3 puis M1 — l'équivalent de la jambe "
            "du setup OB."
        ),
    },
    {
        "sujet": "sweep : niveaux actifs suivis simultanément",
        "manque": "L'énoncé dit « plusieurs niveaux » sans borne.",
        "choix": (
            "Tous les niveaux non balayés, plafonnés aux plus récents par unité "
            "(quarante par défaut) : sans plafond, un niveau traversé de cent "
            "dollars resterait suivi pendant des mois et l'état du scanner en "
            "direct grossirait sans limite. Un niveau évincé n'est jamais réinséré."
        ),
    },
    {
        "sujet": "sweep : mouvement de référence du Fibonacci",
        "manque": "« Le dernier mouvement directionnel précédant le sweep » n'est pas borné.",
        "choix": (
            "Sur l'unité d'ancrage choisie, du dernier retournement confirmé "
            "(même règle de swing que la jambe du setup OB) jusqu'à l'extrême du "
            "sweep. La cible est à 0,72 de ce mouvement, mesurée depuis l'extrême. "
            "Sans retournement confirmé, ou si la cible est déjà dépassée à "
            "l'entrée, le setup est abandonné."
        ),
    },
    {
        "sujet": "sweep : niveau structurel de la variante 2",
        "manque": "Le solde vise « le premier niveau non balayé » sans dire s'il peut être plus proche que le 0,72.",
        "choix": (
            "Le premier niveau non balayé au-delà de la cible 0,72 : un palier "
            "structurel plus proche que le premier palier n'aurait aucun sens. "
            "La variante 3, elle, vise le premier niveau au-delà de l'entrée."
        ),
    },
)

__all__ = [
    "OrderBlock",
    "FairValueGap",
    "NiveauLiquidite",
    "SENSIBILITE_SWING",
    "SENSIBILITE_PIVOT",
    "COTE_HAUT",
    "COTE_BAS",
    "detecter_niveaux_liquidite",
    "avancer_niveau",
    "confirmer_balayage",
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


# ---------------------------------------------------------------------------
# Niveaux de liquidité (setup sweep)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class NiveauLiquidite:
    """Un pivot devenu réserve de liquidité, et l'état de son balayage.

    Le prix s'est retourné là : des positions s'y sont créées, leurs stops
    s'accumulent juste au-delà. Le marché a un intérêt structurel à venir
    les chercher — c'est le sweep —, puis à clôturer de l'autre côté du
    niveau, ce qui révèle le piège. Ce second temps est le signal.

    Attributes:
        unite: unité de temps du pivot.
        cote: ``haut`` (ancien plus haut, balayé par le haut, vendu) ou
            ``bas`` (ancien plus bas, balayé par le bas, acheté).
        prix: le niveau lui-même.
        formation: ouverture de la bougie pivot.
        connu_a: clôture de la bougie ``i + sensibilité`` : le niveau
            n'existe pour le moteur qu'à partir de cet instant, puisqu'il
            faut voir les bougies de droite pour savoir que c'était un pivot.
        en_sweep: ``True`` dès qu'une bougie M1 a dépassé le niveau sans
            qu'une bougie de l'unité ait encore clôturé de l'autre côté.
        extreme_sweep: le point le plus loin atteint pendant la prise de
            liquidité — c'est là que se place le stop, au-delà.
        debut_sweep: ouverture de la première bougie M1 au-delà du niveau.
        balaye: ``True`` une fois le sweep confirmé ; le niveau ne ressert
            jamais.
        horodatage_balayage: instant de la clôture qui a confirmé le sweep.
    """

    unite: str
    cote: str
    prix: float
    formation: pd.Timestamp
    connu_a: pd.Timestamp
    en_sweep: bool = False
    extreme_sweep: float | None = None
    debut_sweep: pd.Timestamp | None = None
    balaye: bool = False
    horodatage_balayage: pd.Timestamp | None = None
    #: ``True`` quand le niveau a été évincé de la liste active par le plafond
    #: par unité : il ne sert plus ni au sweep ni comme cible structurelle —
    #: le scanner en direct, qui ne garde que la liste active, ne le voit pas.
    evince: bool = False

    @property
    def sens_trade(self) -> str:
        """Sens du trade qu'un balayage de ce niveau déclenche."""
        return HAUSSIER if self.cote == COTE_BAS else BAISSIER

    def to_dict(self) -> dict[str, Any]:
        """Sérialise le niveau pour le journal et les alertes."""
        return {
            "unite": self.unite,
            "cote": self.cote,
            "prix": self.prix,
            "formation": str(self.formation),
            "connu_a": str(self.connu_a),
            "en_sweep": self.en_sweep,
            "extreme_sweep": self.extreme_sweep,
            "debut_sweep": "" if self.debut_sweep is None else str(self.debut_sweep),
            "balaye": self.balaye,
        }


def detecter_niveaux_liquidite(
    cadre: pd.DataFrame, unite: str, sensibilite: int = SENSIBILITE_PIVOT
) -> list[NiveauLiquidite]:
    """Repère les pivots d'une unité et en fait des niveaux de liquidité.

    Un pivot haut est une bougie dont le plus haut dépasse strictement celui
    des ``sensibilite`` bougies de chaque côté (voir :func:`detecter_swings`,
    même règle, même comparaison stricte). Point de causalité : le niveau
    porte l'instant où il **devient connu** — la clôture de la bougie
    ``i + sensibilite`` —, jamais celui du pivot lui-même. Un moteur qui le
    consulterait avant cet instant lirait l'avenir.

    Args:
        cadre: bougies OHLC de l'unité, closes, indexées par ouverture.
        unite: nom de l'unité, repris dans les niveaux produits.
        sensibilite: nombre de bougies exigées de chaque côté.

    Returns:
        Niveaux, triés par instant de connaissance (à égalité, le plus haut
        avant le plus bas).
    """
    k = max(int(sensibilite), 1)
    if cadre is None or len(cadre) < 2 * k + 1:
        return []

    from backtest.data import DUREES

    duree = DUREES.get(unite, pd.Timedelta(0))
    sommets, creux = detecter_swings(cadre, k)
    haut = cadre["high"].to_numpy(dtype="float64")
    bas = cadre["low"].to_numpy(dtype="float64")
    index = cadre.index

    niveaux = [
        NiveauLiquidite(unite, COTE_HAUT, float(haut[i]), index[i], index[i + k] + duree) for i in sommets
    ] + [
        NiveauLiquidite(unite, COTE_BAS, float(bas[i]), index[i], index[i + k] + duree) for i in creux
    ]
    # Tri stable : à instant de connaissance égal, les sommets (énumérés en
    # premier) précèdent les creux — le portage JavaScript fait de même.
    niveaux.sort(key=lambda n: n.connu_a)
    _LOG.debug("%s : %d niveau(x) de liquidité (sensibilité %d).", unite, len(niveaux), k)
    return niveaux


def avancer_niveau(niveau: NiveauLiquidite, haut: float, bas: float, ouverture: pd.Timestamp) -> bool:
    """Met à jour l'état de sweep d'un niveau avec une bougie M1.

    Une mèche au-delà suffit à ouvrir le sweep (inégalité stricte : toucher
    le niveau exactement n'est pas le dépasser). Tant que le sweep dure,
    l'extrême suit le point le plus loin atteint.

    Args:
        niveau: niveau actif, muté sur place.
        haut: plus haut de la bougie M1.
        bas: plus bas de la bougie M1.
        ouverture: ouverture de la bougie M1.

    Returns:
        ``True`` si la bougie a dépassé le niveau.
    """
    if niveau.balaye:
        return False
    if niveau.cote == COTE_BAS:
        depasse = bas < niveau.prix
        extreme = bas
        plus_loin = niveau.extreme_sweep is None or extreme < niveau.extreme_sweep
    else:
        depasse = haut > niveau.prix
        extreme = haut
        plus_loin = niveau.extreme_sweep is None or extreme > niveau.extreme_sweep
    if not depasse:
        return False
    if not niveau.en_sweep:
        niveau.en_sweep = True
        niveau.debut_sweep = ouverture
        niveau.extreme_sweep = float(extreme)
    elif plus_loin:
        niveau.extreme_sweep = float(extreme)
    return True


def confirmer_balayage(niveau: NiveauLiquidite, cloture: float, instant: pd.Timestamp) -> bool:
    """Teste si une clôture de l'unité du niveau confirme le sweep.

    Le niveau doit être en sweep, et la bougie clôturer **strictement** de
    l'autre côté : au-dessus d'un plus bas balayé, en dessous d'un plus haut
    balayé. Une clôture qui reste du côté du sweep laisse le niveau actif —
    traversé sans clôture de l'autre côté, c'est le cas intéressant, pas un
    échec.

    Args:
        niveau: niveau actif, muté sur place s'il est confirmé.
        cloture: clôture de la bougie de l'unité du niveau.
        instant: instant de cette clôture.

    Returns:
        ``True`` si le sweep est confirmé ; le niveau est alors ``balaye``.
    """
    if niveau.balaye or not niveau.en_sweep:
        return False
    reprise = cloture > niveau.prix if niveau.cote == COTE_BAS else cloture < niveau.prix
    if not reprise:
        return False
    niveau.balaye = True
    niveau.horodatage_balayage = instant
    return True
