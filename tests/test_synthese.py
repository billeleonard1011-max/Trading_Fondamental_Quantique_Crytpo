"""Tests des paragraphes de synthèse de rubrique.

Ce que ces tests figent, dans l'ordre des règles de rédaction :

1. **Aucun chiffre sans sa conséquence** — un nombre cité doit être suivi,
   dans la même phrase ou la suivante, de ce qu'il implique pour le marché.
   Un tableau déguisé en prose est refusé.
2. **Aucun chiffre inventé, aucune recommandation** — les deux garde-fous
   déjà en place ailleurs dans le projet s'appliquent aux quatre rubriques.
3. **Le récit long terme s'appuie sur les séries** — quand le texte parle de
   six mois ou d'un an, la donnée correspondante existe ; il n'est jamais
   forcé quand elle manque.
4. **Le facteur commun est repris partout où il compte** — pas de nombre
   minimum imposé, mais dès qu'un appétit pour le risque est mesuré, l'or,
   le quantique et la crypto en tirent chacun leur propre conséquence.
5. **Jamais vide** — avec un indicateur principal inexploitable ou une
   source manquante, le paragraphe reste substantiel.
6. **Le texte suit les données** — changer une donnée change le paragraphe.

Aucun test n'accède au réseau.

Exécution :
    pytest tests/test_synthese.py -v
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from modules import synthese


# ---------------------------------------------------------------------------
# Fabriques
# ---------------------------------------------------------------------------
def _contexte_macro(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "disponible": True,
        "regime": {"axes": {
            "appetit_risque": {"disponible": True, "valeur": -0.42, "niveau": "risk-off", "commentaire": ""},
            "inflation": {"disponible": True, "valeur": 2.4, "niveau": "proche de la cible", "commentaire": ""},
            "liquidite_nette": {"disponible": True, "valeur": 6100.0, "niveau": "en baisse", "commentaire": ""},
            "stress_credit": {"disponible": True, "valeur": 62.0, "niveau": "normal", "commentaire": ""},
        }},
        "recul": {
            "disponible": True,
            "taux_reels": {"actuel_pct": 2.43, "il_y_a_6_mois_pct": 2.11, "ecart_points_base": 32.0},
            "dollar": {"actuel": 121.4, "variation_6_mois_pct": -2.3},
            "inflation": {"actuel_pct": 2.4, "il_y_a_6_mois_pct": 2.9, "ecart_points": -0.5},
            "vix": {"actuel": 16.5, "moyenne_6_mois": 18.2, "ecart_moyenne_pct": -9.3},
        },
    }
    base.update(overrides)
    return base


def _rapport_or(**overrides: Any) -> dict[str, Any]:
    """Rapport or complet, tel que le publie modules.gold.run."""
    base: dict[str, Any] = {
        "meta": {"date": "2026-09-11"},
        "prix": {
            "disponible": True, "variation_5j_pct": 1.6, "variation_20j_pct": 0.8,
            "variation_63j_pct": 4.1, "variation_126j_pct": 11.7, "variation_252j_pct": 28.4,
            "plus_haut_252j": 4480.0, "plus_bas_252j": 3490.0, "position_intervalle_252j_pct": 93.0,
        },
        "juste_valeur": {"disponible": True, "fiable": True, "z_score": 1.82, "r2": 0.71, "seuil_r2": 0.50},
        "positionnement_cot": {
            "disponible": True, "percentile_managed_money": 73.5,
            "variation_hebdo_managed_money": -7976, "n_semaines_percentile": 260,
        },
        "calendrier": {"echeances": [{"nom": "Décision de la Réserve fédérale", "minutes_restantes": 2880}]},
        "contexte_macro": _contexte_macro(),
        "geopolitique": {
            "disponible": True, "intensite_max": 2.4, "dossier_dominant": "israel_gaza",
            "dossiers": [
                {
                    "id": "israel_gaza", "nom_affiche": "Israël - Gaza", "disponible": True,
                    "intensite_ratio": 2.4, "trajectoire": "en accélération",
                    "n_nouveaux_developpements": 3, "n_evenements_bilateraux": 6,
                    "chaine_de_transmission": {"chaine_rompue": False, "n_maillons_mesures": 4},
                },
                {
                    "id": "russie_ukraine", "nom_affiche": "Russie - Ukraine", "disponible": True,
                    "intensite_ratio": 0.9, "trajectoire": "en essoufflement",
                    "n_nouveaux_developpements": 0, "n_evenements_bilateraux": 2,
                    "chaine_de_transmission": {
                        "chaine_rompue": True, "n_maillons_mesures": 4,
                        "maillons": {"2_petrole": {"disponible": True, "variation": -3.2, "unite_variation": "%"}},
                    },
                },
            ],
            "deja_dans_les_prix": {"disponible": True, "valeur": True, "z_score_prime": 1.82},
        },
        "biais": {
            "biais": "haussier", "score_composite": 0.184, "conviction": "moyenne",
            "couverture_donnees": 0.85,
            "composantes": [
                {"nom": "confirmation_minieres", "disponible": True, "contribution": 0.1429},
                {"nom": "positionnement_cot", "disponible": True, "contribution": -0.1005},
                {"nom": "tendance_dollar", "disponible": True, "contribution": 0.0999},
            ],
        },
    }
    base.update(overrides)
    return base


def _rapport_quantique(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "prix": {
            "disponible": True,
            "variations_du_jour": {"RGTI": 1.2, "QBTS": -0.4, "IONQ": 0.3},
            "variations_longues": {
                "RGTI": {"variation_3_mois_pct": 18.4, "variation_6_mois_pct": 22.1},
                "QBTS": {"variation_3_mois_pct": -5.2, "variation_6_mois_pct": 9.7},
                "IONQ": {"variation_3_mois_pct": 2.1, "variation_6_mois_pct": 14.3},
            },
        },
        "contexte_macro": {"disponible": True, "variation_taux_reels_points": 0.05, "variation_marche_pct": -0.4},
        "mouvements": {
            "n_mouvements": 2,
            "mouvements": [
                {"ticker": "RGTI", "variation_pct": 11.4, "classification": "sectoriel"},
                {"ticker": "IONQ", "variation_pct": -9.2, "classification": "specifique"},
            ],
        },
        "secteur": {
            "correlation_positions": {
                "disponible": True, "correlation_max": 0.83, "correlation_elevee": True, "n_seances_effectives": 60,
            },
            "tresorerie": {"RGTI": {"runway_trimestres": 6.5}, "QBTS": {"runway_trimestres": 3.2}},
        },
        "facteur_commun": synthese.facteur_commun(_rapport_or()),
    }
    base.update(overrides)
    return base


def _rapport_crypto(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "regime": {
            "regime_btc": {"disponible": True, "regime": "expansion", "mvrv": 2.35},
            "regime_eth": {"disponible": True, "regime": "accumulation", "mvrv": 1.42},
        },
        "rotation": {"synthese": {
            "etat": "rotation_alts",
            "contributions": [{"mesure": "largeur_marche", "vote": "rotation_alts"}],
        }},
        "positionnement": {
            "positions": {"positions": [
                {"symbole": "BTC", "variation_24h_pct": 2.1, "variation_30j_pct": 6.8},
                {"symbole": "ETH", "variation_24h_pct": -1.3, "variation_30j_pct": -3.4},
            ]},
            "funding": {"disponible": True, "percentile": 94.0},
        },
        "facteur_commun": synthese.facteur_commun(_rapport_or()),
    }
    base.update(overrides)
    return base


def _tous() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("or", synthese.synthetiser_or(_rapport_or())),
        ("geopolitique", synthese.synthetiser_geopolitique(_rapport_or())),
        ("quantique", synthese.synthetiser_quantique(_rapport_quantique())),
        ("crypto", synthese.synthetiser_crypto(_rapport_crypto())),
    ]


def _phrases(texte: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+", texte) if p.strip()]


# ---------------------------------------------------------------------------
# 1. Aucun chiffre sans sa conséquence
# ---------------------------------------------------------------------------
def test_chaque_chiffre_cite_porte_une_consequence_marche() -> None:
    """La règle centrale de la refonte : un chiffre nu est un tableau déguisé."""
    for nom, resultat in _tous():
        assert resultat["publiable"], f"{nom} : {resultat['motif']}"
        fautives = synthese.phrases_sans_consequence(resultat["texte"])
        assert fautives == [], f"{nom} — chiffre sans conséquence : {fautives}"


def test_le_detecteur_de_consequence_mord_vraiment() -> None:
    assert synthese.phrases_sans_consequence("Le VIX est à 16,5. Il fait beau.") == ["Le VIX est à 16,5."]
    assert synthese.phrases_sans_consequence("Le VIX est à 16,5, ce qui signale une prime de risque faible.") == []
    # La conséquence peut arriver dans la phrase suivante.
    assert synthese.phrases_sans_consequence("Le VIX est à 16,5. Cela soutient les actifs risqués.") == []


def test_aucune_definition_dindicateur_a_la_place_de_son_impact() -> None:
    """« Le COT mesure… » n'apprend rien ; « les spéculateurs sont peu engagés » si."""
    texte = synthese.synthetiser_or(_rapport_or())["texte"].lower()
    for definition in ("mesure le positionnement", "est un indicateur", "se définit comme", "désigne le rapport"):
        assert definition not in texte, f"définition à la place d'un impact : « {definition} »"
    assert "spéculateurs" in texte and any(m in texte for m in ("encombre", "marge", "sans excès"))


