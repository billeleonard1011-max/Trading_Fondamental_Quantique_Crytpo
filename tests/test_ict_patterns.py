"""Tests des motifs ICT : order blocks, jambes, FVG et Fibonacci.

Les motifs sont construits à la main, bougie par bougie, pour que chaque cas
limite soit vérifiable de tête. Un motif détecté par erreur produit un trade
qui n'existe pas ; un motif manqué en fait disparaître un vrai. Les deux
faussent le backtest, et rien dans les résultats ne le signalerait.

Aucun test n'accède au réseau.

Exécution :
    pytest tests/test_ict_patterns.py -v
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest import data as bt_data
from backtest import ict


def _bougies(lignes: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Construit un cadre OHLC horaire à partir de quadruplets.

    Args:
        lignes: suites ``(open, high, low, close)``.

    Returns:
        DataFrame indexé par heure UTC.
    """
    index = pd.date_range("2026-01-01", periods=len(lignes), freq="h", tz="UTC")
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


# ---------------------------------------------------------------------------
# 1. Order blocks
# ---------------------------------------------------------------------------
def test_ob_baissier_valide() -> None:
    """Bougie 1 haussière, bougie 2 clôturant dessous, bougie 3 qui n'y revient pas."""
    cadre = _bougies([
        (100.0, 105.0, 99.0, 104.0),   # 1 : haussière, zone [99, 105]
        (104.0, 104.0, 95.0, 96.0),    # 2 : baissière, clôture 96 < 99
        (96.0, 97.0, 95.0, 96.5),      # 3 : plus haut 97 < 99
    ])
    zones = ict.detecter_order_blocks(cadre, "H1")
    assert len(zones) == 1

    zone = zones[0]
    assert zone.sens == ict.BAISSIER
    assert zone.bas == 99.0 and zone.haut == 105.0
    # La zone n'est connue qu'à la clôture de la troisième bougie.
    assert zone.fin_motif == cadre.index[2] + bt_data.DUREES["H1"]
    # La mèche de la bougie 2 sert au placement du stop.
    assert zone.meche_bougie2 == 104.0


def test_ob_haussier_valide() -> None:
    """Le motif symétrique donne une zone d'achat."""
    cadre = _bougies([
        (105.0, 106.0, 100.0, 101.0),  # 1 : baissière, zone [100, 106]
        (101.0, 110.0, 101.0, 109.0),  # 2 : haussière, clôture 109 > 106
        (109.0, 111.0, 107.0, 108.0),  # 3 : plus bas 107 > 106
    ])
    zones = ict.detecter_order_blocks(cadre, "H1")
    assert len(zones) == 1
    assert zones[0].sens == ict.HAUSSIER
    assert zones[0].bas == 100.0 and zones[0].haut == 106.0


def test_ob_rejete_si_bougie3_touche_exactement() -> None:
    """Cas limite : la bougie 3 touchant pile l'extrême de la bougie 1 invalide.

    C'est ce contact qui refermerait l'écart laissé ouvert par le motif : la
    comparaison doit donc être strictement inférieure, pas « inférieure ou
    égale ».
    """
    cadre = _bougies([
        (100.0, 105.0, 99.0, 104.0),
        (104.0, 104.0, 95.0, 96.0),
        (96.0, 99.0, 95.0, 96.5),      # plus haut = 99.0 = bas de la bougie 1
    ])
    assert ict.detecter_order_blocks(cadre, "H1") == []

    # Un cheveu en dessous, et le motif est valide.
    cadre.iloc[2, cadre.columns.get_loc("high")] = 98.999
    assert len(ict.detecter_order_blocks(cadre, "H1")) == 1


def test_ob_rejete_si_bougie2_ne_cloture_pas_au_dela() -> None:
    """Une bougie 2 qui n'a pas clôturé au-delà de l'extrême ne fait pas motif."""
    cadre = _bougies([
        (100.0, 105.0, 99.0, 104.0),
        (104.0, 104.0, 95.0, 99.5),    # clôture 99.5 > 99 : insuffisant
        (99.5, 98.0, 95.0, 96.0),
    ])
    assert ict.detecter_order_blocks(cadre, "H1") == []


def test_ob_rejete_si_bougie2_meme_sens() -> None:
    """La bougie 2 doit être de sens opposé à la bougie 1."""
    cadre = _bougies([
        (100.0, 105.0, 99.0, 104.0),   # haussière
        (104.0, 110.0, 104.0, 109.0),  # haussière aussi
        (109.0, 111.0, 107.0, 108.0),
    ])
    assert ict.detecter_order_blocks(cadre, "H1") == []


