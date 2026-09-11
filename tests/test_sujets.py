"""Tests de la découverte et du classement des sujets géopolitiques.

Ce que ces tests figent :

1. **Découverte sans liste préétablie** — une paire de pays absente de toute
   configuration remonte quand même de l'export Events ; une paire couverte
   par un dossier bilatéral ou régional y est rattachée, jamais perdue.
2. **Pertinence marché robuste** — un sujet n'est classé que sur assez de
   séances et de pics ; en deçà, l'insuffisance est écrite. Quand les pics
   coïncident avec de grandes séances, le sujet « réagit » ; quand rien ne
   bouge, il est « inerte ».
3. **Classement, pas filtre** — actif, veille, candidat, épinglé ; un
   dossier configuré peut être rétrogradé en veille.
4. **Filet « Autres »** — ce qui ne relève d'aucun dossier y atterrit s'il
   est significatif ; le bruit (petits comptes, couverture ordinaire) reste
   dehors.
5. **Le cas de l'attaque au Moyen-Orient** — une paire Yémen–Arabie saoudite
   se rattache au dossier régional, pas à « Autres », et la chaîne pétrole
   la chiffre.
6. **Dossiers thématiques** — même garde-fous : chiffre avec conséquence,
   dégradation gracieuse, canal de transmission cohérent.

Aucun test n'accède au réseau.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from modules import synthese
from modules.geopolitique import sujets
from modules.gold import geopolitics


# ---------------------------------------------------------------------------
# Fabriques
# ---------------------------------------------------------------------------
def _ligne(a: str, b: str, quad: str = "4", mentions: int = 5, goldstein: float = -7.0) -> list[str]:
    ligne = [""] * 61
    ligne[7], ligne[17], ligne[26], ligne[29] = a, b, "190", quad
    ligne[30], ligne[31], ligne[34], ligne[60] = str(goldstein), str(mentions), "-4.0", "https://exemple.test/e"
    return ligne


def _export(paires: dict[tuple[str, str], int], quad: str = "4") -> list[list[str]]:
    lignes: list[list[str]] = []
    for (a, b), n in paires.items():
        lignes.extend(_ligne(a, b, quad=quad) for _ in range(n))
    return lignes


def _dossiers_cfg() -> list[dict[str, Any]]:
    return [
        {"id": "israel_gaza", "nom_affiche": "Israël - Gaza", "acteurs_gdelt": ["ISR", "PSE"], "mots_cles": ["Gaza"]},
        {"id": "moyen_orient", "nom_affiche": "Moyen-Orient", "type": "regional", "canal": "petrole",
         "pays": ["ISR", "PSE", "IRN", "SAU", "YEM"], "mots_cles": ["Middle East"]},
        {"id": "politique_monetaire", "nom_affiche": "Politique monétaire", "type": "thematique",
         "canal": "taux", "epingle": True, "mots_cles": ["Federal Reserve"]},
    ]


#: Intensité « GDELT muet », pour que mesurer_dossier ne parte jamais sur le réseau.
_MUET = {"disponible": False, "commentaire": "GDELT non interrogé (test hors ligne)"}


def _marches(n: int = 40, pics: list[int] | None = None, amplitude: float = 3.0) -> tuple[pd.DataFrame, dict[str, float]]:
    """Marché calme, sauf les jours de pic où la couverture et les prix s'emballent."""
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2026-07-01", periods=n)
    couverture = np.full(n, 20.0)
    petrole = rng.normal(0.0, 0.3, n)
    or_ = rng.normal(0.0, 0.2, n)
    for i in pics or []:
        couverture[i] = 120.0
        petrole[i] = amplitude
        or_[i] = amplitude / 2
    marches = pd.DataFrame({"petrole": petrole, "or": or_}, index=dates)
    volumes = {d.strftime("%Y-%m-%d"): float(v) for d, v in zip(dates, couverture)}
    return marches, volumes


