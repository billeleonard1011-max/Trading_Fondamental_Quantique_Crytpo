"""Moteur de backtest : avance barre par barre, sans jamais regarder devant.

Comment la causalité est garantie
---------------------------------
Le moteur parcourt la série d'une minute dans l'ordre. À chaque barre close
à l'instant ``t``, il ne dispose que :

* des bougies des unités supérieures **dont la clôture est antérieure ou
  égale à ``t``** — un order block H1 formé à 10:00 n'existe qu'à 11:00 ;
* de la barre M1 courante, pour les touches de zone et les sorties ;
* de son propre état, construit lors des barres précédentes.

Aucune structure n'est consultée par indice futur, et les motifs sont
pré-calculés avec l'instant où ils **deviennent connus**, jamais celui où ils
commencent. La propriété est vérifiée par troncature : rejouer le backtest
sur un historique coupé doit produire exactement les mêmes trades sur la
partie commune.

Deux conventions, faute de règle explicite
------------------------------------------
* **Stop et objectif touchés dans la même minute** : le stop l'emporte. Rien
  dans la stratégie ne tranche ce cas, et l'hypothèse inverse flatterait les
  résultats sans qu'on puisse le vérifier.
* **Sorties évaluées sur la barre en cours**, jamais sur la suivante : une
  sortie jugée sur une clôture pas encore vue est l'erreur classique à ce
  stade, et elle est ici structurellement impossible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np
import pandas as pd

from backtest import data as bt_data
from backtest import execution as bt_exec
from backtest import ict

_LOG: Final = logging.getLogger(__name__)

#: Fenêtre d'expiration d'un setup, en barres de l'unité de travail.
EXPIRATION_DEFAUT: Final[int] = 120

#: Profondeur maximale d'une jambe remontée avant l'order block, en bougies.
PROFONDEUR_JAMBE: Final[int] = 50

#: Motifs d'abandon d'un setup.
ABANDON_SANS_FVG: Final = "aucun_fvg_trouve"
ABANDON_TAILLE: Final = "taille_non_prenable"
ABANDON_EXPIRATION: Final = "expiration_sans_confirmation"

#: Nombre maximal de zones de liquidité retenues pour une sortie par paliers.
#: Les candidats (extrêmes de la veille, de la session asiatique, order blocks
#: actifs) peuvent être cinq ou six devant le prix ; au-delà de trois, les
#: tranches deviennent trop fines pour dire quoi que ce soit et la dernière
#: cible est si lointaine qu'elle n'est presque jamais atteinte.
MAX_ZONES_PALIERS: Final[int] = 3

#: Part de la position close à la première zone touchée (TP1). Le solde se
#: répartit à parts égales sur les zones suivantes ; avec une seule zone au
#: total, elle prend 100 % — il n'y a alors pas de palier.
FRACTION_TP1: Final[float] = 0.5

#: Motifs de sortie d'une tranche.
SORTIE_OBJECTIF: Final = "objectif"
SORTIE_STOP: Final = "stop"
SORTIE_BREAK_EVEN: Final = "break_even"

#: Les deux setups d'entrée. Ils coexistent (une seule position à la fois,
#: la stratégie ne cumule pas) et se testent séparément ou ensemble.
SETUP_ORDER_BLOCK: Final = "order_block"
SETUP_SWEEP: Final = "sweep"

#: Les trois familles d'objectif du setup sweep.
#: ``S1_fibo`` : tout à 0,72 du mouvement de référence ; ``S2_fibo_structurel`` :
#: une part à 0,72, le solde sur le premier niveau non balayé au-delà ;
#: ``S3_structurel`` : tout sur le premier niveau non balayé au-delà de l'entrée.
OBJECTIF_SWEEP_FIBO: Final = "S1_fibo"
OBJECTIF_SWEEP_FIBO_STRUCTUREL: Final = "S2_fibo_structurel"
OBJECTIF_SWEEP_STRUCTUREL: Final = "S3_structurel"

#: Retracement du mouvement de référence visé par les variantes 1 et 2.
RATIO_FIBO_DEFAUT: Final[float] = 0.72

#: Part de la position close au 0,72 dans la variante 2 (le reste vise le
#: niveau structurel). Les trois répartitions demandées sont 0,5, 0,33 et 0,67.
FRACTION_FIBO_DEFAUT: Final[float] = 0.5

#: Niveaux de liquidité actifs conservés au plus par unité — les plus
#: récents. Voir ict.CHOIX_INTERPRETATION (« niveaux actifs suivis »).
MAX_NIVEAUX_PAR_UNITE: Final[int] = 40

#: Motifs d'abandon propres au setup sweep.
ABANDON_SANS_REFERENCE: Final = "aucun_mouvement_de_reference"
ABANDON_SANS_STRUCTUREL: Final = "aucun_niveau_structurel"

#: Origines des cibles du setup sweep, traduites avant tout affichage.
ORIGINE_FIBO: Final = "fibonacci_0_72"
ORIGINE_NIVEAU_HAUT: Final = "niveau_haut"
ORIGINE_NIVEAU_BAS: Final = "niveau_bas"

__all__ = [
    "ConfigBacktest", "Palier", "Trade", "Backtest", "repartir_paliers",
    "SETUP_ORDER_BLOCK", "SETUP_SWEEP",
    "OBJECTIF_SWEEP_FIBO", "OBJECTIF_SWEEP_FIBO_STRUCTUREL", "OBJECTIF_SWEEP_STRUCTUREL",
]


def repartir_paliers(n_zones: int, fraction_tp1: float = FRACTION_TP1) -> list[float]:
    """Répartit la position sur les zones de liquidité retenues.

    Règle confirmée avec l'utilisateur : la première zone touchée emporte
    ``fraction_tp1`` de la position, le solde se répartit à parts égales sur
    les zones suivantes. Une zone unique prend tout — il n'y a alors pas de
    palier, le comportement est celui d'une sortie unique classique.

    Les parts sont des fractions, jamais des lots : le pas de lot du
    dimensionnement est de 0,01 et vingt des quarante-quatre trades
    historiques tiennent en 0,03 ou 0,04 lot, taille qu'aucun découpage en
    trois ne peut respecter sans déformer le risque. Le moteur mesure ici une
    mécanique, il ne place pas d'ordre.

    Args:
        n_zones: nombre de zones retenues, au moins une.
        fraction_tp1: part de la position close à la première zone.

    Returns:
        Les parts, dans l'ordre des zones, de somme exactement 1.

    Raises:
        ValueError: si ``n_zones`` est nul ou négatif.
    """
    if n_zones <= 0:
        raise ValueError("Une sortie par paliers exige au moins une zone.")
    if n_zones == 1:
        return [1.0]
    reste = (1.0 - fraction_tp1) / (n_zones - 1)
    return [fraction_tp1] + [reste] * (n_zones - 1)


@dataclass(slots=True, frozen=True)
class ConfigBacktest:
    """Paramètres du backtest.

    Attributes:
        execution: coûts et dimensionnement.
        expiration_barres: nombre de barres au-delà duquel un setup non
            confirmé est abandonné.
        mode_tp: ``structurel`` ou ``ratio``.
        ratio_tp: multiple du risque, quand ``mode_tp`` vaut ``ratio``.
        unites_ob: unités où chercher les order blocks.
        sensibilite_swing: nombre de bougies exigées de chaque côté pour
            valider un point de retournement, sur l'unité de l'order block.
            Ce paramètre décide du découpage de la jambe, donc de la fenêtre
            où chercher le FVG.
    """

    execution: bt_exec.ConfigExecution = field(default_factory=bt_exec.ConfigExecution)
    expiration_barres: int = EXPIRATION_DEFAUT
    mode_tp: str = "ratio"
    ratio_tp: float = 2.0
    unites_ob: tuple[str, ...] = bt_data.UNITES_ORDER_BLOCK
    sensibilite_swing: int = ict.SENSIBILITE_SWING
    #: Setups d'entrée joués. Par défaut l'order block seul : les résultats
    #: et fixtures de parité existants restent inchangés.
    setups: tuple[str, ...] = (SETUP_ORDER_BLOCK,)
    #: Unités où les niveaux de liquidité sont détectés.
    unites_sweep: tuple[str, ...] = bt_data.UNITES_ORDER_BLOCK
    #: Bougies exigées de chaque côté d'un pivot (testé à 3, 4 et 5).
    sensibilite_pivot: int = ict.SENSIBILITE_PIVOT
    #: Marge du stop au-delà de la mèche du sweep. ``None`` reprend la marge
    #: du setup OB, pour rester cohérent entre les deux ; paramétrable à part.
    marge_stop_sweep: float | None = None
    #: Élargit le stop, en multiple de sa distance à l'entrée, après calcul.
    #:
    #: Sert à mesurer ce que la friction coûte : spread et slippage valent
    #: environ 1,84 $ par aller-retour, soit près de 0,19 R sur un stop
    #: médian de 9,5 $. Élargir le stop dilue ce coût fixe dans un risque
    #: plus grand — et le dimensionnement réduit la taille en proportion,
    #: si bien que le risque en euros ne bouge pas. La marge, elle, ne pèse
    #: qu'un dollar sur ce stop : la tripler ne l'élargit que d'un cinquième.
    #: 1,0 laisse le comportement d'origine.
    multiplicateur_stop: float = 1.0
    #: Famille d'objectif du setup sweep.
    objectif_sweep: str = OBJECTIF_SWEEP_FIBO
    #: Unité d'ancrage du mouvement de référence du Fibonacci.
    unite_fibo: str = "M15"
    ratio_fibo: float = RATIO_FIBO_DEFAUT
    #: Variante 2 : part close au 0,72, et passage à break-even du solde.
    fraction_fibo: float = FRACTION_FIBO_DEFAUT
    break_even_sweep: bool = True
    max_niveaux_par_unite: int = MAX_NIVEAUX_PAR_UNITE
    #: Règle de priorité à l'essai : refuser un sweep si un order block actif
    #: (connu, non mitigé), de même sens, est proche du prix de confirmation
    #: — l'OB prime, le sweep ne doit pas occuper la position à sa place.
    #: Désactivée par défaut : elle se mesure avant de se retenir.
    priorite_ob: bool = False
    proximite_ob_usd: float = 5.0


@dataclass(slots=True)
class Palier:
    """Une tranche de sortie, visant une zone de liquidité.

    Le journal garde chaque tranche séparément — zone visée, part de la
    position, prix de sortie, résultat en R — plutôt qu'un seul total : sans
    ce détail, on ne peut pas dire si un trade doit son résultat au premier
    palier ou aux suivants.

    Attributes:
        rang: 1 pour la première zone (TP1), 2 pour la suivante, etc.
        zone: niveau de liquidité visé.
        origine: d'où vient ce niveau (``veille_haut``, ``asie_bas``,
            ``order_block``...). Identifiant technique : il passe par le
            dictionnaire de libellés avant tout affichage.
        fraction: part de la position portée par cette tranche.
        ratio_risque: rapport (distance à la zone) / (distance au stop), tel
            qu'affiché dans l'alerte au moment de l'entrée.
        prix_sortie: prix net de sortie, coûts appliqués. ``None`` tant que
            la tranche est ouverte.
        horodatage_sortie: instant de la clôture de la tranche.
        resultat_r: résultat de la tranche, en multiple du risque initial,
            déjà pondéré par ``fraction``.
        motif_sortie: ``objectif``, ``stop`` ou ``break_even``.
    """

    rang: int
    zone: float
    origine: str
    fraction: float
    ratio_risque: float
    prix_sortie: float | None = None
    horodatage_sortie: pd.Timestamp | None = None
    resultat_r: float | None = None
    motif_sortie: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Sérialise la tranche pour le journal."""
        return {
            "rang": self.rang,
            "zone": round(self.zone, 4),
            "origine": self.origine,
            "fraction": round(self.fraction, 4),
            "ratio_risque": round(self.ratio_risque, 3),
            "prix_sortie": None if self.prix_sortie is None else round(self.prix_sortie, 4),
            "horodatage_sortie": (
                "" if self.horodatage_sortie is None else str(self.horodatage_sortie)
            ),
            "resultat_r": None if self.resultat_r is None else round(self.resultat_r, 4),
            "motif_sortie": self.motif_sortie,
        }


