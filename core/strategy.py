"""Contrat de stratégie partagé par le backtest et le scan quotidien.

Raison d'être
-------------
Une stratégie n'existe qu'à un seul endroit : ici. Le backtester et le scan
du matin importent la *même* classe et appellent les *mêmes* méthodes. Sans
cette contrainte, deux implémentations dérivent l'une de l'autre et le
signal reçu à 7 h du matin n'est plus celui qui a été testé sur dix ans
d'historique — c'est la façon la plus courante de se mentir à soi-même.

Ce module ne contient donc **aucune règle de trading réelle**. Il définit
l'interface et le dimensionnement par le risque. La stratégie effective
(approche ICT / order flow sur XAUUSD) sera fournie plus tard sous forme
d'une sous-classe de :class:`Strategy`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

import pandas as pd

from core import indicators

_LOG: Final = logging.getLogger(__name__)

__all__ = [
    "Signal",
    "Position",
    "RiskConfig",
    "Strategy",
    "ExampleTrendStrategy",
    "scan_today",
]


class Signal(Enum):
    """Sens d'une position."""

    LONG = "long"
    SHORT = "short"
    FLAT = "flat"

    @property
    def sens(self) -> int:
        """Coefficient directionnel : +1 acheteur, -1 vendeur, 0 hors marché."""
        return {Signal.LONG: 1, Signal.SHORT: -1, Signal.FLAT: 0}[self]


@dataclass(slots=True)
class Position:
    """Intention de position, complète et autoportante.

    Le stop est **obligatoire**. Une intention sans stop n'est pas
    dimensionnable par le risque, donc pas exécutable par ce système.

    Attributes:
        signal: sens de la position.
        entry: prix d'entrée retenu.
        stop: prix d'invalidation. Sous l'entrée si LONG, au-dessus si SHORT.
        target: objectif éventuel. ``None`` si la sortie est gérée par
            :meth:`Strategy.exit_signal` plutôt que par un objectif fixe.
        size_pct: fraction du capital effectivement engagée, remplie par
            :meth:`Strategy.position_size`. ``None`` tant que non dimensionnée.
        reason: justification lisible, reportée telle quelle dans le rapport.
        meta: informations libres (contexte macro, niveaux ICT, etc.).
    """

    signal: Signal
    entry: float
    stop: float
    target: float | None = None
    size_pct: float | None = None
    reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Valide la cohérence géométrique de l'intention.

        Raises:
            ValueError: si le stop est du mauvais côté de l'entrée, ou si un
                prix n'est pas strictement positif.
        """
        if self.signal is Signal.FLAT:
            raise ValueError(
                "Une Position représente une intention d'être en marché. "
                "Pour l'absence de position, renvoyez None plutôt que Signal.FLAT."
            )
        if not (self.entry > 0.0) or not (self.stop > 0.0):
            raise ValueError("Les prix d'entrée et de stop doivent être positifs.")
        if self.signal is Signal.LONG and self.stop >= self.entry:
            raise ValueError(
                f"Position LONG : le stop ({self.stop}) doit être strictement "
                f"sous l'entrée ({self.entry})."
            )
        if self.signal is Signal.SHORT and self.stop <= self.entry:
            raise ValueError(
                f"Position SHORT : le stop ({self.stop}) doit être strictement "
                f"au-dessus de l'entrée ({self.entry})."
            )
        if self.target is not None:
            if self.signal is Signal.LONG and self.target <= self.entry:
                raise ValueError("Position LONG : l'objectif doit dépasser l'entrée.")
            if self.signal is Signal.SHORT and self.target >= self.entry:
                raise ValueError("Position SHORT : l'objectif doit être sous l'entrée.")

    @property
    def risk_per_unit(self) -> float:
        """Perte encourue par unité si le stop est touché, en unité de prix."""
        return abs(self.entry - self.stop)

    @property
    def reward_per_unit(self) -> float | None:
        """Gain par unité si l'objectif est atteint. ``None`` sans objectif."""
        if self.target is None:
            return None
        return abs(self.target - self.entry)

    @property
    def rr(self) -> float | None:
        """Rapport gain/risque de l'intention. ``None`` sans objectif."""
        gain = self.reward_per_unit
        if gain is None or self.risk_per_unit == 0.0:
            return None
        return gain / self.risk_per_unit


