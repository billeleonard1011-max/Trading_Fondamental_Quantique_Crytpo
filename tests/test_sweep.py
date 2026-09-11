"""Tests du setup de prise de liquidité (sweep).

Ce que ces tests figent, dans l'ordre d'importance :

1. **Causalité** — la détection de pivot regarde par construction vers la
   droite : un niveau à la position ``i`` n'est connu qu'à ``i + k``. Le
   test de troncature (rejouer sur un historique coupé doit produire les
   mêmes trades sur la partie commune) est joué pour k = 3, 4 et 5, sweep
   seul puis avec l'order block — le piège déjà attrapé sur les swings de
   jambe ne doit pas revenir par ici.
2. **Cycle de vie d'un niveau** — traversé sans clôture de l'autre côté :
   toujours actif ; balayé (mèche au-delà puis clôture de l'autre côté) :
   sorti de la liste, il ne ressert jamais.
3. **Symétrie** — sur des données miroir, le setup produit les mêmes trades
   en sens inverse, aux mêmes instants.
4. **Objectifs** — les trois familles visent ce qu'elles annoncent, et le
   break-even se débraye.
5. **Sensibilité du pivot** — 3, 4 et 5 changent le nombre de niveaux.

Aucun test n'accède au réseau : la série est fabriquée.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest import data as bt_data
from backtest import execution as bt_exec
from backtest import ict, moteur


# ---------------------------------------------------------------------------
# Fabriques
# ---------------------------------------------------------------------------
def _serie_m1(n: int = 6000, graine: int = 20260908) -> pd.DataFrame:
    """Même générateur que tests/test_backtest_engine.py : une marche aléatoire à mèches."""
    alea = np.random.default_rng(graine)
    index = pd.date_range("2026-01-05 00:00", periods=n, freq="min", tz="UTC")
    pas = alea.normal(0.0, 0.35, n)
    cloture = 3000.0 + np.cumsum(pas)
    ouverture = np.concatenate([[3000.0], cloture[:-1]])
    amplitude = np.abs(alea.normal(0.0, 0.25, n))
    return pd.DataFrame(
        {
            "open": ouverture,
            "high": np.maximum(ouverture, cloture) + amplitude,
            "low": np.minimum(ouverture, cloture) - amplitude,
            "close": cloture,
            "volume": alea.integers(20, 200, n).astype("float64"),
        },
        index=index,
    )


def _taux_eurusd(m1: pd.DataFrame) -> pd.Series:
    jours = pd.date_range(m1.index.min().normalize(), m1.index.max().normalize(), freq="D", tz="UTC")
    return pd.Series(np.linspace(1.07, 1.10, len(jours)), index=jours)


def _miroir(m1: pd.DataFrame, pivot: float = 3000.0) -> pd.DataFrame:
    """Réfléchit la série autour d'un prix : hauts et bas s'échangent."""
    return pd.DataFrame(
        {
            "open": 2 * pivot - m1["open"],
            "high": 2 * pivot - m1["low"],
            "low": 2 * pivot - m1["high"],
            "close": 2 * pivot - m1["close"],
            "volume": m1["volume"],
        },
        index=m1.index,
    )


def _bougies(lignes: list[tuple[float, float, float, float]], unite: str = "M15") -> pd.DataFrame:
    """Cadre OHLC de l'unité, à partir de quadruplets (open, high, low, close)."""
    pas = bt_data.DUREES[unite]
    index = pd.date_range("2026-01-05 00:00", periods=len(lignes), freq=pas, tz="UTC")
    return pd.DataFrame(
        {"open": [l[0] for l in lignes], "high": [l[1] for l in lignes],
         "low": [l[2] for l in lignes], "close": [l[3] for l in lignes], "volume": 1.0},
        index=index,
    )


def _config(**kw) -> moteur.ConfigBacktest:
    base = dict(setups=(moteur.SETUP_SWEEP,), objectif_sweep=moteur.OBJECTIF_SWEEP_FIBO)
    base.update(kw)
    return moteur.ConfigBacktest(**base)


