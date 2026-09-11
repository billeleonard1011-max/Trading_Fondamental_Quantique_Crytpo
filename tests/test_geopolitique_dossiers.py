"""Tests des dossiers de conflits géopolitiques (partie B du prompt
« sorties par paliers + géopolitique »).

Remplace l'ancienne organisation par thèmes économiques abstraits par des
conflits nommés et identifiables, avec un état qui persiste d'une exécution
à l'autre plutôt que d'être recalculé isolément.

Aucun test n'accède au réseau : GDELT DOC, GDELT Events et la configuration
sont systématiquement injectés.

Exécution :
    pytest tests/test_geopolitique_dossiers.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from dataio.news import NewsItem
from modules.gold import geopolitics


# ---------------------------------------------------------------------------
# Fabriques
# ---------------------------------------------------------------------------
def _dossier_cfg(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "israel_gaza",
        "nom_affiche": "Israël - Gaza",
        "acteurs_gdelt": ["ISR", "PSE"],
        "mots_cles": ["Israël", "Gaza"],
    }
    base.update(overrides)
    return base


def _volumes(profil: list[float], depart: str = "2026-08-08") -> dict[str, float]:
    dates = pd.date_range(depart, periods=len(profil), freq="D")
    return {d.strftime("%Y-%m-%d"): float(v) for d, v in zip(dates, profil)}


def _intensite(ratio: float | None) -> dict[str, Any]:
    return {
        "query": "test", "volume_24h": 100.0, "moyenne_journaliere_30j": 50.0,
        "ratio": ratio, "alerte": bool(ratio and ratio >= 2.0),
        "jours_observes": 30, "disponible": True, "commentaire": "",
    }


def _article(titre: str, url: str = "https://exemple.test/a",
             source: str = "Reuters", jour: str = "2026-08-30") -> NewsItem:
    return NewsItem(titre=titre, url=url, source=source,
                     date=datetime.fromisoformat(f"{jour}T10:00:00+00:00"))


def _lignes_events(acteur1: str, acteur2: str) -> list[list[str]]:
    from dataio import gdelt_events as ge
    ligne = ["0"] * ge._NB_COLONNES_MIN
    ligne[ge._COL_ACTOR1_PAYS] = acteur1
    ligne[ge._COL_ACTOR2_PAYS] = acteur2
    ligne[ge._COL_CODE_EVENEMENT] = "172"
    ligne[ge._COL_GOLDSTEIN] = "-5.0"
    ligne[ge._COL_TONALITE] = "-3.3"
    ligne[ge._COL_SOURCE_URL] = "https://exemple.test/evenement"
    return [ligne]


# ---------------------------------------------------------------------------
# 1. Configuration
# ---------------------------------------------------------------------------
def test_charge_les_trois_dossiers_de_depart() -> None:
    """La configuration réelle du dépôt définit les trois dossiers demandés."""
    dossiers = geopolitics.charger_dossiers_config()
    ids = [d["id"] for d in dossiers]
    # Les trois conflits de départ, dans l'ordre, puis le régional et les
    # thématiques permanents (élargissement au-delà des conflits).
    assert ids[:3] == ["israel_gaza", "iran_etats_unis", "russie_ukraine"]
    natures = {d["id"]: d.get("type", "conflit") for d in dossiers}
    assert natures["moyen_orient"] == "regional" and dossiers[3].get("pays")
    assert {natures[i] for i in ("politique_monetaire", "semiconducteurs_ia", "commerce_international")} == {"thematique"}
    assert all(d.get("epingle") for d in dossiers if d.get("type") == "thematique")
    assert geopolitics.charger_reglages_decouverte()["min_observations"] == 20
    for d in dossiers:
        assert d["nom_affiche"]
        assert d.get("type", "conflit") != "conflit" or len(d["acteurs_gdelt"]) == 2
        assert d["mots_cles"]


def test_configuration_absente_degrade_vers_une_liste_vide(tmp_path: Path) -> None:
    """Un fichier manquant ne lève pas d'exception : liste vide, dégradation."""
    assert geopolitics.charger_dossiers_config(tmp_path / "absent.yaml") == []