@dataclass(slots=True, frozen=True)
class RiskConfig:
    """Paramètres de gestion du risque, exprimés en pourcentage du capital.

    Les valeurs par défaut correspondent à un compte prop firm de 10 000 $ :
    prudentes, elles ne constituent pas une recommandation.

    Attributes:
        risk_per_trade_pct: perte acceptée si le stop est touché, en % du capital.
        max_positions: nombre maximal de positions simultanées.
        max_exposure_pct: exposition notionnelle cumulée maximale, en % du capital.
        max_daily_loss_pct: perte journalière au-delà de laquelle on cesse de
            trader. La plupart des prop firms imposent cette limite.
        commission_pct: commission par transaction, en % du notionnel.
        slippage_pct: glissement supposé à l'exécution, en % du notionnel.
    """

    risk_per_trade_pct: float = 0.5
    max_positions: int = 3
    max_exposure_pct: float = 100.0
    max_daily_loss_pct: float = 3.0
    commission_pct: float = 0.02
    slippage_pct: float = 0.02

    def __post_init__(self) -> None:
        """Rejette les paramétrages incohérents.

        Raises:
            ValueError: si un pourcentage est négatif ou si le risque par
                trade dépasse la perte journalière tolérée.
        """
        if self.risk_per_trade_pct <= 0.0:
            raise ValueError("risk_per_trade_pct doit être strictement positif.")
        if self.max_positions < 1:
            raise ValueError("max_positions doit valoir au moins 1.")
        for nom in ("max_exposure_pct", "max_daily_loss_pct", "commission_pct", "slippage_pct"):
            if getattr(self, nom) < 0.0:
                raise ValueError(f"{nom} ne peut pas être négatif.")
        if self.risk_per_trade_pct > self.max_daily_loss_pct:
            raise ValueError(
                "Un seul trade perdant dépasserait la perte journalière autorisée "
                f"({self.risk_per_trade_pct} % > {self.max_daily_loss_pct} %)."
            )


