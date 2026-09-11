"""Point d'entrée du backtest : cinq variantes d'objectif, comparées.

Exécution :
    python -m backtest.run
    python -m backtest.run --debut 2025-03-01 --fin 2025-08-31
    python -m backtest.run --spread 0.80 --slippage 0.50

Écrit dans ``reports/backtest/`` : le journal complet des trades en CSV pour
chaque variante, et une synthèse JSON. Les cinq variantes — objectif
structurel, ratios 1:1,5, 1:2 et 1:3, puis sortie par paliers sur les zones
de liquidité successives — sont jouées sur exactement les mêmes données et
les mêmes setups, ce qui rend leur comparaison directe.

Ce module mesure une stratégie. Il n'en recommande aucune, et ne conclut pas
à sa place : les répartitions par unité de temps, par unité de FVG et par
heure sont là pour que l'utilisateur voie **où** se trouve l'espérance, y
compris quand la réponse est « nulle part ».
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from backtest import execution as bt_exec
from backtest import ict, moteur, propfirm

_LOG: Final = logging.getLogger("backtest.run")

#: Racine du dépôt.
RACINE: Final = Path(__file__).resolve().parents[1]

#: Dossier des données mises en cache.
DOSSIER_DONNEES: Final = RACINE / "data"

#: Dossier de publication des résultats.
DOSSIER_RAPPORTS: Final = RACINE / "reports" / "backtest"

#: Les cinq variantes d'objectif comparées.
#:
#: ``C_paliers`` sort en plusieurs fois sur les zones de liquidité successives
#: et passe à break-even dès la première atteinte ; les autres sortent d'un
#: seul bloc. Elle partage la population de setups de ``A_structurel`` — les
#: deux abandonnent quand aucun niveau n'est devant le prix —, ce qui rend la
#: comparaison entre sortie unique et sortie échelonnée directement lisible.
VARIANTES: Final[tuple[tuple[str, str, float], ...]] = (
    ("A_structurel", "structurel", 0.0),
    ("B_ratio_1.5", "ratio", 1.5),
    ("B_ratio_2", "ratio", 2.0),
    ("B_ratio_3", "ratio", 3.0),
    ("C_paliers", "paliers", 0.0),
)

#: Les huit variantes d'objectif du setup sweep : sortie complète au 0,72 ;
#: sortie partielle au 0,72 puis niveau structurel, trois répartitions, avec
#: et sans break-even ; structurel seul.
VARIANTES_SWEEP: Final[tuple[tuple[str, str, float, bool], ...]] = (
    ("S1_fibo", moteur.OBJECTIF_SWEEP_FIBO, 1.0, True),
    ("S2_fibo_structurel_50_50_be", moteur.OBJECTIF_SWEEP_FIBO_STRUCTUREL, 0.5, True),
    ("S2_fibo_structurel_50_50_sans_be", moteur.OBJECTIF_SWEEP_FIBO_STRUCTUREL, 0.5, False),
    ("S2_fibo_structurel_33_67_be", moteur.OBJECTIF_SWEEP_FIBO_STRUCTUREL, 0.33, True),
    ("S2_fibo_structurel_33_67_sans_be", moteur.OBJECTIF_SWEEP_FIBO_STRUCTUREL, 0.33, False),
    ("S2_fibo_structurel_67_33_be", moteur.OBJECTIF_SWEEP_FIBO_STRUCTUREL, 0.67, True),
    ("S2_fibo_structurel_67_33_sans_be", moteur.OBJECTIF_SWEEP_FIBO_STRUCTUREL, 0.67, False),
    ("S3_structurel", moteur.OBJECTIF_SWEEP_STRUCTUREL, 1.0, True),
)

#: Unités d'ancrage du Fibonacci et sensibilités de pivot comparées.
UNITES_FIBO: Final[tuple[str, ...]] = ("M15", "M30", "H1")
#: 2 inclus depuis la mesure du 11 septembre 2026 : la tendance observée sur
#: 3, 4 et 5 (plus le pivot est fin, meilleur le résultat) se prolonge à 2,
#: ce qui est précisément le signe qu'il faut la surveiller sur plus d'un
#: mois avant d'y voir un edge — voir la note de README sur le sens
#: structurel d'un pivot à deux bougies.
SENSIBILITES_PIVOT: Final[tuple[int, ...]] = (2, 3, 4, 5)

__all__ = [
    "metriques", "repartir", "executer_variantes", "executer_variantes_sweep",
    "statistiques_paliers", "main", "VARIANTES_SWEEP",
]


# ---------------------------------------------------------------------------
# Métriques
# ---------------------------------------------------------------------------
def metriques(trades: list[moteur.Trade]) -> dict[str, Any]:
    """Calcule les métriques classiques d'une série de trades.

    Le ratio de Sharpe est calculé **par trade**, non annualisé : annualiser
    demanderait une hypothèse sur la fréquence des trades qui ne se déduit pas
    des données, et produirait un chiffre flatteur autant qu'arbitraire.

    Args:
        trades: trades dénoués.

    Returns:
        Dictionnaire de métriques. Les champs restent ``None`` plutôt que
        d'être remplis par des zéros quand ils n'ont pas de sens.
    """
    if not trades:
        return {
            "n_trades": 0,
            "taux_reussite": None,
            "esperance_eur": None,
            "profit_factor": None,
            "drawdown_max_eur": None,
            "sharpe_par_trade": None,
            "resultat_total_eur": 0.0,
            "motif": "aucun trade généré",
        }

    resultats = np.array([t.resultat_eur for t in trades], dtype="float64")
    gains = resultats[resultats > 0]
    pertes = resultats[resultats < 0]

    # Courbe de capital et pire repli, en euros.
    courbe = np.cumsum(resultats)
    sommets = np.maximum.accumulate(np.concatenate([[0.0], courbe]))[1:]
    drawdown = float(np.min(courbe - sommets)) if courbe.size else 0.0

    ecart_type = float(resultats.std(ddof=1)) if resultats.size > 1 else 0.0

    return {
        "n_trades": int(resultats.size),
        "n_gagnants": int(gains.size),
        "n_perdants": int(pertes.size),
        "taux_reussite": float(gains.size / resultats.size),
        "esperance_eur": float(resultats.mean()),
        "gain_moyen_eur": float(gains.mean()) if gains.size else 0.0,
        "perte_moyenne_eur": float(pertes.mean()) if pertes.size else 0.0,
        "profit_factor": (
            float(gains.sum() / abs(pertes.sum())) if pertes.size and pertes.sum() else None
        ),
        "drawdown_max_eur": drawdown,
        "sharpe_par_trade": (
            float(resultats.mean() / ecart_type) if ecart_type > 0 else None
        ),
        "resultat_total_eur": float(resultats.sum()),
        "resultat_moyen_r": float(np.mean([t.resultat_r for t in trades])),
        "motif": "",
    }


def repartir(trades: list[moteur.Trade], cle: str) -> dict[str, Any]:
    """Ventile les trades selon un attribut, avec leurs métriques.

    C'est la sortie qui dit **où** se trouve l'espérance : une stratégie dont
    le résultat global est positif peut ne devoir ce résultat qu'à une seule
    unité de temps, le reste étant à perte. La moyenne le cacherait.

    Args:
        trades: trades dénoués.
        cle: attribut de ventilation, par exemple ``unite_ob``.

    Returns:
        Métriques par valeur de l'attribut.
    """
    groupes: dict[Any, list[moteur.Trade]] = {}
    for trade in trades:
        groupes.setdefault(getattr(trade, cle), []).append(trade)
    return {
        str(valeur): metriques(sous_ensemble)
        for valeur, sous_ensemble in sorted(groupes.items(), key=lambda kv: str(kv[0]))
    }


# ---------------------------------------------------------------------------
# Exécution des variantes
# ---------------------------------------------------------------------------
def executer_variantes(
    m1: pd.DataFrame,
    eurusd: pd.Series,
    config_execution: bt_exec.ConfigExecution,
    expiration: int,
    n_tirages: int = propfirm.N_TIRAGES,
    sensibilite_swing: int = ict.SENSIBILITE_SWING,
) -> dict[str, Any]:
    """Joue les cinq variantes d'objectif sur les mêmes données.

    Args:
        m1: bougies d'une minute de XAUUSD.
        eurusd: taux de change quotidiens.
        config_execution: coûts et dimensionnement.
        expiration: fenêtre d'expiration d'un setup, en barres.
        n_tirages: nombre de tirages Monte Carlo.
        sensibilite_swing: bougies exigées de chaque côté d'un retournement.

    Returns:
        Résultats par variante, avec métriques, répartitions et survie.
    """
    resultats: dict[str, Any] = {}

    for nom, mode, ratio in VARIANTES:
        _LOG.info("Variante %s...", nom)
        config = moteur.ConfigBacktest(
            execution=config_execution,
            expiration_barres=expiration,
            mode_tp=mode,
            ratio_tp=ratio,
            sensibilite_swing=sensibilite_swing,
        )
        backtest = moteur.Backtest(m1, eurusd, config)
        backtest.executer()

        gains = [t.resultat_eur for t in backtest.trades]
        jours = [pd.Timestamp(t.horodatage_entree).date() for t in backtest.trades]

        resultats[nom] = {
            "mode_tp": mode,
            "ratio_tp": ratio if mode == "ratio" else None,
            "metriques": metriques(backtest.trades),
            "par_unite_ob": repartir(backtest.trades, "unite_ob"),
            "par_unite_fvg": repartir(backtest.trades, "unite_fvg"),
            "par_type_entree": repartir(backtest.trades, "type_entree"),
            "par_heure": repartir(backtest.trades, "heure_entree"),
            "abandons": dict(backtest.abandons),
            "n_abandons_total": sum(backtest.abandons.values()),
            "touches_simultanees": {
                "n": backtest.touches_simultanees,
                "n_unites_distinctes": sum(
                    1 for d in backtest.detail_touches_simultanees
                    if d["unites_distinctes"]
                ),
                "part_des_setups": (
                    backtest.touches_simultanees
                    / max(len(backtest.trades) + sum(backtest.abandons.values()), 1)
                ),
                "detail": backtest.detail_touches_simultanees[:20],
                "convention": (
                    "La stratégie ne dit pas quelle zone prime quand plusieurs sont "
                    "touchées dans la même minute. Le moteur retient la première "
                    "confirmée ; ce compteur dit si le cas mérite une règle."
                ),
            },
            "propfirm": (
                propfirm.monte_carlo(gains, n_tirages=n_tirages)
                if gains
                else {"disponible": False, "motif": "aucun trade à simuler"}
            ),
            "chemin_journal": "",
            "_trades": backtest.trades,
        }
        # Une séquence chronologique réelle, à côté de la distribution.
        if gains:
            resultats[nom]["propfirm_chemin_reel"] = propfirm.simuler_compte(gains, jours)

    return resultats


def _bloc_resultats(backtest: moteur.Backtest, n_tirages: int) -> dict[str, Any]:
    """Métriques, ventilations et survie d'une exécution du moteur."""
    gains = [t.resultat_eur for t in backtest.trades]
    jours = [pd.Timestamp(t.horodatage_entree).date() for t in backtest.trades]
    bloc = {
        "metriques": metriques(backtest.trades),
        "par_setup": repartir(backtest.trades, "setup"),
        "par_unite_detection": repartir(backtest.trades, "unite_detection"),
        "par_unite_fvg": repartir(backtest.trades, "unite_fvg"),
        "par_heure": repartir(backtest.trades, "heure_entree"),
        "abandons": dict(backtest.abandons),
        "n_abandons_total": sum(backtest.abandons.values()),
        "n_sweeps_confirmes": backtest.sweeps_confirmes,
        "n_niveaux_detectes": len(backtest.niveaux),
        "interferences": dict(backtest.interferences),
        "touches_simultanees": backtest.touches_simultanees,
        "propfirm": (
            propfirm.monte_carlo(gains, n_tirages=n_tirages)
            if gains and n_tirages else {"disponible": False, "motif": "aucun trade à simuler" if not gains else "tirages désactivés"}
        ),
        "_trades": backtest.trades,
    }
    if gains:
        bloc["propfirm_chemin_reel"] = propfirm.simuler_compte(gains, jours)
    return bloc