# ---------------------------------------------------------------------------
# 1. Découverte
# ---------------------------------------------------------------------------
def test_une_paire_inconnue_de_toute_configuration_remonte_quand_meme() -> None:
    export = _export({("YEM", "SAU"): 12, ("ISR", "PSE"): 9, ("PRK", "KOR"): 15})
    candidats = sujets.candidats_evenements(export, _dossiers_cfg())
    noms = [c["libelle"] for c in candidats]
    assert "South Korea – North Korea" in noms      # codes triés : KOR avant PRK
    coree = next(c for c in candidats if c["paire"] == ["KOR", "PRK"])
    assert coree["rattachement"] is None and coree["requete"] == '"South Korea" "North Korea"'


def test_une_paire_couverte_par_un_dossier_bilateral_y_est_rattachee() -> None:
    candidats = sujets.candidats_evenements(_export({("PSE", "ISR"): 10}), _dossiers_cfg())
    assert candidats[0]["rattachement"] == {"type": "dossier", "id": "israel_gaza", "nom_affiche": "Israël - Gaza"}


def test_une_paire_de_la_region_se_rattache_au_dossier_regional() -> None:
    """Le cas de l'exemple : Yémen–Arabie saoudite n'est pas bilatéral, mais régional."""
    candidats = sujets.candidats_evenements(_export({("YEM", "SAU"): 10}), _dossiers_cfg())
    assert candidats[0]["rattachement"]["id"] == "moyen_orient"


def test_seuls_les_evenements_de_conflit_comptent() -> None:
    coop = _export({("FRA", "DEU"): 30}, quad="1")      # coopération verbale
    assert sujets.candidats_evenements(coop, []) == []


def test_un_export_vide_ne_produit_aucun_candidat() -> None:
    assert sujets.candidats_evenements([], _dossiers_cfg()) == []


# ---------------------------------------------------------------------------
# 2. Pertinence marché
# ---------------------------------------------------------------------------
def test_trop_peu_de_seances_communes_interdit_de_classer() -> None:
    marches, volumes = _marches(n=8, pics=[2])
    p = sujets.pertinence_marche(volumes, marches)
    assert p["disponible"] is False and p["suffisant"] is False
    assert "20 requises" in p["motif"] and p["n_observations"] == 8


def test_trop_peu_de_pics_interdit_de_classer() -> None:
    marches, volumes = _marches(n=40, pics=[10])
    p = sujets.pertinence_marche(volumes, marches)
    assert p["disponible"] is False and "3 requis" in p["motif"]


def test_des_pics_qui_coincident_avec_de_grandes_seances_donnent_reagit() -> None:
    marches, volumes = _marches(n=40, pics=[5, 12, 20, 33], amplitude=4.0)
    p = sujets.pertinence_marche(volumes, marches)
    assert p["disponible"] and p["lecture"] == "réagit"
    assert p["score"] >= sujets.SEUIL_REAGIT and p["n_pics"] == 4
    # Chaque chiffre du commentaire est dans le dictionnaire.
    conforme, rejetes = synthese.verifier_nombres(p["commentaire"], p)
    assert conforme, rejetes


def test_un_classement_sur_peu_de_pics_est_publie_avec_sa_fiabilite_faible() -> None:
    """Le piège des 37 trades : classer, oui, mais en disant sur quoi ça repose."""
    marches, volumes = _marches(n=40, pics=[5, 12, 20, 33], amplitude=4.0)
    p = sujets.pertinence_marche(volumes, marches)
    assert p["fiabilite"] == "faible" and "Fiabilité faible" in p["commentaire"]
    marches, volumes = _marches(n=60, pics=[3, 9, 15, 22, 30, 41, 50], amplitude=4.0)
    p = sujets.pertinence_marche(volumes, marches)
    assert p["fiabilite"] == "correcte" and "Fiabilité faible" not in p["commentaire"]


def test_des_pics_sans_mouvement_donnent_inerte() -> None:
    marches, volumes = _marches(n=40, pics=[5, 12, 20, 33], amplitude=0.0)
    p = sujets.pertinence_marche(volumes, marches)
    assert p["disponible"] and p["lecture"] == "inerte"