@dataclass(slots=True)
class Trade:
    """Un trade effectivement pris, de l'entrée à la sortie."""

    horodatage_entree: pd.Timestamp
    horodatage_sortie: pd.Timestamp | None = None
    sens: str = ""
    unite_ob: str = ""
    ob_haut: float = 0.0
    ob_bas: float = 0.0
    ob_ouverture_bougie1: pd.Timestamp | None = None
    unite_fvg: str = ""
    type_entree: str = ""
    prix_entree: float = 0.0
    stop: float = 0.0
    objectif: float = 0.0
    prix_sortie: float = 0.0
    lots: float = 0.0
    resultat_eur: float = 0.0
    resultat_r: float = 0.0
    motif_sortie: str = ""
    heure_entree: int = 0
    #: Tranches de sortie, dans l'ordre des zones. Vide hors mode ``paliers``.
    paliers: list[Palier] = field(default_factory=list)
    #: Prix du FVG qui a confirmé l'entrée, pour l'alerte.
    fvg_haut: float | None = None
    fvg_bas: float | None = None
    #: Setup d'origine et unité de détection (unité de l'OB ou du niveau) :
    #: la ventilation « par timeframe de détection » lit ce champ commun.
    setup: str = SETUP_ORDER_BLOCK
    unite_detection: str = ""
    #: Setup sweep : le niveau balayé, quand il s'est formé, l'extrême du
    #: sweep — ce que l'alerte doit dire.
    niveau_prix: float | None = None
    niveau_cote: str = ""
    niveau_unite: str = ""
    niveau_formation: pd.Timestamp | None = None
    sweep_extreme: float | None = None
    sweep_debut: pd.Timestamp | None = None
    unite_fibo: str = ""
    reference_prix: float | None = None
    #: ``False`` pour une variante à paliers sans passage à break-even.
    break_even_actif: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Sérialise le trade pour le journal CSV."""
        return {
            "setup": self.setup,
            "unite_detection": self.unite_detection,
            "niveau_prix": "" if self.niveau_prix is None else round(self.niveau_prix, 4),
            "niveau_cote": self.niveau_cote,
            "niveau_unite": self.niveau_unite,
            "niveau_formation": "" if self.niveau_formation is None else str(self.niveau_formation),
            "sweep_extreme": "" if self.sweep_extreme is None else round(self.sweep_extreme, 4),
            "unite_fibo": self.unite_fibo,
            "reference_prix": "" if self.reference_prix is None else round(self.reference_prix, 4),
            "horodatage_entree": str(self.horodatage_entree),
            "horodatage_sortie": "" if self.horodatage_sortie is None else str(self.horodatage_sortie),
            "sens": self.sens,
            "unite_ob": self.unite_ob,
            "ob_haut": round(self.ob_haut, 4),
            "ob_bas": round(self.ob_bas, 4),
            "ob_ouverture_bougie1": (
                "" if self.ob_ouverture_bougie1 is None else str(self.ob_ouverture_bougie1)
            ),
            "unite_fvg": self.unite_fvg,
            "type_entree": self.type_entree,
            "prix_entree": round(self.prix_entree, 4),
            "stop": round(self.stop, 4),
            "objectif": round(self.objectif, 4),
            "prix_sortie": round(self.prix_sortie, 4),
            "lots": self.lots,
            "resultat_eur": round(self.resultat_eur, 2),
            "resultat_r": round(self.resultat_r, 3),
            "motif_sortie": self.motif_sortie,
            "heure_entree": self.heure_entree,
            "fvg_haut": "" if self.fvg_haut is None else round(self.fvg_haut, 4),
            "fvg_bas": "" if self.fvg_bas is None else round(self.fvg_bas, 4),
            "n_paliers": len(self.paliers),
        }

    def lignes_paliers(self) -> list[dict[str, Any]]:
        """Sérialise les tranches, une ligne par tranche.

        Le détail vit dans son propre journal plutôt que dans des colonnes
        numérotées du journal principal : le nombre de tranches varie d'un
        trade à l'autre, et des colonnes ``palier_3_*`` vides sur la plupart
        des lignes se lisent mal.

        Returns:
            Une ligne par tranche, rattachée au trade par son horodatage
            d'entrée.
        """
        return [
            {"horodatage_entree": str(self.horodatage_entree), "sens": self.sens, **palier.to_dict()}
            for palier in self.paliers
        ]


@dataclass(slots=True)
class _Setup:
    """Setup en cours d'instruction, entre le déclencheur et l'entrée.

    Deux origines : la touche d'un order block (``ob`` renseigné) ou la
    confirmation d'un sweep (``niveau`` renseigné). La suite — FVG, touche,
    clôture au-delà, entrée au marché — est la même pour les deux.
    """

    origine: str
    sens: str
    debut: pd.Timestamp
    ob: ict.OrderBlock | None = None
    niveau: ict.NiveauLiquidite | None = None
    fvg: ict.FairValueGap | None = None
    unite_fvg: str = ""
    fvg_touche: bool = False
    barres: int = 0

    @property
    def unite(self) -> str:
        """Unité de détection : celle de l'OB ou du niveau."""
        return self.ob.unite if self.ob is not None else (self.niveau.unite if self.niveau else "")