# ---------------------------------------------------------------------------
# 2. Aucun chiffre inventé, aucune recommandation
# ---------------------------------------------------------------------------
def test_les_quatre_syntheses_ne_contiennent_que_des_chiffres_des_donnees() -> None:
    for nom, resultat in _tous():
        assert resultat["publiable"], f"{nom} : {resultat['motif']}"
        assert resultat["nombres_rejetes"] == []


def test_un_chiffre_absent_des_donnees_fait_rejeter_le_paragraphe() -> None:
    resultat = synthese.verifier_synthese("Le score atteint 42,7 points, ce qui pèse.", {"score": 3.1})
    assert not resultat["publiable"]
    assert 42.7 in resultat["nombres_rejetes"]
    assert resultat["texte"] == ""


def test_aucune_synthese_ne_contient_de_motif_de_recommandation() -> None:
    for nom, resultat in _tous():
        assert resultat["infractions"] == [], f"{nom} : {resultat['infractions']}"


def test_la_synthese_quantique_ne_hierarchise_jamais_les_societes() -> None:
    texte = synthese.synthetiser_quantique(_rapport_quantique())["texte"].lower()
    for jugement in (
        "le plus légitime", "la plus légitime", "le meilleur", "la meilleure", "le mieux placé",
        "la mieux placée", "le plus solide", "la plus solide", "à privilégier", "le plus prometteur",
    ):
        assert jugement not in texte, f"jugement de valeur : « {jugement} »"


