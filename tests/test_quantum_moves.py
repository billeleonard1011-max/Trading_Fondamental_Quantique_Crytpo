"""Tests de la veille quantique : classification et interdiction de conseiller.

Deux propriétés sont vérifiées, et la seconde est la plus importante.

1. **Sectoriel contre spécifique.** Le test central rejoue l'épisode Pasqal
   de début septembre 2026 avec des données synthétiques aux proportions
   réelles : chute d'environ 58 % sur la semaine, autres valeurs quantiques
   en baisse concomitante, taux réels en hausse. Le module doit conclure
   ``sectoriel``. Se tromper ici, c'est attribuer à une société un mouvement
   qui vient des taux, et chercher une cause là où il n'y en a pas.

2. **Aucune recommandation.** Le paquet n'a pas le droit de suggérer d'agir.
   La contrainte est vérifiée par recherche de motifs interdits sur la sortie
   réellement produite, pas sur une intention déclarée.

Aucun test n'accède au réseau.

Exécution :
    pytest tests/test_quantum_moves.py -v
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from modules.quantum import industry, moves

#: Réglages proches de ceux de ``config/universe.yaml``.
_CONFIG = {
    "min_valeurs_concordantes": 2,
    "ratio_amplitude_min": 0.40,
    "seuil_variation_taux_pct": 0.03,
    "serie_taux_reels": "DFII10",
    "reference_marche": "QQQ",
    "seuil_runway_trimestres": 4.0,
    "fenetre_correlation_seances": 60,
    "seuil_correlation_elevee": 0.70,
    "min_mentions_for_alert": 3,
    "incumbents": ["IonQ", "Rigetti", "D-Wave", "IBM"],
}


def _serie(depart: float, taux_journalier: float, n: int = 5) -> pd.DataFrame:
    """Fabrique un historique à variation journalière constante.

    Args:
        depart: cours initial.
        taux_journalier: variation quotidienne, en fraction.
        n: nombre de séances.

    Returns:
        DataFrame au format de :mod:`dataio.market`.
    """
    dates = pd.bdate_range("2026-08-31", periods=n, name="date")
    return pd.DataFrame({"close": depart * (1.0 + taux_journalier) ** np.arange(n)}, index=dates)


def _watchlist(tickers: list[str]) -> list[dict[str, Any]]:
    """Construit une watchlist minimale pour les tests."""
    noms = {
        "PSQL": "Pasqal Holding",
        "RGTI": "Rigetti Computing",
        "QBTS": "D-Wave Quantum",
        "IONQ": "IonQ",
    }
    return [
        {"ticker": t, "name": noms.get(t, t), "seuil_mouvement_pct": 8.0} for t in tickers
    ]


# ---------------------------------------------------------------------------
# 1. L'épisode Pasqal
# ---------------------------------------------------------------------------
def _scenario_pasqal() -> dict[str, pd.DataFrame]:
    """Rejoue les proportions réelles de la semaine du 31 août 2026.

    Le titre perd 58,4 % sur la semaine pendant que le reste du secteur recule
    d'environ 8 à 9 % par séance. C'est le **rapport** entre ces amplitudes,
    et non les niveaux absolus, qui décide de la classification.

    Note sur les chiffres de référence : les trois valeurs citées dans les
    commentaires de marché — sommet à 24,69 $, baisse de 58,4 %, plancher à
    7,96 $ — ne sont pas conciliables entre elles. Partir de 24,69 $ et
    retrancher 58,4 % donne 10,27 $, pas 7,96 $ ; atteindre 7,96 $ depuis
    24,69 $ demanderait -67,8 %. Les deux niveaux se rapportent donc à des
    points de mesure différents du sommet retenu pour le pourcentage.
    Le test s'ancre sur le **pourcentage**, qui est la grandeur qui calibre le
    module, et n'affirme rien sur les niveaux absolus.
    """
    quotidien = (1.0 - 0.584) ** (1.0 / 4.0) - 1.0   # ≈ -19,7 % par séance
    return {
        "PSQL": _serie(24.69, quotidien),
        "RGTI": _serie(30.0, -0.085),
        "QBTS": _serie(20.0, -0.078),
        "IONQ": _serie(45.0, -0.091),
    }


def test_episode_pasqal_classe_sectoriel() -> None:
    """La chute de Pasqal, accompagnée du secteur, est un mouvement sectoriel."""
    mouvements = moves.detecter_mouvements(
        _scenario_pasqal(),
        _watchlist(["PSQL", "RGTI", "QBTS", "IONQ"]),
        _CONFIG,
        variation_taux_reels=0.07,      # taux réels en hausse de 7 points de base
        variation_marche_pct=-1.4,
    )
    psql = next(m for m in mouvements if m.ticker == "PSQL")

    assert psql.classification == moves.SECTORIEL, (
        f"Classé « {psql.classification} » : le mouvement a été attribué à la société "
        "alors que tout le secteur baissait."
    )
    assert len(psql.valeurs_concordantes) >= 2
    # Le cumul de la semaine reproduit bien l'amplitude de l'épisode réel.
    prix = _scenario_pasqal()["PSQL"]["close"]
    cumul = float(prix.iloc[-1] / prix.iloc[0] - 1.0) * 100.0
    assert cumul == pytest.approx(-58.4, abs=0.5)
    # Et le déclencheur baisse bien beaucoup plus fort que ses comparables,
    # ce qui est la condition qui rend la classification non triviale.
    assert abs(psql.variation_pct) > 2.0 * max(
        abs(v) for t, v in psql.variations_secteur.items()
    )


def test_episode_pasqal_signale_la_hausse_des_taux() -> None:
    """La concomitance taux réels en hausse / valeurs en baisse est nommée."""
    mouvements = moves.detecter_mouvements(
        _scenario_pasqal(),
        _watchlist(["PSQL", "RGTI", "QBTS", "IONQ"]),
        _CONFIG,
        variation_taux_reels=0.07,
        variation_marche_pct=-1.4,
    )
    psql = next(m for m in mouvements if m.ticker == "PSQL")
    assert "taux réels" in psql.constat
    assert "points de base" in psql.constat
    # Le constat doit désamorcer l'attribution à la société elle-même.
    assert "ne dit rien de la société" in psql.constat


def test_mouvement_isole_classe_specifique() -> None:
    """Un titre qui chute seul n'est pas un mouvement de secteur."""
    prix = {
        "RGTI": _serie(30.0, -0.15),   # seul à chuter
        "QBTS": _serie(20.0, 0.004),
        "IONQ": _serie(45.0, -0.002),
        "PSQL": _serie(10.0, 0.001),
    }
    mouvements = moves.detecter_mouvements(
        prix, _watchlist(["RGTI", "QBTS", "IONQ", "PSQL"]), _CONFIG,
        variation_taux_reels=0.0, variation_marche_pct=0.1,
    )
    rgti = next(m for m in mouvements if m.ticker == "RGTI")
    assert rgti.classification == moves.SPECIFIQUE
    assert rgti.valeurs_concordantes == []