def test_ob_bougie1_sans_corps_ignoree() -> None:
    """Une bougie 1 en doji n'a pas de sens : le motif n'a pas de sens non plus."""
    cadre = _bougies([
        (100.0, 105.0, 99.0, 100.0),   # ouverture = clôture
        (100.0, 100.0, 95.0, 96.0),
        (96.0, 97.0, 95.0, 96.5),
    ])
    assert ict.detecter_order_blocks(cadre, "H1") == []


# ---------------------------------------------------------------------------
# 2. Classification de la jambe
# ---------------------------------------------------------------------------
def test_jambe_violente_toutes_bougies_meme_sens() -> None:
    """Sans aucune bougie contraire, la jambe est violente."""
    cadre = _bougies([(i, i + 1.2, i - 0.1, i + 1.0) for i in range(1, 7)])
    classification, detail = ict.classifier_jambe(cadre)
    assert classification == ict.VIOLENTE
    assert detail["n_contraires"] == 0


def test_jambe_normale_avec_bougie_contraire_marquee() -> None:
    """Une bougie contraire au corps franc casse la violence."""
    lignes = [(i, i + 1.2, i - 0.1, i + 1.0) for i in range(1, 6)]
    # Bougie contraire de corps 1.0, comparable aux autres.
    lignes.append((6.0, 6.2, 4.8, 5.0))
    classification, detail = ict.classifier_jambe(_bougies(lignes))
    assert classification == ict.NORMALE
    assert detail["n_contraires"] == 1
    assert detail["n_contraires_negligeables"] == 0


def test_jambe_violente_malgre_une_bougie_contraire_negligeable() -> None:
    """Une bougie contraire minuscule ne casse pas la violence."""
    lignes = [(float(i), i + 1.2, i - 0.1, float(i + 1)) for i in range(1, 6)]
    # Corps moyen des bougies motrices : 1.0. Corps contraire : 0.1, soit 10 %.
    lignes.append((6.0, 6.2, 5.8, 5.9))
    classification, detail = ict.classifier_jambe(_bougies(lignes))
    assert classification == ict.VIOLENTE
    assert detail["n_contraires"] == 1
    assert detail["n_contraires_negligeables"] == 1


def test_jambe_cas_limite_exactement_30_pourcent() -> None:
    """Une bougie contraire à exactement 30 % du corps moyen casse la violence.

    Le seuil est franchi de façon stricte : « moins de 30 % » exclut 30 %.
    Ce cas limite décide de la classification, donc du type d'entrée, donc du
    trade — il mérite d'être verrouillé.
    """
    # Cinq bougies de corps 1.0, puis une contraire dont on ajuste le corps
    # pour que la moyenne de tous les corps rende le rapport exact.
    lignes = [(float(i), i + 1.2, i - 0.1, float(i + 1)) for i in range(1, 6)]

    # Avec cinq corps de 1.0 et un corps contraire c : moyenne = (5 + c) / 6.
    # On veut c = 0.30 × (5 + c) / 6, soit c = 1.5 / 5.7.
    corps = 1.5 / 5.7
    lignes.append((6.0, 6.2, 6.0 - corps - 0.1, 6.0 - corps))
    cadre = _bougies(lignes)

    corps_reels = (cadre["close"] - cadre["open"]).abs()
    rapport = corps_reels.iloc[-1] / corps_reels.mean()
    assert rapport == pytest.approx(0.30, abs=1e-9), "Le cas limite doit valoir 30 %."

    classification, _ = ict.classifier_jambe(cadre)
    assert classification == ict.NORMALE, "À 30 % pile, la bougie compte."

    # Juste en dessous, elle est négligeable et la jambe reste violente.
    lignes[-1] = (6.0, 6.2, 6.0 - corps * 0.9 - 0.1, 6.0 - corps * 0.9)
    assert ict.classifier_jambe(_bougies(lignes))[0] == ict.VIOLENTE


def test_jambe_trois_bougies_contraires_negligeables_cassent() -> None:
    """Au-delà de deux bougies contraires, même minuscules, la jambe est normale."""
    lignes = [(float(i), i + 1.2, i - 0.1, float(i + 1)) for i in range(1, 8)]
    for k in (2, 4, 6):
        base = lignes[k][0]
        lignes[k] = (base, base + 0.2, base - 0.15, base - 0.05)
    classification, detail = ict.classifier_jambe(_bougies(lignes))
    assert detail["n_contraires"] == 3
    assert classification == ict.NORMALE