def executer_variantes_sweep(
    m1: pd.DataFrame,
    eurusd: pd.Series,
    config_execution: bt_exec.ConfigExecution,
    expiration: int,
    n_tirages: int = propfirm.N_TIRAGES,
    sensibilite_swing: int = ict.SENSIBILITE_SWING,
    unites_fibo: tuple[str, ...] = UNITES_FIBO,
    sensibilites_pivot: tuple[int, ...] = SENSIBILITES_PIVOT,
    sensibilite_pivot_defaut: int = ict.SENSIBILITE_PIVOT,
    unite_fibo_defaut: str = "M15",
    avec_ensemble: bool = True,
) -> dict[str, Any]:
    """Joue la matrice du setup sweep : variantes × ancrage Fibonacci × pivot.

    Trois blocs, pour répondre aux trois questions posées :

    * ``par_fibo`` — les huit variantes, pour chaque unité d'ancrage du
      Fibonacci, à la sensibilité de pivot par défaut : laquelle porte
      l'edge ? (La variante structurelle seule ne dépend pas de l'ancrage :
      elle n'est jouée qu'une fois, sous la première unité.)
    * ``par_sensibilite_pivot`` — les huit variantes à l'ancrage par défaut,
      pour chaque sensibilité de pivot : le paramètre pilote-t-il la
      performance ou seulement la fréquence ?
    * ``ensemble`` — sweep et order block joués ensemble (order block en
      objectif structurel, le mieux placé des tests précédents), ventilés
      par setup : lequel des deux porte réellement l'edge quand ils se
      partagent la même position ?

    Args:
        m1: bougies d'une minute.
        eurusd: taux de change quotidiens.
        config_execution: coûts et dimensionnement.
        expiration: fenêtre d'expiration d'un setup, en barres.
        n_tirages: tirages Monte Carlo (0 pour les blocs de comparaison).
        sensibilite_swing: bougies de chaque côté d'un retournement de jambe.
        unites_fibo: unités d'ancrage comparées.
        sensibilites_pivot: sensibilités de pivot comparées.
        sensibilite_pivot_defaut: sensibilité retenue pour ``par_fibo`` et ``ensemble``.
        unite_fibo_defaut: ancrage retenu pour ``par_sensibilite_pivot`` et ``ensemble``.
        avec_ensemble: ``False`` pour ne pas rejouer les deux setups ensemble.

    Returns:
        ``{"par_fibo", "par_sensibilite_pivot", "ensemble"}``, chaque feuille
        étant un bloc de :func:`_bloc_resultats` (``_trades`` compris, à
        retirer avant sérialisation).
    """
    def _jouer(nom: str, mode: str, fraction: float, break_even: bool, unite_fibo: str,
               sensibilite_pivot: int, setups: tuple[str, ...], tirages: int) -> dict[str, Any]:
        _LOG.info("Sweep %s (fibo %s, pivot %d, setups %s)...", nom, unite_fibo, sensibilite_pivot, "+".join(setups))
        config = moteur.ConfigBacktest(
            execution=config_execution,
            expiration_barres=expiration,
            mode_tp="structurel",
            sensibilite_swing=sensibilite_swing,
            setups=setups,
            sensibilite_pivot=sensibilite_pivot,
            objectif_sweep=mode,
            unite_fibo=unite_fibo,
            fraction_fibo=fraction,
            break_even_sweep=break_even,
        )
        backtest = moteur.Backtest(m1, eurusd, config)
        backtest.executer()
        bloc = _bloc_resultats(backtest, tirages)
        bloc.update({"objectif_sweep": mode, "fraction_fibo": fraction, "break_even": break_even,
                     "unite_fibo": unite_fibo, "sensibilite_pivot": sensibilite_pivot, "setups": list(setups)})
        return bloc

    par_fibo: dict[str, Any] = {}
    for unite in unites_fibo:
        par_fibo[unite] = {}
        for nom, mode, fraction, be in VARIANTES_SWEEP:
            if mode == moteur.OBJECTIF_SWEEP_STRUCTUREL and unite != unites_fibo[0]:
                continue
            par_fibo[unite][nom] = _jouer(
                nom, mode, fraction, be, unite, sensibilite_pivot_defaut, (moteur.SETUP_SWEEP,),
                n_tirages if unite == unite_fibo_defaut else 0,
            )

    par_pivot: dict[str, Any] = {}
    for k in sensibilites_pivot:
        par_pivot[str(k)] = {}
        for nom, mode, fraction, be in VARIANTES_SWEEP:
            if k == sensibilite_pivot_defaut and unite_fibo_defaut in par_fibo and nom in par_fibo[unite_fibo_defaut]:
                bloc = par_fibo[unite_fibo_defaut][nom]      # déjà joué, même configuration
            elif k == sensibilite_pivot_defaut and mode == moteur.OBJECTIF_SWEEP_STRUCTUREL:
                bloc = par_fibo[unites_fibo[0]][nom]
            else:
                bloc = _jouer(nom, mode, fraction, be, unite_fibo_defaut, k, (moteur.SETUP_SWEEP,), 0)
            par_pivot[str(k)][nom] = {
                "metriques": bloc["metriques"], "n_abandons_total": bloc["n_abandons_total"],
                "n_sweeps_confirmes": bloc["n_sweeps_confirmes"], "n_niveaux_detectes": bloc["n_niveaux_detectes"],
                "par_unite_detection": {u: v["n_trades"] for u, v in bloc["par_unite_detection"].items()},
            }

    ensemble: dict[str, Any] = {}
    if avec_ensemble:
        for nom, mode, fraction, be in VARIANTES_SWEEP:
            ensemble[nom] = _jouer(
                nom, mode, fraction, be, unite_fibo_defaut, sensibilite_pivot_defaut,
                (moteur.SETUP_ORDER_BLOCK, moteur.SETUP_SWEEP), 0,
            )

    return {"par_fibo": par_fibo, "par_sensibilite_pivot": par_pivot, "ensemble": ensemble}


