"""Motifs ICT : order blocks, jambes, FVG et retracement de Fibonacci.

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

#: Classifications de jambe.
NORMALE: Final = "normale"
VIOLENTE: Final = "violente"

#: Part du corps moyen sous laquelle une bougie contraire est tolérée.
SEUIL_CORPS_CONTRAIRE: Final[float] = 0.30

#: Nombre de bougies contraires tolérées sous ce seuil.
MAX_BOUGIES_CONTRAIRES: Final[int] = 2

#: Nombre de bougies exigées de chaque côté pour valider un point de
#: retournement. Une valeur basse repère des swings ténus et découpe des
#: jambes courtes ; une valeur haute ne retient que les retournements francs
#: et allonge les jambes. Le paramètre est exposé partout parce qu'il décide
#: du découpage de la jambe, donc de sa classification, donc du type d'entrée.
SENSIBILITE_SWING: Final[int] = 4

#: Bornes de la zone OTE, et niveau clé.
OTE_DEBUT: Final[float] = 0.618
OTE_FIN: Final[float] = 0.79
OTE_CLE: Final[float] = 0.72

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
        "sujet": "mouvement M5 servant de base au Fibonacci",
        "manque": "L'énoncé dit « le mouvement M5 de confirmation » sans le borner.",
        "choix": (
            "Le mouvement part de l'extrême atteint au moment de la confirmation "
            "du FVG, dans le sens du trade, et se prolonge tant que le prix fait "
            "de nouveaux extrêmes."
        ),
    },
    {
        "sujet": "clôture « au-delà » de la zone OTE",
        "manque": "Même imprécision que pour le FVG.",
        "choix": (
            "La borne la moins profonde, soit 61,8 % : le prix entre dans la zone "
            "puis en ressort dans le sens du trade."
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
    "NORMALE",
    "VIOLENTE",
    "CHOIX_INTERPRETATION",
    "detecter_order_blocks",
    "classifier_jambe",
    "detecter_fvg",
    "sommet_fibonacci",
    "zone_ote",
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
# Classification de la jambe
# ---------------------------------------------------------------------------
def classifier_jambe(
    cadre: pd.DataFrame,
    seuil: float = SEUIL_CORPS_CONTRAIRE,
    max_contraires: int = MAX_BOUGIES_CONTRAIRES,
) -> tuple[str, dict[str, Any]]:
    """Dit si une jambe est violente ou normale.

    Une jambe est **violente** quand toutes ses bougies vont dans le même
    sens. La tolérance est double, et les deux volets comptent : une bougie à
    contre-sens ne casse la condition que si son corps atteint au moins
    ``seuil`` du corps moyen de la jambe, et il en faut plus de
    ``max_contraires`` sous ce seuil pour disqualifier la jambe.

    Le seuil est franchi de façon **stricte** : un corps valant exactement
    30 % du corps moyen compte comme une vraie bougie contraire. C'est le cas
    limite retenu par les tests.

    Args:
        cadre: bougies de la jambe, sur l'unité de l'order block.
        seuil: part du corps moyen sous laquelle une bougie est négligeable.
        max_contraires: nombre de bougies négligeables tolérées.

    Returns:
        Couple ``(classification, détail)``. Le détail expose le décompte,
        pour que le journal des trades soit vérifiable à la main.
    """
    detail: dict[str, Any] = {
        "n_bougies": 0,
        "sens_dominant": "",
        "n_contraires": 0,
        "n_contraires_negligeables": 0,
        "corps_moyen": 0.0,
        "seuil_applique": seuil,
    }
    if cadre is None or cadre.empty:
        return NORMALE, detail

    ouverture = cadre["open"].to_numpy(dtype="float64")
    cloture = cadre["close"].to_numpy(dtype="float64")
    corps = np.abs(cloture - ouverture)
    detail["n_bougies"] = int(len(cadre))
    corps_moyen = float(corps.mean()) if corps.size else 0.0
    detail["corps_moyen"] = corps_moyen

    # Le sens dominant est celui du déplacement net de la jambe : c'est lui
    # qui définit ce qu'est une bougie « à contre-sens ».
    net = float(cloture[-1] - ouverture[0])
    dominant = HAUSSIER if net >= 0 else BAISSIER
    detail["sens_dominant"] = dominant

    if dominant == HAUSSIER:
        contraires = corps[(cloture - ouverture) < 0]
    else:
        contraires = corps[(cloture - ouverture) > 0]

    detail["n_contraires"] = int(contraires.size)
    if contraires.size == 0:
        return VIOLENTE, detail

    if corps_moyen <= 0.0:
        # Des bougies sans corps ne permettent aucune comparaison relative.
        return NORMALE, detail

    negligeables = contraires[contraires < seuil * corps_moyen]
    detail["n_contraires_negligeables"] = int(negligeables.size)

    # Une bougie contraire au-delà du seuil casse la violence, quel qu'en
    # soit le nombre ; en deçà, il en faut plus que le maximum toléré.
    if negligeables.size < contraires.size:
        return NORMALE, detail
    if contraires.size > max_contraires:
        return NORMALE, detail
    return VIOLENTE, detail


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
# Fibonacci à sommet mobile
# ---------------------------------------------------------------------------
def sommet_fibonacci(
    cadre: pd.DataFrame, sens: str
) -> tuple[int | None, float | None]:
    """Suit l'extrême d'un mouvement jusqu'à ce qu'il se fige.

    Le sommet suit chaque nouvel extrême et **se fige dès qu'une bougie n'en
    fait pas un nouveau par rapport à la bougie précédente**. La comparaison
    porte donc sur la bougie qui précède immédiatement, non sur le maximum
    courant : une bougie qui ne dépasse pas la précédente fige le sommet même
    si le maximum du mouvement est plus ancien.

    La fonction ne regarde jamais au-delà de la bougie qui fige le sommet :
    c'est ce qui la rend utilisable en temps réel, et le test de causalité
    vérifie qu'elle rend le même résultat sur un historique tronqué.

    Args:
        cadre: bougies du mouvement, dans l'ordre chronologique.
        sens: ``haussier`` pour suivre les plus hauts, ``baissier`` pour les
            plus bas.

    Returns:
        Couple ``(position, valeur)`` du sommet figé. ``(None, None)`` si le
        cadre est vide. Si aucune bougie ne fige le sommet, la dernière
        disponible est rendue — le sommet est alors encore provisoire.
    """
    if cadre is None or cadre.empty:
        return None, None

    if sens == HAUSSIER:
        extremes = cadre["high"].to_numpy(dtype="float64")
        progresse = lambda a, b: a > b  # noqa: E731 - lisible tel quel
    else:
        extremes = cadre["low"].to_numpy(dtype="float64")
        progresse = lambda a, b: a < b  # noqa: E731

    position = 0
    for i in range(1, len(extremes)):
        if progresse(extremes[i], extremes[i - 1]):
            position = i
            continue
        # Première bougie sans nouvel extrême : le sommet est figé ici.
        return position, float(extremes[position])

    return position, float(extremes[position])


def zone_ote(
    origine: float,
    sommet: float,
    debut: float = OTE_DEBUT,
    fin: float = OTE_FIN,
    cle: float = OTE_CLE,
) -> dict[str, float]:
    """Calcule la zone de retracement optimale d'un mouvement.

    Les niveaux sont exprimés en retracement depuis le sommet vers l'origine :
    61,8 % est le plus proche du sommet, 79 % le plus profond.

    Args:
        origine: extrême de départ du mouvement, point fixe.
        sommet: extrême d'arrivée, une fois figé.
        debut: borne la moins profonde de la zone.
        fin: borne la plus profonde.
        cle: niveau clé publié à titre indicatif.

    Returns:
        Dictionnaire des niveaux : ``debut``, ``fin``, ``cle``, plus les
        bornes ordonnées ``bas`` et ``haut``.
    """
    amplitude = sommet - origine
    niveaux = {
        "debut": sommet - amplitude * debut,
        "fin": sommet - amplitude * fin,
        "cle": sommet - amplitude * cle,
    }
    niveaux["bas"] = min(niveaux["debut"], niveaux["fin"])
    niveaux["haut"] = max(niveaux["debut"], niveaux["fin"])
    return niveaux