def test_le_garde_fou_de_recommandation_mord_vraiment() -> None:
    resultat = synthese.verifier_synthese("Il faut acheter maintenant.", {})
    assert not resultat["publiable"]
    assert resultat["infractions"]


# ---------------------------------------------------------------------------
# 3. Le récit long terme s'appuie sur les séries
# ---------------------------------------------------------------------------
def test_le_recit_a_un_an_cite_les_variations_reellement_publiees() -> None:
    texte = synthese.synthetiser_or(_rapport_or())["texte"]
    assert "Sur un an" in texte and "six mois" in texte
    # Les nombres cités sont ceux des champs de profondeur, pas des inventions.
    assert "28,4 %" in texte and "11,7 %" in texte and "93 %" in texte


def test_sans_profondeur_le_recit_ne_force_pas_les_six_mois() -> None:
    """Pas de seuil imposé : quand les séries manquent, le long terme n'apparaît pas."""
    court = _rapport_or(
        prix={"disponible": True, "variation_5j_pct": 1.6, "variation_20j_pct": 0.8},
        contexte_macro=_contexte_macro(recul={"disponible": False}),
    )
    texte = synthese.synthetiser_or(court)["texte"]
    assert "Sur un an" not in texte
    assert "six mois" not in texte
    assert "vingt séances" in texte


def test_le_recul_macro_cite_reellement_les_series() -> None:
    texte = synthese.synthetiser_or(_rapport_or())["texte"]
    assert "32 points de base" in texte          # taux_reels.ecart_points_base
    assert "2,9 % à 2,4 %" in texte              # inflation il_y_a_6_mois -> actuel
    assert "affaibli de 2,3 %" in texte          # dollar.variation_6_mois_pct


def test_aucun_evenement_narratif_invente() -> None:
    """Rien du type « suite aux tensions de février » : les données ne le montrent pas."""
    for nom, resultat in _tous():
        texte = resultat["texte"].lower()
        for invention in ("suite aux tensions", "après l'annonce", "depuis la crise", "en février", "en mars", "cet été"):
            assert invention not in texte, f"{nom} — événement inventé : « {invention} »"


