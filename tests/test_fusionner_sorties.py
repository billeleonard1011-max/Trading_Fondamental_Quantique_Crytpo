"""Tests de la fusion des sorties concurrentes.

Le scénario reproduit ici est celui qui a fait échouer le workflow en
production : deux exécutions se chevauchent, la seconde repart de l'état
publié par la première. Ce qui compte n'est pas seulement que la
publication réussisse, mais qu'aucune ligne déjà écrite ne disparaisse au
passage — un article dont la trace de « déjà vu » est perdue resurgit en
nouveauté quelques heures plus tard.

Aucun test n'accède au réseau ni à git.

Exécution :
    pytest tests/test_fusionner_sorties.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import fusionner_sorties as fusion


# ---------------------------------------------------------------------------
# Journaux en ajout seul
# ---------------------------------------------------------------------------
def test_la_fusion_garde_les_lignes_des_deux_executions() -> None:
    """Le cas réel : chaque exécution a ajouté une ligne que l'autre ignore."""
    publiee = '{"id": "a"}\n{"id": "b"}\n'
    locale = '{"id": "a"}\n{"id": "c"}\n'
    resultat = fusion.fusionner_lignes(publiee, locale)
    assert resultat.splitlines() == ['{"id": "a"}', '{"id": "b"}', '{"id": "c"}']


def test_la_fusion_ne_duplique_jamais_une_ligne() -> None:
    identique = '{"id": "a"}\n{"id": "b"}\n'
    assert fusion.fusionner_lignes(identique, identique) == identique


def test_la_fusion_conserve_lordre_chronologique_dorigine() -> None:
    """Un journal en ajout seul se lit dans l'ordre où il a été écrit."""
    publiee = '{"id": "1"}\n{"id": "2"}\n'
    locale = '{"id": "3"}\n'
    assert fusion.fusionner_lignes(publiee, locale).splitlines() == [
        '{"id": "1"}', '{"id": "2"}', '{"id": "3"}',
    ]


def test_les_lignes_vides_sont_ignorees() -> None:
    assert fusion.fusionner_lignes('{"id": "a"}\n\n\n', '\n{"id": "b"}\n') == (
        '{"id": "a"}\n{"id": "b"}\n'
    )


def test_deux_journaux_vides_ne_produisent_pas_de_ligne_fantome() -> None:
    assert fusion.fusionner_lignes("", "") == ""


# ---------------------------------------------------------------------------
# Cache d'observations quotidiennes
# ---------------------------------------------------------------------------
def _cache(dates: list[str], plafond: int = 760, valeur: float = 50.0) -> str:
    return json.dumps({
        "_commentaire": "test",
        "max_observations": plafond,
        "observations": [
            {"date": d, "dominance_btc_pct": valeur, "dominance_eth_pct": 10.0}
            for d in dates
        ],
    })


def test_les_observations_des_deux_executions_sont_conservees() -> None:
    fusionne = json.loads(fusion.fusionner_observations(
        _cache(["2026-09-08", "2026-09-09"]), _cache(["2026-09-10"]),
    ))
    assert [o["date"] for o in fusionne["observations"]] == [
        "2026-09-08", "2026-09-09", "2026-09-10",
    ]


def test_a_date_identique_la_mesure_locale_gagne() -> None:
    """Les deux ont mesuré le même jour : la mesure fraîche prime."""
    fusionne = json.loads(fusion.fusionner_observations(
        _cache(["2026-09-10"], valeur=55.0), _cache(["2026-09-10"], valeur=58.5),
    ))
    assert len(fusionne["observations"]) == 1
    assert fusionne["observations"][0]["dominance_btc_pct"] == 58.5


def test_le_plafond_de_profondeur_est_respecte() -> None:
    fusionne = json.loads(fusion.fusionner_observations(
        _cache(["2026-09-07", "2026-09-08"], plafond=2),
        _cache(["2026-09-09", "2026-09-10"], plafond=2),
    ))
    assert [o["date"] for o in fusionne["observations"]] == ["2026-09-09", "2026-09-10"]


def test_les_observations_restent_triees_par_date() -> None:
    fusionne = json.loads(fusion.fusionner_observations(
        _cache(["2026-09-10"]), _cache(["2026-09-08", "2026-09-09"]),
    ))
    dates = [o["date"] for o in fusionne["observations"]]
    assert dates == sorted(dates)


def test_un_cache_publie_illisible_ne_fait_pas_perdre_le_local() -> None:
    """Le fichier distant peut être corrompu : le local doit survivre."""
    fusionne = json.loads(fusion.fusionner_observations("pas du json", _cache(["2026-09-10"])))
    assert [o["date"] for o in fusionne["observations"]] == ["2026-09-10"]


def test_un_cache_local_illisible_refuse_la_fusion() -> None:
    """Publier un cache illisible effacerait l'historique : on refuse."""
    with pytest.raises(ValueError):
        fusion.fusionner_observations(_cache(["2026-09-09"]), "pas du json")


# ---------------------------------------------------------------------------
# Parcours complet, sur des fichiers réels
# ---------------------------------------------------------------------------
def test_fusionner_tout_ignore_les_fichiers_absents(tmp_path: Path, monkeypatch) -> None:
    """Un journal pas encore créé n'est pas une erreur."""
    monkeypatch.setattr(fusion, "_version_publiee", lambda ref, chemin: None)
    assert fusion.fusionner_tout("origin/main", racine=tmp_path) == []


def test_fusionner_tout_reecrit_le_journal_fusionne(tmp_path: Path, monkeypatch) -> None:
    chemin = fusion.FICHIERS_JSONL[0]
    fichier = tmp_path / chemin
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_text('{"id": "local"}\n', encoding="utf-8")

    monkeypatch.setattr(
        fusion, "_version_publiee",
        lambda ref, c: '{"id": "publiee"}\n' if c == chemin else None,
    )

    fusionnes = fusion.fusionner_tout("origin/main", racine=tmp_path)
    assert chemin in fusionnes
    lignes = fichier.read_text(encoding="utf-8").splitlines()
    assert lignes == ['{"id": "publiee"}', '{"id": "local"}']


def test_fusionner_tout_ne_touche_pas_un_fichier_deja_a_jour(tmp_path: Path, monkeypatch) -> None:
    """Rien à fusionner : le fichier n'est pas réécrit, pas de diff inutile."""
    chemin = fusion.FICHIERS_JSONL[0]
    fichier = tmp_path / chemin
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_text('{"id": "a"}\n', encoding="utf-8")
    monkeypatch.setattr(fusion, "_version_publiee", lambda ref, c: '{"id": "a"}\n')

    assert fusion.fusionner_tout("origin/main", racine=tmp_path) == []


# ---------------------------------------------------------------------------
# Cohérence des chemins
# ---------------------------------------------------------------------------
def test_le_fichier_fusionne_par_union_est_celui_que_le_module_ecrit() -> None:
    """Déplacer l'historique de dominance sans suivre ici réintroduirait le conflit."""
    from modules.crypto import rotation

    relatif = rotation.CACHE_ROTATION.relative_to(fusion.RACINE).as_posix()
    assert relatif == fusion.FICHIER_OBSERVATIONS
    assert relatif.startswith("reports/"), "un journal accumulé se publie avec reports/, jamais depuis config/"