def _trades_clos_avant(bt: moteur.Backtest, limite: pd.Timestamp) -> list[dict]:
    return [
        t.to_dict() for t in bt.trades
        if t.horodatage_sortie is not None and t.horodatage_sortie <= limite
    ]


# ---------------------------------------------------------------------------
# 1. Détection des niveaux
# ---------------------------------------------------------------------------
def test_un_pivot_devient_un_niveau_connu_a_la_cloture_de_la_bougie_i_plus_k() -> None:
    hauts = [1.0, 2.0, 3.0, 4.0, 9.0, 4.0, 3.0, 2.0, 1.0, 2.0, 3.0]
    cadre = _bougies([(h - 0.5, h, h - 1.0, h - 0.2) for h in hauts])
    niveaux = ict.detecter_niveaux_liquidite(cadre, "M15", sensibilite=4)
    sommet = next(n for n in niveaux if n.cote == ict.COTE_HAUT and n.prix == 9.0)
    assert sommet.formation == cadre.index[4]
    assert sommet.connu_a == cadre.index[8] + bt_data.DUREES["M15"], "connu seulement quand la 4e bougie de droite est close"
    assert sommet.sens_trade == ict.BAISSIER


def test_la_sensibilite_du_pivot_change_le_nombre_de_niveaux() -> None:
    m1 = _serie_m1(6000)
    m15 = bt_data.agreger(m1, "M15")
    comptes = {k: len(ict.detecter_niveaux_liquidite(m15, "M15", k)) for k in (3, 4, 5)}
    assert comptes[3] >= comptes[4] >= comptes[5]
    assert comptes[3] > comptes[5], "une sensibilité plus fine doit repérer davantage de pivots"


def test_les_niveaux_sont_tries_par_instant_de_connaissance() -> None:
    m1 = _serie_m1(3000)
    niveaux = ict.detecter_niveaux_liquidite(bt_data.agreger(m1, "M15"), "M15")
    assert niveaux == sorted(niveaux, key=lambda n: n.connu_a)


# ---------------------------------------------------------------------------
# 2. Cycle de vie d'un niveau
# ---------------------------------------------------------------------------
def _niveau_bas(prix: float = 100.0) -> ict.NiveauLiquidite:
    t = pd.Timestamp("2026-01-05 00:00", tz="UTC")
    return ict.NiveauLiquidite("M15", ict.COTE_BAS, prix, t, t)


def test_un_niveau_traverse_sans_cloture_de_lautre_cote_reste_actif() -> None:
    niveau = _niveau_bas(100.0)
    t = pd.Timestamp("2026-01-05 01:00", tz="UTC")
    assert ict.avancer_niveau(niveau, haut=100.5, bas=99.2, ouverture=t)
    assert niveau.en_sweep and niveau.extreme_sweep == 99.2 and niveau.debut_sweep == t
    # La bougie de l'unité clôture encore sous le niveau : rien n'est confirmé.
    assert ict.confirmer_balayage(niveau, cloture=99.8, instant=t) is False
    assert niveau.balaye is False and niveau.en_sweep is True
    # L'extrême suit le point le plus loin, le début du sweep ne bouge pas.
    ict.avancer_niveau(niveau, haut=99.9, bas=98.7, ouverture=t + pd.Timedelta(minutes=1))
    assert niveau.extreme_sweep == 98.7 and niveau.debut_sweep == t


def test_toucher_le_niveau_exactement_nest_pas_le_depasser() -> None:
    niveau = _niveau_bas(100.0)
    assert ict.avancer_niveau(niveau, haut=101.0, bas=100.0, ouverture=pd.Timestamp("2026-01-05", tz="UTC")) is False
    assert niveau.en_sweep is False


def test_un_niveau_balaye_est_confirme_puis_ne_ressert_jamais() -> None:
    niveau = _niveau_bas(100.0)
    t = pd.Timestamp("2026-01-05 01:00", tz="UTC")
    ict.avancer_niveau(niveau, haut=100.5, bas=99.0, ouverture=t)
    assert ict.confirmer_balayage(niveau, cloture=100.3, instant=t) is True
    assert niveau.balaye and niveau.horodatage_balayage == t
    # Une nouvelle traversée n'ouvre plus de sweep, une nouvelle clôture ne confirme plus rien.
    assert ict.avancer_niveau(niveau, haut=100.5, bas=98.0, ouverture=t) is False
    assert ict.confirmer_balayage(niveau, cloture=100.4, instant=t) is False