# ---------------------------------------------------------------------------
# 4. Le facteur commun est repris dans chaque rubrique concernée
# ---------------------------------------------------------------------------
def test_un_appetit_mesure_est_repris_par_les_trois_actifs_avec_leur_propre_effet() -> None:
    """Un même fait, trois conséquences — jamais mentionné dans une seule rubrique."""
    or_ = synthese.synthetiser_or(_rapport_or())["texte"]
    quantique = synthese.synthetiser_quantique(_rapport_quantique())["texte"]
    crypto = synthese.synthetiser_crypto(_rapport_crypto())["texte"]
    for texte in (or_, quantique, crypto):
        assert "risk-off" in texte, "le climat mesuré doit être repris"
    # Effets spécifiques, pas une phrase copiée-collée.
    assert "valeur refuge" in or_
    assert "profits lointains" in quantique
    assert "assèche" in crypto or "cryptos avant les autres" in crypto


def test_sans_appetit_mesure_aucune_rubrique_ne_fabrique_de_lien() -> None:
    """Réciproque : pas de facteur commun inventé les jours où il n'y en a pas."""
    sans = _rapport_or(contexte_macro={"disponible": False})
    or_ = synthese.synthetiser_or(sans)["texte"]
    quantique = synthese.synthetiser_quantique(
        _rapport_quantique(facteur_commun=synthese.facteur_commun(sans))
    )["texte"]
    for texte in (or_, quantique):
        assert "climat de marché" not in texte
        assert "risk-off" not in texte and "risk-on" not in texte


def test_facteur_commun_est_vide_mais_sain_sans_rapport_or() -> None:
    facteur = synthese.facteur_commun({})
    assert facteur["disponible"] is False
    assert facteur["appetit"] is None


def test_charger_rapport_or_degrade_sans_lever(tmp_path: Path) -> None:
    assert synthese.charger_rapport_or(tmp_path / "absent.json") == {}
    (tmp_path / "casse.json").write_text("{pas du json", encoding="utf-8")
    assert synthese.charger_rapport_or(tmp_path / "casse.json") == {}


# ---------------------------------------------------------------------------
# 5. Jamais vide
# ---------------------------------------------------------------------------
def _substantiel(resultat: dict[str, Any], minimum: int = 3) -> None:
    assert resultat["publiable"], resultat["motif"]
    phrases = _phrases(resultat["texte"])
    assert len(phrases) >= minimum, f"paragraphe trop maigre ({len(phrases)} phrase(s)) : {resultat['texte']}"
    assert not all("indisponible" in p.lower() or "inexploitable" in p.lower() for p in phrases)


def test_or_reste_substantiel_avec_le_modele_de_juste_valeur_inexploitable() -> None:
    degrade = _rapport_or(
        juste_valeur={"disponible": True, "fiable": False, "z_score": 0.5, "r2": 0.19, "seuil_r2": 0.5},
    )
    resultat = synthese.synthetiser_or(degrade)
    _substantiel(resultat)
    assert "seuil de fiabilité" in resultat["texte"]      # dit en une incise
    assert resultat["texte"].count("fiabilité") == 1      # et une seule


def test_or_reste_substantiel_sans_cot_ni_geopolitique() -> None:
    degrade = _rapport_or(
        positionnement_cot={"disponible": False, "motif": "CFTC indisponible"},
        geopolitique={"disponible": False, "motif": "GDELT indisponible"},
    )
    _substantiel(synthese.synthetiser_or(degrade))


def test_geopolitique_nomme_le_maillon_qui_rompt_la_chaine() -> None:
    """« Rompue » ne suffit pas : le lecteur doit savoir où, et de combien."""
    texte = synthese.synthetiser_geopolitique(_rapport_or())["texte"]
    assert "le pétrole (-3,20 %)" in texte
    assert "cohérente" in texte and "rompue" in texte


def test_une_chaine_non_mesuree_est_dite_telle_quelle() -> None:
    rapport = _rapport_or()
    for d in rapport["geopolitique"]["dossiers"]:
        d["chaine_de_transmission"] = {"chaine_rompue": None, "n_maillons_mesures": 0}
    texte = synthese.synthetiser_geopolitique(rapport)["texte"]
    assert "n'est mesurable pour aucun dossier" in texte
    assert "rompue" not in texte


def test_geopolitique_reste_substantielle_sans_aucun_dossier() -> None:
    degrade = _rapport_or(geopolitique={
        "disponible": False, "motif": "GDELT indisponible",
        "deja_dans_les_prix": {"disponible": True, "valeur": False, "z_score_prime": 0.4},
    })
    resultat = synthese.synthetiser_geopolitique(degrade)
    _substantiel(resultat, minimum=2)
    assert "prime de risque" in resultat["texte"]