def test_amplitude_insuffisante_ne_vaut_pas_concordance() -> None:
    """Un secteur qui bouge à peine ne « confirme » pas une chute violente.

    Sans seuil d'amplitude, trois valeurs en baisse de 0,3 % suffiraient à
    faire passer une chute de 20 % pour un mouvement sectoriel.
    """
    prix = {
        "RGTI": _serie(30.0, -0.20),
        "QBTS": _serie(20.0, -0.003),
        "IONQ": _serie(45.0, -0.002),
        "PSQL": _serie(10.0, -0.004),
    }
    mouvements = moves.detecter_mouvements(
        prix, _watchlist(["RGTI", "QBTS", "IONQ", "PSQL"]), _CONFIG,
    )
    rgti = next(m for m in mouvements if m.ticker == "RGTI")
    assert rgti.classification == moves.SPECIFIQUE
    assert rgti.valeurs_concordantes == []


def test_seuil_par_valeur_respecte() -> None:
    """Une variation sous le seuil configuré ne déclenche rien."""
    prix = {"RGTI": _serie(30.0, -0.05), "QBTS": _serie(20.0, -0.05), "IONQ": _serie(45.0, -0.05)}
    watch = _watchlist(["RGTI", "QBTS", "IONQ"])
    assert moves.detecter_mouvements(prix, watch, _CONFIG) == []

    # Le même mouvement avec un seuil abaissé pour RGTI seul le fait ressortir.
    watch[0]["seuil_mouvement_pct"] = 3.0
    detectes = moves.detecter_mouvements(prix, watch, _CONFIG)
    assert [m.ticker for m in detectes] == ["RGTI"]


