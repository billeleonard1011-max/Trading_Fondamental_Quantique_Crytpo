"""Tests du plafond quotidien d'analyses et de la collecte sans GDELT.

Ce que ces tests figent :

1. **Le plafond est quotidien, pas par exécution.** Il se remet à zéro au
   changement de jour, survit d'une exécution à l'autre par son fichier, et
   ne dépasse jamais le budget même quand plusieurs fils tirent dessus.
2. **La collecte sans GDELT n'appelle pas GDELT.** C'est la promesse de la
   voie rapide : si un appel passait quand même, la cadence de trente
   minutes multiplierait par quarante-huit la pression sur une source qui
   nous renvoie déjà des 429. Le test force ``fetch_gdelt`` à lever.
3. **Les requêtes crypto sont groupées.** Dix appels devenaient la moitié du
   temps d'exécution ; le groupement doit réduire leur nombre sans perdre un
   seul jeton suivi.
4. **Le fil publié se fusionne par union.** Une exécution sans GDELT n'a
   qu'une vue partielle : si elle écrasait le fil, celui-ci rétrécirait
   toutes les trente minutes.

Aucun test n'accède au réseau.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from dataio import news
from modules import quota_llm
from modules.crypto import feed as feed_crypto
from modules.geopolitique import feed as feed_geo
from modules.quantum import feed as feed_quantum
from scripts import fusionner_sorties as fusion


# ---------------------------------------------------------------------------
# 1. Plafond quotidien
# ---------------------------------------------------------------------------
def test_un_compteur_absent_vaut_zero(tmp_path: Path) -> None:
    fichier = tmp_path / "quota.json"
    assert quota_llm.lire(fichier)["analyses"] == 0
    assert quota_llm.restant(120, fichier) == 120


def test_le_compteur_survit_dune_execution_a_lautre(tmp_path: Path) -> None:
    """C'est tout l'intérêt du fichier : un exécuteur CI est éphémère."""
    fichier = tmp_path / "quota.json"
    jour = date(2026, 9, 12)
    assert quota_llm.consommer(8, fichier, jour) == 8
    assert quota_llm.consommer(8, fichier, jour) == 16
    assert quota_llm.restant(120, fichier, jour) == 104


def test_le_compteur_repart_de_zero_au_changement_de_jour(tmp_path: Path) -> None:
    fichier = tmp_path / "quota.json"
    quota_llm.consommer(100, fichier, date(2026, 9, 12))
    assert quota_llm.restant(120, fichier, date(2026, 9, 12)) == 20
    assert quota_llm.restant(120, fichier, date(2026, 9, 13)) == 120


def test_le_plafond_atteint_ne_laisse_rien_passer(tmp_path: Path) -> None:
    fichier = tmp_path / "quota.json"
    jour = date(2026, 9, 12)
    quota_llm.consommer(120, fichier, jour)
    assert quota_llm.restant(120, fichier, jour) == 0
    # Un dépassement ne rend jamais un solde négatif.
    quota_llm.consommer(30, fichier, jour)
    assert quota_llm.restant(120, fichier, jour) == 0


def test_un_plafond_nul_coupe_la_couche_pedagogique(tmp_path: Path) -> None:
    """Mettre 0 est la façon assumée de désactiver les analyses."""
    assert quota_llm.restant(0, tmp_path / "quota.json") == 0


def test_un_compteur_corrompu_ne_fait_pas_echouer(tmp_path: Path) -> None:
    fichier = tmp_path / "quota.json"
    fichier.write_text("{pas du json", encoding="utf-8")
    assert quota_llm.lire(fichier)["analyses"] == 0


def test_consommer_zero_necrit_rien(tmp_path: Path) -> None:
    """Une exécution sans analyse ne doit pas créer de diff à publier."""
    fichier = tmp_path / "quota.json"
    quota_llm.consommer(0, fichier)
    assert not fichier.exists()


def test_le_plafond_du_jour_borne_les_trois_fils(tmp_path: Path, monkeypatch) -> None:
    """Le plafond est commun : trois fils à huit analyses ne font pas 24 si le budget est 10."""
    fichier = tmp_path / "quota.json"
    monkeypatch.setattr(quota_llm, "FICHIER_QUOTA", fichier)
    jour = date(2026, 9, 12)
    quota_llm.consommer(114, fichier, jour)          # il reste 6 sur 120
    assert quota_llm.restant(120, fichier, jour) == 6
    assert min(8, quota_llm.restant(120, fichier, jour)) == 6


# ---------------------------------------------------------------------------
# 2. Collecte sans GDELT
# ---------------------------------------------------------------------------
def _interdire_gdelt(monkeypatch) -> None:
    """Fait échouer tout appel GDELT : la voie rapide ne doit jamais en émettre."""
    def _interdit(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("GDELT appelé alors que la collecte est censée s'en passer")

    monkeypatch.setattr(news, "fetch_gdelt", _interdit)
    for module in (feed_quantum, feed_crypto, feed_geo):
        monkeypatch.setattr(module.news, "fetch_gdelt", _interdit)


def _sans_rss(monkeypatch) -> None:
    """Neutralise le canal RSS : aucun test n'accède au réseau."""
    for module in (feed_quantum, feed_crypto, feed_geo):
        monkeypatch.setattr(module.news, "fetch_rss", lambda *a, **k: [])