def test_quantique_reste_substantiel_sans_aucun_mouvement_du_jour() -> None:
    """« Aucune valeur ne dépasse son seuil » ne peut plus être tout le paragraphe."""
    plat = _rapport_quantique(mouvements={"n_mouvements": 0, "mouvements": []})
    resultat = synthese.synthetiser_quantique(plat)
    _substantiel(resultat, minimum=4)
    assert "Sur six mois" in resultat["texte"]
    assert "risk-off" in resultat["texte"]


def test_crypto_reste_substantielle_sans_regime() -> None:
    degrade = _rapport_crypto(regime={"regime_btc": {"disponible": False}, "regime_eth": {"disponible": False}})
    _substantiel(synthese.synthetiser_crypto(degrade))


def test_un_rapport_vide_refuse_avec_un_motif() -> None:
    for fn in (synthese.synthetiser_or, synthese.synthetiser_geopolitique,
               synthese.synthetiser_quantique, synthese.synthetiser_crypto):
        resultat = fn({})
        assert not resultat["publiable"]
        assert resultat["motif"]


# ---------------------------------------------------------------------------
# 6. Le paragraphe suit les données
# ---------------------------------------------------------------------------
def test_changer_le_z_score_change_la_synthese_or() -> None:
    cher = synthese.synthetiser_or(_rapport_or())["texte"]
    bon_marche = synthese.synthetiser_or(_rapport_or(
        juste_valeur={"disponible": True, "fiable": True, "z_score": -1.9, "r2": 0.71, "seuil_r2": 0.50},
    ))["texte"]
    assert cher != bon_marche
    assert "statistiquement cher" in cher and "bon marché" in bon_marche


def test_changer_lappetit_change_les_trois_rubriques_en_meme_temps() -> None:
    or_off = synthese.synthetiser_or(_rapport_or())["texte"]
    rapport_on = _rapport_or(contexte_macro=_contexte_macro(regime={"axes": {
        "appetit_risque": {"disponible": True, "valeur": 0.55, "niveau": "risk-on", "commentaire": ""},
    }}))
    or_on = synthese.synthetiser_or(rapport_on)["texte"]
    crypto_on = synthese.synthetiser_crypto(
        _rapport_crypto(facteur_commun=synthese.facteur_commun(rapport_on))
    )["texte"]
    assert or_off != or_on
    assert "détourne des flux de la valeur refuge" in or_on
    assert "alimente ici les actifs les plus spéculatifs" in crypto_on


def test_changer_le_positionnement_cot_change_la_consequence() -> None:
    encombre = synthese.synthetiser_or(_rapport_or(positionnement_cot={
        "disponible": True, "percentile_managed_money": 88.0, "variation_hebdo_managed_money": 1200,
    }))["texte"]
    degage = synthese.synthetiser_or(_rapport_or(positionnement_cot={
        "disponible": True, "percentile_managed_money": 12.0, "variation_hebdo_managed_money": -300,
    }))["texte"]
    assert "encombre" in encombre and "88e percentile" in encombre
    assert "marge" in degage and "12e percentile" in degage


def test_changer_le_mvrv_change_la_synthese_crypto() -> None:
    avant = synthese.synthetiser_crypto(_rapport_crypto())["texte"]
    apres = synthese.synthetiser_crypto(_rapport_crypto(regime={
        "regime_btc": {"disponible": True, "regime": "capitulation", "mvrv": 0.82},
        "regime_eth": {"disponible": True, "regime": "capitulation", "mvrv": 0.91},
    }))["texte"]
    assert avant != apres and "perte latente" in apres


# ---------------------------------------------------------------------------
# 7. Forme
# ---------------------------------------------------------------------------
def test_aucun_identifiant_technique_brut_dans_les_syntheses() -> None:
    for nom, resultat in _tous():
        for brut in ("rotation_alts", "dominance_btc", "israel_gaza", "regime_btc", "appetit_risque",
                     "z_score", "correlation_max", "confirmation_minieres", "positionnement_cot"):
            assert brut not in resultat["texte"], f"{nom} — identifiant technique : « {brut} »"


def test_les_faits_sont_chaines_par_causalite() -> None:
    """Un récit, pas une liste : les connecteurs causaux sont partout."""
    for nom, resultat in _tous():
        phrases = _phrases(resultat["texte"])
        causales = sum(1 for p in phrases if any(m in p.lower() for m in ("ce qui", "donc", "c'est", "cela")))
        assert causales >= len(phrases) * 0.6, f"{nom} — trop peu de liens causaux ({causales}/{len(phrases)})"