def test_sans_series_de_marche_la_pertinence_est_indisponible_avec_motif() -> None:
    _, volumes = _marches(n=40, pics=[5, 12, 20])
    p = sujets.pertinence_marche(volumes, pd.DataFrame())
    assert p["disponible"] is False and "marché" in p["motif"]


def test_marches_journaliers_se_derive_des_series_fred_et_de_lor() -> None:
    dates = pd.bdate_range("2026-08-01", periods=10)
    fred = pd.DataFrame({"DCOILWTICO": np.linspace(70, 80, 10), "DFII10": np.linspace(2.0, 2.2, 10)}, index=dates)
    or_ = pd.Series(np.linspace(4000, 4100, 10), index=dates)
    m = sujets.marches_journaliers(fred, or_)
    assert set(m.columns) == {"petrole", "taux_reels", "or"}
    assert sujets.marches_journaliers(None, None).empty


# ---------------------------------------------------------------------------
# 3. Classement
# ---------------------------------------------------------------------------
def test_le_classement_distingue_actif_veille_candidat_et_epingle() -> None:
    classe = sujets.classer([
        {"nom": "A", "epingle": False, "intensite_ratio": 1.0, "pertinence": {"disponible": True, "lecture": "inerte", "score": 0.6}},
        {"nom": "B", "epingle": False, "intensite_ratio": 2.0, "pertinence": {"disponible": True, "lecture": "réagit", "score": 2.1}},
        {"nom": "C", "epingle": False, "intensite_ratio": 3.0, "pertinence": {"disponible": False, "motif": "trop court"}},
        {"nom": "D", "epingle": True, "intensite_ratio": 0.5, "pertinence": {"disponible": False}},
    ])
    # Par pertinence mesurée : l'épingle garantit le suivi, pas le rang.
    assert [(c["nom"], c["statut"], c["rang"]) for c in classe] == [
        ("B", "actif", 1), ("A", "veille", 2), ("C", "candidat", 3), ("D", "epingle", 4),
    ]
    assert classe[2]["donnees_suffisantes"] is False and classe[3]["donnees_suffisantes"] is False


def test_un_dossier_configure_peut_etre_retrograde_en_veille() -> None:
    """Les dossiers écrits à la main n'ont aucun statut privilégié."""
    marches, volumes = _marches(n=40, pics=[5, 12, 20, 33], amplitude=0.0)
    dossier = geopolitics.mesurer_dossier(
        {"id": "russie_ukraine", "nom_affiche": "Russie - Ukraine", "acteurs_gdelt": ["RUS", "UKR"], "mots_cles": ["Ukraine"]},
        set(), volumes=volumes, intensite={"disponible": True, "ratio": 1.0, "volume_24h": 20.0, "alerte": False},
        articles=[], lignes_events=[], motif_events="",
    )
    bloc = geopolitics.analyser_dossiers(dossiers_precalcules=[dossier], marches=marches, mesurer_candidats=False)
    d = bloc["dossiers"][0]
    assert d["classement"]["statut"] == "veille"
    assert d["pertinence"]["lecture"] == "inerte"
    assert bloc["classement"][0]["nom"] == "Russie - Ukraine"


# ---------------------------------------------------------------------------
# 4. Filet « Autres »
# ---------------------------------------------------------------------------
def test_un_evenement_hors_dossier_atterrit_dans_autres_et_nest_pas_perdu() -> None:
    export = _export({("PRK", "KOR"): 15, ("ISR", "PSE"): 5})
    dossier = geopolitics.mesurer_dossier(_dossiers_cfg()[0], set(), volumes={}, intensite=_MUET, articles=[], lignes_events=export, motif_events="")
    bloc = geopolitics.analyser_dossiers(
        dossiers_precalcules=[dossier], lignes_events=export, marches=pd.DataFrame(), mesurer_candidats=False,
    )
    assert [a["libelle"] for a in bloc["autres"]] == ["South Korea – North Korea"]
    assert "événements de conflit" in bloc["autres"][0]["critere"]
    assert bloc["autres"][0]["pertinence"]["disponible"] is False      # pas mesuré : dit, pas inventé