def ecrire_journal(trades: list[moteur.Trade], chemin: Path) -> bool:
    """Écrit le journal complet des trades au format CSV.

    Args:
        trades: trades dénoués.
        chemin: fichier de destination.

    Returns:
        ``True`` si l'écriture a réussi.
    """
    colonnes = [
        "setup", "unite_detection",
        "horodatage_entree", "horodatage_sortie", "sens", "unite_ob",
        "ob_haut", "ob_bas", "ob_ouverture_bougie1",
        "niveau_prix", "niveau_cote", "niveau_unite", "niveau_formation", "sweep_extreme",
        "unite_fibo", "reference_prix",
        "unite_fvg", "fvg_haut", "fvg_bas",
        "type_entree", "prix_entree", "stop", "objectif", "prix_sortie",
        "lots", "resultat_eur", "resultat_r", "motif_sortie", "heure_entree",
        "n_paliers",
    ]
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        with chemin.open("w", encoding="utf-8", newline="") as flux:
            redacteur = csv.DictWriter(flux, fieldnames=colonnes)
            redacteur.writeheader()
            for trade in trades:
                redacteur.writerow(trade.to_dict())
    except OSError as exc:
        _LOG.error("Journal non écrit (%s) : %s", chemin, exc)
        return False
    return True


def statistiques_paliers(trades: list[moteur.Trade]) -> dict[str, Any]:
    """Résume le comportement des tranches d'une variante à paliers.

    Répond aux deux questions que la sortie échelonnée pose et que le total
    d'un trade ne montre pas : chaque zone est-elle réellement atteinte, et
    combien de fois le solde finit-il par sortir à break-even plutôt que sur
    une zone.

    Args:
        trades: trades dénoués.

    Returns:
        Statistiques par rang de zone, et décompte des motifs de sortie.
        ``{"disponible": False, ...}`` si aucun trade ne porte de tranche.
    """
    paliers = [p for trade in trades for p in trade.paliers]
    if not paliers:
        return {"disponible": False, "motif": "aucune tranche : aucun trade à paliers"}

    par_rang: dict[str, Any] = {}
    for rang in sorted({p.rang for p in paliers}):
        lot = [p for p in paliers if p.rang == rang]
        atteints = [p for p in lot if p.motif_sortie == moteur.SORTIE_OBJECTIF]
        resolus = [p for p in lot if p.resultat_r is not None]
        par_rang[str(rang)] = {
            "n_tranches": len(lot),
            "n_zone_atteinte": len(atteints),
            "part_zone_atteinte": len(atteints) / len(lot),
            "fraction_moyenne": sum(p.fraction for p in lot) / len(lot),
            "ratio_risque_moyen": sum(p.ratio_risque for p in lot) / len(lot),
            "resultat_r_moyen": (
                sum(p.resultat_r for p in resolus) / len(resolus) if resolus else None
            ),
        }

    motifs: dict[str, int] = {}
    for palier in paliers:
        motifs[palier.motif_sortie or "non_denoue"] = (
            motifs.get(palier.motif_sortie or "non_denoue", 0) + 1
        )

    n_zones = [len(t.paliers) for t in trades if t.paliers]
    return {
        "disponible": True,
        "n_trades_a_paliers": len(n_zones),
        "n_zones_moyen": sum(n_zones) / len(n_zones),
        "repartition_n_zones": {
            str(n): n_zones.count(n) for n in sorted(set(n_zones))
        },
        "par_rang": par_rang,
        "motifs_de_sortie": motifs,
    }