def test_une_cloture_sans_sweep_prealable_ne_confirme_rien() -> None:
    niveau = _niveau_bas(100.0)
    assert ict.confirmer_balayage(niveau, cloture=100.5, instant=pd.Timestamp("2026-01-05", tz="UTC")) is False


def test_le_moteur_ne_reprend_jamais_un_niveau_balaye() -> None:
    """Sur une série réelle, chaque niveau produit au plus un setup."""
    m1 = _serie_m1(8000)
    bt = moteur.Backtest(m1, _taux_eurusd(m1), _config())
    bt.executer()
    balayes = [n for n in bt.niveaux if n.balaye]
    assert balayes, "la série doit produire des sweeps pour que le test soit probant"
    assert bt.sweeps_confirmes == len(balayes)
    cles = [(t.niveau_unite, t.niveau_formation, t.niveau_cote) for t in bt.trades]
    assert len(cles) == len(set(cles)), "un même niveau a ouvert deux trades"


# ---------------------------------------------------------------------------
# 3. Causalité
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("setups", [(moteur.SETUP_SWEEP,), (moteur.SETUP_ORDER_BLOCK, moteur.SETUP_SWEEP)])
def test_aucun_look_ahead_par_troncature_pour_chaque_sensibilite_de_pivot(setups: tuple[str, ...]) -> None:
    m1 = _serie_m1(6000)
    taux = _taux_eurusd(m1)
    coupure = 4000
    limite = m1.index[coupure - 1]
    for k in (3, 4, 5):
        for objectif in (moteur.OBJECTIF_SWEEP_FIBO, moteur.OBJECTIF_SWEEP_FIBO_STRUCTUREL, moteur.OBJECTIF_SWEEP_STRUCTUREL):
            config = _config(setups=setups, sensibilite_pivot=k, objectif_sweep=objectif, mode_tp="ratio", ratio_tp=2.0)
            complet = moteur.Backtest(m1, taux, config)
            complet.executer()
            tronque = moteur.Backtest(m1.iloc[:coupure], taux, config)
            tronque.executer()
            a, b = _trades_clos_avant(complet, limite), _trades_clos_avant(tronque, limite)
            assert a == b, f"setups={setups} k={k} objectif={objectif} : le moteur voit des données futures ({len(a)} vs {len(b)})"


def test_aucun_look_ahead_par_perturbation_du_futur() -> None:
    """Changer les bougies après la coupure ne change rien avant."""
    m1 = _serie_m1(6000)
    taux = _taux_eurusd(m1)
    coupure = 4000
    limite = m1.index[coupure - 1]
    perturbe = m1.copy()
    perturbe.iloc[coupure:, :4] = perturbe.iloc[coupure:, :4].to_numpy() + 40.0
    config = _config()
    a = moteur.Backtest(m1, taux, config); a.executer()
    b = moteur.Backtest(perturbe, taux, config); b.executer()
    assert _trades_clos_avant(a, limite) == _trades_clos_avant(b, limite)


def test_les_niveaux_ne_sont_jamais_actifs_avant_detre_connus() -> None:
    """Un sweep ne peut pas commencer avant l'instant de connaissance du niveau."""
    m1 = _serie_m1(6000)
    bt = moteur.Backtest(m1, _taux_eurusd(m1), _config())
    bt.executer()
    for t in bt.trades:
        niveau = next(n for n in bt.niveaux if n.unite == t.niveau_unite and n.formation == t.niveau_formation and n.cote == t.niveau_cote)
        assert t.sweep_debut >= niveau.connu_a - bt_data.DUREES["M1"], "sweep amorcé avant que le niveau existe"
        assert t.niveau_formation < t.sweep_debut