def test_configuration_malformee_degrade_vers_une_liste_vide(tmp_path: Path) -> None:
    chemin = tmp_path / "casse.yaml"
    chemin.write_text("dossiers: [ceci n'est pas du yaml valide: :", encoding="utf-8")
    assert geopolitics.charger_dossiers_config(chemin) == []


# ---------------------------------------------------------------------------
# 2. mesurer_dossier — intensité, narratif, événements bilatéraux
# ---------------------------------------------------------------------------
def test_mesurer_dossier_sans_reseau() -> None:
    """Un dossier se mesure entièrement à partir de données injectées."""
    dossier = geopolitics.mesurer_dossier(
        _dossier_cfg(),
        identifiants_connus=set(),
        volumes=_volumes([10.0] * 10 + [40.0] * 4),
        intensite=_intensite(3.0),
        articles=[_article("Ceasefire talks resume")],
        lignes_events=_lignes_events("ISR", "PSE"),
    )
    assert dossier.id == "israel_gaza"
    assert dossier.theme.disponible
    assert dossier.theme.intensite_ratio == 3.0
    assert dossier.n_evenements_bilateraux == 1
    assert dossier.exemple_evenement["code_evenement"] == "172"
    assert len(dossier.developpements_recents) == 1
    assert dossier.mots_cles == ["Israël", "Gaza"]
    assert dossier.to_dict()["mots_cles"] == ["Israël", "Gaza"]


def test_dossier_sans_mots_cles_est_marque_indisponible() -> None:
    """Un dossier mal configuré (sans mots-clés) ne casse pas l'exécution."""
    dossier = geopolitics.mesurer_dossier(_dossier_cfg(mots_cles=[]), identifiants_connus=set())
    assert not dossier.theme.disponible
    assert "aucun mot-clé" in dossier.theme.motif


def test_evenements_bilateraux_exigent_exactement_deux_acteurs() -> None:
    """Un dossier mal configuré avec un seul acteur ne déclenche pas le comptage."""
    dossier = geopolitics.mesurer_dossier(
        _dossier_cfg(acteurs_gdelt=["ISR"]),
        identifiants_connus=set(),
        volumes=_volumes([10.0] * 14), intensite=_intensite(1.0),
        articles=[], lignes_events=_lignes_events("ISR", "PSE"),
    )
    assert dossier.n_evenements_bilateraux is None


def test_events_indisponible_est_signale_sans_casser_le_dossier() -> None:
    """Un export Events en échec dégrade le signal par acteur, pas tout le dossier."""
    dossier = geopolitics.mesurer_dossier(
        _dossier_cfg(), identifiants_connus=set(),
        volumes=_volumes([10.0] * 14), intensite=_intensite(1.0), articles=[],
        lignes_events=None, motif_events="export GDELT Events injoignable : timeout",
    )
    assert dossier.n_evenements_bilateraux is None
    assert "injoignable" in dossier.motif_events
    assert dossier.theme.disponible  # le reste du dossier n'est pas affecté


# ---------------------------------------------------------------------------
# 3. Aucun identifiant technique brut dans le texte produit
# ---------------------------------------------------------------------------
def test_etat_actuel_ne_contient_aucun_identifiant_brut() -> None:
    dossier = geopolitics.mesurer_dossier(
        _dossier_cfg(), identifiants_connus=set(),
        volumes=_volumes([10.0] * 10 + [40.0] * 4), intensite=_intensite(3.0),
        articles=[_article("Ceasefire talks resume")],
    )
    for brut in ("2_petrole", "3_inflation_anticipee", "4_taux_reels", "5_or", "1_evenement"):
        assert brut not in dossier.etat_actuel
        assert brut not in dossier.invalidation