@pytest.mark.parametrize(
    "module, configuration",
    [
        (feed_quantum, {"quantum_watchlist": [{"ticker": "RGTI", "keywords": ["Rigetti"]}],
                        "quantum_industry_watch": {"query": '("quantum computing")'}}),
        (feed_crypto, {"crypto_watchlist": [{"symbol": "BTC", "keywords": ["Bitcoin"]}]}),
        (feed_geo, {"themes": [{"nom": "T", "query": '("oil supply")'}], "feed": {}}),
    ],
)
def test_la_collecte_sans_gdelt_nemet_aucun_appel(module, configuration, monkeypatch) -> None:
    _interdire_gdelt(monkeypatch)
    _sans_rss(monkeypatch)
    assert module.collecter(configuration, flux_rss=[], avec_gdelt=False) == []


def test_la_collecte_avec_gdelt_lappelle_bien(monkeypatch) -> None:
    """Le contrôle inverse : sans le drapeau, le canal GDELT reste actif."""
    appels: list[str] = []
    monkeypatch.setattr(feed_geo.news, "fetch_rss", lambda *a, **k: [])
    monkeypatch.setattr(
        feed_geo.news, "fetch_gdelt",
        lambda query, **k: appels.append(query) or [],
    )
    feed_geo.collecter({"themes": [{"nom": "T", "query": '("oil supply")'}], "feed": {}}, flux_rss=[])
    assert appels == ['("oil supply")']


# ---------------------------------------------------------------------------
# 3. Groupement des requêtes crypto
# ---------------------------------------------------------------------------
def test_les_jetons_sont_groupes_sans_en_perdre_un_seul() -> None:
    watchlist = [
        {"symbol": "BTC", "keywords": ["Bitcoin"]},
        {"symbol": "ETH", "keywords": ["Ethereum", "Ether"]},
        {"symbol": "SOL", "keywords": ["Solana"]},
        {"symbol": "DOGE", "keywords": ["Dogecoin"]},
        {"symbol": "ASTER", "keywords": ["Aster DEX", "AsterDex", "Aster perpetuals"]},
        {"symbol": "VIDE", "keywords": []},
    ]
    groupes = feed_crypto._grouper_watchlist(watchlist, max_termes=4)
    groupes_symboles = [[e["symbol"] for e in g] for g in groupes]
    assert len(groupes) < len([w for w in watchlist if w["keywords"]]), "le groupement doit réduire le nombre d'appels"
    plats = [s for g in groupes_symboles for s in g]
    assert plats == ["BTC", "ETH", "SOL", "DOGE", "ASTER"], "aucun jeton suivi ne doit disparaître"
    assert "VIDE" not in plats, "un jeton sans mot-clé n'a rien à faire dans une requête par mots-clés"
    for groupe in groupes:
        assert sum(len(e["keywords"]) for e in groupe) <= 4 or len(groupe) == 1