# ---------------------------------------------------------------------------
# 3. Fair value gaps
# ---------------------------------------------------------------------------
def test_fvg_haussier_et_baissier() -> None:
    """Les deux sens d'écart sont reconnus, avec leurs bornes."""
    haussier = _bougies([
        (100.0, 101.0, 99.0, 100.5),
        (100.5, 106.0, 100.0, 105.0),
        (105.0, 107.0, 102.0, 106.0),   # bas 102 > haut 101 de la bougie 1
    ])
    ecarts = ict.detecter_fvg(haussier, "M5")
    assert len(ecarts) == 1
    assert ecarts[0].sens == ict.HAUSSIER
    assert ecarts[0].bas == 101.0 and ecarts[0].haut == 102.0

    baissier = _bougies([
        (105.0, 107.0, 104.0, 105.5),
        (105.5, 106.0, 100.0, 100.5),
        (100.5, 103.0, 99.0, 100.0),    # haut 103 < bas 104 de la bougie 1
    ])
    ecarts = ict.detecter_fvg(baissier, "M5")
    assert len(ecarts) == 1
    assert ecarts[0].sens == ict.BAISSIER
    assert ecarts[0].bas == 103.0 and ecarts[0].haut == 104.0


def test_fvg_absent_si_les_fourchettes_se_recouvrent() -> None:
    """Sans écart réel entre la première et la troisième bougie, pas de FVG."""
    cadre = _bougies([
        (100.0, 103.0, 99.0, 102.0),
        (102.0, 106.0, 101.0, 105.0),
        (105.0, 107.0, 102.5, 106.0),   # bas 102.5 < haut 103 : recouvrement
    ])
    assert ict.detecter_fvg(cadre, "M5") == []


def test_fvg_filtre_par_sens() -> None:
    """Le filtre par sens ne rend que les écarts demandés."""
    cadre = _bougies([
        (100.0, 101.0, 99.0, 100.5),
        (100.5, 106.0, 100.0, 105.0),
        (105.0, 107.0, 102.0, 106.0),
    ])
    assert len(ict.detecter_fvg(cadre, "M5", sens=ict.HAUSSIER)) == 1
    assert ict.detecter_fvg(cadre, "M5", sens=ict.BAISSIER) == []


# ---------------------------------------------------------------------------
# 4. Sommet mobile du Fibonacci
# ---------------------------------------------------------------------------
def test_sommet_se_fige_a_la_premiere_bougie_sans_nouvel_extreme() -> None:
    """Le sommet suit les nouveaux extrêmes et se fige au premier échec.

    La comparaison porte sur la bougie **précédente**, pas sur le maximum
    courant : une bougie qui ne dépasse pas celle d'avant fige le sommet,
    même si le prix remonte ensuite.
    """
    # Plus hauts : 10, 11, 12, 11.5, 13 → le sommet se fige à 12, position 2.
    cadre = _bougies([
        (9.0, 10.0, 8.0, 9.5),
        (9.5, 11.0, 9.0, 10.5),
        (10.5, 12.0, 10.0, 11.5),
        (11.5, 11.5, 10.5, 11.0),   # ne dépasse pas 12 : fige ici
        (11.0, 13.0, 10.8, 12.5),   # postérieur, ne doit rien changer
    ])
    position, valeur = ict.sommet_fibonacci(cadre, ict.HAUSSIER)
    assert position == 2
    assert valeur == 12.0


def test_sommet_ne_se_fige_ni_avant_ni_apres() -> None:
    """Le sommet ne se fige pas tant que chaque bougie fait un nouvel extrême."""
    cadre = _bougies([(float(i), float(i + 1), float(i - 1), float(i)) for i in range(5)])
    position, valeur = ict.sommet_fibonacci(cadre, ict.HAUSSIER)
    # Aucune bougie n'échoue : le sommet est la dernière, encore provisoire.
    assert position == len(cadre) - 1
    assert valeur == pytest.approx(5.0)


def test_sommet_baissier_symetrique() -> None:
    """Sur un mouvement baissier, le sommet suit les plus bas."""
    cadre = _bougies([
        (10.0, 10.5, 9.0, 9.5),
        (9.5, 9.8, 8.0, 8.5),
        (8.5, 8.8, 7.0, 7.5),
        (7.5, 8.0, 7.5, 7.8),   # ne descend pas sous 7.0 : fige
    ])
    position, valeur = ict.sommet_fibonacci(cadre, ict.BAISSIER)
    assert position == 2
    assert valeur == 7.0