def test_le_bruit_ne_remonte_pas_dans_autres() -> None:
    """Deux événements entre deux pays, ou une couverture ordinaire : pas significatif."""
    retenu, critere = sujets.est_significatif({"type": "paire", "n_evenements": 2, "part": 0.01, "intensite_ratio": None})
    assert retenu is False and "seuils" in critere
    retenu, _ = sujets.est_significatif({"type": "theme", "intensite_ratio": 1.1})
    assert retenu is False
    retenu, critere = sujets.est_significatif({"type": "theme", "intensite_ratio": 2.3})
    assert retenu is True and "2,3×" in critere


def test_une_paire_rattachee_a_un_dossier_ne_va_jamais_dans_autres() -> None:
    export = _export({("YEM", "SAU"): 40})
    dossiers = [
        geopolitics.mesurer_dossier(cfg, set(), volumes={}, intensite=_MUET, articles=[], lignes_events=export, motif_events="")
        for cfg in _dossiers_cfg()
    ]
    bloc = geopolitics.analyser_dossiers(
        dossiers_precalcules=dossiers, lignes_events=export, marches=pd.DataFrame(), mesurer_candidats=False,
    )
    assert bloc["autres"] == []
    regional = next(d for d in bloc["dossiers"] if d["id"] == "moyen_orient")
    assert regional["sujets_rattaches"][0]["libelle"] == "Saudi Arabia – Yemen"


# ---------------------------------------------------------------------------
# 5. L'attaque au Moyen-Orient, de bout en bout
# ---------------------------------------------------------------------------
def test_une_attaque_qui_fait_monter_le_petrole_est_chiffree_par_la_chaine_du_dossier_regional() -> None:
    dates = pd.bdate_range("2026-08-01", periods=30)
    petrole = np.full(30, 80.0); petrole[-5:] = [90.0, 96.0, 101.0, 103.0, 104.0]      # +30 % en cinq séances
    fred = pd.DataFrame({
        "DCOILWTICO": petrole,
        "T10YIE": np.linspace(2.3, 2.5, 30),        # anticipations en hausse
        "DFII10": np.linspace(2.1, 1.9, 30),        # taux réels en baisse
    }, index=dates)
    prix_or = pd.Series(np.linspace(4000.0, 4200.0, 30), index=dates)
    export = _export({("YEM", "SAU"): 40})

    regional = geopolitics.mesurer_dossier(
        _dossiers_cfg()[1], set(), volumes={}, intensite=_MUET, articles=[], lignes_events=export, motif_events="",
        series_macro=fred, prix_or=prix_or,
    )
    assert regional.type == "regional" and regional.n_evenements_bilateraux == 40
    chaine = regional.chaine_transmission
    assert chaine["canal"] == "petrole" and chaine["chaine_rompue"] is False
    assert chaine["maillons"]["2_petrole"]["variation"] > 20.0

    bloc = geopolitics.analyser_dossiers(
        dossiers_precalcules=[regional], lignes_events=export, marches=pd.DataFrame(), mesurer_candidats=False,
    )
    assert bloc["autres"] == [], "l'attaque doit être dans le dossier régional, pas dans Autres"
    assert bloc["dossiers"][0]["sujets_rattaches"][0]["n_evenements"] == 40


# ---------------------------------------------------------------------------
# 6. Dossiers thématiques
# ---------------------------------------------------------------------------
def test_le_canal_taux_est_coherent_quand_taux_reels_et_or_divergent() -> None:
    dates = pd.bdate_range("2026-08-01", periods=30)
    fred = pd.DataFrame({"DFII10": np.linspace(2.0, 2.4, 30)}, index=dates)     # taux réels en hausse
    prix_or = pd.Series(np.linspace(4200.0, 4000.0, 30), index=dates)            # or en baisse
    chaine = geopolitics.chaine_de_transmission(fred, prix_or, intensite_max=1.2, canal="taux")
    assert set(chaine["maillons"]) == {"1_evenement", "4_taux_reels", "5_or"}
    assert chaine["chaine_rompue"] is False
    inverse = geopolitics.chaine_de_transmission(fred, pd.Series(np.linspace(4000.0, 4200.0, 30), index=dates), canal="taux")
    assert inverse["chaine_rompue"] is True