# ---------------------------------------------------------------------------
# 4. Symétrie
# ---------------------------------------------------------------------------
def test_le_setup_se_declenche_identiquement_dans_les_deux_sens_sur_des_donnees_miroir() -> None:
    m1 = _serie_m1(8000)
    taux = _taux_eurusd(m1)
    config = _config(objectif_sweep=moteur.OBJECTIF_SWEEP_FIBO)
    droit = moteur.Backtest(m1, taux, config); droit.executer()
    miroir = moteur.Backtest(_miroir(m1), taux, config); miroir.executer()
    assert droit.trades, "la série doit produire des trades pour que le test soit probant"
    assert len(droit.trades) == len(miroir.trades)
    for a, b in zip(droit.trades, miroir.trades):
        assert a.horodatage_entree == b.horodatage_entree
        assert a.sens != b.sens
        assert a.niveau_unite == b.niveau_unite and a.niveau_cote != b.niveau_cote
        assert abs((a.niveau_prix + b.niveau_prix) - 6000.0) < 1e-9
        assert abs((a.sweep_extreme + b.sweep_extreme) - 6000.0) < 1e-9
        assert abs((a.stop + b.stop) - 6000.0) < 1e-9
        assert abs((a.objectif + b.objectif) - 6000.0) < 1e-9
        assert a.motif_sortie == b.motif_sortie and a.horodatage_sortie == b.horodatage_sortie


# ---------------------------------------------------------------------------
# 5. Objectifs
# ---------------------------------------------------------------------------
def test_les_trois_familles_dobjectif_visent_ce_quelles_annoncent() -> None:
    m1 = _serie_m1(8000)
    taux = _taux_eurusd(m1)
    resultats = {}
    for objectif in (moteur.OBJECTIF_SWEEP_FIBO, moteur.OBJECTIF_SWEEP_FIBO_STRUCTUREL, moteur.OBJECTIF_SWEEP_STRUCTUREL):
        bt = moteur.Backtest(m1, taux, _config(objectif_sweep=objectif))
        bt.executer()
        resultats[objectif] = bt
        assert bt.trades, f"{objectif} : aucun trade, le test n'est pas probant"

    for t in resultats[moteur.OBJECTIF_SWEEP_FIBO].trades:
        achat = t.sens == ict.HAUSSIER
        attendu = t.sweep_extreme + 0.72 * abs(t.reference_prix - t.sweep_extreme) if achat else t.sweep_extreme - 0.72 * abs(t.reference_prix - t.sweep_extreme)
        assert abs(t.objectif - attendu) < 1e-9
        assert not t.paliers and t.unite_fibo == "M15"
        assert (t.objectif > t.prix_entree) if achat else (t.objectif < t.prix_entree)
        assert (t.stop < t.sweep_extreme) if achat else (t.stop > t.sweep_extreme)

    for t in resultats[moteur.OBJECTIF_SWEEP_FIBO_STRUCTUREL].trades:
        assert len(t.paliers) == 2
        assert t.paliers[0].origine == moteur.ORIGINE_FIBO and abs(t.paliers[0].fraction - 0.5) < 1e-12
        assert t.paliers[1].origine in (moteur.ORIGINE_NIVEAU_HAUT, moteur.ORIGINE_NIVEAU_BAS, "order_block")
        # Le structurel est au-delà du 0,72, jamais plus proche.
        plus_loin = t.paliers[1].zone > t.paliers[0].zone if t.sens == ict.HAUSSIER else t.paliers[1].zone < t.paliers[0].zone
        assert plus_loin

    for t in resultats[moteur.OBJECTIF_SWEEP_STRUCTUREL].trades:
        assert not t.paliers and t.unite_fibo == ""
        assert (t.objectif > t.prix_entree) if t.sens == ict.HAUSSIER else (t.objectif < t.prix_entree)