def test_sommet_sur_cadre_vide() -> None:
    """Un cadre vide ne lève pas d'exception."""
    assert ict.sommet_fibonacci(pd.DataFrame(), ict.HAUSSIER) == (None, None)


def test_zone_ote_bornes_et_niveau_cle() -> None:
    """La zone OTE se calcule en retracement depuis le sommet."""
    zone = ict.zone_ote(origine=100.0, sommet=200.0)
    assert zone["debut"] == pytest.approx(200.0 - 61.8)
    assert zone["fin"] == pytest.approx(200.0 - 79.0)
    assert zone["cle"] == pytest.approx(200.0 - 72.0)
    assert zone["bas"] == pytest.approx(121.0)
    assert zone["haut"] == pytest.approx(138.2)


# ---------------------------------------------------------------------------
# 5. Agrégation et bougies closes
# ---------------------------------------------------------------------------
def test_agregation_depuis_m1() -> None:
    """Les unités supérieures se construisent depuis la seule série M1."""
    index = pd.date_range("2026-01-01", periods=15, freq="min", tz="UTC")
    m1 = pd.DataFrame(
        {
            "open": np.arange(15.0),
            "high": np.arange(15.0) + 0.5,
            "low": np.arange(15.0) - 0.5,
            "close": np.arange(15.0) + 0.2,
            "volume": np.ones(15),
        },
        index=index,
    )
    m5 = bt_data.agreger(m1, "M5")
    assert len(m5) == 3
    assert m5.iloc[0]["open"] == 0.0
    assert m5.iloc[0]["high"] == pytest.approx(4.5)
    assert m5.iloc[0]["low"] == pytest.approx(-0.5)
    assert m5.iloc[0]["close"] == pytest.approx(4.2)
    assert m5.iloc[0]["volume"] == 5.0


def test_fenetre_close_exclut_la_bougie_en_cours() -> None:
    """Une bougie n'existe qu'une fois close : c'est la barrière anti-anticipation."""
    index = pd.date_range("2026-01-01", periods=10, freq="min", tz="UTC")
    m1 = pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=index
    )
    m5 = bt_data.agreger(m1, "M5")
    assert len(m5) == 2

    # À 00:04, la première bougie M5 n'est pas encore close.
    visible = bt_data.fenetre_close(m5, "M5", pd.Timestamp("2026-01-01 00:04", tz="UTC"))
    assert len(visible) == 0

    # À 00:05 pile, elle l'est.
    visible = bt_data.fenetre_close(m5, "M5", pd.Timestamp("2026-01-01 00:05", tz="UTC"))
    assert len(visible) == 1


# ---------------------------------------------------------------------------
# 6. Points de retournement et origine de la jambe
# ---------------------------------------------------------------------------
def test_swings_detectes_avec_la_sensibilite_demandee() -> None:
    """Un sommet doit dépasser strictement ses voisins de chaque côté."""
    # Plus hauts : 1 2 3 4 5 4 3 2 1 → un seul sommet, en position 4.
    hauts = [1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0, 2.0, 1.0]
    cadre = _bougies([(h - 0.5, h, h - 1.0, h - 0.2) for h in hauts])

    sommets, _ = ict.detecter_swings(cadre, sensibilite=4)
    assert sommets == [4]

    # Avec une sensibilité de 2, le même sommet est trouvé, plus tôt confirmé.
    sommets, _ = ict.detecter_swings(cadre, sensibilite=2)
    assert sommets == [4]


def test_swing_rejette_un_palier() -> None:
    """Deux bougies au même plus haut ne font pas deux sommets.

    La comparaison stricte évite qu'un palier découpe la jambe au mauvais
    endroit, voire qu'il en produise plusieurs.
    """
    hauts = [1.0, 2.0, 3.0, 5.0, 5.0, 3.0, 2.0, 1.0, 0.5]
    cadre = _bougies([(h - 0.5, h, h - 1.0, h - 0.2) for h in hauts])
    sommets, _ = ict.detecter_swings(cadre, sensibilite=3)
    assert sommets == []


