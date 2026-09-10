"""Tests des paragraphes de synthèse de rubrique.

Trois garanties sont vérifiées ici, et ce sont les trois qui distinguent un
paragraphe composé d'un habillage rédactionnel :

1. **aucun chiffre inventé** — chaque nombre du paragraphe existe dans les
   données de l'exécution, via le vérificateur déjà utilisé pour les
   explications du moteur or ;
2. **aucune recommandation** — même liste de motifs que partout ailleurs,
   avec une attention particulière à la section quantique, où comparer trois
   sociétés invite à trancher ;
3. **le texte suit les données** — changer une donnée source change le
   paragraphe. C'est ce qui prouve qu'il n'est pas figé.

Aucun test n'accède au réseau.

Exécution :
    pytest tests/test_synthese.py -v
"""

from __future__ import annotations

import re
from typing import Any

from modules import synthese


# ---------------------------------------------------------------------------
# Fabriques
# ---------------------------------------------------------------------------
def _rapport_or(**overrides: Any) -> dict[str, Any]:
    """Rapport or minimal mais complet, tel que le publie modules.gold.run."""
    base: dict[str, Any] = {
        "prix": {"disponible": True, "variation_5j_pct": 1.6, "variation_20j_pct": 0.8},
        "juste_valeur": {
            "disponible": True, "fiable": True, "z_score": 1.82,
            "r2": 0.71, "seuil_r2": 0.50,
        },
        "geopolitique": {
            "disponible": True,
            "intensite_max": 2.4,
            "dossier_dominant": "israel_gaza",
            "dossiers": [
                {
                    "id": "israel_gaza", "nom_affiche": "Israël - Gaza", "disponible": True,
                    "intensite_ratio": 2.4, "trajectoire": "en accélération",
                    "n_nouveaux_developpements": 3,
                    "chaine_de_transmission": {"chaine_rompue": False},
                },
                {
                    "id": "russie_ukraine", "nom_affiche": "Russie - Ukraine", "disponible": True,
                    "intensite_ratio": 0.9, "trajectoire": "en essoufflement",
                    "n_nouveaux_developpements": 0,
                    "chaine_de_transmission": {"chaine_rompue": True},
                },
            ],
            "deja_dans_les_prix": {
                "disponible": True, "valeur": True, "z_score_prime": 1.82,
            },
        },
        "contexte_macro": {
            "regime": {"axes": {"appetit_risque": {
                "disponible": True, "valeur": -0.42, "niveau": "risk-off",
            }}},
        },
        "biais": {
            "biais": "haussier", "score_composite": 0.184,
            "conviction": "moyenne", "couverture_donnees": 0.85,
        },
    }
    base.update(overrides)
    return base


def _rapport_quantique(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "mouvements": {
            "n_mouvements": 2,
            "mouvements": [
                {"ticker": "RGTI", "variation_pct": 11.4, "classification": "sectoriel"},
                {"ticker": "IONQ", "variation_pct": -9.2, "classification": "specifique"},
            ],
        },
        "secteur": {"correlation_positions": {
            "disponible": True, "correlation_max": 0.83, "n_seances_effectives": 60,
        }},
        "tresorerie": {
            "RGTI": {"runway_trimestres": 6.5},
            "QBTS": {"runway_trimestres": 4.2},
        },
        "industrie": {"n_articles": 14},
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
                {"symbole": "BTC", "variation_24h_pct": 2.1},
                {"symbole": "ETH", "variation_24h_pct": -1.3},
            ]},
            "funding": {"disponible": True, "percentile": 94.0},
        },
    }
    base.update(overrides)
    return base