def test_chaine_de_transmission_dun_dossier_traduit_ses_maillons() -> None:
    """Même garde-fou que pour l'ancien système : le libellé, jamais la clé."""
    dates = pd.bdate_range("2026-08-01", periods=30, name="date")
    macro = pd.DataFrame({
        "DCOILWTICO": pd.Series(range(30), index=dates, dtype="float64") + 70.0,
        "T10YIE": pd.Series([2.3] * 30, index=dates) - pd.Series(range(30), index=dates) * 0.01,
        "DFII10": pd.Series([0.5] * 30, index=dates) + pd.Series(range(30), index=dates) * 0.01,
    })
    prix_or = pd.Series(range(30), index=dates, dtype="float64") + 4000.0

    dossier = geopolitics.mesurer_dossier(
        _dossier_cfg(), identifiants_connus=set(),
        volumes=_volumes([10.0] * 14), intensite=_intensite(2.0), articles=[],
        series_macro=macro, prix_or=prix_or, z_score_prime=1.0,
    )
    assert dossier.chaine_transmission["chaine_rompue"] is True
    assert "4_taux_reels" not in str(dossier.chaine_transmission["commentaire"])
    assert "Taux réel" in str(dossier.chaine_transmission["commentaire"])


# ---------------------------------------------------------------------------
# 4. Détection des nouveautés — même patron que le fil quantique
# ---------------------------------------------------------------------------
def test_premiere_execution_signale_tout_comme_nouveau() -> None:
    dossier = geopolitics.mesurer_dossier(
        _dossier_cfg(), identifiants_connus=set(),
        volumes=_volumes([10.0] * 14), intensite=_intensite(1.5),
        articles=[_article("Article un"), _article("Article deux", url="https://exemple.test/b")],
    )
    assert len(dossier.nouveaux_developpements) == 2
    assert "développement(s) nouveau(x)" in dossier.etat_actuel


def test_developpement_deja_connu_nest_pas_resignale() -> None:
    """Reprend un développement déjà vu : il ne doit pas réapparaître comme nouveau."""
    premier = geopolitics.mesurer_dossier(
        _dossier_cfg(), identifiants_connus=set(),
        volumes=_volumes([10.0] * 14), intensite=_intensite(1.5),
        articles=[_article("Article un")],
    )
    connus = {item["id"] for item in premier.developpements_recents}

    second = geopolitics.mesurer_dossier(
        _dossier_cfg(), identifiants_connus=connus,
        volumes=_volumes([10.0] * 14), intensite=_intensite(1.5),
        articles=[_article("Article un")],
    )
    assert second.nouveaux_developpements == []
    assert "Rien de nouveau depuis la dernière vérification" in second.etat_actuel


def test_aucun_article_le_dit_explicitement() -> None:
    dossier = geopolitics.mesurer_dossier(
        _dossier_cfg(), identifiants_connus=set(),
        volumes=_volumes([10.0] * 14), intensite=_intensite(1.5), articles=[],
    )
    assert "Aucun article récent trouvé" in dossier.etat_actuel


# ---------------------------------------------------------------------------
# 5. Historique persistant — append-only, jamais réexpliqué
# ---------------------------------------------------------------------------
def test_historique_absent_rend_un_dictionnaire_vide(tmp_path: Path) -> None:
    assert geopolitics.charger_historique_dossiers(tmp_path / "absent.jsonl") == {}


def test_publier_puis_charger_lhistorique_conserve_les_identifiants(tmp_path: Path) -> None:
    chemin = tmp_path / "historique.jsonl"
    dossier = geopolitics.mesurer_dossier(
        _dossier_cfg(), identifiants_connus=set(),
        volumes=_volumes([10.0] * 14), intensite=_intensite(1.5),
        articles=[_article("Article un"), _article("Article deux", url="https://exemple.test/c")],
    )
    geopolitics.publier_historique_dossiers([dossier], chemin)

    relu = geopolitics.charger_historique_dossiers(chemin)
    assert "israel_gaza" in relu
    assert len(relu["israel_gaza"]) == 2


def test_dossiers_distincts_ne_partagent_pas_leurs_identifiants_connus(tmp_path: Path) -> None:
    """Le même article resterait « nouveau » pour un autre dossier."""
    chemin = tmp_path / "historique.jsonl"
    d1 = geopolitics.mesurer_dossier(
        _dossier_cfg(id="israel_gaza"), identifiants_connus=set(),
        volumes=_volumes([10.0] * 14), intensite=_intensite(1.5), articles=[_article("Article un")],
    )
    geopolitics.publier_historique_dossiers([d1], chemin)

    relu = geopolitics.charger_historique_dossiers(chemin)
    assert relu.get("iran_etats_unis", set()) == set()