def test_la_watchlist_reelle_tient_en_moins_dappels_quavant() -> None:
    import yaml

    watchlist = yaml.safe_load(Path("config/universe.yaml").read_text(encoding="utf-8")).get("crypto_watchlist") or []
    avec_mots = [e for e in watchlist if e.get("keywords")]
    groupes = feed_crypto._grouper_watchlist(watchlist)
    assert len(groupes) <= 4, f"{len(avec_mots)} jetons doivent tenir en quatre appels au plus, obtenu {len(groupes)}"
    assert sum(len(g) for g in groupes) == len(avec_mots)


def test_le_groupement_conserve_le_rattachement_par_jeton() -> None:
    """Le jeton vient du titre, pas de l'étiquette GDELT : grouper ne perd rien."""
    watchlist = [{"symbol": "BTC", "keywords": ["Bitcoin"]}, {"symbol": "SOL", "keywords": ["Solana"]}]
    assert feed_crypto._entites_liees("Solana network sees record volume", watchlist) == ["SOL"]


def test_le_groupement_interroge_gdelt_une_fois_par_groupe(monkeypatch) -> None:
    requetes: list[str] = []
    monkeypatch.setattr(feed_crypto.news, "fetch_rss", lambda *a, **k: [])
    monkeypatch.setattr(feed_crypto.news, "fetch_gdelt", lambda query, **k: requetes.append(query) or [])
    configuration = {"crypto_watchlist": [
        {"symbol": "BTC", "keywords": ["Bitcoin"]},
        {"symbol": "ETH", "keywords": ["Ethereum"]},
        {"symbol": "SOL", "keywords": ["Solana"]},
    ]}
    feed_crypto.collecter(configuration, flux_rss=[], requetes_gdelt=[])
    assert len(requetes) == 1, f"trois jetons doivent tenir en un appel, obtenu {len(requetes)}"
    for mot in ("Bitcoin", "Ethereum", "Solana"):
        assert mot in requetes[0]


# ---------------------------------------------------------------------------
# 4. Fusion du fil publié
# ---------------------------------------------------------------------------
def _item(identifiant: str, horodatage: str) -> dict[str, Any]:
    return {"id": identifiant, "titre_affiche": f"Item {identifiant}", "horodatage_utc": horodatage}


def test_une_execution_sans_gdelt_nampute_pas_le_fil_publie() -> None:
    """Le cas réel : la voie rapide n'a qu'une vue partielle."""
    publiee = json.dumps([_item("gdelt-1", "2026-09-12T10:00:00Z"), _item("rss-1", "2026-09-12T09:00:00Z")])
    locale = json.dumps([_item("rss-2", "2026-09-12T10:30:00Z"), _item("rss-1", "2026-09-12T09:00:00Z")])
    fusionne = json.loads(fusion.fusionner_fil(publiee, locale))
    assert [i["id"] for i in fusionne] == ["rss-2", "gdelt-1", "rss-1"]


def test_litem_local_gagne_a_identifiant_egal() -> None:
    publiee = json.dumps([{**_item("a", "2026-09-12T10:00:00Z"), "analyse_interne": None}])
    locale = json.dumps([{**_item("a", "2026-09-12T10:00:00Z"), "analyse_interne": "expliqué"}])
    fusionne = json.loads(fusion.fusionner_fil(publiee, locale))
    assert fusionne[0]["analyse_interne"] == "expliqué"


def test_le_fil_fusionne_reste_plafonne() -> None:
    # Tous antérieurs à l'item local, pour que le plafond se lise sans ambiguïté.
    publiee = json.dumps([_item(f"p{i}", f"2026-09-11T{i % 24:02d}:00:00Z") for i in range(200)])
    locale = json.dumps([_item("neuf", "2026-09-12T23:00:00Z")])
    fusionne = json.loads(fusion.fusionner_fil(publiee, locale, maximum=50))
    assert len(fusionne) == 50
    assert fusionne[0]["id"] == "neuf"


