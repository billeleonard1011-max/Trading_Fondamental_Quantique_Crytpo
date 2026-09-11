"""Génère les cas partagés entre les tests Python et le portage JavaScript.

Chaque fixture porte les entrées et les sorties calculées par le code Python
*déjà testé* du backtest. Le test JS (tests/parite.test.js) relit ces mêmes
fixtures, appelle les fonctions JavaScript avec les mêmes entrées, et vérifie
l'identité des résultats — c'est la preuve que le portage ne trahit pas la
logique d'origine, pas une simple relecture du code.

Exécution, depuis la racine du dépôt :
    .venv/bin/python worker-scanner/tests/fixtures/generer.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RACINE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RACINE))

from backtest import data as bt_data  # noqa: E402
from backtest import execution as bt_exec  # noqa: E402
from backtest import ict  # noqa: E402
from backtest import moteur  # noqa: E402

DOSSIER = Path(__file__).resolve().parent


def _bougies(lignes: list[tuple[float, float, float, float]], debut_ms: int = 0, pas_ms: int = 3_600_000) -> pd.DataFrame:
    """Construit un cadre OHLC à partir de quadruplets (open, high, low, close)."""
    index = pd.to_datetime([debut_ms + i * pas_ms for i in range(len(lignes))], unit="ms", utc=True)
    return pd.DataFrame(
        {
            "open": [l[0] for l in lignes],
            "high": [l[1] for l in lignes],
            "low": [l[2] for l in lignes],
            "close": [l[3] for l in lignes],
            "volume": [1.0] * len(lignes),
        },
        index=index,
    )


def _bougies_vers_json(cadre: pd.DataFrame) -> list[dict]:
    """Sérialise un cadre OHLC au format attendu par le JS (t en ms, champs français)."""
    return [
        {
            "t": int(idx.value // 1_000_000),
            "ouverture": float(r["open"]),
            "haut": float(r["high"]),
            "bas": float(r["low"]),
            "cloture": float(r["close"]),
            "volume": float(r["volume"]),
        }
        for idx, r in cadre.iterrows()
    ]


def _serie_m1(n: int = 6000, graine: int = 20260908, debut: str = "2026-01-05 00:00") -> pd.DataFrame:
    """Reprend exactement le générateur de tests/test_backtest_engine.py::_serie_m1."""
    alea = np.random.default_rng(graine)
    index = pd.date_range(debut, periods=n, freq="min", tz="UTC")
    pas = alea.normal(0.0, 0.35, n)
    cloture = 3000.0 + np.cumsum(pas)
    ouverture = np.concatenate([[3000.0], cloture[:-1]])
    amplitude = np.abs(alea.normal(0.0, 0.25, n))
    haut = np.maximum(ouverture, cloture) + amplitude
    bas = np.minimum(ouverture, cloture) - amplitude
    return pd.DataFrame(
        {"open": ouverture, "high": haut, "low": bas, "close": cloture, "volume": 1.0}, index=index
    )


def _taux_eurusd(m1: pd.DataFrame) -> pd.Series:
    """Reprend exactement tests/test_backtest_engine.py::_taux_eurusd."""
    jours = pd.date_range(
        m1.index.min().normalize(), m1.index.max().normalize(), freq="D", tz="UTC"
    )
    return pd.Series(np.linspace(1.07, 1.10, len(jours)), index=jours)


def ecrire(nom: str, donnees: dict) -> None:
    chemin = DOSSIER / f"{nom}.json"
    chemin.write_text(json.dumps(donnees, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"écrit : {chemin.relative_to(RACINE)}")


# ---------------------------------------------------------------------------
# 1. Agrégation
# ---------------------------------------------------------------------------
def fixture_agregation() -> None:
    m1 = _serie_m1(400, graine=1)
    cas = []
    for unite in ("M3", "M5", "M15", "M30", "H1"):
        cadre = bt_data.agreger(m1, unite)
        cas.append({"unite": unite, "attendu": _bougies_vers_json(cadre)})
    ecrire("agregation", {"m1": _bougies_vers_json(m1), "cas": cas})


def fixture_fenetre_close() -> None:
    m1 = _serie_m1(200, graine=2)
    h1 = bt_data.agreger(m1, "H1")
    instants = [int(m1.index[k].value // 1_000_000) for k in (0, 59, 60, 119, 120, 199)]
    cas = []
    for instant in instants:
        visible = bt_data.fenetre_close(h1, "H1", pd.Timestamp(instant, unit="ms", tz="UTC"))
        cas.append({"instantMs": instant, "attendu": _bougies_vers_json(visible)})
    ecrire("fenetre_close", {"h1": _bougies_vers_json(h1), "cas": cas})


# ---------------------------------------------------------------------------
# 2. Order blocks
# ---------------------------------------------------------------------------
def fixture_order_blocks() -> None:
    cas = []

    # Cas 1 : OB baissier valide (bougie 1 haussière).
    lignes = [(100.0, 105.0, 99.0, 104.0), (104.0, 104.5, 90.0, 91.0), (91.0, 92.0, 88.0, 89.0)]
    cadre = _bougies(lignes)
    cas.append({"nom": "ob_baissier_valide", "cadre": _bougies_vers_json(cadre),
                "attendu": _ob_vers_json(ict.detecter_order_blocks(cadre, "H1"))})

    # Cas 2 : OB haussier valide (symétrique).
    lignes = [(104.0, 105.0, 99.0, 100.0), (100.0, 112.0, 99.5, 111.0), (111.0, 113.0, 110.5, 112.0)]
    cadre = _bougies(lignes)
    cas.append({"nom": "ob_haussier_valide", "cadre": _bougies_vers_json(cadre),
                "attendu": _ob_vers_json(ict.detecter_order_blocks(cadre, "H1"))})

    # Cas 3 : rejeté, bougie 3 touche exactement l'extrême.
    lignes = [(100.0, 105.0, 99.0, 104.0), (104.0, 104.5, 90.0, 91.0), (91.0, 99.0, 88.0, 89.0)]
    cadre = _bougies(lignes)
    cas.append({"nom": "ob_rejete_bougie3_touche", "cadre": _bougies_vers_json(cadre),
                "attendu": _ob_vers_json(ict.detecter_order_blocks(cadre, "H1"))})

    # Cas 4 : série réaliste, plusieurs OB.
    m1 = _serie_m1(2000, graine=3)
    h1 = bt_data.agreger(m1, "H1")
    cas.append({"nom": "serie_realiste_h1", "cadre": _bougies_vers_json(h1),
                "attendu": _ob_vers_json(ict.detecter_order_blocks(h1, "H1"))})

    ecrire("order_blocks", {"cas": cas})


def _ob_vers_json(zones: list) -> list[dict]:
    return [
        {
            "unite": z.unite, "sens": z.sens, "haut": z.haut, "bas": z.bas,
            "ouvertureBougie1": int(pd.Timestamp(z.ouverture_bougie1).value // 1_000_000),
            "finMotif": int(pd.Timestamp(z.fin_motif).value // 1_000_000),
            "mecheBougie2": z.meche_bougie2,
        }
        for z in zones
    ]


# ---------------------------------------------------------------------------
# 3. Swings et origine de la jambe
# ---------------------------------------------------------------------------
def fixture_swings() -> None:
    cas = []
    hauts = [1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0, 2.0, 1.0]
    cadre = _bougies([(h - 0.5, h, h - 1.0, h - 0.2) for h in hauts])
    for sensibilite in (2, 3, 4):
        sommets, creux = ict.detecter_swings(cadre, sensibilite=sensibilite)
        cas.append({"nom": f"palier_sensibilite_{sensibilite}", "cadre": _bougies_vers_json(cadre),
                    "sensibilite": sensibilite, "attendu": {"sommets": sommets, "creux": creux}})

    bas = [9, 7, 5, 3, 1, 3, 5, 7, 9, 8, 7, 6, 4, 6, 8, 10, 12, 14, 16, 18]
    cadre2 = _bougies([(b + 1.0, b + 1.5, float(b), b + 0.5) for b in bas])
    depart = ict.origine_de_jambe(cadre2, ict.BAISSIER, sensibilite=4)
    cas.append({"nom": "origine_dernier_retournement", "cadre": _bougies_vers_json(cadre2),
                "sensObOuSens": ict.BAISSIER, "sensibilite": 4, "attendu": {"origine": depart}})

    ecrire("swings", {"cas": cas})


# ---------------------------------------------------------------------------
# 4. FVG
# ---------------------------------------------------------------------------
def fixture_fvg() -> None:
    cas = []
    haussier = _bougies([(100.0, 101.0, 99.0, 100.5), (100.5, 106.0, 100.0, 105.0), (105.0, 107.0, 102.0, 106.0)])
    cas.append({"nom": "fvg_haussier", "cadre": _bougies_vers_json(haussier), "sens": None,
                "attendu": _fvg_vers_json(ict.detecter_fvg(haussier, "M5"))})

    baissier = _bougies([(105.0, 107.0, 104.0, 105.5), (105.5, 106.0, 100.0, 100.5), (100.5, 103.0, 99.0, 100.0)])
    cas.append({"nom": "fvg_baissier", "cadre": _bougies_vers_json(baissier), "sens": None,
                "attendu": _fvg_vers_json(ict.detecter_fvg(baissier, "M5"))})

    recouvrement = _bougies([(100.0, 103.0, 99.0, 102.0), (102.0, 106.0, 101.0, 105.0), (105.0, 107.0, 102.5, 106.0)])
    cas.append({"nom": "fvg_absent_recouvrement", "cadre": _bougies_vers_json(recouvrement), "sens": None,
                "attendu": _fvg_vers_json(ict.detecter_fvg(recouvrement, "M5"))})

    ecrire("fvg", {"cas": cas})


def _fvg_vers_json(ecarts: list) -> list[dict]:
    return [
        {"unite": e.unite, "sens": e.sens, "haut": e.haut, "bas": e.bas,
         "finMotif": int(pd.Timestamp(e.fin_motif).value // 1_000_000)}
        for e in ecarts
    ]


# ---------------------------------------------------------------------------
# 5. Exécution : stop, dimensionnement, coûts
# ---------------------------------------------------------------------------
def fixture_execution() -> None:
    cas_stop = []
    for sens, ob_haut, ob_bas, meche, marge in [
        (ict.HAUSSIER, 105.0, 100.0, 101.0, 1.0),
        (ict.BAISSIER, 105.0, 100.0, 104.0, 1.0),
        (ict.HAUSSIER, 105.0, 100.0, 97.0, 1.0),   # mèche dépasse la marge nominale
        (ict.BAISSIER, 105.0, 100.0, 108.0, 1.0),
    ]:
        cas_stop.append({
            "sens": sens, "obHaut": ob_haut, "obBas": ob_bas, "mecheBougie2": meche, "marge": marge,
            "attendu": bt_exec.calculer_stop(sens, ob_haut, ob_bas, meche, marge),
        })

    config = bt_exec.ConfigExecution()
    cas_taille = []
    for distance, taux in [(5.0, 1.08), (1.5, 1.10), (50.0, 0.90), (0.0, 1.08), (5.0, 0.0)]:
        resultat = bt_exec.dimensionner(distance, taux, config)
        cas_taille.append({
            "distanceStopUsd": distance, "tauxEurusd": taux,
            "attendu": {"prenable": resultat["prenable"], "lots": resultat["lots"], "perteEur": resultat["perte_eur"]},
        })

    cas_couts = []
    for prix, sens in [(100.0, ict.HAUSSIER), (100.0, ict.BAISSIER)]:
        cas_couts.append({
            "prix": prix, "sens": sens,
            "entree": bt_exec.appliquer_couts_entree(prix, sens, config),
            "sortie": bt_exec.appliquer_couts_sortie(prix, sens, config),
        })

    ecrire("execution", {"stop": cas_stop, "dimensionnement": cas_taille, "couts": cas_couts})


# ---------------------------------------------------------------------------
# 6. Moteur complet, dégradé sur une seule variante (parité avec le backtest)
# ---------------------------------------------------------------------------
def fixture_moteur_complet() -> None:
    """Rejoue le backtest Python sur une série réaliste, mode ratio 1:2.

    Le test JS rejoue la même série à travers traiterNouvellesBougies, en
    limitant variantesActives à ["b2"] — ce qui neutralise la différence
    d'architecture (une entrée, quatre sorties) documentée dans moteur.js et
    reproduit exactement le blocage à une seule position du backtest Python.
    Si les deux listes de trades ne coïncident pas, la logique de fond a
    divergé entre les deux langages.
    """
    n = 20000
    m1 = _serie_m1(n, graine=20260908)
    taux = _taux_eurusd(m1)
    config = moteur.ConfigBacktest(mode_tp="ratio", ratio_tp=2.0)
    bt = moteur.Backtest(m1, taux, config)
    bt.executer()

    trades = [
        {
            "horodatageEntree": int(pd.Timestamp(t.horodatage_entree).value // 1_000_000),
            "horodatageSortie": int(pd.Timestamp(t.horodatage_sortie).value // 1_000_000) if t.horodatage_sortie is not None else None,
            "sens": t.sens,
            "uniteOb": t.unite_ob,
            "obHaut": t.ob_haut,
            "obBas": t.ob_bas,
            "uniteFvg": t.unite_fvg,
            "prixEntree": round(t.prix_entree, 6),
            "stop": round(t.stop, 6),
            "objectif": round(t.objectif, 6),
            "prixSortie": round(t.prix_sortie, 6),
            "lots": t.lots,
            "motifSortie": t.motif_sortie,
        }
        for t in bt.trades
    ]

    ecrire(
        "moteur_complet",
        {
            "m1": _bougies_vers_json(m1),
            "tauxEurusdParJour": {
                str(idx.date()): float(v) for idx, v in taux.items()
            },
            "ratioTp": 2.0,
            "attenduTrades": trades,
            "attenduNTrades": len(trades),
        },
    )


def fixture_paliers() -> None:
    """Sortie par paliers : trades et détail de chaque tranche.

    C'est la fixture de parité de la partie A. Elle rejoue le vrai moteur
    Python en ``mode_tp="paliers"`` sur la même série que
    :func:`fixture_moteur_complet`, et publie non seulement les trades mais
    **chaque tranche** — zone visée, origine du niveau, part de la position,
    prix de sortie, motif et résultat en R. Le portage JavaScript doit
    retrouver tout cela à l'identique, y compris le passage à break-even.
    """
    n = 20000
    m1 = _serie_m1(n, graine=20260908)
    taux = _taux_eurusd(m1)
    config = moteur.ConfigBacktest(mode_tp="paliers")
    bt = moteur.Backtest(m1, taux, config)
    bt.executer()

    def _ms(instant) -> int | None:
        return None if instant is None else int(pd.Timestamp(instant).value // 1_000_000)

    trades = [
        {
            "horodatageEntree": _ms(t.horodatage_entree),
            "horodatageSortie": _ms(t.horodatage_sortie),
            "sens": t.sens,
            "uniteOb": t.unite_ob,
            "uniteFvg": t.unite_fvg,
            "prixEntree": round(t.prix_entree, 6),
            "stop": round(t.stop, 6),
            "objectif": round(t.objectif, 6),
            "lots": t.lots,
            "fvgHaut": None if t.fvg_haut is None else round(t.fvg_haut, 6),
            "fvgBas": None if t.fvg_bas is None else round(t.fvg_bas, 6),
            "paliers": [
                {
                    "rang": p.rang,
                    "zone": round(p.zone, 6),
                    "origine": p.origine,
                    "fraction": round(p.fraction, 10),
                    "ratioRisque": round(p.ratio_risque, 6),
                    "prixSortie": None if p.prix_sortie is None else round(p.prix_sortie, 6),
                    "horodatageResolution": _ms(p.horodatage_sortie),
                    "motifSortie": p.motif_sortie,
                }
                for p in t.paliers
            ],
        }
        for t in bt.trades
    ]

    ecrire(
        "paliers",
        {
            "m1": _bougies_vers_json(m1),
            "tauxEurusdParJour": {
                str(idx.date()): float(v) for idx, v in taux.items()
            },
            "fractionTp1": moteur.FRACTION_TP1,
            "maxZones": moteur.MAX_ZONES_PALIERS,
            "attenduTrades": trades,
            "attenduNTrades": len(trades),
            "repartitions": {
                str(n_zones): moteur.repartir_paliers(n_zones) for n_zones in range(1, 6)
            },
        },
    )


# ---------------------------------------------------------------------------
# 8. Setup sweep : niveaux, cycle de vie, moteur complet par variante
# ---------------------------------------------------------------------------
def _niveau_vers_json(n) -> dict:
    return {
        "unite": n.unite, "cote": n.cote, "prix": n.prix,
        "formation": int(pd.Timestamp(n.formation).value // 1_000_000),
        "connuA": int(pd.Timestamp(n.connu_a).value // 1_000_000),
    }


def fixture_niveaux() -> None:
    """Détection des pivots (trois sensibilités) et cycle de vie d'un niveau."""
    cas = []
    m1 = _serie_m1(3000, graine=11)
    m15 = bt_data.agreger(m1, "M15")
    for k in (3, 4, 5):
        cas.append({"nom": f"m15_sensibilite_{k}", "unite": "M15", "sensibilite": k,
                    "cadre": _bougies_vers_json(m15),
                    "attendu": [_niveau_vers_json(n) for n in ict.detecter_niveaux_liquidite(m15, "M15", k)]})
    h1 = bt_data.agreger(_serie_m1(6000, graine=12), "H1")
    cas.append({"nom": "h1_sensibilite_4", "unite": "H1", "sensibilite": 4, "cadre": _bougies_vers_json(h1),
                "attendu": [_niveau_vers_json(n) for n in ict.detecter_niveaux_liquidite(h1, "H1", 4)]})

    # Cycle de vie : une séquence de bougies M1 puis de clôtures d'unité,
    # rejouée pas à pas ; chaque étape publie l'état attendu du niveau.
    t0 = pd.Timestamp("2026-01-05 00:00", tz="UTC")
    sequences = []
    for cote, prix, etapes in [
        (ict.COTE_BAS, 100.0, [("m1", 100.4, 99.6), ("cloture", 99.9), ("m1", 100.2, 99.1), ("cloture", 100.3), ("m1", 100.5, 98.0), ("cloture", 100.6)]),
        (ict.COTE_HAUT, 100.0, [("m1", 100.0, 99.0), ("cloture", 99.5), ("m1", 101.2, 99.8), ("m1", 101.9, 100.3), ("cloture", 99.7), ("m1", 102.0, 99.0)]),
    ]:
        niveau = ict.NiveauLiquidite("M15", cote, prix, t0, t0)
        trace = []
        for i, etape in enumerate(etapes):
            instant = t0 + pd.Timedelta(minutes=i)
            if etape[0] == "m1":
                retour = ict.avancer_niveau(niveau, etape[1], etape[2], instant)
            else:
                retour = ict.confirmer_balayage(niveau, etape[1], instant)
            trace.append({"etape": etape, "instantMs": int(instant.value // 1_000_000), "retour": retour,
                          "etat": {"enSweep": niveau.en_sweep, "extremeSweep": niveau.extreme_sweep,
                                   "debutSweep": None if niveau.debut_sweep is None else int(niveau.debut_sweep.value // 1_000_000),
                                   "balaye": niveau.balaye}})
        sequences.append({"cote": cote, "prix": prix, "trace": trace})
    ecrire("niveaux", {"cas": cas, "sequences": sequences})


def _trades_sweep_vers_json(trades: list) -> list[dict]:
    def _ms(instant) -> int | None:
        return None if instant is None else int(pd.Timestamp(instant).value // 1_000_000)
    return [
        {
            "setup": t.setup,
            "horodatageEntree": _ms(t.horodatage_entree),
            "horodatageSortie": _ms(t.horodatage_sortie),
            "sens": t.sens,
            "uniteDetection": t.unite_detection,
            "uniteFvg": t.unite_fvg,
            "niveauPrix": t.niveau_prix, "niveauCote": t.niveau_cote, "niveauUnite": t.niveau_unite,
            "niveauFormation": _ms(t.niveau_formation),
            "sweepExtreme": t.sweep_extreme, "sweepDebut": _ms(t.sweep_debut),
            "referencePrix": t.reference_prix, "uniteFibo": t.unite_fibo,
            "prixEntree": round(t.prix_entree, 6), "stop": round(t.stop, 6), "objectif": round(t.objectif, 6),
            "prixSortie": round(t.prix_sortie, 6), "lots": t.lots, "motifSortie": t.motif_sortie,
            "fvgHaut": None if t.fvg_haut is None else round(t.fvg_haut, 6),
            "fvgBas": None if t.fvg_bas is None else round(t.fvg_bas, 6),
            "paliers": [
                {"rang": p.rang, "zone": round(p.zone, 6), "origine": p.origine, "fraction": round(p.fraction, 10),
                 "ratioRisque": round(p.ratio_risque, 6),
                 "prixSortie": None if p.prix_sortie is None else round(p.prix_sortie, 6),
                 "horodatageResolution": _ms(p.horodatage_sortie), "motifSortie": p.motif_sortie}
                for p in t.paliers
            ],
        }
        for t in trades
    ]


def fixture_sweep() -> None:
    """Le moteur Python en mode sweep seul, une exécution par variante du scanner.

    Le test JS rejoue la même série avec variantesActives réduite à la
    variante correspondante (« s1 », « s2 », « s3 »), ce qui reproduit le
    blocage à une position du backtest — même démarche que moteur_complet.
    La quatrième exécution joue les deux setups ensemble (order block en
    objectif structurel + sweep S1) : côté JS, variantesActives = ["a", "s1"].
    """
    n = 20000
    m1 = _serie_m1(n, graine=20260908)
    taux = _taux_eurusd(m1)
    executions = {
        "s1": dict(setups=(moteur.SETUP_SWEEP,), objectif_sweep=moteur.OBJECTIF_SWEEP_FIBO),
        "s2": dict(setups=(moteur.SETUP_SWEEP,), objectif_sweep=moteur.OBJECTIF_SWEEP_FIBO_STRUCTUREL, fraction_fibo=0.5, break_even_sweep=True),
        "s3": dict(setups=(moteur.SETUP_SWEEP,), objectif_sweep=moteur.OBJECTIF_SWEEP_STRUCTUREL),
        "ensemble_a_s1": dict(setups=(moteur.SETUP_ORDER_BLOCK, moteur.SETUP_SWEEP), mode_tp="structurel", objectif_sweep=moteur.OBJECTIF_SWEEP_FIBO),
    }
    sortie = {"m1": _bougies_vers_json(m1), "tauxEurusdParJour": {str(idx.date()): float(v) for idx, v in taux.items()},
              "sensibilitePivot": ict.SENSIBILITE_PIVOT, "uniteFibo": "M15", "executions": {}}
    for nom, reglages in executions.items():
        bt = moteur.Backtest(m1, taux, moteur.ConfigBacktest(**reglages))
        bt.executer()
        sortie["executions"][nom] = {
            "variantesActives": ["a", "s1"] if nom == "ensemble_a_s1" else [nom],
            "attenduTrades": _trades_sweep_vers_json(bt.trades),
            "attenduNTrades": len(bt.trades),
            "nSweepsConfirmes": bt.sweeps_confirmes,
            "nNiveaux": len(bt.niveaux),
        }
        print(f"  {nom} : {len(bt.trades)} trade(s), {bt.sweeps_confirmes} sweep(s) confirmé(s)")
    ecrire("sweep", sortie)


if __name__ == "__main__":
    fixture_agregation()
    fixture_fenetre_close()
    fixture_order_blocks()
    fixture_swings()
    fixture_fvg()
    fixture_execution()
    fixture_moteur_complet()
    fixture_paliers()
    fixture_niveaux()
    fixture_sweep()
    print("Fixtures générées.")