class Strategy(ABC):
    """Interface que toute stratégie doit implémenter.

    Le backtester et le scan quotidien n'appellent que ces quatre méthodes.
    Toute logique supplémentaire reste interne à la sous-classe.

    Attributes:
        risk: paramètres de risque appliqués au dimensionnement.
        name: nom lisible, repris dans les rapports.
    """

    #: Une sous-classe destinée à la production doit passer cet attribut à True.
    TRADABLE: bool = False

    def __init__(self, risk: RiskConfig | None = None, name: str | None = None) -> None:
        """Initialise la stratégie.

        Args:
            risk: paramètres de risque. Valeurs par défaut si absent.
            name: nom lisible. Nom de la classe si absent.
        """
        self.risk = risk or RiskConfig()
        self.name = name or type(self).__name__

    @abstractmethod
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Précalcule tout ce dont la stratégie a besoin, une fois pour toutes.

        Appelée une seule fois avant la boucle du backtest et une seule fois
        avant le scan. Toutes les colonnes ajoutées ici doivent être causales.

        Args:
            df: DataFrame OHLCV brut.

        Returns:
            DataFrame enrichi, de même index.
        """

    @abstractmethod
    def entry_signal(
        self, df: pd.DataFrame, i: int, context: dict[str, Any] | None = None
    ) -> Position | None:
        """Décide d'une entrée à la barre ``i``.

        L'implémentation ne doit lire que ``df.iloc[:i + 1]``. Lire au-delà
        revient à connaître le futur.

        Args:
            df: DataFrame déjà passé par :meth:`prepare`.
            i: position entière de la barre évaluée.
            context: état extérieur du jour (régime macro, actualités,
                positions déjà ouvertes...).

        Returns:
            Une :class:`Position` si une entrée est justifiée, sinon ``None``.
        """

    @abstractmethod
    def exit_signal(
        self, df: pd.DataFrame, i: int, position: Position
    ) -> tuple[bool, str]:
        """Décide d'une sortie à la barre ``i`` pour une position ouverte.

        Args:
            df: DataFrame déjà passé par :meth:`prepare`.
            i: position entière de la barre évaluée.
            position: position actuellement ouverte.

        Returns:
            Couple ``(sortir, motif)``. Le motif est journalisé et reporté.
        """

    def position_size(self, capital: float, position: Position) -> float:
        """Dimensionne la position **par le risque**.

        La taille est choisie pour que toucher le stop coûte exactement
        ``risk_per_trade_pct`` % du capital :

        ``unités = capital × risk_per_trade_pct / 100 / |entrée − stop|``

        Le notionnel obtenu est ensuite plafonné par ``max_exposure_pct``.
        Quand ce plafond mord, la perte au stop devient *inférieure* au risque
        cible : on réduit, jamais on n'augmente.

        Args:
            capital: capital de référence, dans la devise du prix.
            position: intention à dimensionner. ``size_pct`` est renseigné
                comme effet de bord.

        Returns:
            Nombre d'unités à traiter. ``0.0`` si le dimensionnement est
            impossible.
        """
        if capital <= 0.0:
            _LOG.warning("Capital nul ou négatif (%s) : taille forcée à 0.", capital)
            return 0.0

        risque_unitaire = position.risk_per_unit
        if risque_unitaire <= 0.0:
            _LOG.warning(
                "Distance entrée-stop nulle sur %s : dimensionnement impossible.",
                position.reason or position.signal.value,
            )
            return 0.0

        montant_risque = capital * self.risk.risk_per_trade_pct / 100.0
        unites = montant_risque / risque_unitaire

        # Plafond d'exposition notionnelle.
        notionnel_max = capital * self.risk.max_exposure_pct / 100.0
        notionnel = unites * position.entry
        if notionnel > notionnel_max:
            _LOG.info(
                "Exposition plafonnée : %.2f ramené à %.2f (%.1f %% du capital).",
                notionnel,
                notionnel_max,
                self.risk.max_exposure_pct,
            )
            unites = notionnel_max / position.entry
            notionnel = notionnel_max

        position.size_pct = notionnel / capital * 100.0
        return unites

    def cout_aller_retour(self, notionnel: float) -> float:
        """Coût de friction estimé d'un aller-retour complet.

        Commission et glissement s'appliquent à l'entrée puis à la sortie.

        Args:
            notionnel: montant notionnel de la position.

        Returns:
            Coût total en devise.
        """
        taux = (self.risk.commission_pct + self.risk.slippage_pct) / 100.0
        return abs(notionnel) * taux * 2.0


class ExampleTrendStrategy(Strategy):
    """Exemple de forme uniquement — **à ne pas trader**.

    Cette classe existe pour montrer comment remplir le contrat
    :class:`Strategy` : ce que ``prepare`` doit précalculer, ce que
    ``entry_signal`` doit renvoyer, à quoi ressemble un motif de sortie.

    Les règles employées (croisement de moyennes mobiles, stop à deux ATR)
    sont les plus banales qui soient. Elles n'ont **fait l'objet d'aucun
    backtest**, ne reposent sur **aucune hypothèse de marché vérifiée**, et
    n'ont **rien à voir** avec l'approche ICT / order flow visée sur XAUUSD.
    ``TRADABLE`` reste à ``False`` et l'instanciation journalise un
    avertissement pour que ce code ne se retrouve jamais en production par
    inadvertance.
    """

    TRADABLE = False

    def __init__(
        self,
        risk: RiskConfig | None = None,
        ma_courte: int = 50,
        ma_longue: int = 200,
        multiple_atr: float = 2.0,
    ) -> None:
        """Initialise l'exemple.

        Args:
            risk: paramètres de risque.
            ma_courte: période de la moyenne rapide.
            ma_longue: période de la moyenne lente.
            multiple_atr: distance du stop, en multiples d'ATR.
        """
        super().__init__(risk=risk, name="ExampleTrendStrategy (démonstration)")
        self.ma_courte = ma_courte
        self.ma_longue = ma_longue
        self.multiple_atr = multiple_atr
        _LOG.warning(
            "ExampleTrendStrategy est une démonstration de format. "
            "Ses règles ne sont pas testées et ne doivent pas être tradées."
        )

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ajoute les indicateurs nécessaires à l'exemple.

        Args:
            df: DataFrame OHLCV brut.

        Returns:
            DataFrame enrichi.
        """
        out = indicators.enrich(df)
        out["ex_ma_courte"] = indicators.sma(out["close"], self.ma_courte)
        out["ex_ma_longue"] = indicators.sma(out["close"], self.ma_longue)
        return out

    def entry_signal(
        self, df: pd.DataFrame, i: int, context: dict[str, Any] | None = None
    ) -> Position | None:
        """Illustre la construction d'une :class:`Position`.

        Args:
            df: DataFrame issu de :meth:`prepare`.
            i: position entière de la barre évaluée.
            context: état extérieur, ignoré par cet exemple.

        Returns:
            Une position de démonstration, ou ``None``.
        """
        if i < 1 or i >= len(df):
            return None
        barre = df.iloc[i]
        precedente = df.iloc[i - 1]

        requis = ("ex_ma_courte", "ex_ma_longue", "atr_14", "close")
        if any(pd.isna(barre.get(colonne)) for colonne in requis):
            return None
        if pd.isna(precedente.get("ex_ma_courte")) or pd.isna(precedente.get("ex_ma_longue")):
            return None

        croisement_haussier = (
            precedente["ex_ma_courte"] <= precedente["ex_ma_longue"]
            and barre["ex_ma_courte"] > barre["ex_ma_longue"]
        )
        if not croisement_haussier:
            return None

        entree = float(barre["close"])
        distance = float(barre["atr_14"]) * self.multiple_atr
        if distance <= 0.0 or distance >= entree:
            return None

        return Position(
            signal=Signal.LONG,
            entry=entree,
            stop=entree - distance,
            target=entree + 2.0 * distance,
            reason=(
                f"DÉMONSTRATION — croisement MM{self.ma_courte}/MM{self.ma_longue}, "
                f"stop à {self.multiple_atr} ATR"
            ),
            meta={"tradable": False, "barre": str(df.index[i])},
        )

    def exit_signal(
        self, df: pd.DataFrame, i: int, position: Position
    ) -> tuple[bool, str]:
        """Illustre la forme d'un signal de sortie.

        Args:
            df: DataFrame issu de :meth:`prepare`.
            i: position entière de la barre évaluée.
            position: position ouverte.

        Returns:
            Couple ``(sortir, motif)``.
        """
        if i < 0 or i >= len(df):
            return False, ""
        barre = df.iloc[i]
        cloture = float(barre["close"])

        if cloture <= position.stop:
            return True, "stop touché"
        if position.target is not None and cloture >= position.target:
            return True, "objectif atteint"
        if not pd.isna(barre.get("ex_ma_courte")) and not pd.isna(barre.get("ex_ma_longue")):
            if barre["ex_ma_courte"] < barre["ex_ma_longue"]:
                return True, "croisement inverse"
        return False, ""