def test_un_fil_local_illisible_refuse_la_fusion() -> None:
    with pytest.raises(ValueError):
        fusion.fusionner_fil("[]", "pas du json")


def test_un_fil_publie_illisible_ne_fait_pas_perdre_le_local() -> None:
    locale = json.dumps([_item("a", "2026-09-12T10:00:00Z")])
    assert json.loads(fusion.fusionner_fil("pas du json", locale))[0]["id"] == "a"


# ---------------------------------------------------------------------------
# 5. Fusion du compteur d'analyses
# ---------------------------------------------------------------------------
def test_le_compteur_fusionne_retient_le_plus_grand_du_jour() -> None:
    a = json.dumps({"jour": "2026-09-12", "analyses": 40})
    b = json.dumps({"jour": "2026-09-12", "analyses": 55})
    assert json.loads(fusion.fusionner_quota(a, b))["analyses"] == 55
    assert json.loads(fusion.fusionner_quota(b, a))["analyses"] == 55


def test_un_compteur_dhier_ne_ralentit_pas_aujourdhui() -> None:
    hier = json.dumps({"jour": "2026-09-11", "analyses": 120})
    aujourdhui = json.dumps({"jour": "2026-09-12", "analyses": 3})
    assert json.loads(fusion.fusionner_quota(hier, aujourdhui)) == {"jour": "2026-09-12", "analyses": 3}


def test_un_compteur_local_illisible_refuse_la_fusion() -> None:
    with pytest.raises(ValueError):
        fusion.fusionner_quota(json.dumps({"jour": "2026-09-12", "analyses": 1}), "pas du json")


def test_un_repli_sur_le_gabarit_ne_consomme_pas_le_budget(tmp_path, monkeypatch) -> None:
    """Sans clé OpenAI, l'explication est déterministe : elle ne coûte rien.

    Le défaut corrigé : le compteur s'incrémentait à chaque item expliqué,
    y compris quand aucun appel n'était parti — une exécution `--sans-analyse`
    rognait le budget de la journée sans avoir rien dépensé.
    """
    fichier = tmp_path / "quota.json"
    monkeypatch.setattr(quota_llm, "FICHIER_QUOTA", fichier)
    monkeypatch.setattr(feed_geo.news, "fetch_rss", lambda *a, **k: [])
    # _analyser_item rend (texte, produit_par_modele) : False = gabarit.
    monkeypatch.setattr(feed_geo, "_analyser_item", lambda *a, **k: ("Texte de gabarit.", False))

    articles = [
        news.NewsItem(titre=f"Gaza talks resume, round {i}", url=f"https://exemple.test/{i}",
                      source="Reuters", resume="")
        for i in range(5)
    ]
    items = feed_geo.construire_fil(
        {"themes": [], "feed": {}}, articles, identifiants_connus=set(),
        dossiers=[{"id": "israel_gaza", "nom_affiche": "Israël - Gaza", "mots_cles": ["Gaza"]}],
    )
    assert len(items) == 5, "les items sont bien publiés, avec leur explication de gabarit"
    assert quota_llm.lire(fichier)["analyses"] == 0, "aucun appel facturé, aucun budget consommé"


def test_une_analyse_reellement_produite_consomme_le_budget(tmp_path, monkeypatch) -> None:
    fichier = tmp_path / "quota.json"
    monkeypatch.setattr(quota_llm, "FICHIER_QUOTA", fichier)
    monkeypatch.setattr(feed_geo.news, "fetch_rss", lambda *a, **k: [])
    monkeypatch.setattr(feed_geo, "_analyser_item", lambda *a, **k: ("Texte du modèle.", True))

    articles = [
        news.NewsItem(titre=f"Gaza talks resume, round {i}", url=f"https://exemple.test/{i}",
                      source="Reuters", resume="")
        for i in range(3)
    ]
    feed_geo.construire_fil(
        {"themes": [], "feed": {}}, articles, identifiants_connus=set(),
        dossiers=[{"id": "israel_gaza", "nom_affiche": "Israël - Gaza", "mots_cles": ["Gaza"]}],
    )
    assert quota_llm.lire(fichier)["analyses"] == 3
