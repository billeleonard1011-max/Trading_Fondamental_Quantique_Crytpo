"""Tests des garde-fous du moteur or : géopolitique, biais, explications.

Trois mécanismes sont vérifiés ici, chacun étant un endroit où le système
peut mentir sans qu'on s'en aperçoive :

1. ``deja_dans_les_prix`` — croiser l'ancienneté d'un thème avec la prime
   déjà payée. C'est la thèse centrale du système ; si ce croisement est
   faux, le biais recommande d'acheter ce qui a déjà monté.
2. **La renormalisation des pondérations** — une composante muette ne doit
   pas être comptée comme un avis neutre, sinon l'absence d'information
   ressemble à un signal d'équilibre.
3. **Le vérificateur numérique des explications** — un nombre inventé par un
   modèle de langage est indétectable à la lecture et parfaitement crédible.

Aucun test n'accède au réseau : GDELT et OpenAI sont remplacés par des
données et un client factices.

Exécution :
    pytest tests/test_gold_engine.py -v
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from modules.gold import bias, explain, geopolitics


# ---------------------------------------------------------------------------
# Fabriques
# ---------------------------------------------------------------------------
def _volumes(profil: list[float], depart: str = "2026-08-08") -> dict[str, float]:
    """Fabrique une série de volumes journaliers GDELT.

    Args:
        profil: volumes, du plus ancien au plus récent.
        depart: première date.

    Returns:
        Dictionnaire date ``AAAA-MM-JJ`` vers volume.
    """
    dates = pd.date_range(depart, periods=len(profil), freq="D")
    return {d.strftime("%Y-%m-%d"): float(v) for d, v in zip(dates, profil)}


def _intensite(ratio: float | None, disponible: bool = True) -> dict[str, Any]:
    """Fabrique une sortie de ``news.gdelt_intensity``."""
    return {
        "query": "test",
        "volume_24h": 100.0,
        "moyenne_journaliere_30j": 50.0,
        "ratio": ratio,
        "alerte": bool(ratio and ratio >= 2.0),
        "jours_observes": 30,
        "disponible": disponible,
        "commentaire": "",
    }


# ---------------------------------------------------------------------------
# 1. Trajectoire et ancienneté d'un thème
# ---------------------------------------------------------------------------
def test_trajectoire_detecte_acceleration_et_essoufflement() -> None:
    """La trajectoire compare les trois derniers jours aux quatre précédents."""
    acceleration = geopolitics._trajectoire(_volumes([10, 10, 10, 10, 40, 40, 40]))
    assert acceleration[0] == "en accélération"
    assert acceleration[1] == pytest.approx(4.0)

    essoufflement = geopolitics._trajectoire(_volumes([40, 40, 40, 40, 10, 10, 10]))
    assert essoufflement[0] == "en essoufflement"

    stable = geopolitics._trajectoire(_volumes([20, 20, 20, 20, 20, 20, 20]))
    assert stable[0] == "stable"
    assert stable[1] == pytest.approx(1.0)


def test_trajectoire_refuse_une_fenetre_trop_courte() -> None:
    """Moins de cinq jours ne permet pas de comparer deux sous-périodes."""
    assert geopolitics._trajectoire(_volumes([10, 20, 30])) == ("", None)


def test_anciennete_compte_les_jours_au_dessus_de_la_mediane() -> None:
    """L'ancienneté est le nombre de jours consécutifs au-dessus de la normale."""
    # Dix jours calmes puis quatre jours élevés : ancienneté de quatre jours.
    profil = [10.0] * 10 + [50.0] * 4
    assert geopolitics._anciennete(_volumes(profil)) == 4

    # Série trop courte : on ne devine pas.
    assert geopolitics._anciennete(_volumes([10.0] * 5)) is None

    # Le dernier jour retombe sous la médiane : l'ancienneté est nulle.
    assert geopolitics._anciennete(_volumes([10.0] * 10 + [50.0] * 3 + [1.0])) == 0