def ecrire_journal_paliers(trades: list[moteur.Trade], chemin: Path) -> bool:
    """Écrit le détail des tranches de sortie, une ligne par tranche.

    Sans ce détail, un trade à sorties échelonnées ne serait qu'un total
    opaque : on ne saurait pas si le résultat vient de la première zone ou
    des suivantes, ni combien de fois le solde est finalement sorti à
    break-even.

    Args:
        trades: trades dénoués de la variante à paliers.
        chemin: fichier de destination.

    Returns:
        ``True`` si l'écriture a réussi, ``False`` si aucune tranche n'existe
        ou si le disque refuse.
    """
    lignes = [ligne for trade in trades for ligne in trade.lignes_paliers()]
    if not lignes:
        return False
    colonnes = [
        "horodatage_entree", "sens", "rang", "zone", "origine", "fraction",
        "ratio_risque", "prix_sortie", "horodatage_sortie", "resultat_r", "motif_sortie",
    ]
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        with chemin.open("w", encoding="utf-8", newline="") as flux:
            redacteur = csv.DictWriter(flux, fieldnames=colonnes)
            redacteur.writeheader()
            redacteur.writerows(lignes)
    except OSError as exc:
        _LOG.error("Journal des paliers non écrit (%s) : %s", chemin, exc)
        return False
    return True