def test_creux_symetriques_des_sommets() -> None:
    """Les creux se détectent selon la même règle, sur les plus bas."""
    bas = [5.0, 4.0, 3.0, 2.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    cadre = _bougies([(b + 1.0, b + 1.5, b, b + 0.5) for b in bas])
    _, creux = ict.detecter_swings(cadre, sensibilite=4)
    assert creux == [4]


def test_origine_de_jambe_part_du_dernier_retournement() -> None:
    """La jambe part du **dernier** retournement, pas du plus lointain.

    C'est toute la différence entre mesurer le dernier segment directionnel
    et mesurer tout l'historique : un mouvement qui hésite longtemps avant de
    partir franchement ne doit pas voir ses hésitations comptées.
    """
    # Deux creux successifs : un profond en position 4, un plus proche en
    # position 12. La jambe montant vers une zone de vente doit partir du
    # second, plus récent, même s'il est moins bas.
    bas = [9, 7, 5, 3, 1, 3, 5, 7, 9, 8, 7, 6, 4, 6, 8, 10, 12, 14, 16, 18]
    cadre = _bougies([(b + 1.0, b + 1.5, float(b), b + 0.5) for b in bas])

    depart = ict.origine_de_jambe(cadre, ict.BAISSIER, sensibilite=4)
    assert depart == 12, f"Départ en {depart} : ce n'est pas le dernier creux."

    # Le creux profond existe bien, mais il n'est pas retenu.
    _, creux = ict.detecter_swings(cadre, sensibilite=4)
    assert 4 in creux and 12 in creux


def test_origine_de_jambe_choisit_le_bon_sens() -> None:
    """Une zone de vente part d'un creux, une zone d'achat d'un sommet."""
    valeurs = [9, 7, 5, 3, 1, 3, 5, 7, 9, 11, 13, 11, 9, 7, 5, 3, 1, 0, -1, -2]
    cadre = _bougies([(v + 1.0, v + 1.5, float(v), v + 0.5) for v in valeurs])

    # Zone de vente : le prix monte vers elle, la jambe part d'un creux.
    depart_vente = ict.origine_de_jambe(cadre, ict.BAISSIER, sensibilite=4)
    # Zone d'achat : le prix descend vers elle, la jambe part d'un sommet.
    depart_achat = ict.origine_de_jambe(cadre, ict.HAUSSIER, sensibilite=4)

    sommets, creux = ict.detecter_swings(cadre, sensibilite=4)
    assert depart_vente in creux
    assert depart_achat in sommets
    assert depart_vente != depart_achat


def test_swing_non_confirme_est_ecarte() -> None:
    """Un retournement trop récent pour être confirmé n'existe pas encore.

    Un swing en position ``i`` exige ``k`` bougies à sa droite : il n'est
    connu qu'en ``i + k``. Le retenir plus tôt serait lire l'avenir, et c'est
    exactement la fuite que ce test verrouille.
    """
    # Creux en position 4, avec seulement deux bougies à sa droite.
    bas = [9.0, 7.0, 5.0, 3.0, 1.0, 3.0, 5.0]
    cadre = _bougies([(b + 1.0, b + 1.5, b, b + 0.5) for b in bas])

    # Sensibilité 2 : le creux est confirmé en position 6, la dernière.
    assert ict.origine_de_jambe(cadre, ict.BAISSIER, sensibilite=2) == 4

    # Sensibilité 4 : il faudrait la position 8, qui n'existe pas encore.
    assert ict.origine_de_jambe(cadre, ict.BAISSIER, sensibilite=4) is None


def test_origine_absente_sur_serie_sans_retournement() -> None:
    """Une série monotone n'offre aucun point de départ."""
    cadre = _bougies([(float(i), i + 1.0, float(i), i + 0.5) for i in range(20)])
    assert ict.origine_de_jambe(cadre, ict.BAISSIER, sensibilite=4) is None


def test_sensibilite_change_le_decoupage() -> None:
    """Une sensibilité plus fine repère des retournements plus proches.

    C'est le mécanisme par lequel ce paramètre agit sur la classification :
    une jambe plus courte contient moins de bougies contraires, donc a plus
    de chances d'être jugée violente.
    """
    valeurs = [9, 7, 5, 6, 5, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24]
    cadre = _bougies([(v + 1.0, v + 1.5, float(v), v + 0.5) for v in valeurs])

    fine = ict.origine_de_jambe(cadre, ict.BAISSIER, sensibilite=2)
    grossiere = ict.origine_de_jambe(cadre, ict.BAISSIER, sensibilite=4)

    # Les deux trouvent un départ, mais pas nécessairement le même.
    assert fine is not None
    if grossiere is not None:
        assert fine >= grossiere, (
            "Une sensibilité fine doit repérer un retournement au moins aussi "
            "récent qu'une sensibilité grossière."
        )
