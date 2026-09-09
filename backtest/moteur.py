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

__all__ = ["ConfigBacktest", "Trade", "Backtest"]


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

    def to_dict(self) -> dict[str, Any]:
        """Sérialise le trade pour le journal CSV."""
        return {
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
        }


@dataclass(slots=True)
class _Setup:
    """Setup en cours d'instruction, entre la touche et l'entrée."""

    ob: ict.OrderBlock
    debut: pd.Timestamp
    fvg: ict.FairValueGap | None = None
    unite_fvg: str = ""
    fvg_touche: bool = False
    barres: int = 0


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
        self, ob: ict.OrderBlock, debut: pd.Timestamp, instant: pd.Timestamp
    ) -> tuple[ict.FairValueGap | None, str]:
        """Cherche un FVG de sens opposé à la zone, en M5 puis M3 puis M1.

        Args:
            ob: zone touchée.
            debut: début de la jambe.
            instant: instant du contact.

        Returns:
            Couple ``(écart, unité)``. L'écart est ``None`` si aucune des
            trois unités n'en fournit.
        """
        sens_voulu = ict.BAISSIER if ob.sens == ict.HAUSSIER else ict.HAUSSIER
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
        setups: list[_Setup] = []
        position: Trade | None = None
        stop_courant = objectif_courant = 0.0
        risque_eur = 0.0

        for i in range(len(index)):
            ouverture_barre = index[i]
            fin_barre = ouverture_barre + duree_m1

            # 1. Zones devenues connues à la clôture de cette barre.
            while (
                prochaine_zone < len(self.order_blocks)
                and self.order_blocks[prochaine_zone].fin_motif <= fin_barre
            ):
                zones_actives.append(self.order_blocks[prochaine_zone])
                prochaine_zone += 1

            # 2. Gestion de la position ouverte, sur cette barre seulement.
            if position is not None:
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
            if position is None:
                nouvelles: list[_Setup] = []
                restantes: list[ict.OrderBlock] = []
                for zone in zones_actives:
                    if not zone.mitige and zone.contient(haut[i], bas[i]):
                        zone.mitige = True
                        zone.horodatage_mitigation = fin_barre
                        jambe = self._jambe(zone, fin_barre)
                        if len(jambe) >= 2:
                            nouvelles.append(_Setup(ob=zone, debut=jambe.index[0]))
                        continue
                    if not zone.mitige:
                        restantes.append(zone)
                zones_actives = restantes

                # Plusieurs zones touchées dans la même minute : la stratégie
                # ne tranche pas, on note le cas pour pouvoir en décider.
                if len(nouvelles) > 1:
                    unites = sorted({s.ob.unite for s in nouvelles})
                    self.touches_simultanees += 1
                    self.detail_touches_simultanees.append(
                        {
                            "horodatage": str(fin_barre),
                            "n_zones": len(nouvelles),
                            "unites": unites,
                            "unites_distinctes": len(unites) > 1,
                        }
                    )
                setups.extend(nouvelles)
            else:
                # Position ouverte : les zones touchées sont tout de même
                # mitigées — le prix y est passé —, mais aucun setup n'est
                # ouvert, la stratégie ne prévoyant pas de cumuler.
                restantes = []
                for zone in zones_actives:
                    if zone.contient(haut[i], bas[i]):
                        zone.mitige = True
                        zone.horodatage_mitigation = fin_barre
                        continue
                    restantes.append(zone)
                zones_actives = restantes

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
                    break
                setups = encore

        _LOG.info(
            "Backtest terminé : %d trade(s), %d abandon(s).",
            len(self.trades), sum(self.abandons.values()),
        )

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
        achat = setup.ob.sens == ict.HAUSSIER

        # -- Trouver puis confirmer le FVG, seule mécanique d'entrée --------
        if setup.fvg is None:
            setup.fvg, setup.unite_fvg = self._chercher_fvg(
                setup.ob, setup.debut, fin_barre
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
        sens = setup.ob.sens
        achat = sens == ict.HAUSSIER
        prix_entree = bt_exec.appliquer_couts_entree(prix, sens, self.config.execution)

        stop = bt_exec.calculer_stop(
            sens, setup.ob.haut, setup.ob.bas, setup.ob.meche_bougie2,
            self.config.execution.marge_stop,
        )
        distance = abs(prix_entree - stop)
        taux = self._taux(fin_barre)
        if taux is None:
            return ABANDON_TAILLE

        taille = bt_exec.dimensionner(distance, taux, self.config.execution)
        if not taille["prenable"]:
            return ABANDON_TAILLE

        if self.config.mode_tp == "ratio":
            objectif = (
                prix_entree + distance * self.config.ratio_tp
                if achat
                else prix_entree - distance * self.config.ratio_tp
            )
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
        )
        return trade, stop, objectif, float(taille["perte_eur"])

    def _objectif_structurel(
        self, setup: _Setup, prix: float, achat: bool, instant: pd.Timestamp
    ) -> float | None:
        """Cherche le niveau de liquidité le plus proche dans le sens du trade.

        Les candidats sont ceux que la stratégie énumère : extrêmes de la
        session asiatique du jour, extrêmes de la veille, et order blocks
        encore actifs.

        Args:
            setup: setup confirmé.
            prix: prix d'entrée.
            achat: sens de la position.
            instant: instant de l'entrée.

        Returns:
            Le niveau retenu, ou ``None`` si aucun n'est devant le prix.
        """
        niveaux: list[float] = []

        veille, asiatique = self._niveaux_de_session(instant)
        niveaux.extend(veille)
        niveaux.extend(asiatique)

        for zone in self.order_blocks:
            if zone.mitige or zone.fin_motif > instant:
                continue
            niveaux.append(zone.bas if achat else zone.haut)

        # Le niveau le plus proche devant le prix, dans le sens du trade.
        if achat:
            devant = [n for n in niveaux if n > prix]
            return min(devant) if devant else None
        devant = [n for n in niveaux if n < prix]
        return max(devant) if devant else None

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