def scan_today(
    strategy: Strategy,
    df: pd.DataFrame,
    context: dict[str, Any] | None = None,
) -> Position | None:
    """Applique la stratégie à la dernière barre disponible.

    C'est le point d'entrée du scan quotidien. Il appelle exactement la même
    séquence que le backtest — ``prepare`` puis ``entry_signal`` — de sorte
    que le signal du matin soit celui qui a été testé.

    Args:
        strategy: stratégie à appliquer.
        df: DataFrame OHLCV brut, trié en ordre chronologique croissant.
        context: état extérieur du jour.

    Returns:
        La :class:`Position` proposée pour la dernière barre, ou ``None``.
    """
    if df.empty:
        _LOG.warning("Scan impossible : historique vide.")
        return None
    if not df.index.is_monotonic_increasing:
        raise ValueError("L'historique doit être trié en ordre croissant avant le scan.")
    if not strategy.TRADABLE:
        _LOG.warning(
            "Stratégie « %s » marquée non tradable : résultat à usage de "
            "démonstration uniquement.",
            strategy.name,
        )

    prepare = strategy.prepare(df)
    dernier = len(prepare) - 1

    position = strategy.entry_signal(prepare, dernier, context)
    if position is None:
        _LOG.info("Aucun signal sur la barre du %s.", prepare.index[dernier])
        return None

    _LOG.info(
        "Signal %s sur la barre du %s : %s",
        position.signal.value.upper(),
        prepare.index[dernier],
        position.reason,
    )
    return position