def test_analyser_theme_sans_reseau() -> None:
    """Un thème se calcule entièrement à partir de données injectées."""
    theme = geopolitics.analyser_theme(
        "Test",
        "requête",
        volumes=_volumes([10.0] * 10 + [50.0] * 4),
        intensite=_intensite(2.5),
    )
    assert theme.disponible
    assert theme.intensite_ratio == 2.5
    assert theme.alerte
    assert theme.trajectoire == "en accélération"
    assert theme.anciennete_jours == 4


# ---------------------------------------------------------------------------
# 2. deja_dans_les_prix : la thèse centrale du système
# ---------------------------------------------------------------------------
def test_theme_installe_et_prime_elevee_est_deja_paye() -> None:
    """Thème ancien + prime tendue : le catalyseur haussier a disparu."""
    theme = geopolitics.Theme(
        nom="Conflit installé", disponible=True, anciennete_jours=21, intensite_ratio=2.0
    )
    resultat = geopolitics._deja_dans_les_prix([theme], z_score_prime=2.0)

    assert resultat["valeur"] is True
    assert resultat["disponible"]
    assert "Conflit installé" in resultat["themes_installes"]
    assert "asymétrie" in resultat["commentaire"]


def test_theme_recent_meme_intense_nest_pas_deja_paye() -> None:
    """Une prime élevée sans thème installé ne s'explique pas par la géopolitique."""
    theme = geopolitics.Theme(nom="Choc récent", disponible=True, anciennete_jours=2)
    resultat = geopolitics._deja_dans_les_prix([theme], z_score_prime=2.0)

    assert resultat["valeur"] is False
    assert "Partiellement" in resultat["commentaire"]


def test_theme_installe_mais_prime_faible_nest_pas_deja_paye() -> None:
    """Un thème ancien que le marché ne valorise pas reste un catalyseur possible."""
    theme = geopolitics.Theme(nom="Tension ancienne", disponible=True, anciennete_jours=30)
    resultat = geopolitics._deja_dans_les_prix([theme], z_score_prime=0.2)

    assert resultat["valeur"] is False
    assert "ne le valorise pas encore" in resultat["commentaire"]


def test_sans_z_score_on_refuse_de_conclure() -> None:
    """Sans mesure de la prime, la question n'a pas de réponse."""
    theme = geopolitics.Theme(nom="Thème", disponible=True, anciennete_jours=30)
    resultat = geopolitics._deja_dans_les_prix([theme], z_score_prime=None)

    assert resultat["valeur"] is None
    assert not resultat["disponible"]
    assert resultat["motif"]


def test_chaine_de_transmission_signale_une_rupture() -> None:
    """Une chaîne dont un maillon va à contresens est signalée comme rompue."""
    dates = pd.bdate_range("2026-08-01", periods=30, name="date")
    # Pétrole en hausse et anticipations en hausse, mais taux réels en hausse
    # aussi : le maillon 4 contredit le schéma attendu.
    macro_series = pd.DataFrame(
        {
            "DCOILWTICO": np.linspace(70.0, 80.0, 30),
            "T10YIE": np.linspace(2.0, 2.3, 30),
            "DFII10": np.linspace(0.5, 0.9, 30),
        },
        index=dates,
    )
    prix_or = pd.Series(np.linspace(4000.0, 4200.0, 30), index=dates)

    chaine = geopolitics.chaine_de_transmission(macro_series, prix_or, intensite_max=2.0)
    assert chaine["chaine_rompue"] is True
    assert chaine["n_maillons_conformes"] < chaine["n_maillons_mesures"]
    # Le libellé lisible du maillon en rupture apparaît, jamais son
    # identifiant technique brut — même catégorie de bug que les
    # identifiants non traduits ailleurs sur le site.
    assert "Taux réel 10 ans" in chaine["commentaire"]
    assert "4_taux_reels" not in chaine["commentaire"]
    # La formulation vague est bannie : le maillon en rupture est cité avec
    # sa variation mesurée, jamais une affirmation sans chiffre.
    assert "points de base" in chaine["commentaire"]
    assert "canaux habituels" not in chaine["commentaire"]

    # Les taux se lisent en points de base, jamais en pourcentage.
    assert chaine["maillons"]["4_taux_reels"]["unite_variation"] == "points de base"
    assert chaine["maillons"]["2_petrole"]["unite_variation"] == "%"