def test_signaux_contradictoires_listes_sans_arbitrage() -> None:
    """Chute du titre et achat d'initié coexistent sans être départagés.

    C'est le cas Pasqal : le titre s'effondre pendant qu'un administrateur
    déclare un achat. Le module doit poser les deux faits côte à côte, en
    nommant le déposant et son rôle.
    """
    achat = {
        "identite": {
            "nom": "Bpifrance Investissement",
            "role": "administrateur",
            "titre_fonction": None,
        },
        "sens": "achat",
        "nombre_titres": 1_300_000,
        "valeur_totale_usd": 10_348_000.0,
        "date_transaction": "2026-08-28",
        "libelle_code": "achat sur le marché",
    }
    mouvements = moves.detecter_mouvements(
        _scenario_pasqal(),
        _watchlist(["PSQL", "RGTI", "QBTS", "IONQ"]),
        _CONFIG,
        variation_taux_reels=0.07,
        inities_par_ticker={"PSQL": {"disponible": True, "transactions": [achat]}},
    )
    psql = next(m for m in mouvements if m.ticker == "PSQL")
    assert len(psql.signaux_contradictoires) == 1

    signal = psql.signaux_contradictoires[0]
    assert "achat d'initié" in signal["nature"]
    # L'identité et le rôle sont cités.
    assert "Bpifrance Investissement" in signal["constat"]
    assert "administrateur" in signal["constat"]
    assert "1 300 000" in signal["constat"]
    # Le texte constate, il ne conclut pas.
    assert "rapporté tel quel" in signal["constat"]
    assert moves.verifier_absence_recommandation(psql.to_dict()) == []


def test_identite_manquante_se_rabat_sur_le_montant() -> None:
    """Sans identité, l'opération est décrite par son sens et son montant."""
    achat_anonyme = {
        "identite": None,
        "sens": "achat",
        "nombre_titres": 1_300_000,
        "valeur_totale_usd": 10_348_000.0,
        "date_transaction": "2026-08-28",
    }
    signaux = moves._signaux_contradictoires(-19.7, {"transactions": [achat_anonyme]}, [])
    assert len(signaux) == 1
    assert "un déposant non identifié" in signaux[0]["constat"]
    assert "1 300 000" in signaux[0]["constat"]


def test_operation_de_sens_indetermine_nest_pas_une_contradiction() -> None:
    """Un exercice d'options n'est pas un contrepoint à une baisse.

    Ces opérations ne traduisent aucune décision de marché : les présenter
    comme un signal contraire serait trompeur.
    """
    exercice = {
        "identite": {"nom": "Un dirigeant", "role": "dirigeant"},
        "sens": "indetermine",
        "code_transaction": "M",
        "libelle_code": "exercice d'un instrument dérivé",
        "nombre_titres": 25_000,
    }
    assert moves._signaux_contradictoires(-19.7, {"transactions": [exercice]}, []) == []