# ---------------------------------------------------------------------------
# Chargement des données
# ---------------------------------------------------------------------------
def charger_donnees(
    debut: date,
    fin: date,
    dossier: Path | None = None,
    hors_ligne: bool = False,
) -> tuple[pd.DataFrame, pd.Series]:
    """Charge XAUUSD et EUR/USD, depuis le cache de ticks ou par téléchargement.

    Le cache de ticks fait office de source : il n'y a pas de fichier
    intermédiaire à tenir à jour, donc pas de risque qu'il diverge des ticks
    dont il est issu. La reconstruction du M1 coûte quelques secondes, ce qui
    est sans commune mesure avec le téléchargement.

    Args:
        debut: premier jour inclus.
        fin: dernier jour inclus.
        dossier: dossier des données.
        hors_ligne: ``True`` pour se limiter à ce que le cache contient.

    Returns:
        Couple ``(bougies M1 de l'or, taux EUR/USD quotidiens)``.
    """
    racine = dossier or DOSSIER_DONNEES
    from dataio import dukascopy as dk

    or_m1 = dk.charger_m1_depuis_cache("XAUUSD", racine)
    # Le cache global peut être non vide (une autre période déjà téléchargée)
    # sans pour autant couvrir la fenêtre demandée : ne regarder que la
    # vacuité globale laisserait une nouvelle période silencieusement sans
    # données. Les deux bornes doivent être couvertes, pas seulement l'une
    # des deux.
    couvre_debut = not or_m1.empty and or_m1.index.min() <= pd.Timestamp(debut, tz="UTC")
    couvre_fin = not or_m1.empty and or_m1.index.max() >= pd.Timestamp(fin, tz="UTC")
    if not (couvre_debut and couvre_fin) and not hors_ligne:
        _LOG.info(
            "Cache XAUUSD ne couvre pas %s → %s : téléchargement (le cache déjà "
            "présent pour d'autres périodes n'est ni supprimé ni retéléchargé).",
            debut, fin,
        )
        dk.charger_m1("XAUUSD", debut, fin, racine)
        # Recombiné avec tout le cache, y compris les autres périodes déjà
        # présentes : c'est le même fichier de cache qui sert de source pour
        # toutes les fenêtres, jamais réécrit ni tronqué.
        or_m1 = dk.charger_m1_depuis_cache("XAUUSD", racine)

    if not or_m1.empty:
        masque = (or_m1.index >= pd.Timestamp(debut, tz="UTC")) & (
            or_m1.index <= pd.Timestamp(fin, tz="UTC") + pd.Timedelta(days=1)
        )
        or_m1 = or_m1[masque]

    # Le taux de change ne sert qu'au dimensionnement, qui se fait à la
    # journée : un taux par jour suffit, et n'en demander qu'un par jour
    # divise par vingt-quatre le nombre de requêtes.
    if hors_ligne:
        brut = dk.charger_m1_depuis_cache("EURUSD", racine)
        eurusd = (
            brut["close"].resample("1D").last().dropna()
            if not brut.empty
            else pd.Series(dtype="float64")
        )
    else:
        eurusd = dk.charger_taux_quotidien("EURUSD", debut, fin, dossier_cache=racine)

    if eurusd.empty:
        _LOG.error(
            "EUR/USD indisponible : le dimensionnement en euros est impossible et "
            "aucun trade ne sera pris."
        )
    return or_m1, eurusd


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """Exécute le backtest complet et publie les résultats.

    Returns:
        0 si le backtest a produit une sortie, 1 sinon.
    """
    analyseur = argparse.ArgumentParser(description="Backtest ICT sur XAUUSD.")
    analyseur.add_argument("--debut", default="2025-03-01", help="premier jour, AAAA-MM-JJ.")
    analyseur.add_argument("--fin", default="2025-08-31", help="dernier jour, AAAA-MM-JJ.")
    analyseur.add_argument("--spread", type=float, default=bt_exec.SPREAD_DEFAUT)
    analyseur.add_argument("--slippage", type=float, default=bt_exec.SLIPPAGE_DEFAUT)
    analyseur.add_argument("--marge-stop", type=float, default=bt_exec.MARGE_STOP_DEFAUT)
    analyseur.add_argument("--expiration", type=int, default=moteur.EXPIRATION_DEFAUT)
    analyseur.add_argument(
        "--sensibilite-swing",
        type=int,
        default=ict.SENSIBILITE_SWING,
        help="bougies exigées de chaque côté pour valider un retournement.",
    )
    analyseur.add_argument(
        "--comparer-sensibilite",
        action="store_true",
        help="rejoue le backtest pour les sensibilités 3, 4 et 5 et les compare.",
    )
    analyseur.add_argument(
        "--sweep",
        action="store_true",
        help="joue aussi la matrice du setup sweep (variantes × ancrage Fibonacci × pivot, puis avec l'order block).",
    )
    analyseur.add_argument(
        "--sensibilite-pivot",
        type=int,
        default=ict.SENSIBILITE_PIVOT,
        help="bougies exigées de chaque côté d'un pivot (niveaux de liquidité du sweep).",
    )
    analyseur.add_argument("--tirages", type=int, default=propfirm.N_TIRAGES)
    analyseur.add_argument(
        "--hors-ligne",
        action="store_true",
        help="n'utilise que le cache de ticks, sans aucun téléchargement.",
    )
    analyseur.add_argument("--verbeux", action="store_true")
    arguments = analyseur.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if arguments.verbeux else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s : %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        debut = datetime.strptime(arguments.debut, "%Y-%m-%d").date()
        fin = datetime.strptime(arguments.fin, "%Y-%m-%d").date()
    except ValueError:
        _LOG.error("Dates invalides : format attendu AAAA-MM-JJ.")
        return 1

    m1, eurusd = charger_donnees(debut, fin, hors_ligne=arguments.hors_ligne)
    if m1.empty:
        _LOG.error("Aucune bougie XAUUSD : backtest impossible.")
        return 1

    config_execution = bt_exec.ConfigExecution(
        spread=arguments.spread,
        slippage=arguments.slippage,
        marge_stop=arguments.marge_stop,
    )

    resultats = executer_variantes(
        m1, eurusd, config_execution, arguments.expiration, arguments.tirages,
        sensibilite_swing=arguments.sensibilite_swing,
    )

    # Sensibilité du découpage de la jambe : le paramètre décide de la
    # fenêtre où le FVG est cherché. Savoir s'il change matériellement les
    # résultats compte autant que les résultats eux-mêmes.
    comparaison_sensibilite: dict[str, Any] = {}
    if arguments.comparer_sensibilite:
        for valeur in (3, 4, 5):
            _LOG.info("Sensibilité de swing %d...", valeur)
            bloc = executer_variantes(
                m1, eurusd, config_execution, arguments.expiration,
                n_tirages=0, sensibilite_swing=valeur,
            )
            comparaison_sensibilite[str(valeur)] = {
                nom: {
                    "metriques": sous["metriques"],
                    "n_abandons_total": sous["n_abandons_total"],
                }
                for nom, sous in bloc.items()
            }
            for sous in bloc.values():
                sous.pop("_trades", None)

    # Matrice du setup sweep : jouée à part, publiée dans sa propre synthèse,
    # et ses variantes par défaut (ancrage M15, pivot par défaut) reprises
    # dans la synthèse principale pour que le site les compare au scanner.
    sweep: dict[str, Any] = {}
    if arguments.sweep:
        sweep = executer_variantes_sweep(
            m1, eurusd, config_execution, arguments.expiration, arguments.tirages,
            sensibilite_swing=arguments.sensibilite_swing,
            sensibilite_pivot_defaut=arguments.sensibilite_pivot,
        )

    # Journaux CSV, puis synthèse JSON sans les objets de trade.
    DOSSIER_RAPPORTS.mkdir(parents=True, exist_ok=True)
    if sweep:
        for unite, blocs in sweep["par_fibo"].items():
            for nom, bloc in blocs.items():
                trades_variante = bloc.pop("_trades")
                if unite == "M15":
                    chemin = DOSSIER_RAPPORTS / f"trades_sweep_{nom}.csv"
                    if ecrire_journal(trades_variante, chemin):
                        bloc["chemin_journal"] = str(chemin.relative_to(RACINE))
                    chemin_paliers = DOSSIER_RAPPORTS / f"paliers_sweep_{nom}.csv"
                    if ecrire_journal_paliers(trades_variante, chemin_paliers):
                        bloc["chemin_journal_paliers"] = str(chemin_paliers.relative_to(RACINE))
                        bloc["paliers"] = statistiques_paliers(trades_variante)
                    resultats[f"sweep_{nom}"] = dict(bloc)
        for nom, bloc in sweep["ensemble"].items():
            trades_variante = bloc.pop("_trades")
            chemin = DOSSIER_RAPPORTS / f"trades_ensemble_{nom}.csv"
            if ecrire_journal(trades_variante, chemin):
                bloc["chemin_journal"] = str(chemin.relative_to(RACINE))
        chemin_sweep = DOSSIER_RAPPORTS / "sweep_synthese.json"
        chemin_sweep.write_text(
            json.dumps({
                "meta": {"horodatage_utc": pd.Timestamp.now(tz="UTC").isoformat(),
                         "sensibilite_pivot_defaut": arguments.sensibilite_pivot, "unite_fibo_defaut": "M15",
                         "variantes": [v[0] for v in VARIANTES_SWEEP]},
                **sweep,
            }, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    for nom, bloc in resultats.items():
        if "_trades" not in bloc:
            continue
        chemin = DOSSIER_RAPPORTS / f"trades_{nom}.csv"
        trades_variante = bloc.pop("_trades")
        if ecrire_journal(trades_variante, chemin):
            bloc["chemin_journal"] = str(chemin.relative_to(RACINE))
        chemin_paliers = DOSSIER_RAPPORTS / f"paliers_{nom}.csv"
        if ecrire_journal_paliers(trades_variante, chemin_paliers):
            bloc["chemin_journal_paliers"] = str(chemin_paliers.relative_to(RACINE))
            bloc["paliers"] = statistiques_paliers(trades_variante)

    synthese = {
        "meta": {
            "horodatage_utc": pd.Timestamp.now(tz="UTC").isoformat(),
            "periode_demandee": {"debut": str(debut), "fin": str(fin)},
            "periode_couverte": {
                "debut": str(m1.index.min()),
                "fin": str(m1.index.max()),
                "n_bougies_m1": int(len(m1)),
                "n_jours": int(m1.index.normalize().nunique()),
            },
            "instrument": "XAUUSD",
            "source": "Dukascopy, ticks agrégés en M1 puis en unités supérieures",
            "couts": {
                "spread_usd": arguments.spread,
                "slippage_usd": arguments.slippage,
                "source_spread": (
                    "écart médian mesuré sur les ticks Dukascopy d'une journée "
                    "complète de juin 2025 : 0,619 $"
                ),
            },
            "marge_stop_usd": arguments.marge_stop,
            "expiration_barres": arguments.expiration,
            "filtre_fondamental": (
                "volontairement absent : le backtest mesure la version mécanique pure"
            ),
            "sensibilite_swing": arguments.sensibilite_swing,
            "sensibilite_pivot": arguments.sensibilite_pivot,
            "setup_sweep_joue": bool(sweep),
            "choix_interpretation": [dict(c) for c in ict.CHOIX_INTERPRETATION],
        },
        "variantes": resultats,
        "comparaison_sensibilite_swing": comparaison_sensibilite,
    }

    chemin_synthese = DOSSIER_RAPPORTS / "synthese.json"
    chemin_synthese.write_text(
        json.dumps(synthese, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    # --- Affichage ---------------------------------------------------------
    meta = synthese["meta"]["periode_couverte"]
    print(f"\nPériode couverte : {meta['debut'][:10]} → {meta['fin'][:10]} "
          f"({meta['n_bougies_m1']:,} bougies M1, {meta['n_jours']} jours)".replace(",", " "))
    print(f"Coûts : spread {arguments.spread:.2f} $, slippage {arguments.slippage:.2f} $\n")

    entete = f"{'Variante':<16}{'Trades':>8}{'Réussite':>10}{'Espér.€':>10}{'PF':>8}{'DD max €':>11}{'Sharpe':>9}{'Total €':>11}"
    print(entete)
    print("-" * len(entete))
    for nom, bloc in resultats.items():
        m = bloc["metriques"]
        if not m["n_trades"]:
            print(f"{nom:<16}{'0':>8}   aucun trade")
            continue
        pf = f"{m['profit_factor']:.2f}" if m["profit_factor"] else "—"
        sh = f"{m['sharpe_par_trade']:.3f}" if m["sharpe_par_trade"] else "—"
        print(
            f"{nom:<16}{m['n_trades']:>8}{m['taux_reussite']:>9.1%}"
            f"{m['esperance_eur']:>10.2f}{pf:>8}{m['drawdown_max_eur']:>11.2f}"
            f"{sh:>9}{m['resultat_total_eur']:>11.2f}"
        )

    if sweep:
        print("\nSetup sweep — ancrage Fibonacci M15, sensibilité de pivot "
              f"{arguments.sensibilite_pivot} (ventilation complète dans reports/backtest/sweep_synthese.json)")
        print(entete)
        print("-" * len(entete))
        for nom, bloc in sweep["par_fibo"].get("M15", {}).items():
            m = bloc["metriques"]
            if not m["n_trades"]:
                print(f"{nom:<16}{'0':>8}   aucun trade")
                continue
            pf = f"{m['profit_factor']:.2f}" if m["profit_factor"] else "—"
            sh = f"{m['sharpe_par_trade']:.3f}" if m["sharpe_par_trade"] else "—"
            print(
                f"{nom[:16]:<16}{m['n_trades']:>8}{m['taux_reussite']:>9.1%}"
                f"{m['esperance_eur']:>10.2f}{pf:>8}{m['drawdown_max_eur']:>11.2f}"
                f"{sh:>9}{m['resultat_total_eur']:>11.2f}"
            )

    print(f"\nRésultats écrits dans {chemin_synthese.relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