def test_chaine_de_transmission_sans_donnees() -> None:
    """Sans série, chaque maillon est marqué indisponible avec son motif."""
    chaine = geopolitics.chaine_de_transmission(None, None, intensite_max=None)
    assert chaine["chaine_rompue"] is None
    assert chaine["n_maillons_mesures"] == 0
    assert not chaine["maillons"]["2_petrole"]["disponible"]
    assert chaine["maillons"]["2_petrole"]["motif"]


# ---------------------------------------------------------------------------
# 3. Biais : renormalisation et décomposition
# ---------------------------------------------------------------------------
_CONFIG_BIAIS = {
    "ponderations": {
        "ecart_juste_valeur": 0.30,
        "positionnement_cot": 0.15,
        "dynamique_taux_reels": 0.20,
        "tendance_dollar": 0.15,
        "intensite_geopolitique": 0.10,
        "confirmation_minieres": 0.10,
    },
    "seuil_haussier": 0.20,
    "seuil_vendeur": -0.20,
    "couverture_min_pour_conviction": 0.60,
}


def _fv(z: float, fiable: bool = True) -> dict[str, Any]:
    """Fabrique un bloc de juste valeur."""
    return {
        "disponible": True,
        "fiable": fiable,
        "z_score": z,
        "r2": 0.75,
        "seuil_r2": 0.5,
        "prix_observe": 4400.0,
        "prix_theorique": 4000.0,
        "motif": "",
    }


def test_composante_indisponible_ne_compte_pas_comme_neutre() -> None:
    """Une source muette est retirée du dénominateur, pas comptée comme zéro.

    Avec une seule composante disponible et fortement négative, le score
    composite doit valoir ce score, et non une fraction diluée par cinq
    composantes absentes traitées comme neutres.
    """
    resultat = bias.calculer_biais(_CONFIG_BIAIS, fair_value=_fv(2.0))

    composante = next(c for c in resultat["composantes"] if c["nom"] == "ecart_juste_valeur")
    assert composante["disponible"]
    assert composante["poids_effectif"] == pytest.approx(1.0)
    assert resultat["score_composite"] == pytest.approx(composante["score"])
    assert resultat["score_composite"] == pytest.approx(-1.0)

    # La couverture reste basse et le signale.
    assert resultat["couverture_donnees"] == pytest.approx(0.30)
    assert resultat["donnees_partielles"]
    assert "partielles" in resultat["avertissement"]


def test_somme_des_contributions_egale_le_score() -> None:
    """La décomposition doit reconstituer exactement le score composite."""
    resultat = bias.calculer_biais(
        _CONFIG_BIAIS,
        fair_value=_fv(1.0),
        cot={"disponible": True, "percentile_managed_money": 80.0, "age_jours": 4},
        delta_taux_reels=-0.10,
        delta_dollar=-1.0,
        flux={"ratio_gdx_or": {"disponible": True, "variation_pct": 4.0}},
    )
    somme = sum(c["contribution"] for c in resultat["composantes"] if c["disponible"])
    assert somme == pytest.approx(resultat["score_composite"], abs=1e-9)

    poids = sum(c["poids_effectif"] for c in resultat["composantes"] if c["disponible"])
    assert poids == pytest.approx(1.0, abs=1e-9)


def test_juste_valeur_et_cot_sont_contrariens() -> None:
    """Une prime tendue et un positionnement saturé donnent un score négatif."""
    resultat = bias.calculer_biais(
        _CONFIG_BIAIS,
        fair_value=_fv(2.5),
        cot={"disponible": True, "percentile_managed_money": 95.0, "age_jours": 3},
    )
    scores = {c["nom"]: c["score"] for c in resultat["composantes"] if c["disponible"]}
    assert scores["ecart_juste_valeur"] < 0.0
    assert scores["positionnement_cot"] < 0.0
    assert resultat["biais"] == "vendeur"