def test_actualite_non_remontee_sur_un_mouvement_sectoriel() -> None:
    """Sur un mouvement sectoriel, on ne remonte pas de dépêches sur la société.

    La cause est ailleurs : afficher des articles sur l'entreprise
    entretiendrait une explication fausse.
    """
    mouvements = moves.detecter_mouvements(
        _scenario_pasqal(),
        _watchlist(["PSQL", "RGTI", "QBTS", "IONQ"]),
        _CONFIG,
        variation_taux_reels=0.07,
        actualites_par_ticker={"PSQL": [{"titre": "Une dépêche quelconque", "url": "http://x"}]},
    )
    psql = next(m for m in mouvements if m.ticker == "PSQL")
    assert psql.classification == moves.SECTORIEL
    assert psql.actualites == []


# ---------------------------------------------------------------------------
# 2. Interdiction de recommander
# ---------------------------------------------------------------------------
def test_sortie_reelle_sans_recommandation() -> None:
    """La sortie effectivement produite ne contient aucun motif interdit."""
    mouvements = moves.detecter_mouvements(
        _scenario_pasqal(),
        _watchlist(["PSQL", "RGTI", "QBTS", "IONQ"]),
        _CONFIG,
        variation_taux_reels=0.07,
        variation_marche_pct=-1.4,
        inities_par_ticker={"PSQL": {"disponible": True, "n_transactions": 1}},
        depots_par_ticker={"RGTI": [{"type": "S-3", "date_depot": "2026-09-01"}]},
    )
    infractions = moves.verifier_absence_recommandation([m.to_dict() for m in mouvements])
    assert infractions == [], (
        "Formulation de recommandation détectée : "
        + "; ".join(f"{i['chemin']} → {i['extrait']}" for i in infractions)
    )


def test_sortie_du_module_secteur_sans_recommandation() -> None:
    """Le module secteur est soumis au même contrôle."""
    prix = _scenario_pasqal()
    runways = {
        "RGTI": {
            "disponible": True,
            "trimestres_restants": 1.7,
            "tresorerie_usd": 27_763_000.0,
            "consommation_trimestrielle_usd": 16_218_673.0,
            "commentaire": "Trésorerie de 27.8 M$ au 2026-06-30.",
            "motif": "",
        }
    }
    bloc = industry.analyser_secteur(
        _watchlist(["RGTI", "QBTS", "IONQ"]), _CONFIG, prix, runways,
        intensite_financements={"disponible": True, "ratio": 1.8, "volume_24h": 120.0},
        articles_secteur=[{"titre": "Quantum funding news from Acme Labs"}],
    )
    assert moves.verifier_absence_recommandation(bloc) == []


def test_le_controle_detecte_bien_une_infraction() -> None:
    """Preuve que le contrôle n'est pas une formalité qui passe toujours.

    Un test anti-recommandation qui ne serait jamais capable d'échouer ne
    prouverait rien. On lui soumet donc des formulations manifestement
    interdites, et il doit toutes les attraper.
    """
    fautifs = [
        {"constat": "Le titre est intéressant à ce niveau."},
        {"constat": "Il faudrait acheter maintenant."},
        {"constat": "Ce point d'entrée mérite une position."},
        {"constat": "Analysts recommend a strong buy."},
        {"constat": "Le titre paraît sous-évalué."},
        {"constat": "Nous conseillons de vendre."},
    ]
    for cas in fautifs:
        infractions = moves.verifier_absence_recommandation(cas)
        assert infractions, f"Formulation non détectée : {cas['constat']!r}"


def test_les_citations_externes_ne_declenchent_pas_le_controle() -> None:
    """Un titre de presse contenant « buy » est une citation, pas un avis.

    Sans cette exception, le garde-fou produirait des faux positifs sur des
    contenus que le module ne fait que rapporter — et un garde-fou qui crie
    au loup finit par être désactivé.
    """
    sortie = {
        "actualites": [
            {"titre": "Analysts say buy IonQ now", "url": "https://exemple.test/a"},
        ],
        "constat": "Le titre recule de 9,1 % sur la séance.",
    }
    assert moves.verifier_absence_recommandation(sortie) == []

    # Mais le même mot dans un champ rédigé par le module est bien attrapé.
    sortie_fautive = dict(sortie)
    sortie_fautive["constat"] = "Analysts say buy IonQ now"
    assert moves.verifier_absence_recommandation(sortie_fautive)