class Backtest:
    """Rejoue la stratégie sur une série M1, sans anticipation.

    Args:
        m1: bougies d'une minute de XAUUSD.
        eurusd_quotidien: taux EUR/USD par jour, pour le dimensionnement.
        config: paramètres du backtest.
    """

    def __init__(
        self,
        m1: pd.DataFrame,
        eurusd_quotidien: pd.Series,
        config: ConfigBacktest | None = None,
    ) -> None:
        self.m1 = m1
        self.eurusd = eurusd_quotidien
        self.config = config or ConfigBacktest()

        # Les unités supérieures sont construites une fois. Ce n'est pas du
        # look-ahead : chaque bougie porte son instant de clôture, et le
        # moteur ne consultera que celles déjà closes.
        self.cadres: dict[str, pd.DataFrame] = {
            unite: bt_data.agreger(m1, unite) for unite in bt_data.UNITES
        }

        # Order blocks, avec l'instant où chacun devient connu.
        self.order_blocks: list[ict.OrderBlock] = []
        for unite in self.config.unites_ob:
            self.order_blocks.extend(
                ict.detecter_order_blocks(self.cadres[unite], unite)
            )
        self.order_blocks.sort(key=lambda o: o.fin_motif)

        # Niveaux de liquidité du setup sweep, avec l'instant où chacun devient
        # connu (clôture de la bougie i + sensibilité) — même discipline que
        # les order blocks. Vides si le setup n'est pas joué.
        self.niveaux: list[ict.NiveauLiquidite] = []
        if SETUP_SWEEP in self.config.setups:
            for unite in self.config.unites_sweep:
                self.niveaux.extend(
                    ict.detecter_niveaux_liquidite(
                        self.cadres[unite], unite, self.config.sensibilite_pivot
                    )
                )
            self.niveaux.sort(key=lambda n: n.connu_a)
        # Clôtures des bougies de chaque unité de sweep, dans l'ordre : le
        # moteur y lit « une bougie de l'unité vient de clore » sans jamais
        # regarder une clôture future. Les trous (week-end) ne posent pas de
        # problème : une bougie est traitée quand sa fin est passée, quelle
        # que soit la minute où le moteur s'en aperçoit.
        self.clotures_unites: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for unite in self.config.unites_sweep:
            cadre = self.cadres[unite]
            fins = (cadre.index + bt_data.DUREES[unite]).to_numpy()
            self.clotures_unites[unite] = (fins, cadre["close"].to_numpy(dtype="float64"))

        self.trades: list[Trade] = []
        # Combien de fois plusieurs zones, d'unités différentes, sont
        # touchées dans la même minute. La stratégie ne dit pas laquelle
        # prime ; le moteur retient la première confirmée, et ce compteur dit
        # si le cas est marginal ou s'il mérite une règle de priorité.
        self.touches_simultanees: int = 0
        self.detail_touches_simultanees: list[dict[str, Any]] = []
        self.abandons: dict[str, int] = {
            ABANDON_SANS_FVG: 0,
            ABANDON_TAILLE: 0,
            ABANDON_EXPIRATION: 0,
            ABANDON_SANS_REFERENCE: 0,
            ABANDON_SANS_STRUCTUREL: 0,
        }
        #: Sweeps confirmés, y compris ceux survenus pendant une position
        #: ouverte (le niveau est consommé, aucun setup n'est ouvert).
        self.sweeps_confirmes: int = 0
        #: Interférence entre setups : déclencheurs survenus pendant une
        #: position ouverte de l'autre setup (zone touchée sans setup, sweep
        #: confirmé sans setup), et setups perdus par le « break » quand
        #: l'autre setup a confirmé le premier dans la même minute.
        self.interferences: dict[str, int] = {
            "ob_touche_pendant_position_sweep": 0,
            "sweep_confirme_pendant_position_ob": 0,
            "ob_perdu_par_confirmation_sweep": 0,
            "sweep_perdu_par_confirmation_ob": 0,
            "sweep_refuse_priorite_ob": 0,
        }

    # -- Outils ------------------------------------------------------------
    def _taux(self, moment: pd.Timestamp) -> float | None:
        """Taux EUR/USD applicable à une date.

        Args:
            moment: instant de l'entrée.

        Returns:
            Le taux du jour, ou le dernier connu **avant** ce jour. ``None``
            si aucun taux antérieur n'existe : reprendre un taux postérieur
            serait du look-ahead sur une grandeur qui change tous les jours.
        """
        if self.eurusd is None or self.eurusd.empty:
            return None
        jour = pd.Timestamp(moment).normalize()
        anterieurs = self.eurusd[self.eurusd.index <= jour]
        return float(anterieurs.iloc[-1]) if len(anterieurs) else None

    def _jambe(self, ob: ict.OrderBlock, instant: pd.Timestamp) -> pd.DataFrame:
        """Isole la jambe qui a mené le prix jusqu'à l'order block.

        La jambe est le **dernier segment directionnel** atteignant la zone :
        elle part du dernier point de retournement confirmé sur l'unité de
        l'order block, pas de l'extrême le plus lointain depuis la formation
        de la zone. Un mouvement qui erre pendant vingt bougies avant de
        partir franchement vers l'order block ne doit pas voir ses hésitations
        élargir la fenêtre où le FVG est cherché.

        Args:
            ob: zone touchée.
            instant: instant du contact.

        Returns:
            Bougies de la jambe, sur l'unité de l'order block. Cadre vide si
            aucun retournement confirmé ne précède le contact.
        """
        cadre = bt_data.fenetre_close(self.cadres[ob.unite], ob.unite, instant)
        if cadre.empty:
            return cadre
        # Une profondeur bornée suffit : la jambe est par définition le
        # dernier segment, pas tout l'historique.
        cadre = cadre.iloc[-PROFONDEUR_JAMBE:]

        depart = ict.origine_de_jambe(cadre, ob.sens, self.config.sensibilite_swing)
        if depart is None:
            return cadre.iloc[:0]
        return cadre.iloc[depart:]

    def _chercher_fvg(
        self, sens_trade: str, debut: pd.Timestamp, instant: pd.Timestamp
    ) -> tuple[ict.FairValueGap | None, str]:
        """Cherche un FVG dans le sens du trade, en M5 puis M3 puis M1.

        Pour un order block, le sens du trade est celui de la zone, et le
        FVG cherché est de sens opposé à la zone (voir ict.CHOIX_INTERPRETATION) ;
        pour un sweep, c'est le sens de la reprise. Dans les deux cas c'est
        l'appelant qui dit quel sens de FVG il veut.

        Args:
            sens_trade: ``haussier`` (achat) ou ``baissier`` (vente).
            debut: début de la fenêtre (jambe, ou début du sweep).
            instant: instant courant.

        Returns:
            Couple ``(écart, unité)``. L'écart est ``None`` si aucune des
            trois unités n'en fournit.
        """
        # CHOIX D'INTERPRÉTATION (inchangé) : pour un achat, l'écart cherché est
        # baissier — c'est la borne haute d'un FVG baissier que la clôture
        # doit dépasser ; symétrique pour une vente.
        sens_voulu = ict.BAISSIER if sens_trade == ict.HAUSSIER else ict.HAUSSIER
        for unite in bt_data.UNITES_FVG:
            cadre = bt_data.fenetre_close(self.cadres[unite], unite, instant)
            cadre = cadre[(cadre.index >= debut) & (cadre.index <= instant)]
            ecarts = ict.detecter_fvg(cadre, unite, sens=sens_voulu)
            if ecarts:
                return ecarts[-1], unite
        return None, ""

    # -- Boucle principale -------------------------------------------------
    def executer(self) -> None:
        """Parcourt la série et joue la stratégie."""
        if self.m1 is None or self.m1.empty:
            _LOG.error("Aucune bougie M1 : backtest impossible.")
            return

        haut = self.m1["high"].to_numpy(dtype="float64")
        bas = self.m1["low"].to_numpy(dtype="float64")
        cloture = self.m1["close"].to_numpy(dtype="float64")
        index = self.m1.index
        duree_m1 = bt_data.DUREES["M1"]

        prochaine_zone = 0
        zones_actives: list[ict.OrderBlock] = []
        prochain_niveau = 0
        niveaux_actifs: list[ict.NiveauLiquidite] = []
        pointeurs_clotures: dict[str, int] = {unite: 0 for unite in self.clotures_unites}
        setups: list[_Setup] = []
        position: Trade | None = None
        stop_courant = objectif_courant = 0.0
        risque_eur = 0.0
        joue_ob = SETUP_ORDER_BLOCK in self.config.setups
        joue_sweep = SETUP_SWEEP in self.config.setups

        for i in range(len(index)):
            ouverture_barre = index[i]
            fin_barre = ouverture_barre + duree_m1

            # 1. Zones devenues connues à la clôture de cette barre. Les
            # order blocks restent détectés même quand leur setup n'est pas
            # joué : ils servent de niveaux structurels au setup sweep.
            while (
                prochaine_zone < len(self.order_blocks)
                and self.order_blocks[prochaine_zone].fin_motif <= fin_barre
            ):
                zones_actives.append(self.order_blocks[prochaine_zone])
                prochaine_zone += 1

            # 1b. Niveaux de liquidité devenus connus, plafonnés par unité
            # aux plus récents (voir ict.CHOIX_INTERPRETATION).
            while (
                prochain_niveau < len(self.niveaux)
                and self.niveaux[prochain_niveau].connu_a <= fin_barre
            ):
                nouveau = self.niveaux[prochain_niveau]
                niveaux_actifs.append(nouveau)
                prochain_niveau += 1
                memes = [n for n in niveaux_actifs if n.unite == nouveau.unite]
                if len(memes) > self.config.max_niveaux_par_unite:
                    memes[0].evince = True
                    niveaux_actifs.remove(memes[0])

            # 2. Gestion de la position ouverte, sur cette barre seulement.
            if position is not None and position.paliers:
                stop_courant, termine = self._avancer_paliers(
                    position, i, haut, bas, fin_barre, ouverture_barre,
                    stop_courant, risque_eur,
                )
                if termine:
                    self.trades.append(position)
                    position = None
            elif position is not None:
                sortie = None
                if position.sens == ict.HAUSSIER:
                    # Convention : à égalité dans la même minute, le stop
                    # l'emporte. L'hypothèse inverse flatterait le résultat.
                    if bas[i] <= stop_courant:
                        sortie = (stop_courant, "stop")
                    elif haut[i] >= objectif_courant:
                        sortie = (objectif_courant, "objectif")
                else:
                    if haut[i] >= stop_courant:
                        sortie = (stop_courant, "stop")
                    elif bas[i] <= objectif_courant:
                        sortie = (objectif_courant, "objectif")

                if sortie is not None:
                    prix, motif = sortie
                    prix_net = bt_exec.appliquer_couts_sortie(
                        prix, position.sens, self.config.execution
                    )
                    sens = 1.0 if position.sens == ict.HAUSSIER else -1.0
                    variation = (prix_net - position.prix_entree) * sens
                    taux = self._taux(ouverture_barre) or 1.0
                    position.prix_sortie = prix_net
                    position.horodatage_sortie = fin_barre
                    position.motif_sortie = motif
                    position.resultat_eur = (
                        variation * bt_exec.ONCES_PAR_LOT * position.lots / taux
                    )
                    position.resultat_r = (
                        position.resultat_eur / risque_eur if risque_eur else 0.0
                    )
                    self.trades.append(position)
                    position = None

            # 3. Mitigation des zones : une zone touchée cesse d'être offerte.
            nouvelles: list[_Setup] = []
            if position is None:
                restantes: list[ict.OrderBlock] = []
                for zone in zones_actives:
                    if not zone.mitige and zone.contient(haut[i], bas[i]):
                        zone.mitige = True
                        zone.horodatage_mitigation = fin_barre
                        if joue_ob:
                            jambe = self._jambe(zone, fin_barre)
                            if len(jambe) >= 2:
                                nouvelles.append(_Setup(
                                    origine=SETUP_ORDER_BLOCK, sens=zone.sens,
                                    debut=jambe.index[0], ob=zone,
                                ))
                        continue
                    if not zone.mitige:
                        restantes.append(zone)
                zones_actives = restantes

            else:
                # Position ouverte : les zones touchées sont tout de même
                # mitigées — le prix y est passé —, mais aucun setup n'est
                # ouvert, la stratégie ne prévoyant pas de cumuler.
                restantes = []
                for zone in zones_actives:
                    if zone.contient(haut[i], bas[i]):
                        zone.mitige = True
                        zone.horodatage_mitigation = fin_barre
                        if joue_ob and position.setup == SETUP_SWEEP:
                            self.interferences["ob_touche_pendant_position_sweep"] += 1
                        continue
                    restantes.append(zone)
                zones_actives = restantes

            # 3b. Sweeps : la traversée se lit sur la barre M1 (une mèche
            # suffit), la confirmation sur la clôture d'une bougie de l'unité
            # du niveau. L'ordre compte : l'extrême de cette barre est connu
            # avant qu'une bougie qui la contient ne soit examinée. Un sweep
            # confirmé pendant une position ouverte consomme le niveau sans
            # ouvrir de setup — même règle que la mitigation des zones.
            if joue_sweep and niveaux_actifs:
                for niveau in niveaux_actifs:
                    ict.avancer_niveau(niveau, haut[i], bas[i], ouverture_barre)
                for unite, (fins, clotures_unite) in self.clotures_unites.items():
                    ptr = pointeurs_clotures[unite]
                    while ptr < len(fins) and fins[ptr] <= fin_barre:
                        instant_cloture = pd.Timestamp(fins[ptr])
                        cloture_unite = float(clotures_unite[ptr])
                        for niveau in niveaux_actifs:
                            if niveau.unite != unite or not niveau.en_sweep:
                                continue
                            if ict.confirmer_balayage(niveau, cloture_unite, instant_cloture):
                                self.sweeps_confirmes += 1
                                if position is None:
                                    if self._ob_prioritaire(niveau, cloture_unite, zones_actives):
                                        self.interferences["sweep_refuse_priorite_ob"] += 1
                                        continue
                                    nouvelles.append(_Setup(
                                        origine=SETUP_SWEEP, sens=niveau.sens_trade,
                                        debut=niveau.debut_sweep, niveau=niveau,
                                    ))
                                elif position.setup == SETUP_ORDER_BLOCK:
                                    self.interferences["sweep_confirme_pendant_position_ob"] += 1
                        ptr += 1
                    pointeurs_clotures[unite] = ptr
                niveaux_actifs = [n for n in niveaux_actifs if not n.balaye]

            if position is None:
                # Plusieurs déclencheurs dans la même minute : la stratégie
                # ne tranche pas, on note le cas pour pouvoir en décider.
                if len(nouvelles) > 1:
                    unites = sorted({s.unite for s in nouvelles})
                    self.touches_simultanees += 1
                    self.detail_touches_simultanees.append(
                        {
                            "horodatage": str(fin_barre),
                            "n_zones": len(nouvelles),
                            "unites": unites,
                            "unites_distinctes": len(unites) > 1,
                            "setups": sorted({s.origine for s in nouvelles}),
                        }
                    )
                setups.extend(nouvelles)

            # 4. Instruction des setups en cours.
            if position is None:
                encore: list[_Setup] = []
                for setup in setups:
                    setup.barres += 1
                    if setup.barres > self.config.expiration_barres:
                        self.abandons[ABANDON_EXPIRATION] += 1
                        continue

                    resultat = self._avancer(setup, i, haut, bas, cloture, fin_barre)
                    if resultat is None:
                        encore.append(setup)
                        continue
                    if isinstance(resultat, str):
                        self.abandons[resultat] = self.abandons.get(resultat, 0) + 1
                        continue

                    position, stop_courant, objectif_courant, risque_eur = resultat
                    # Les setups pas encore visités sont perdus (voir la note
                    # de portage dans worker-scanner/src/moteur.js) : on
                    # compte ceux que l'autre setup vient d'évincer.
                    for perdu in setups[setups.index(setup) + 1:]:
                        if perdu.origine != position.setup:
                            cle = ("ob_perdu_par_confirmation_sweep" if perdu.origine == SETUP_ORDER_BLOCK
                                   else "sweep_perdu_par_confirmation_ob")
                            self.interferences[cle] += 1
                    break
                setups = encore

        _LOG.info(
            "Backtest terminé : %d trade(s), %d abandon(s).",
            len(self.trades), sum(self.abandons.values()),
        )

    def _avancer_paliers(
        self,
        position: Trade,
        i: int,
        haut: np.ndarray,
        bas: np.ndarray,
        fin_barre: pd.Timestamp,
        ouverture_barre: pd.Timestamp,
        stop_courant: float,
        risque_eur: float,
    ) -> tuple[float, bool]:
        """Fait avancer une position à sorties échelonnées, sur une barre.

        Deux conventions, dans le prolongement de celles du moteur :

        * **le stop reste prioritaire** : il est examiné avant les zones, et
          s'il est touché il emporte tout le solde de la position ;
        * **le passage à break-even ne prend effet qu'à la barre suivante**.
          Le stop de la barre courante a déjà été évalué quand la première
          tranche se clôture ; considérer le break-even actif dans la même
          minute reviendrait à décider de l'ordre des évènements à
          l'intérieur d'une bougie, que la série d'une minute ne donne pas.
          L'hypothèse retenue est la moins flatteuse des deux.

        Args:
            position: trade en cours, porteur de ses tranches.
            i: indice de la barre courante.
            haut: hauts de la série M1.
            bas: bas de la série M1.
            fin_barre: instant de clôture de la barre.
            ouverture_barre: instant d'ouverture, pour le taux de change.
            stop_courant: stop en vigueur au début de la barre.
            risque_eur: perte en euros au stop initial, dénominateur du R.

        Returns:
            Couple ``(stop en vigueur pour la barre suivante, position close)``.
        """
        achat = position.sens == ict.HAUSSIER
        signe = 1.0 if achat else -1.0
        taux = self._taux(ouverture_barre) or 1.0
        au_break_even = stop_courant == position.prix_entree

        def clore(palier: Palier, prix: float, motif: str) -> None:
            """Clôture une tranche et lui impute sa part du résultat."""
            prix_net = bt_exec.appliquer_couts_sortie(
                prix, position.sens, self.config.execution
            )
            variation = (prix_net - position.prix_entree) * signe
            resultat_eur = (
                variation * bt_exec.ONCES_PAR_LOT * position.lots * palier.fraction / taux
            )
            palier.prix_sortie = prix_net
            palier.horodatage_sortie = fin_barre
            palier.motif_sortie = motif
            palier.resultat_r = resultat_eur / risque_eur if risque_eur else 0.0
            position.resultat_eur += resultat_eur
            position.resultat_r += palier.resultat_r
            position.prix_sortie = prix_net
            position.horodatage_sortie = fin_barre
            position.motif_sortie = motif

        ouverts = [p for p in position.paliers if p.prix_sortie is None]
        if not ouverts:
            return stop_courant, True

        # Le stop, d'abord : touché, il emporte tout le solde.
        touche_stop = bas[i] <= stop_courant if achat else haut[i] >= stop_courant
        if touche_stop:
            motif = SORTIE_BREAK_EVEN if au_break_even else SORTIE_STOP
            for palier in ouverts:
                clore(palier, stop_courant, motif)
            return stop_courant, True

        # Puis les zones, dans l'ordre : une barre ample peut en franchir
        # plusieurs, chacune se dénouant à son propre niveau.
        for palier in ouverts:
            atteinte = haut[i] >= palier.zone if achat else bas[i] <= palier.zone
            if atteinte:
                clore(palier, palier.zone, SORTIE_OBJECTIF)

        reste = [p for p in position.paliers if p.prix_sortie is None]
        if not reste:
            return stop_courant, True

        # Une tranche au moins vient de tomber : le solde passe à
        # break-even, et le stop n'y bougera plus — sauf pour une variante
        # jouée sans break-even, où le stop initial reste en place.
        if len(reste) < len(ouverts) and not au_break_even and position.break_even_actif:
            stop_courant = position.prix_entree
        return stop_courant, False

    def _avancer(
        self,
        setup: _Setup,
        i: int,
        haut: np.ndarray,
        bas: np.ndarray,
        cloture: np.ndarray,
        fin_barre: pd.Timestamp,
    ) -> tuple[Trade, float, float, float] | str | None:
        """Fait progresser un setup d'une barre.

        Args:
            setup: setup en cours.
            i: position de la barre courante.
            haut: plus hauts M1.
            bas: plus bas M1.
            cloture: clôtures M1.
            fin_barre: instant de clôture de la barre.

        Returns:
            Le trade ouvert et ses niveaux, un motif d'abandon, ou ``None``
            si le setup reste en attente.
        """
        achat = setup.sens == ict.HAUSSIER

        # -- Trouver puis confirmer le FVG, seule mécanique d'entrée --------
        if setup.fvg is None:
            setup.fvg, setup.unite_fvg = self._chercher_fvg(
                setup.sens, setup.debut, fin_barre
            )
            if setup.fvg is None:
                return ABANDON_SANS_FVG

        ecart = setup.fvg
        if not setup.fvg_touche:
            if bas[i] <= ecart.haut and haut[i] >= ecart.bas:
                setup.fvg_touche = True
            return None

        # CHOIX D'INTERPRÉTATION — « clôturer au-delà » est entendu comme
        # dépasser la borne du FVG située dans le sens du trade.
        confirme = cloture[i] > ecart.haut if achat else cloture[i] < ecart.bas
        if not confirme:
            return None

        # Confirmation acquise : entrée au marché, toujours, sans exception.
        return self._ouvrir(setup, cloture[i], fin_barre)

    def _ouvrir(
        self, setup: _Setup, prix: float, fin_barre: pd.Timestamp
    ) -> tuple[Trade, float, float, float] | str:
        """Ouvre une position si la taille tient dans la fourchette de risque.

        Args:
            setup: setup confirmé.
            prix: prix théorique d'entrée.
            fin_barre: instant d'entrée.

        Returns:
            Le trade et ses niveaux, ou un motif d'abandon.
        """
        if setup.origine == SETUP_SWEEP:
            return self._ouvrir_sweep(setup, prix, fin_barre)

        sens = setup.ob.sens
        achat = sens == ict.HAUSSIER
        prix_entree = bt_exec.appliquer_couts_entree(prix, sens, self.config.execution)

        stop = bt_exec.calculer_stop(
            sens, setup.ob.haut, setup.ob.bas, setup.ob.meche_bougie2,
            self.config.execution.marge_stop,
        )
        stop = self._elargir_stop(stop, prix_entree, achat)
        distance = abs(prix_entree - stop)
        taux = self._taux(fin_barre)
        if taux is None:
            return ABANDON_TAILLE

        taille = bt_exec.dimensionner(distance, taux, self.config.execution)
        if not taille["prenable"]:
            return ABANDON_TAILLE

        paliers: list[Palier] = []
        if self.config.mode_tp == "ratio":
            objectif = (
                prix_entree + distance * self.config.ratio_tp
                if achat
                else prix_entree - distance * self.config.ratio_tp
            )
        elif self.config.mode_tp == "paliers":
            zones = self._niveaux_de_liquidite(prix_entree, achat, fin_barre)
            if not zones:
                # Même règle que la variante structurelle : sans niveau de
                # liquidité devant, la stratégie ne prévoit pas de repli. Les
                # deux variantes abandonnent donc les mêmes setups, ce qui les
                # rend comparables sur exactement la même population.
                return ABANDON_EXPIRATION
            fractions = repartir_paliers(len(zones))
            paliers = [
                Palier(
                    rang=rang,
                    zone=niveau,
                    origine=origine,
                    fraction=fraction,
                    ratio_risque=abs(niveau - prix_entree) / distance if distance else 0.0,
                )
                for rang, ((niveau, origine), fraction) in enumerate(
                    zip(zones, fractions), start=1
                )
            ]
            # L'objectif affiché reste la première zone : c'est celle qui
            # décide de la sortie de la première tranche et du passage à
            # break-even.
            objectif = zones[0][0]
        else:
            objectif = self._objectif_structurel(setup, prix_entree, achat, fin_barre)
            if objectif is None:
                # Aucun niveau de liquidité devant : la stratégie ne prévoit
                # pas de repli, le setup est abandonné.
                return ABANDON_EXPIRATION

        trade = Trade(
            horodatage_entree=fin_barre,
            sens=sens,
            unite_ob=setup.ob.unite,
            ob_haut=setup.ob.haut,
            ob_bas=setup.ob.bas,
            ob_ouverture_bougie1=setup.ob.ouverture_bougie1,
            unite_fvg=setup.unite_fvg,
            type_entree="marche",
            prix_entree=prix_entree,
            stop=stop,
            objectif=objectif,
            lots=taille["lots"],
            heure_entree=int(pd.Timestamp(fin_barre).hour),
            paliers=paliers,
            fvg_haut=None if setup.fvg is None else setup.fvg.haut,
            fvg_bas=None if setup.fvg is None else setup.fvg.bas,
            setup=SETUP_ORDER_BLOCK,
            unite_detection=setup.ob.unite,
        )
        return trade, stop, objectif, float(taille["perte_eur"])

    def _elargir_stop(self, stop: float, prix_entree: float, achat: bool) -> float:
        """Éloigne le stop de l'entrée, en multiple de sa distance d'origine.

        Le stop garde son côté : un stop d'achat ne peut que descendre. À
        multiplicateur 1,0 — le défaut — la valeur est rendue telle quelle,
        au flottant près, de sorte que les résultats publiés ne bougent pas.

        Args:
            stop: niveau calculé par la règle du setup.
            prix_entree: prix d'entrée, coûts appliqués.
            achat: sens de la position.

        Returns:
            Le niveau élargi.
        """
        multiplicateur = float(self.config.multiplicateur_stop)
        if multiplicateur == 1.0:
            return stop
        distance = abs(prix_entree - stop) * multiplicateur
        return prix_entree - distance if achat else prix_entree + distance

    # -- Setup sweep ---------------------------------------------------------
    def _ouvrir_sweep(
        self, setup: _Setup, prix: float, fin_barre: pd.Timestamp
    ) -> tuple[Trade, float, float, float] | str:
        """Ouvre une position sur un sweep confirmé.

        Stop au-delà de la mèche du sweep ; objectif selon la famille
        configurée (voir :data:`OBJECTIF_SWEEP_FIBO` et suivantes).

        Args:
            setup: setup sweep confirmé par son FVG.
            prix: prix théorique d'entrée.
            fin_barre: instant d'entrée.

        Returns:
            Le trade et ses niveaux, ou un motif d'abandon.
        """
        niveau = setup.niveau
        assert niveau is not None and niveau.extreme_sweep is not None
        sens = setup.sens
        achat = sens == ict.HAUSSIER
        prix_entree = bt_exec.appliquer_couts_entree(prix, sens, self.config.execution)

        marge = (
            self.config.execution.marge_stop
            if self.config.marge_stop_sweep is None else self.config.marge_stop_sweep
        )
        stop = niveau.extreme_sweep - marge if achat else niveau.extreme_sweep + marge
        stop = self._elargir_stop(stop, prix_entree, achat)
        distance = abs(prix_entree - stop)
        taux = self._taux(fin_barre)
        if taux is None:
            return ABANDON_TAILLE
        taille = bt_exec.dimensionner(distance, taux, self.config.execution)
        if not taille["prenable"]:
            return ABANDON_TAILLE

        objectif_sweep = self.config.objectif_sweep
        paliers: list[Palier] = []
        reference: float | None = None
        cible_fibo: float | None = None
        if objectif_sweep in (OBJECTIF_SWEEP_FIBO, OBJECTIF_SWEEP_FIBO_STRUCTUREL):
            reference = self._reference_fibo(niveau, achat, fin_barre)
            if reference is None:
                return ABANDON_SANS_REFERENCE
            amplitude = abs(reference - niveau.extreme_sweep)
            cible_fibo = (
                niveau.extreme_sweep + amplitude * self.config.ratio_fibo
                if achat else niveau.extreme_sweep - amplitude * self.config.ratio_fibo
            )
            # Une cible déjà dépassée à l'entrée n'est pas un objectif.
            if (cible_fibo <= prix_entree) if achat else (cible_fibo >= prix_entree):
                return ABANDON_SANS_REFERENCE

        if objectif_sweep == OBJECTIF_SWEEP_FIBO:
            objectif = float(cible_fibo)
        elif objectif_sweep == OBJECTIF_SWEEP_FIBO_STRUCTUREL:
            structurels = self._niveaux_structurels(float(cible_fibo), achat, fin_barre)
            if not structurels:
                return ABANDON_SANS_STRUCTUREL
            niveau_struct, origine_struct = structurels[0]
            fraction = self.config.fraction_fibo
            paliers = [
                Palier(rang=1, zone=float(cible_fibo), origine=ORIGINE_FIBO, fraction=fraction,
                       ratio_risque=abs(cible_fibo - prix_entree) / distance if distance else 0.0),
                Palier(rang=2, zone=niveau_struct, origine=origine_struct, fraction=1.0 - fraction,
                       ratio_risque=abs(niveau_struct - prix_entree) / distance if distance else 0.0),
            ]
            objectif = float(cible_fibo)
        elif objectif_sweep == OBJECTIF_SWEEP_STRUCTUREL:
            structurels = self._niveaux_structurels(prix_entree, achat, fin_barre)
            if not structurels:
                return ABANDON_SANS_STRUCTUREL
            objectif = structurels[0][0]
        else:
            raise ValueError(f"Famille d'objectif sweep inconnue : {objectif_sweep}")

        trade = Trade(
            horodatage_entree=fin_barre,
            sens=sens,
            unite_ob="",
            unite_fvg=setup.unite_fvg,
            type_entree="marche",
            prix_entree=prix_entree,
            stop=stop,
            objectif=objectif,
            lots=taille["lots"],
            heure_entree=int(pd.Timestamp(fin_barre).hour),
            paliers=paliers,
            fvg_haut=None if setup.fvg is None else setup.fvg.haut,
            fvg_bas=None if setup.fvg is None else setup.fvg.bas,
            setup=SETUP_SWEEP,
            unite_detection=niveau.unite,
            niveau_prix=niveau.prix,
            niveau_cote=niveau.cote,
            niveau_unite=niveau.unite,
            niveau_formation=niveau.formation,
            sweep_extreme=niveau.extreme_sweep,
            sweep_debut=niveau.debut_sweep,
            unite_fibo=self.config.unite_fibo if reference is not None else "",
            reference_prix=reference,
            break_even_actif=self.config.break_even_sweep,
        )
        return trade, stop, objectif, float(taille["perte_eur"])

    def _ob_prioritaire(
        self, niveau: ict.NiveauLiquidite, prix: float, zones_actives: list[ict.OrderBlock]
    ) -> bool:
        """Dit si un order block actif prime sur ce sweep (règle à l'essai).

        Un order block connu, non mitigé, de même sens que le trade du sweep,
        dont la borne la plus proche est à moins de ``proximite_ob_usd`` du
        prix de confirmation : l'OB est la zone que le prix vient chercher,
        le sweep ne doit pas occuper la position à sa place.

        Args:
            niveau: niveau dont le sweep vient d'être confirmé.
            prix: clôture de confirmation.
            zones_actives: order blocks actifs à cet instant.

        Returns:
            ``True`` si le sweep doit être refusé.
        """
        if not self.config.priorite_ob:
            return False
        sens = niveau.sens_trade
        for zone in zones_actives:
            if zone.mitige or zone.sens != sens:
                continue
            distance = min(abs(prix - zone.haut), abs(prix - zone.bas))
            if zone.contient(prix, prix) or distance <= self.config.proximite_ob_usd:
                return True
        return False

    def _reference_fibo(
        self, niveau: ict.NiveauLiquidite, achat: bool, instant: pd.Timestamp
    ) -> float | None:
        """Situe l'origine du dernier mouvement directionnel précédant le sweep.

        Sur l'unité d'ancrage, le mouvement qui a mené au sweep d'un plus bas
        est une descente : elle part du dernier sommet confirmé. Même règle
        de swing (et même précaution de causalité) que la jambe du setup OB.

        Args:
            niveau: niveau balayé.
            achat: sens du trade.
            instant: instant courant.

        Returns:
            Le prix d'origine du mouvement, ou ``None`` s'il n'est pas
            mesurable (aucun retournement confirmé, ou origine du mauvais
            côté de l'extrême).
        """
        unite = self.config.unite_fibo
        cadre = bt_data.fenetre_close(self.cadres[unite], unite, instant)
        if cadre.empty:
            return None
        cadre = cadre.iloc[-PROFONDEUR_JAMBE:]
        # Achat : jambe descendante, origine à un sommet — c'est le cas que
        # origine_de_jambe traite pour un order block haussier.
        depart = ict.origine_de_jambe(
            cadre, ict.HAUSSIER if achat else ict.BAISSIER, self.config.sensibilite_swing
        )
        if depart is None:
            return None
        origine = float(cadre["high"].iloc[depart]) if achat else float(cadre["low"].iloc[depart])
        extreme = float(niveau.extreme_sweep)
        if (origine <= extreme) if achat else (origine >= extreme):
            return None
        return origine

    def _niveaux_structurels(
        self, seuil: float, achat: bool, instant: pd.Timestamp
    ) -> list[tuple[float, str]]:
        """Énumère les niveaux de liquidité non balayés au-delà d'un seuil.

        Candidats : les niveaux de pivot encore actifs et non traversés, sur
        toutes les unités suivies (un ancien plus haut pour un achat, un
        ancien plus bas pour une vente), et les order blocks actifs — la
        liste que l'énoncé donne pour les variantes 2 et 3.

        Args:
            seuil: prix au-delà duquel chercher (entrée, ou cible 0,72).
            achat: sens du trade.
            instant: instant courant.

        Returns:
            Couples ``(niveau, origine)``, du plus proche au plus lointain.
        """
        candidats: list[tuple[float, str]] = []
        cote_voulu = ict.COTE_HAUT if achat else ict.COTE_BAS
        for niveau in self.niveaux:
            if (
                niveau.connu_a > instant or niveau.balaye or niveau.en_sweep
                or niveau.evince or niveau.cote != cote_voulu
            ):
                continue
            candidats.append((niveau.prix, ORIGINE_NIVEAU_HAUT if achat else ORIGINE_NIVEAU_BAS))
        for zone in self.order_blocks:
            if zone.mitige or zone.fin_motif > instant:
                continue
            candidats.append((zone.bas if achat else zone.haut, "order_block"))
        devant = [(n, o) for n, o in candidats if (n > seuil if achat else n < seuil)]
        vus: dict[float, str] = {}
        for niveau, origine in devant:
            vus.setdefault(niveau, origine)
        return sorted(vus.items(), key=lambda c: c[0] if achat else -c[0])

    def _niveaux_de_liquidite(
        self,
        prix: float,
        achat: bool,
        instant: pd.Timestamp,
        maximum: int = MAX_ZONES_PALIERS,
    ) -> list[tuple[float, str]]:
        """Énumère les zones de liquidité devant le prix, de la plus proche
        à la plus lointaine.

        Les candidats sont ceux que la stratégie énumère : extrêmes de la
        veille, extrêmes de la session asiatique du jour, et order blocks
        encore actifs. Chaque niveau garde son origine, pour que l'alerte
        puisse dire *quelle* liquidité elle vise plutôt qu'un simple prix.

        Les doublons sont écartés : deux candidats peuvent tomber sur le même
        prix — le haut de la veille et celui de la session asiatique
        coïncident dès que le sommet du jour précédent a été fait le soir —,
        et les compter deux fois donnerait deux tranches sur un seul niveau.

        Args:
            prix: prix d'entrée.
            achat: sens de la position.
            instant: instant de l'entrée.
            maximum: nombre de zones retenues au plus.

        Returns:
            Couples ``(niveau, origine)``, triés du plus proche au plus
            lointain, au plus ``maximum`` éléments. Liste vide si aucun
            niveau n'est devant le prix.
        """
        veille, asiatique = self._niveaux_de_session(instant)
        candidats: list[tuple[float, str]] = []
        for niveau, origine in zip(veille, ("veille_haut", "veille_bas")):
            candidats.append((niveau, origine))
        for niveau, origine in zip(asiatique, ("asie_haut", "asie_bas")):
            candidats.append((niveau, origine))

        for zone in self.order_blocks:
            if zone.mitige or zone.fin_motif > instant:
                continue
            candidats.append((zone.bas if achat else zone.haut, "order_block"))

        devant = [(n, o) for n, o in candidats if (n > prix if achat else n < prix)]

        # Dédoublonnage sur le niveau, en gardant la première origine
        # rencontrée : l'ordre des candidats ci-dessus fait primer les
        # extrêmes de session sur les order blocks, plus nombreux.
        vus: dict[float, str] = {}
        for niveau, origine in devant:
            vus.setdefault(niveau, origine)

        ordonnes = sorted(vus.items(), key=lambda c: c[0] if achat else -c[0])
        return ordonnes[:maximum]

    def _objectif_structurel(
        self, setup: _Setup, prix: float, achat: bool, instant: pd.Timestamp
    ) -> float | None:
        """Cherche le niveau de liquidité le plus proche dans le sens du trade.

        Le plus proche des niveaux énumérés par
        :meth:`_niveaux_de_liquidite` — ni le dédoublonnage ni le plafond de
        cette dernière ne peuvent changer *quel* niveau vient en tête, la
        variante A garde donc exactement le comportement qu'elle avait avant
        l'introduction des paliers.

        Args:
            setup: setup confirmé.
            prix: prix d'entrée.
            achat: sens de la position.
            instant: instant de l'entrée.

        Returns:
            Le niveau retenu, ou ``None`` si aucun n'est devant le prix.
        """
        niveaux = self._niveaux_de_liquidite(prix, achat, instant)
        return niveaux[0][0] if niveaux else None

    def _niveaux_de_session(
        self, instant: pd.Timestamp
    ) -> tuple[list[float], list[float]]:
        """Calcule les extrêmes de la veille et de la session asiatique.

        La session asiatique retenue court de 20h00 à minuit, heure de
        New York — le réglage de l'indicateur TradingView de l'utilisateur.
        La conversion passe par le fuseau nommé, ce qui gère seul les
        changements d'heure : 20h00 à New York reste 20h00 toute l'année,
        même si l'écart avec UTC change de une heure entre mars et novembre.

        Args:
            instant: instant de l'entrée.

        Returns:
            Couple ``(extrêmes de la veille, extrêmes de la session asiatique)``.
        """
        from zoneinfo import ZoneInfo

        ny = ZoneInfo("America/New_York")
        local = pd.Timestamp(instant).tz_convert(ny)
        jour = local.normalize()

        # Session asiatique : la veille au soir, de 20h00 à minuit.
        debut_asie = (jour - pd.Timedelta(days=1)).replace(hour=20)
        fin_asie = jour

        fenetre = self.m1[
            (self.m1.index >= debut_asie.tz_convert("UTC"))
            & (self.m1.index < min(fin_asie.tz_convert("UTC"), pd.Timestamp(instant)))
        ]
        asiatique = (
            [float(fenetre["high"].max()), float(fenetre["low"].min())]
            if not fenetre.empty
            else []
        )

        debut_veille = (jour - pd.Timedelta(days=1)).tz_convert("UTC")
        veille_cadre = self.m1[
            (self.m1.index >= debut_veille)
            & (self.m1.index < min(jour.tz_convert("UTC"), pd.Timestamp(instant)))
        ]
        veille = (
            [float(veille_cadre["high"].max()), float(veille_cadre["low"].min())]
            if not veille_cadre.empty
            else []
        )
        return veille, asiatique