def test_modele_non_fiable_desactive_la_composante() -> None:
    """Un R² sous le seuil rend le z-score inutilisable : la composante s'efface."""
    resultat = bias.calculer_biais(_CONFIG_BIAIS, fair_value=_fv(2.0, fiable=False))
    composante = next(c for c in resultat["composantes"] if c["nom"] == "ecart_juste_valeur")
    assert not composante["disponible"]
    assert "non fiable" in composante["motif"]


def test_geopolitique_deja_payee_inverse_le_signe() -> None:
    """Une intensité forte mais déjà dans les prix cesse d'être haussière."""
    intense = {"disponible": True, "intensite_max": 2.5, "deja_dans_les_prix": {"valeur": False}}
    deja_paye = {"disponible": True, "intensite_max": 2.5, "deja_dans_les_prix": {"valeur": True}}

    score_intense = bias._score_geopolitique(intense)[0]
    score_deja = bias._score_geopolitique(deja_paye)[0]

    assert score_intense is not None and score_intense > 0.0
    assert score_deja is not None and score_deja < 0.0


def test_aucune_composante_donne_un_biais_indetermine() -> None:
    """Sans aucune donnée, le module ne produit pas un « neutre » trompeur."""
    resultat = bias.calculer_biais(_CONFIG_BIAIS)
    assert resultat["biais"] == "indeterminé"
    assert resultat["score_composite"] == 0.0
    assert resultat["couverture_donnees"] == 0.0
    assert len(resultat["composantes_indisponibles"]) == len(bias.COMPOSANTES)


def test_invalidations_donnent_des_niveaux_chiffres() -> None:
    """Les conditions d'invalidation doivent être vérifiables sur un graphique."""
    resultat = bias.calculer_biais(
        _CONFIG_BIAIS,
        fair_value=_fv(2.0),
        cot={"disponible": True, "percentile_managed_money": 90.0, "age_jours": 3},
        delta_taux_reels=-0.05,
    )
    variables = {i["variable"]: i for i in resultat["invalidations"]}
    assert "prix de l'or" in variables
    assert variables["prix de l'or"]["seuil"] == pytest.approx(4000.0)
    assert all(isinstance(i["seuil"], float) for i in resultat["invalidations"])


# ---------------------------------------------------------------------------
# 4. Garde-fou numérique des explications
# ---------------------------------------------------------------------------
class _ClientFactice:
    """Client OpenAI factice, qui renvoie des textes décidés à l'avance.

    Permet de vérifier tout le circuit de génération, de vérification et de
    repli sans aucun appel réseau.
    """

    def __init__(self, reponses: list[str]) -> None:
        self._reponses = list(reponses)
        self.appels: list[list[dict[str, str]]] = []
        self.chat = self

    @property
    def completions(self) -> "_ClientFactice":
        """Imite l'arborescence ``client.chat.completions``."""
        return self

    def create(self, **kwargs: Any) -> Any:
        """Renvoie la prochaine réponse programmée."""
        self.appels.append(kwargs["messages"])
        texte = self._reponses.pop(0) if self._reponses else ""

        class _Message:
            content = texte

        class _Choix:
            message = _Message()

        class _Reponse:
            choices = [_Choix()]

        return _Reponse()


_DONNEES = {
    "disponible": True,
    "z_score": 1.7649,
    "r2": 0.7231,
    "ecart_pct": 12.4,
    "lecture": "L'or cote 12,4 % au-dessus de sa juste valeur.",
}


def test_texte_exact_est_accepte() -> None:
    """Un texte n'utilisant que des chiffres présents passe la vérification."""
    client = _ClientFactice(
        ["L'or dépasse sa juste valeur de 12,4 %, soit 1,76 écart-type. "
         "Le modèle explique 0,72 des mouvements."]
    )
    resultat = explain.expliquer("juste_valeur", _DONNEES, client=client)

    assert resultat.mode == "openai"
    assert resultat.tentatives == 1
    assert resultat.nombres_rejetes == []