def _phrases(texte: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+", texte) if p.strip()]


# ---------------------------------------------------------------------------
# 1. Aucun chiffre absent des données du jour
# ---------------------------------------------------------------------------
def test_les_quatre_syntheses_ne_contiennent_que_des_chiffres_des_donnees() -> None:
    """Le garde-fou numérique s'applique aux quatre rubriques."""
    for resultat in (
        synthese.synthetiser_or(_rapport_or()),
        synthese.synthetiser_geopolitique(_rapport_or()),
        synthese.synthetiser_quantique(_rapport_quantique()),
        synthese.synthetiser_crypto(_rapport_crypto()),
    ):
        assert resultat["publiable"], resultat["motif"]
        assert resultat["nombres_rejetes"] == []


def test_un_chiffre_absent_des_donnees_fait_rejeter_le_paragraphe() -> None:
    """Le vérificateur doit vraiment mordre, pas seulement être branché."""
    resultat = synthese.verifier_synthese(
        "Le score atteint 42,7 points cette semaine.", {"score": 3.1},
    )
    assert not resultat["publiable"]
    assert 42.7 in resultat["nombres_rejetes"]
    assert resultat["texte"] == "", "un paragraphe rejeté ne doit pas être publié quand même"


def test_un_paragraphe_rejete_publie_son_motif_pas_un_texte_corrige() -> None:
    # 37,4 n'a aucune correspondance, même après les déclinaisons d'arrondi
    # que valeurs_autorisees() applique volontairement (un ratio de 0,15 peut
    # s'écrire « 15 % », d'où le choix d'une valeur franchement à l'écart).
    resultat = synthese.verifier_synthese("Une valeur de 37,4 %.", {"reel": 0.15})
    assert not resultat["publiable"]
    assert resultat["motif"]
    assert "37,4" not in resultat["texte"]


# ---------------------------------------------------------------------------
# 2. Aucune formulation de recommandation
# ---------------------------------------------------------------------------
def test_aucune_synthese_ne_contient_de_motif_de_recommandation() -> None:
    for resultat in (
        synthese.synthetiser_or(_rapport_or()),
        synthese.synthetiser_geopolitique(_rapport_or()),
        synthese.synthetiser_quantique(_rapport_quantique()),
        synthese.synthetiser_crypto(_rapport_crypto()),
    ):
        assert resultat["infractions"] == [], resultat["infractions"]


def test_la_synthese_quantique_ne_hierarchise_jamais_les_societes() -> None:
    """Comparer trois sociétés invite à trancher : la ligne est ici.

    Décrire des faits (mouvement, corrélation, trésorerie) est permis ;
    désigner « la mieux placée » serait un conseil déguisé.
    """
    texte = synthese.synthetiser_quantique(_rapport_quantique())["texte"]
    for jugement in (
        "le plus légitime", "la plus légitime", "le meilleur", "la meilleure",
        "le mieux placé", "la mieux placée", "le plus solide", "la plus solide",
        "à privilégier", "le plus prometteur",
    ):
        assert jugement not in texte.lower(), f"jugement de valeur : « {jugement} »"


def test_le_garde_fou_de_recommandation_mord_vraiment() -> None:
    resultat = synthese.verifier_synthese("Il faut acheter maintenant.", {})
    assert not resultat["publiable"]
    assert resultat["infractions"]


# ---------------------------------------------------------------------------
# 3. Le paragraphe suit les données — il n'est pas figé
# ---------------------------------------------------------------------------
def test_changer_le_z_score_change_la_synthese_or() -> None:
    """Le test qui prouve que ce n'est pas un texte statique."""
    cher = synthese.synthetiser_or(_rapport_or())["texte"]
    bon_marche = synthese.synthetiser_or(_rapport_or(
        juste_valeur={"disponible": True, "fiable": True, "z_score": -1.9,
                      "r2": 0.71, "seuil_r2": 0.50},
    ))["texte"]

    assert cher != bon_marche
    assert "statistiquement cher" in cher
    assert "bon marché" in bon_marche


def test_changer_lappetit_pour_le_risque_change_la_synthese_or() -> None:
    avant = synthese.synthetiser_or(_rapport_or())["texte"]
    apres = synthese.synthetiser_or(_rapport_or(
        contexte_macro={"regime": {"axes": {"appetit_risque": {
            "disponible": True, "valeur": 0.55, "niveau": "risk-on",
        }}}},
    ))["texte"]
    assert avant != apres
    assert "risk-off" in avant
    assert "risk-on" in apres


def test_changer_la_trajectoire_change_la_synthese_geopolitique() -> None:
    rapport = _rapport_or()
    avant = synthese.synthetiser_geopolitique(rapport)["texte"]

    modifie = _rapport_or()
    for dossier in modifie["geopolitique"]["dossiers"]:
        dossier["trajectoire"] = "stable"
    apres = synthese.synthetiser_geopolitique(modifie)["texte"]

    assert avant != apres
    assert "s'essoufflent" in avant
    assert "s'essoufflent" not in apres


def test_changer_le_nombre_de_mouvements_change_la_synthese_quantique() -> None:
    avec = synthese.synthetiser_quantique(_rapport_quantique())["texte"]
    sans = synthese.synthetiser_quantique(_rapport_quantique(
        mouvements={"n_mouvements": 0, "mouvements": []},
    ))["texte"]
    assert avec != sans
    assert "Aucune des valeurs suivies" in sans


def test_changer_le_mvrv_change_la_synthese_crypto() -> None:
    avant = synthese.synthetiser_crypto(_rapport_crypto())["texte"]
    apres = synthese.synthetiser_crypto(_rapport_crypto(
        regime={
            "regime_btc": {"disponible": True, "regime": "capitulation", "mvrv": 0.82},
            "regime_eth": {"disponible": True, "regime": "capitulation", "mvrv": 0.91},
        },
    ))["texte"]
    assert avant != apres
    assert "capitulation" in apres


# ---------------------------------------------------------------------------
# 4. Dégradation
# ---------------------------------------------------------------------------
def test_sans_donnees_la_synthese_refuse_de_conclure() -> None:
    for fn in (synthese.synthetiser_or, synthese.synthetiser_geopolitique,
               synthese.synthetiser_quantique, synthese.synthetiser_crypto):
        resultat = fn({})
        assert not resultat["publiable"]
        assert resultat["motif"], "un refus doit toujours dire pourquoi"


def test_aucun_identifiant_technique_brut_dans_les_syntheses() -> None:
    """Même règle que partout : un identifiant interne ne s'affiche pas."""
    textes = [
        synthese.synthetiser_or(_rapport_or())["texte"],
        synthese.synthetiser_geopolitique(_rapport_or())["texte"],
        synthese.synthetiser_quantique(_rapport_quantique())["texte"],
        synthese.synthetiser_crypto(_rapport_crypto())["texte"],
    ]
    for texte in textes:
        for brut in ("rotation_alts", "dominance_btc", "israel_gaza", "russie_ukraine",
                     "regime_btc", "appetit_risque", "z_score", "correlation_max"):
            assert brut not in texte, f"identifiant technique affiché : « {brut} »"


def test_chaque_phrase_des_syntheses_porte_un_chiffre_ou_un_fait_nomme() -> None:
    """Pas de phrase de remplissage : un chiffre, ou un nom propre concret."""
    for texte in (
        synthese.synthetiser_or(_rapport_or())["texte"],
        synthese.synthetiser_geopolitique(_rapport_or())["texte"],
    ):
        for phrase in _phrases(texte):
            assert re.search(r"\d", phrase), f"phrase sans chiffre : « {phrase} »"
