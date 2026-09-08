"""Point d'entrée du backtest : quatre variantes d'objectif, comparées.

Exécution :
    python -m backtest.run
    python -m backtest.run --debut 2025-03-01 --fin 2025-08-31
    python -m backtest.run --spread 0.80 --slippage 0.50

Écrit dans ``reports/backtest/`` : le journal complet des trades en CSV pour
chaque variante, et une synthèse JSON. Les quatre variantes — objectif
structurel, puis ratios 1:1,5, 1:2 et 1:3 — sont jouées sur exactement les
mêmes données et les mêmes setups, ce qui rend leur comparaison directe.

Ce module mesure une stratégie. Il n'en recommande aucune, et ne conclut pas
à sa place : les répartitions par unité de temps, par type de jambe et par
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

#: Les quatre variantes d'objectif comparées.
VARIANTES: Final[tuple[tuple[str, str, float], ...]] = (
    ("A_structurel", "structurel", 0.0),
    ("B_ratio_1.5", "ratio", 1.5),
    ("B_ratio_2", "ratio", 2.0),
    ("B_ratio_3", "ratio", 3.0),
)

__all__ = ["metriques", "repartir", "executer_variantes", "main"]


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
) -> dict[str, Any]:
    """Joue les quatre variantes d'objectif sur les mêmes données.

    Args:
        m1: bougies d'une minute de XAUUSD.
        eurusd: taux de change quotidiens.
        config_execution: coûts et dimensionnement.
        expiration: fenêtre d'expiration d'un setup, en barres.
        n_tirages: nombre de tirages Monte Carlo.

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
            "par_type_jambe": repartir(backtest.trades, "type_jambe"),
            "par_unite_fvg": repartir(backtest.trades, "unite_fvg"),
            "par_type_entree": repartir(backtest.trades, "type_entree"),
            "par_heure": repartir(backtest.trades, "heure_entree"),
            "abandons": dict(backtest.abandons),
            "n_abandons_total": sum(backtest.abandons.values()),
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


def ecrire_journal(trades: list[moteur.Trade], chemin: Path) -> bool:
    """Écrit le journal complet des trades au format CSV.

    Args:
        trades: trades dénoués.
        chemin: fichier de destination.

    Returns:
        ``True`` si l'écriture a réussi.
    """
    colonnes = [
        "horodatage_entree", "horodatage_sortie", "sens", "unite_ob", "type_jambe",
        "unite_fvg", "type_entree", "prix_entree", "stop", "objectif", "prix_sortie",
        "lots", "resultat_eur", "resultat_r", "motif_sortie", "heure_entree",
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


# ---------------------------------------------------------------------------
# Chargement des données
# ---------------------------------------------------------------------------
def charger_donnees(
    debut: date, fin: date, dossier: Path | None = None
) -> tuple[pd.DataFrame, pd.Series]:
    """Charge XAUUSD et EUR/USD depuis le cache, ou les télécharge.

    Args:
        debut: premier jour inclus.
        fin: dernier jour inclus.
        dossier: dossier des données.

    Returns:
        Couple ``(bougies M1 de l'or, taux EUR/USD quotidiens)``.
    """
    racine = dossier or DOSSIER_DONNEES
    from dataio import dukascopy as dk

    series: dict[str, pd.DataFrame] = {}
    for instrument in ("XAUUSD", "EURUSD"):
        chemin = racine / f"{instrument}_M1.parquet"
        if chemin.exists():
            cadre = pd.read_parquet(chemin)
            _LOG.info("%s lu depuis le cache : %d bougie(s).", instrument, len(cadre))
        else:
            _LOG.info("%s absent du cache : téléchargement.", instrument)
            cadre = dk.charger_m1(instrument, debut, fin)
            if not cadre.empty:
                chemin.parent.mkdir(parents=True, exist_ok=True)
                cadre.to_parquet(chemin)
        series[instrument] = cadre

    or_m1 = series["XAUUSD"]
    if not or_m1.empty:
        masque = (or_m1.index >= pd.Timestamp(debut, tz="UTC")) & (
            or_m1.index <= pd.Timestamp(fin, tz="UTC") + pd.Timedelta(days=1)
        )
        or_m1 = or_m1[masque]

    # Le taux de change n'a besoin que d'une valeur par jour : c'est la
    # granularité à laquelle la stratégie dimensionne ses positions.
    eurusd = series["EURUSD"]
    if eurusd.empty:
        _LOG.error(
            "EUR/USD indisponible : le dimensionnement en euros est impossible et "
            "aucun trade ne sera pris."
        )
        return or_m1, pd.Series(dtype="float64")

    quotidien = eurusd["close"].resample("1D").last().dropna()
    return or_m1, quotidien


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
    analyseur.add_argument("--tirages", type=int, default=propfirm.N_TIRAGES)
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

    m1, eurusd = charger_donnees(debut, fin)
    if m1.empty:
        _LOG.error("Aucune bougie XAUUSD : backtest impossible.")
        return 1

    config_execution = bt_exec.ConfigExecution(
        spread=arguments.spread,
        slippage=arguments.slippage,
        marge_stop=arguments.marge_stop,
    )

    resultats = executer_variantes(
        m1, eurusd, config_execution, arguments.expiration, arguments.tirages
    )

    # Journaux CSV, puis synthèse JSON sans les objets de trade.
    DOSSIER_RAPPORTS.mkdir(parents=True, exist_ok=True)
    for nom, bloc in resultats.items():
        chemin = DOSSIER_RAPPORTS / f"trades_{nom}.csv"
        if ecrire_journal(bloc.pop("_trades"), chemin):
            bloc["chemin_journal"] = str(chemin.relative_to(RACINE))

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
            "choix_interpretation": [dict(c) for c in ict.CHOIX_INTERPRETATION],
        },
        "variantes": resultats,
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

    print(f"\nRésultats écrits dans {chemin_synthese.relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