def test_les_citations_externes_du_format_fil_ne_declenchent_pas_le_controle() -> None:
    """Un titre d'actualité cité dans le format de sortie unifié des fils est une citation.

    Cas réel observé en collectant le fil crypto : un communiqué de presse
    titré « Best Crypto To Buy Now : Bitcoin Stalls Near $80K... » a bloqué
    la publication de tout le fil, alors que le module ne fait que citer ce
    titre externe dans ``titre_affiche`` — le même champ que ``titre``
    ci-dessus, sous le nom qu'il porte dans le format de sortie des fils.
    """
    item = {
        "id": "abc123",
        "categorie": "crypto",
        "titre_affiche": "Best Crypto To Buy Now: Bitcoin Stalls Near $80K as Alpha",
        "horodatage_utc": "2026-09-09T10:00:00+00:00",
        "source_nom": "GDELT/openpr.com",
        "url_source": "https://www.openpr.com/news/4625504/best-crypto-to-buy-now",
        "a_une_analyse_interne": False,
        "analyse_interne": None,
        "tickers_ou_themes_lies": ["BTC"],
        "nouveaute": True,
    }
    assert moves.verifier_absence_recommandation([item]) == []

    # Mais la même formulation dans l'analyse rédigée par le module reste attrapée.
    fautif = dict(item)
    fautif["analyse_interne"] = "Best Crypto To Buy Now selon cette dépêche."
    assert moves.verifier_absence_recommandation([fautif])


def test_chemin_de_linfraction_localise() -> None:
    """Une infraction doit être localisable, sinon elle est incorrigible."""
    infractions = moves.verifier_absence_recommandation(
        {"mouvements": [{"ticker": "RGTI", "constat": "Ce titre est attractif."}]}
    )
    assert len(infractions) == 1
    assert infractions[0]["chemin"] == "mouvements[0].constat"
    assert "attractif" in infractions[0]["extrait"]


# ---------------------------------------------------------------------------
# 3. Corrélation et trésorerie
# ---------------------------------------------------------------------------
def test_correlation_elevee_avertit_sur_la_diversification() -> None:
    """Trois valeurs qui bougent ensemble ne font pas trois positions."""
    dates = pd.bdate_range("2026-01-01", periods=80, name="date")
    alea = np.random.default_rng(3)
    commun = alea.normal(0.0, 0.03, len(dates))
    prix = {
        t: pd.DataFrame(
            {"close": 100.0 * np.exp(np.cumsum(commun + alea.normal(0.0, 0.002, len(dates))))},
            index=dates,
        )
        for t in ("RGTI", "QBTS", "IONQ")
    }
    resultat = industry.analyser_correlation(prix, fenetre=60, seuil=0.70)

    assert resultat["disponible"]
    assert resultat["correlation_elevee"] is True
    assert resultat["correlation_max"] >= 0.70
    assert "une seule et même position" in resultat["avertissement"]


def test_correlation_refusee_sur_historique_trop_court() -> None:
    """Sans séances communes suffisantes, on ne publie pas de corrélation."""
    dates = pd.bdate_range("2026-01-01", periods=10, name="date")
    prix = {
        t: pd.DataFrame({"close": np.linspace(100.0, 110.0, 10)}, index=dates)
        for t in ("RGTI", "QBTS")
    }
    resultat = industry.analyser_correlation(prix, fenetre=60)
    assert not resultat["disponible"]
    assert "minimum requises" in resultat["motif"]