def test_le_break_even_se_debraye_et_change_les_sorties() -> None:
    m1 = _serie_m1(12000)
    taux = _taux_eurusd(m1)
    avec = moteur.Backtest(m1, taux, _config(objectif_sweep=moteur.OBJECTIF_SWEEP_FIBO_STRUCTUREL, break_even_sweep=True)); avec.executer()
    sans = moteur.Backtest(m1, taux, _config(objectif_sweep=moteur.OBJECTIF_SWEEP_FIBO_STRUCTUREL, break_even_sweep=False)); sans.executer()
    motifs_sans = {p.motif_sortie for t in sans.trades for p in t.paliers}
    assert moteur.SORTIE_BREAK_EVEN not in motifs_sans, "sans break-even, aucune tranche ne peut sortir au prix d'entrée"
    assert all(t.break_even_actif is False for t in sans.trades)
    assert all(t.break_even_actif is True for t in avec.trades)


def test_la_repartition_de_la_variante_2_est_parametrable() -> None:
    m1 = _serie_m1(8000)
    taux = _taux_eurusd(m1)
    bt = moteur.Backtest(m1, taux, _config(objectif_sweep=moteur.OBJECTIF_SWEEP_FIBO_STRUCTUREL, fraction_fibo=0.33)); bt.executer()
    assert bt.trades
    for t in bt.trades:
        assert abs(t.paliers[0].fraction - 0.33) < 1e-12 and abs(t.paliers[1].fraction - 0.67) < 1e-12


def test_la_marge_du_stop_sweep_est_parametrable_separement() -> None:
    m1 = _serie_m1(6000)
    taux = _taux_eurusd(m1)
    a = moteur.Backtest(m1, taux, _config()); a.executer()
    b = moteur.Backtest(m1, taux, _config(marge_stop_sweep=2.0)); b.executer()
    assert a.trades and b.trades
    for t in a.trades:
        assert abs(abs(t.stop - t.sweep_extreme) - bt_exec.MARGE_STOP_DEFAUT) < 1e-9
    for t in b.trades:
        assert abs(abs(t.stop - t.sweep_extreme) - 2.0) < 1e-9


# ---------------------------------------------------------------------------
# 6. Coexistence
# ---------------------------------------------------------------------------
def test_les_deux_setups_coexistent_sans_cumuler_de_position() -> None:
    m1 = _serie_m1(8000)
    taux = _taux_eurusd(m1)
    bt = moteur.Backtest(m1, taux, _config(setups=(moteur.SETUP_ORDER_BLOCK, moteur.SETUP_SWEEP), mode_tp="ratio", ratio_tp=2.0))
    bt.executer()
    origines = {t.setup for t in bt.trades}
    assert origines == {moteur.SETUP_ORDER_BLOCK, moteur.SETUP_SWEEP}, f"les deux setups doivent produire des trades : {origines}"
    # Jamais deux positions ouvertes en même temps.
    intervalles = sorted((t.horodatage_entree, t.horodatage_sortie) for t in bt.trades)
    for (d1, f1), (d2, _) in zip(intervalles, intervalles[1:]):
        assert f1 is not None and d2 >= f1


def test_le_setup_ob_seul_est_strictement_inchange() -> None:
    """Le comportement par défaut (order block seul) ne bouge pas d'un trade."""
    m1 = _serie_m1(6000)
    taux = _taux_eurusd(m1)
    ancien = moteur.Backtest(m1, taux, moteur.ConfigBacktest(mode_tp="ratio", ratio_tp=2.0)); ancien.executer()
    assert all(t.setup == moteur.SETUP_ORDER_BLOCK for t in ancien.trades)
    assert not ancien.niveaux, "aucun niveau n'est calculé quand le sweep n'est pas joué"


# ---------------------------------------------------------------------------
# 7. Interférence entre setups et règle de priorité (à l'essai)
# ---------------------------------------------------------------------------
def test_les_interferences_sont_comptees_quand_les_deux_setups_coexistent() -> None:
    m1 = _serie_m1(12000)
    taux = _taux_eurusd(m1)
    bt = moteur.Backtest(m1, taux, _config(setups=(moteur.SETUP_ORDER_BLOCK, moteur.SETUP_SWEEP), mode_tp="ratio", ratio_tp=2.0))
    bt.executer()
    assert bt.interferences["sweep_refuse_priorite_ob"] == 0, "la règle est désactivée par défaut"
    total = sum(bt.interferences.values())
    assert total > 0, "deux setups sur la même position doivent se gêner au moins une fois"