def test_le_canal_risque_lit_le_vix_et_reste_gracieux_sans_serie() -> None:
    dates = pd.bdate_range("2026-08-01", periods=30)
    fred = pd.DataFrame({"VIXCLS": np.linspace(15.0, 25.0, 30)}, index=dates)
    prix_or = pd.Series(np.linspace(4000.0, 4100.0, 30), index=dates)
    chaine = geopolitics.chaine_de_transmission(fred, prix_or, canal="risque")
    assert "3_vix" in chaine["maillons"] and chaine["chaine_rompue"] is False
    sans = geopolitics.chaine_de_transmission(None, None, canal="risque")
    assert sans["chaine_rompue"] is None and "non mesurable" in sans["commentaire"]


def test_un_dossier_thematique_muet_se_degrade_sans_exception() -> None:
    dossier = geopolitics.mesurer_dossier(
        _dossiers_cfg()[2], set(), volumes={},
        intensite={"disponible": False, "commentaire": "GDELT n'a pas répondu."},
        articles=[], lignes_events=[], motif_events="",
    )
    assert dossier.type == "thematique" and dossier.canal == "taux" and dossier.epingle is True
    assert dossier.theme.disponible is False
    assert "pas d'activité par acteur" in dossier.motif_events
    bloc = geopolitics.analyser_dossiers(dossiers_precalcules=[dossier], marches=pd.DataFrame(), mesurer_candidats=False)
    assert bloc["dossiers"][0]["classement"]["statut"] == "epingle"


def test_la_synthese_geopolitique_reprend_le_classement_avec_ses_garde_fous() -> None:
    rapport = {
        "geopolitique": {
            "disponible": True, "dossiers": [], "motif": "aucun dossier mesurable",
            "classement": [
                {"nom": "Sanctions", "statut": "actif", "donnees_suffisantes": True,
                 "pertinence": {"disponible": True, "score": 1.9, "n_observations": 27}},
                {"nom": "Russie - Ukraine", "statut": "veille", "donnees_suffisantes": True,
                 "pertinence": {"disponible": True, "score": 0.6, "lecture": "inerte"}},
                {"nom": "Israël - Gaza", "statut": "veille", "donnees_suffisantes": True,
                 "pertinence": {"disponible": True, "score": 1.42, "lecture": "neutre"}},
                {"nom": "Yemen – Saudi Arabia", "statut": "candidat", "donnees_suffisantes": False, "pertinence": {}},
            ],
            "criteres_pertinence": {"reagit": 1.5, "inerte": 0.8},
        },
    }
    resultat = synthese.synthetiser_geopolitique(rapport)
    assert resultat["publiable"], resultat["motif"]
    texte = resultat["texte"]
    assert "Sanctions ressort en tête" in texte and "1,90 fois plus" in texte
    assert "Russie - Ukraine est en veille et inerte" in texte and "intégré dans les prix" in texte
    # Neutre n'est pas inerte : la prose ne doit pas dire « intégré dans les prix » pour 1,42.
    assert "Israël - Gaza (1,42) est en veille" in texte and "dépasser 1,5" in texte
    assert "Yemen – Saudi Arabia" in texte and "observations suffisantes" in texte
    assert synthese.phrases_sans_consequence(texte) == []


# ---------------------------------------------------------------------------
# 7. Une seule requête de couverture : 90 jours pour la pertinence, 30 pour l'intensité
# ---------------------------------------------------------------------------
def test_lintensite_se_lit_sur_trente_jours_et_la_pertinence_sur_toute_la_serie() -> None:
    dates = pd.date_range("2026-06-14", periods=60, freq="D")
    volumes = {d.strftime("%Y-%m-%d"): 100.0 for d in dates[:30]}          # trimestre lointain très couvert
    volumes.update({d.strftime("%Y-%m-%d"): 10.0 for d in dates[30:]})    # mois récent calme
    volumes[dates[-1].strftime("%Y-%m-%d")] = 20.0                          # sauf la dernière journée
    theme = geopolitics.analyser_theme(nom="Test", query="test", volumes=volumes)
    assert theme.disponible
    assert theme.intensite_ratio == 2.0, "le ratio doit être 20/10 (trente jours), pas 20/55 (soixante jours)"
    assert len(theme.volumes) == 60, "la série entière reste disponible pour la pertinence marché"