def test_arrondi_raisonnable_est_accepte() -> None:
    """« environ 1,8 » pour 1,7649 est un arrondi, pas une invention."""
    client = _ClientFactice(["L'écart atteint environ 1,8 écart-type."])
    resultat = explain.expliquer("juste_valeur", _DONNEES, client=client)
    assert resultat.mode == "openai"


def test_nombre_hallucine_declenche_une_seconde_tentative() -> None:
    """Un chiffre inventé est rejeté, puis le modèle est relancé avec un rappel."""
    client = _ClientFactice(
        [
            "L'or est au 94e percentile de son histoire.",   # 94 n'existe pas
            "L'or dépasse sa juste valeur de 12,4 %.",       # correction acceptable
        ]
    )
    resultat = explain.expliquer("juste_valeur", _DONNEES, client=client)

    assert resultat.mode == "openai"
    assert resultat.tentatives == 2
    assert len(client.appels) == 2
    # Le second appel doit rappeler explicitement le nombre fautif.
    rappel = client.appels[1][-1]["content"]
    assert "94" in rappel


def test_deux_echecs_font_tomber_sur_le_gabarit() -> None:
    """Après deux textes non conformes, on publie le gabarit, pas le texte."""
    client = _ClientFactice(
        [
            "L'or est au 94e percentile.",
            "En réalité il est au 88e percentile depuis 47 séances.",
        ]
    )
    resultat = explain.expliquer("juste_valeur", _DONNEES, client=client)

    assert resultat.mode == "gabarit"
    assert "vérification numérique échouée" in resultat.motif_repli
    assert resultat.texte
    # Le gabarit reprend la lecture rédigée à côté du calcul : elle est exacte.
    assert "12,4 %" in resultat.texte


def test_gabarit_produit_quatre_parties() -> None:
    """Le mode dégradé respecte le même découpage en quatre parties."""
    resultat = explain._gabarit("juste_valeur", _DONNEES, "test")
    assert resultat.mode == "gabarit"
    assert len(resultat.texte.split("\n\n")) == 4


def test_gabarit_dit_explicitement_quand_la_donnee_manque() -> None:
    """Une donnée absente est annoncée, pas contournée."""
    resultat = explain._gabarit(
        "positionnement_cot",
        {"disponible": False, "motif": "API CFTC injoignable"},
        "clé absente",
    )
    assert "indisponible" in resultat.texte.lower()
    assert "API CFTC injoignable" in resultat.texte


def test_absence_de_cle_bascule_en_gabarit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans OPENAI_API_KEY, aucun appel n'est tenté."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    resultat = explain.expliquer("juste_valeur", _DONNEES)
    assert resultat.mode == "gabarit"
    assert "OPENAI_API_KEY" in resultat.motif_repli


def test_extraction_gere_les_formats_francais_et_anglais() -> None:
    """Les nombres s'écrivent « 1 234,5 » ou « 1,234.5 » selon la locale."""
    assert explain.extraire_nombres("Le prix est de 4 429,80 dollars.") == [4429.80]
    assert explain.extraire_nombres("Le prix est de 4,429.80 dollars.") == [4429.80]
    assert explain.extraire_nombres("Écart de -0,9 % et de +2,5 %.") == [-0.9, 2.5]


def test_numerotation_de_liste_nest_pas_prise_pour_une_donnee() -> None:
    """« 2. » en début de ligne est un marqueur de liste, pas une affirmation."""
    texte = "1. Premier point sur l'or.\n2. Second point.\n- Troisième point."
    assert explain.extraire_nombres(texte) == []


def test_valeurs_autorisees_incluent_les_chiffres_des_commentaires() -> None:
    """Les commentaires déjà rédigés contiennent des chiffres exacts, citables."""
    autorisees = explain.valeurs_autorisees({"lecture": "L'or cote 12,4 % plus haut."})
    assert any(abs(v - 12.4) < 1e-9 for v in autorisees)