def test_la_priorite_ob_refuse_des_sweeps_et_ne_change_rien_au_setup_ob_seul() -> None:
    m1 = _serie_m1(12000)
    taux = _taux_eurusd(m1)
    sans = moteur.Backtest(m1, taux, _config(setups=(moteur.SETUP_ORDER_BLOCK, moteur.SETUP_SWEEP), mode_tp="ratio", ratio_tp=2.0)); sans.executer()
    avec = moteur.Backtest(m1, taux, _config(setups=(moteur.SETUP_ORDER_BLOCK, moteur.SETUP_SWEEP), mode_tp="ratio", ratio_tp=2.0,
                                             priorite_ob=True, proximite_ob_usd=50.0)); avec.executer()
    assert avec.interferences["sweep_refuse_priorite_ob"] > 0
    n_sweep_avec = sum(1 for t in avec.trades if t.setup == moteur.SETUP_SWEEP)
    n_sweep_sans = sum(1 for t in sans.trades if t.setup == moteur.SETUP_SWEEP)
    assert n_sweep_avec < n_sweep_sans
    # Sweep seul : la règle ne s'applique jamais (aucun OB joué... mais les OB existent) — elle
    # doit rester sans effet sur un backtest OB seul, qui n'ouvre aucun sweep.
    ob = moteur.Backtest(m1, taux, moteur.ConfigBacktest(mode_tp="ratio", ratio_tp=2.0, priorite_ob=True)); ob.executer()
    assert ob.interferences["sweep_refuse_priorite_ob"] == 0


# ---------------------------------------------------------------------------
# 8. Élargissement du stop (mesure de la friction)
# ---------------------------------------------------------------------------
def test_le_multiplicateur_de_stop_elargit_sans_changer_le_cote() -> None:
    m1 = _serie_m1(8000)
    taux = _taux_eurusd(m1)
    base = moteur.Backtest(m1, taux, _config()); base.executer()
    large = moteur.Backtest(m1, taux, _config(multiplicateur_stop=2.0)); large.executer()
    assert base.trades and large.trades
    for t in large.trades:
        if t.sens == ict.HAUSSIER:
            assert t.stop < t.prix_entree, "un stop d'achat reste sous l'entrée"
        else:
            assert t.stop > t.prix_entree, "un stop de vente reste au-dessus"
    d_base = sum(abs(t.prix_entree - t.stop) for t in base.trades) / len(base.trades)
    d_large = sum(abs(t.prix_entree - t.stop) for t in large.trades) / len(large.trades)
    assert d_large > d_base * 1.5, f"le stop doit s'élargir nettement ({d_base:.2f} → {d_large:.2f})"


def test_un_multiplicateur_de_un_ne_change_rien() -> None:
    """Le défaut doit laisser les résultats publiés intacts."""
    m1 = _serie_m1(6000)
    taux = _taux_eurusd(m1)
    a = moteur.Backtest(m1, taux, _config()); a.executer()
    b = moteur.Backtest(m1, taux, _config(multiplicateur_stop=1.0)); b.executer()
    assert [t.to_dict() for t in a.trades] == [t.to_dict() for t in b.trades]


def test_le_risque_en_euros_reste_dans_la_fourchette_malgre_un_stop_large() -> None:
    """Le dimensionnement compense : la taille baisse quand le stop s'élargit."""
    m1 = _serie_m1(8000)
    taux = _taux_eurusd(m1)
    large = moteur.Backtest(m1, taux, _config(multiplicateur_stop=3.0)); large.executer()
    assert large.trades
    for t in large.trades:
        risque = abs(t.prix_entree - t.stop) * bt_exec.ONCES_PAR_LOT * t.lots / 1.08
        assert bt_exec.PERTE_MIN_EUR * 0.9 <= risque <= bt_exec.PERTE_MAX_EUR * 1.1, (
            f"risque hors fourchette : {risque:.1f} €"
        )