def test_alerte_de_runway_sous_le_seuil() -> None:
    """Un runway court est signalé, en nommant la dilution qu'il annonce."""
    resultat = industry.analyser_tresorerie(
        _watchlist(["RGTI"]),
        {"RGTI": {"disponible": True, "trimestres_restants": 1.7, "motif": "", "commentaire": ""}},
        seuil_trimestres=4.0,
    )
    societe = resultat["societes"][0]
    assert societe["sous_le_seuil"] is True
    assert resultat["societes_sous_le_seuil"] == ["RGTI"]
    assert "dilue" in societe["commentaire"]
    # Et le commentaire ne conseille toujours rien.
    assert moves.verifier_absence_recommandation(resultat) == []


def test_runway_indisponible_signale_sans_valeur_inventee() -> None:
    """Une extraction XBRL en échec laisse le champ vide, avec son motif."""
    resultat = industry.analyser_tresorerie(
        _watchlist(["RGTI"]),
        {"RGTI": {"disponible": False, "motif": "aucune donnée us-gaap dans companyfacts"}},
    )
    societe = resultat["societes"][0]
    assert societe["disponible"] is False
    assert societe["trimestres_restants"] is None
    assert "us-gaap" in societe["motif"]


def test_nouveaux_entrants_ignore_les_acteurs_connus() -> None:
    """Un acteur déjà listé n'est pas un nouvel entrant, même très cité."""
    articles = [{"titre": "IonQ wins a new contract"} for _ in range(5)]
    articles += [{"titre": "Nordic Quantum Systems raises funds"} for _ in range(4)]
    resultat = industry.detecter_nouveaux_entrants(
        articles, incumbents=["IonQ", "Rigetti"], min_mentions=3
    )
    noms = {c["nom"] for c in resultat["candidats"]}
    assert not any("IonQ" in n for n in noms)
    assert any("Nordic" in n for n in noms)


def test_nouveaux_entrants_sous_le_seuil_de_mentions() -> None:
    """Une entité citée une seule fois n'est pas signalée."""
    resultat = industry.detecter_nouveaux_entrants(
        [{"titre": "Obscure Startup announces something"}],
        incumbents=["IonQ"],
        min_mentions=3,
    )
    assert resultat["candidats"] == []


def test_avertissement_du_systeme_ne_declenche_pas_le_controle() -> None:
    """La dénégation du système ne doit pas se dénoncer elle-même.

    Pour affirmer qu'un rapport ne contient aucune recommandation d'achat ou
    de vente, il faut écrire les mots « recommandation », « achat » et
    « vente ». Sans exclusion, cette phrase déclencherait le contrôle qu'elle
    sert à garantir, et le point d'entrée refuserait de publier le moindre
    rapport. Le défaut a existé et ce test le verrouille.
    """
    rapport = {
        "meta": {
            "nature_du_rapport": (
                "Ce rapport décrit des faits de marché et leur contexte. Il ne "
                "contient aucune recommandation d'achat, de vente ou de "
                "positionnement, et n'a pas vocation à en contenir."
            ),
        },
        "mouvements": [{"constat": "Le titre recule de 9,1 % sur la séance."}],
    }
    assert moves.verifier_absence_recommandation(rapport) == []

    # Mais une vraie recommandation ailleurs reste bien détectée.
    rapport["mouvements"][0]["constat"] = "Il faudrait acheter ce titre."
    infractions = moves.verifier_absence_recommandation(rapport)
    assert len(infractions) == 1
    assert infractions[0]["chemin"] == "mouvements[0].constat"


def test_rapport_deja_controle_reste_controlable() -> None:
    """Recontrôler un rapport ne doit pas signaler ses propres constats.

    Le bloc de résultat du contrôle cite les formulations fautives trouvées.
    Sans exclusion, un second passage les redétecterait en boucle.
    """
    rapport = {
        "meta": {
            "controle_anti_recommandation": {
                "effectue": True,
                "n_infractions": 1,
                "infractions": [
                    {"chemin": "x.constat", "motif": r"\bachet", "extrait": "Il faudrait acheter."}
                ],
            }
        }
    }
    assert moves.verifier_absence_recommandation(rapport) == []