def test_ligne_corrompue_de_lhistorique_est_ignoree(tmp_path: Path) -> None:
    chemin = tmp_path / "historique.jsonl"
    chemin.write_text('{"id": "abc", "dossier": "israel_gaza"}\nligne cassée\n', encoding="utf-8")
    relu = geopolitics.charger_historique_dossiers(chemin)
    assert relu == {"israel_gaza": {"abc"}}


def test_publier_sans_nouveaute_necrit_rien(tmp_path: Path) -> None:
    """Rien de nouveau à signaler : le fichier n'est pas touché (pas de ligne vide)."""
    chemin = tmp_path / "historique.jsonl"
    dossier = geopolitics.mesurer_dossier(
        _dossier_cfg(), identifiants_connus={"deja-connu"},
        volumes=_volumes([10.0] * 14), intensite=_intensite(1.5), articles=[],
    )
    geopolitics.publier_historique_dossiers([dossier], chemin)
    assert not chemin.exists()


# ---------------------------------------------------------------------------
# 6. analyser_dossiers — agrégation au niveau du rapport
# ---------------------------------------------------------------------------
def test_analyser_dossiers_agrege_lintensite_maximale() -> None:
    dossiers_precalcules = [
        geopolitics.mesurer_dossier(
            _dossier_cfg(id="israel_gaza"), volumes=_volumes([10.0] * 14),
            intensite=_intensite(1.5), articles=[],
        ),
        geopolitics.mesurer_dossier(
            _dossier_cfg(id="russie_ukraine", nom_affiche="Russie - Ukraine"),
            volumes=_volumes([10.0] * 14), intensite=_intensite(4.2), articles=[],
        ),
    ]
    bloc = geopolitics.analyser_dossiers(dossiers_precalcules=dossiers_precalcules)
    assert bloc["disponible"] is True
    assert bloc["intensite_max"] == 4.2
    assert bloc["dossier_dominant"] == "russie_ukraine"
    assert bloc["n_dossiers_mesures"] == 2
    assert len(bloc["dossiers"]) == 2
    # Les objets Python ne doivent pas fuiter dans les dossiers sérialisés.
    for d in bloc["dossiers"]:
        assert "theme" not in d  # to_dict() aplatit le Theme, ne l'expose pas tel quel


def test_analyser_dossiers_sans_aucun_dossier_disponible() -> None:
    dossiers_precalcules = [
        geopolitics.mesurer_dossier(_dossier_cfg(mots_cles=[]), identifiants_connus=set()),
    ]
    bloc = geopolitics.analyser_dossiers(dossiers_precalcules=dossiers_precalcules)
    assert bloc["disponible"] is False
    assert bloc["motif"]
    assert bloc["intensite_max"] is None


def test_analyser_dossiers_expose_les_objets_pour_lhistorique() -> None:
    """La clé interne _dossiers_objets porte les vrais objets Dossier."""
    dossiers_precalcules = [
        geopolitics.mesurer_dossier(_dossier_cfg(), volumes=_volumes([10.0] * 14),
                                     intensite=_intensite(1.5), articles=[]),
    ]
    bloc = geopolitics.analyser_dossiers(dossiers_precalcules=dossiers_precalcules)
    objets = bloc["_dossiers_objets"]
    assert len(objets) == 1
    assert isinstance(objets[0], geopolitics.Dossier)


# ---------------------------------------------------------------------------
# 7. Contrat avec le site — les champs que site/js/rubriques.js lit vraiment
# ---------------------------------------------------------------------------
#
# Ce bloc existe à cause d'un bug réel : le rapport publiait
# « chaine_de_transmission » et le site lisait « chaine_transmission ». Rien
# ne plantait — la section « Impact chiffré » se vidait silencieusement. Un
# test de contrat sur les noms de champs est le seul moyen d'attraper ça.
def test_le_bloc_publie_porte_les_champs_que_le_site_lit() -> None:
    """Contrat de nommage entre analyser_dossiers() et site/js/rubriques.js."""
    dossiers_precalcules = [
        geopolitics.mesurer_dossier(
            _dossier_cfg(), volumes=_volumes([10.0] * 14),
            intensite=_intensite(1.5), articles=[_article("Un développement")],
        ),
    ]
    bloc = geopolitics.analyser_dossiers(dossiers_precalcules=dossiers_precalcules)

    for cle in ("disponible", "dossiers", "intensite_max", "dossier_dominant",
                "n_dossiers_mesures", "n_dossiers_configures", "source"):
        assert cle in bloc, f"le site lit « {cle} », absent du bloc publié"

    assert isinstance(bloc["dossiers"], list) and bloc["dossiers"]
    for dossier in bloc["dossiers"]:
        for cle in ("id", "nom_affiche", "mots_cles", "disponible", "motif",
                    "intensite_ratio", "trajectoire", "etat_actuel",
                    "developpements_recents", "nouveaux_developpements",
                    "chaine_de_transmission", "deja_dans_les_prix", "invalidation",
                    "n_evenements_bilateraux", "motif_events"):
            assert cle in dossier, f"le site lit « dossier.{cle} », absent du dossier publié"