# ---------------------------------------------------------------------------
# 8. Historique du classement : une ligne par sujet et par jour
# ---------------------------------------------------------------------------
from pathlib import Path


def _classement_du_jour(score_ru: float = 0.6, lecture_ru: str = "inerte") -> list[dict[str, Any]]:
    return [
        {"nom": "Sanctions", "id": "", "origine": "theme_generique", "type": "theme", "statut": "actif", "rang": 1,
         "intensite_ratio": 1.8, "pertinence": {"disponible": True, "score": 1.9, "lecture": "réagit", "fiabilite": "correcte",
                                                 "n_observations": 60, "n_pics": 8}},
        {"nom": "Russie - Ukraine", "id": "russie_ukraine", "origine": "dossier", "type": "conflit", "statut": "veille", "rang": 2,
         "intensite_ratio": 0.4, "pertinence": {"disponible": True, "score": score_ru, "lecture": lecture_ru, "fiabilite": "correcte",
                                                 "n_observations": 60, "n_pics": 10}},
    ]


def test_lhistorique_ecrit_une_ligne_par_sujet_et_par_jour_sans_doublon(tmp_path: Path) -> None:
    fichier = tmp_path / "classement.jsonl"
    assert geopolitics.publier_historique_classement(_classement_du_jour(), "2026-09-11", fichier) == 2
    # Relancer le même jour n'invente pas une deuxième observation.
    assert geopolitics.publier_historique_classement(_classement_du_jour(), "2026-09-11", fichier) == 0
    assert geopolitics.publier_historique_classement(_classement_du_jour(), "2026-09-12", fichier) == 2
    lignes = [l for l in fichier.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lignes) == 4
    historique = geopolitics.charger_historique_classement(fichier)
    assert [e["date"] for e in historique["russie_ukraine"]] == ["2026-09-11", "2026-09-12"]
    assert historique["sujet:Sanctions"][0]["score"] == 1.9


def test_une_ligne_corrompue_nempeche_pas_de_lire_le_reste(tmp_path: Path) -> None:
    fichier = tmp_path / "classement.jsonl"
    fichier.write_text('{"date": "2026-09-10", "cle": "russie_ukraine", "statut": "veille"}\npas du json\n', encoding="utf-8")
    assert list(geopolitics.charger_historique_classement(fichier)) == ["russie_ukraine"]
    assert geopolitics.charger_historique_classement(tmp_path / "absent.jsonl") == {}


def _entrees(*jours: tuple[str, str, str | None, float | None]) -> list[dict[str, Any]]:
    return [{"date": d, "statut": s, "lecture": l, "score": sc} for d, s, l, sc in jours]


def test_le_resume_dit_promu_retrograde_et_depuis_combien_de_jours() -> None:
    passe = _entrees(("2026-09-08", "veille", "inerte", 0.6), ("2026-09-09", "veille", "inerte", 0.5),
                     ("2026-09-10", "veille", "inerte", 0.7))
    encore_inerte = geopolitics.resumer_historique_sujet(passe, "veille", "inerte", 0.65, "2026-09-11")
    assert encore_inerte["changement"] == "stable" and encore_inerte["inerte_depuis_jours"] == 4
    assert encore_inerte["jours_consecutifs_statut"] == 4 and encore_inerte["jours_observes"] == 3
    promu = geopolitics.resumer_historique_sujet(passe, "actif", "réagit", 1.8, "2026-09-11")
    assert promu["changement"] == "promu" and promu["inerte_depuis_jours"] == 0 and promu["jours_consecutifs_statut"] == 1
    retrograde = geopolitics.resumer_historique_sujet(
        _entrees(("2026-09-10", "actif", "réagit", 1.9)), "veille", "neutre", 1.1, "2026-09-11",
    )
    assert retrograde["changement"] == "rétrogradé" and retrograde["statut_precedent"] == "actif"
    assert geopolitics.resumer_historique_sujet([], "candidat", None, None, "2026-09-11")["changement"] == "nouveau"