def test_les_developpements_publies_portent_ce_quil_faut_pour_les_afficher() -> None:
    """Un développement sans titre ni lien ne serait pas affichable."""
    dossier = geopolitics.mesurer_dossier(
        _dossier_cfg(), identifiants_connus=set(),
        volumes=_volumes([10.0] * 14), intensite=_intensite(1.5),
        articles=[_article("Un titre réel", url="https://exemple.test/x", source="Reuters")],
    ).to_dict()

    assert dossier["developpements_recents"], "aucun développement à afficher"
    for item in dossier["developpements_recents"]:
        for cle in ("id", "titre", "url", "source", "horodatage_utc"):
            assert cle in item, f"le site lit « {cle} » sur chaque développement"
        assert item["titre"] and item["url"]


def test_le_bloc_serialise_ne_contient_aucun_objet_python() -> None:
    """Tout doit passer en JSON : un objet Python ferait échouer la publication."""
    import json

    dossiers_precalcules = [
        geopolitics.mesurer_dossier(_dossier_cfg(), volumes=_volumes([10.0] * 14),
                                     intensite=_intensite(1.5), articles=[]),
    ]
    bloc = geopolitics.analyser_dossiers(dossiers_precalcules=dossiers_precalcules)
    # _dossiers_objets est la seule clé non sérialisable, et elle est
    # explicitement retirée par run.py avant publication.
    bloc.pop("_dossiers_objets")
    json.dumps(bloc)  # ne doit pas lever


# ---------------------------------------------------------------------------
# Réessai d'un dossier que GDELT n'a pas servi
# ---------------------------------------------------------------------------
def test_un_dossier_muet_est_remesure_avant_detre_declare_indisponible(monkeypatch) -> None:
    """Le défaut corrigé : Russie - Ukraine restait « GDELT n'a pas répondu » au premier refus."""
    appels: list[str] = []
    mesurer_reel = geopolitics.mesurer_dossier

    def _faux_mesurer(cfg, connus, **kwargs):
        appels.append(cfg["id"])
        if len(appels) == 1:
            return geopolitics.Dossier(
                id=cfg["id"], nom_affiche=cfg["nom_affiche"],
                theme=geopolitics.Theme(nom=cfg["nom_affiche"], motif="GDELT n'a pas répondu."),
            )
        return mesurer_reel(
            cfg, connus, volumes=_volumes([10.0] * 14), articles=[], lignes_events=[], motif_events="",
        )

    monkeypatch.setattr(geopolitics, "mesurer_dossier", _faux_mesurer)
    monkeypatch.setattr(geopolitics.time, "sleep", lambda s: None)
    bloc = geopolitics.analyser_dossiers(
        dossiers_configures=[_dossier_cfg()], identifiants_connus={}, lignes_events=[], motif_events="",
    )
    assert appels == [_dossier_cfg()["id"]] * 2
    assert bloc["dossiers"][0]["disponible"] is True


def test_un_dossier_sans_mot_cle_nest_pas_reessaye(monkeypatch) -> None:
    """Attendre n'y changerait rien : pas de réessai, pas d'attente."""
    monkeypatch.setattr(geopolitics.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("attente inutile")))
    bloc = geopolitics.analyser_dossiers(
        dossiers_configures=[_dossier_cfg(mots_cles=[])], identifiants_connus={}, lignes_events=[], motif_events="",
    )
    assert bloc["dossiers"][0]["disponible"] is False