def test_une_ligne_deja_ecrite_aujourdhui_nest_pas_comptee_comme_passee() -> None:
    passe = _entrees(("2026-09-10", "veille", "inerte", 0.6), ("2026-09-11", "veille", "inerte", 0.6))
    resume = geopolitics.resumer_historique_sujet(passe, "veille", "inerte", 0.6, "2026-09-11")
    assert resume["jours_observes"] == 1 and resume["inerte_depuis_jours"] == 2


def test_la_tendance_du_score_compare_deux_semaines() -> None:
    passe = _entrees(*[(f"2026-08-{d:02d}", "veille", "neutre", 0.9) for d in range(1, 8)],
                     *[(f"2026-08-{d:02d}", "veille", "neutre", 1.3) for d in range(8, 14)])
    resume = geopolitics.resumer_historique_sujet(passe, "veille", "neutre", 1.4, "2026-08-14")
    assert resume["tendance_score"] == "en hausse" and resume["score_moyen_30j"] is not None


def test_le_classement_du_jour_est_annote_par_lhistorique() -> None:
    marches, volumes = _marches(n=40, pics=[5, 12, 20, 33], amplitude=0.0)      # inerte aujourd'hui
    dossier = geopolitics.mesurer_dossier(
        {"id": "russie_ukraine", "nom_affiche": "Russie - Ukraine", "acteurs_gdelt": ["RUS", "UKR"], "mots_cles": ["Ukraine"]},
        set(), volumes=volumes, intensite={"disponible": True, "ratio": 1.0, "volume_24h": 20.0, "alerte": False},
        articles=[], lignes_events=[], motif_events="",
    )
    historique = {"russie_ukraine": _entrees(("2026-09-09", "veille", "inerte", 0.6), ("2026-09-10", "veille", "inerte", 0.5))}
    bloc = geopolitics.analyser_dossiers(
        dossiers_precalcules=[dossier], marches=marches, mesurer_candidats=False,
        historique_classement=historique, date_rapport="2026-09-11",
    )
    entree = bloc["classement"][0]
    assert entree["cle"] == "russie_ukraine" and entree["historique"]["inerte_depuis_jours"] == 3
    assert bloc["dossiers"][0]["classement"]["historique"]["changement"] == "stable"


def test_lhistorique_du_classement_est_fusionne_par_union() -> None:
    """Sans cette ligne, une publication concurrente perdrait un jour de classement."""
    from scripts import fusionner_sorties as fusion

    relatif = geopolitics.FICHIER_HISTORIQUE_CLASSEMENT.relative_to(fusion.RACINE).as_posix()
    assert relatif in fusion.FICHIERS_JSONL


def test_la_synthese_cite_les_mouvements_et_linertie_durable() -> None:
    rapport = {"geopolitique": {
        "disponible": True, "dossiers": [], "motif": "aucun dossier mesurable",
        "classement": [
            {"nom": "Sanctions", "statut": "actif", "donnees_suffisantes": True,
             "pertinence": {"disponible": True, "score": 1.9, "n_observations": 60},
             "historique": {"changement": "promu", "inerte_depuis_jours": 0}},
            {"nom": "Conflits majeurs", "statut": "veille", "donnees_suffisantes": True,
             "pertinence": {"disponible": True, "score": 0.6, "lecture": "inerte"},
             "historique": {"changement": "stable", "inerte_depuis_jours": 15}},
        ],
    }}
    resultat = synthese.synthetiser_geopolitique(rapport)
    assert resultat["publiable"], resultat["motif"]
    assert "Sanctions monte d'un cran" in resultat["texte"]
    assert "inerte depuis 15 jours de classement consécutifs" in resultat["texte"]
    assert synthese.phrases_sans_consequence(resultat["texte"]) == []
