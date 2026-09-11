"""Tests de la rotation des dossiers et de la reprise de la dernière mesure.

Deux mécanismes qui se complètent, et dont la propriété commune est qu'aucun
ne doit publier un chiffre sans dire de quand il date.

Aucun test n'accède au réseau : c'est d'ailleurs la propriété centrale
vérifiée ici, puisque tout l'intérêt de la rotation est de ne pas appeler
GDELT pour les dossiers hors du lot.

Exécution :
    pytest tests/test_rotation_geopolitique.py -v
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from modules.gold import geopolitics
from modules.gold import rotation_geopolitique as rotation

JOUR = date(2026, 9, 11)


def _mesure(identifiant: str, jour: date, volumes: dict[str, float] | None = None) -> rotation.Mesure:
    """Fabrique une mesure de test, avec une série de volumes plausible."""
    return rotation.Mesure(
        identifiant, jour,
        volumes or {f"2026-09-{j:02d}": 100.0 + j for j in range(1, 12)},
    )


# ---------------------------------------------------------------------------
# 1. Le choix du lot
# ---------------------------------------------------------------------------
def test_le_lot_prend_les_dossiers_les_moins_recemment_mesures() -> None:
    """C'est la règle de la rotation, et elle suffit à tout tenir à jour."""
    mesures = {
        "a": _mesure("a", date(2026, 9, 10)),
        "b": _mesure("b", date(2026, 9, 6)),
        "c": _mesure("c", date(2026, 9, 8)),
        "d": _mesure("d", date(2026, 9, 11)),
    }
    lot = rotation.choisir_lot(["a", "b", "c", "d"], mesures, taille=2, jour=JOUR)
    assert lot == ["b", "c"]


def test_un_dossier_jamais_mesure_passe_avant_tous_les_autres() -> None:
    """Au démarrage à froid, et à chaque dossier ajouté à la configuration."""
    mesures = {"a": _mesure("a", date(2026, 9, 1))}
    lot = rotation.choisir_lot(["a", "nouveau"], mesures, taille=1, jour=JOUR)
    assert lot == ["nouveau"]


def test_a_anciennete_egale_lordre_de_configuration_tranche() -> None:
    """La rotation doit être reproductible, pas dépendante d'un dictionnaire."""
    mesures = {i: _mesure(i, date(2026, 9, 5)) for i in ("a", "b", "c")}
    assert rotation.choisir_lot(["a", "b", "c"], mesures, taille=2, jour=JOUR) == ["a", "b"]
    assert rotation.choisir_lot(["c", "b", "a"], mesures, taille=2, jour=JOUR) == ["c", "b"]


def test_le_lot_est_rendu_dans_lordre_de_mesure_pas_celui_du_fichier() -> None:
    """Le plus anciennement mesuré passe en premier, et ce n'est pas cosmétique.

    Le budget d'attente de GDELT est commun à toute l'exécution, donc consommé
    par les dossiers mesurés en premier. Suivre l'ordre du fichier revenait à
    n'accorder de repli qu'aux premiers de la liste, toujours les mêmes, et à
    laisser les derniers avec une tentative sèche. Servir d'abord celui qui
    attend depuis le plus longtemps est la seule répartition sans affamé.
    """
    mesures = {"a": _mesure("a", date(2026, 9, 10)), "b": _mesure("b", date(2026, 9, 1))}
    assert rotation.choisir_lot(["a", "b"], mesures, taille=2, jour=JOUR) == ["b", "a"]


def test_un_lot_nul_ne_mesure_rien() -> None:
    """Façon assumée de couper GDELT sans toucher au code."""
    assert rotation.choisir_lot(["a", "b"], {}, taille=0, jour=JOUR) == []


def test_un_lot_plus_grand_que_le_nombre_de_dossiers_les_prend_tous() -> None:
    assert rotation.choisir_lot(["a", "b"], {}, taille=9, jour=JOUR) == ["a", "b"]


def test_un_echec_laisse_le_dossier_en_tete_de_file() -> None:
    """Propriété qui compte : la rotation se répare d'elle-même.

    Un dossier dont la mesure échoue n'est pas enregistré. Sa date reste
    l'ancienne, donc il est toujours le plus ancien, donc il repasse à
    l'exécution suivante — au lieu d'attendre un cycle entier.
    """
    mesures = {"a": _mesure("a", date(2026, 9, 1)), "b": _mesure("b", date(2026, 9, 9))}
    assert rotation.choisir_lot(["a", "b"], mesures, taille=1, jour=JOUR) == ["a"]
    # « a » a échoué : rien d'enregistré, donc rien ne change.
    assert rotation.choisir_lot(["a", "b"], mesures, taille=1, jour=JOUR) == ["a"]


# ---------------------------------------------------------------------------
# 2. L'âge et la péremption
# ---------------------------------------------------------------------------
def test_lage_se_compte_depuis_la_mesure_reelle() -> None:
    assert rotation.age_jours(_mesure("a", date(2026, 9, 8)), jour=JOUR) == 3
    assert rotation.age_jours(_mesure("a", JOUR), jour=JOUR) == 0
    assert rotation.age_jours(None, jour=JOUR) is None


def test_une_mesure_de_plus_dune_semaine_est_perimee() -> None:
    """Au-delà, l'intensité ne dit plus rien du jour, datée ou non."""
    assert not rotation.est_perimee(_mesure("a", date(2026, 9, 4)), jour=JOUR)   # 7 jours
    assert rotation.est_perimee(_mesure("a", date(2026, 9, 3)), jour=JOUR)       # 8 jours
    assert rotation.est_perimee(None, jour=JOUR)


# ---------------------------------------------------------------------------
# 3. Lecture et écriture de l'état
# ---------------------------------------------------------------------------
def test_un_aller_retour_conserve_les_mesures(tmp_path: Path) -> None:
    fichier = tmp_path / "mesures.json"
    mesures = {"a": _mesure("a", date(2026, 9, 9)), "b": _mesure("b", date(2026, 9, 10))}
    assert rotation.enregistrer(mesures, fichier, jour=JOUR)
    relues = rotation.lire(fichier)
    assert set(relues) == {"a", "b"}
    assert relues["a"].mesure_du == date(2026, 9, 9)
    assert relues["a"].volumes == mesures["a"].volumes


def test_un_fichier_absent_est_un_demarrage_a_froid(tmp_path: Path) -> None:
    assert rotation.lire(tmp_path / "jamais_ecrit.json") == {}


def test_un_fichier_illisible_ne_fait_pas_echouer_le_rapport(tmp_path: Path) -> None:
    fichier = tmp_path / "casse.json"
    fichier.write_text("{ceci n'est pas du json", encoding="utf-8")
    assert rotation.lire(fichier) == {}


def test_une_entree_sans_date_exploitable_est_ignoree(tmp_path: Path) -> None:
    """Mieux vaut remesurer qu'exploiter une date qu'on ne sait pas lire."""
    fichier = tmp_path / "mesures.json"
    fichier.write_text(json.dumps({"dossiers": {
        "bonne": {"mesure_du": "2026-09-09", "volumes": {"2026-09-09": 10.0}},
        "sans_date": {"volumes": {"2026-09-09": 10.0}},
        "date_folle": {"mesure_du": "hier", "volumes": {"2026-09-09": 10.0}},
        "sans_volumes": {"mesure_du": "2026-09-09", "volumes": {}},
    }}), encoding="utf-8")
    assert set(rotation.lire(fichier)) == {"bonne"}


def test_seuls_les_dossiers_fraichement_mesures_sont_enregistres() -> None:
    """Enregistrer une reprise rajeunirait sa date et la sortirait de la file.

    C'est le défaut qui rendrait la rotation inopérante sans bruit : un dossier
    repris chaque jour paraîtrait mesuré chaque jour, et ne reviendrait jamais
    dans le lot.
    """
    class _Theme:
        def __init__(self, volumes): self.volumes = volumes

    class _Dossier:
        def __init__(self, identifiant, volumes, reprise):
            self.id, self.theme, self.reprise = identifiant, _Theme(volumes), reprise

    obtenues = rotation.depuis_dossiers([
        _Dossier("mesure", {"2026-09-11": 10.0}, reprise=False),
        _Dossier("repris", {"2026-09-09": 10.0}, reprise=True),
        _Dossier("echoue", {}, reprise=False),
    ], jour=JOUR)
    assert set(obtenues) == {"mesure"}
    assert obtenues["mesure"].mesure_du == JOUR


# ---------------------------------------------------------------------------
# 4. La reprise, telle qu'elle paraît dans le rapport
# ---------------------------------------------------------------------------
_CFG = {"id": "essai", "nom_affiche": "Dossier d'essai", "mots_cles": ["essai"], "type": "thematique"}


def _mesurer_factice(appels: list[str]):
    """Fonction de mesure qui note ses appels et ne touche jamais au réseau."""
    def _mesurer(cfg, volumes=None, intensite=None):
        appels.append(str(cfg.get("id")))
        return geopolitics.mesurer_dossier(
            cfg, set(), volumes=volumes, intensite=intensite, articles=[],
            lignes_events=[], motif_events="export absent",
        )
    return _mesurer


def test_une_mesure_connue_est_republiee_avec_son_age() -> None:
    """Un chiffre d'hier clairement daté vaut mieux qu'un « indisponible »."""
    appels: list[str] = []
    connue = _mesure("essai", date(2026, 9, 9))
    dossier = geopolitics._reprendre(_CFG, connue, _mesurer_factice(appels))
    assert dossier.reprise is True
    assert dossier.mesure_du == "2026-09-09"
    assert dossier.age_mesure_jours == rotation.age_jours(connue)
    assert dossier.theme.disponible, "la mesure reprise doit rester exploitable"
    assert dossier.theme.intensite_ratio is not None


def test_une_mesure_perimee_nest_pas_republiee_mais_expliquee() -> None:
    """Le motif donne la date et l'âge, pas un vague « GDELT indisponible »."""
    dossier = geopolitics._reprendre(
        _CFG, _mesure("essai", date(2026, 8, 1)), _mesurer_factice([])
    )
    assert dossier.reprise is False
    assert dossier.theme.disponible is False
    assert "2026-08-01" in dossier.theme.motif
    assert "trop ancienne" in dossier.theme.motif


def test_un_dossier_jamais_mesure_dit_que_son_tour_vient() -> None:
    """Démarrage à froid : le motif doit se comprendre sans lire le code."""
    dossier = geopolitics._reprendre(_CFG, None, _mesurer_factice([]))
    assert dossier.theme.disponible is False
    assert "rotation" in dossier.theme.motif


def test_la_reprise_serialise_sa_fraicheur_dans_le_rapport() -> None:
    """Le site ne peut afficher l'âge que si le rapport le porte."""
    dossier = geopolitics._reprendre(
        _CFG, _mesure("essai", date(2026, 9, 9)), _mesurer_factice([])
    )
    publie = dossier.to_dict()
    for cle in ("reprise", "mesure_du", "age_mesure_jours"):
        assert cle in publie, f"{cle} manque au rapport publié"
    assert publie["reprise"] is True
    assert publie["mesure_du"] == "2026-09-09"


def test_un_dossier_mesure_du_jour_ne_porte_aucune_reprise() -> None:
    """Le cas normal ne doit pas afficher d'âge, il n'y a rien à signaler."""
    dossier = geopolitics.mesurer_dossier(
        _CFG, set(), volumes={f"2026-09-{j:02d}": 100.0 for j in range(1, 12)},
        articles=[], lignes_events=[], motif_events="",
    )
    assert dossier.reprise is False
    assert dossier.mesure_du == ""
    assert dossier.age_mesure_jours is None


# ---------------------------------------------------------------------------
# 5. La propriété qui justifie tout : aucun appel réseau hors du lot
# ---------------------------------------------------------------------------
def test_une_reprise_nemet_aucune_requete_reseau(monkeypatch: pytest.MonkeyPatch) -> None:
    """C'est tout l'objet de la rotation : moins de pression sur GDELT.

    Le point de coupure est ``_appel_gdelt``, la seule porte de sortie réseau
    du module. Viser plus haut serait faux : ``gdelt_intensity`` est bien
    appelée, mais elle calcule sur les volumes fournis sans rien demander.
    """
    def _interdit(*args, **kwargs):
        raise AssertionError("une reprise ne doit émettre aucune requête réseau")

    monkeypatch.setattr(geopolitics.news, "_appel_gdelt", _interdit)
    monkeypatch.setattr(geopolitics.news.requests, "get", _interdit)

    appels: list[str] = []
    for connue in (_mesure("essai", date(2026, 9, 9)), _mesure("essai", date(2026, 8, 1)), None):
        geopolitics._reprendre(_CFG, connue, _mesurer_factice(appels))
    assert len(appels) == 3


def test_une_mesure_normale_passe_bien_par_le_reseau(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contre-épreuve du test précédent : sans volumes, la requête part.

    Sans cette contre-épreuve, un test qui interdit le réseau resterait vert
    même si la mesure avait cessé de fonctionner pour une tout autre raison.
    """
    appels: list[str] = []
    monkeypatch.setattr(
        geopolitics.news, "_appel_gdelt",
        lambda parametres, **k: appels.append(parametres.get("mode")) or None,
    )
    geopolitics.mesurer_dossier(_CFG, set(), articles=[], lignes_events=[], motif_events="")
    assert appels, "une mesure sans volumes doit interroger GDELT"


# ---------------------------------------------------------------------------
# 6. De bout en bout : ce que l'exécution demande vraiment à GDELT
# ---------------------------------------------------------------------------
_QUATRE = [
    {"id": "un", "nom_affiche": "Un", "mots_cles": ["alpha"], "type": "thematique"},
    {"id": "deux", "nom_affiche": "Deux", "mots_cles": ["beta"], "type": "thematique"},
    {"id": "trois", "nom_affiche": "Trois", "mots_cles": ["gamma"], "type": "thematique"},
    {"id": "quatre", "nom_affiche": "Quatre", "mots_cles": ["delta"], "type": "thematique"},
]


@pytest.fixture
def gdelt_compte(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Compte les requêtes GDELT réellement émises et les fait toutes réussir."""
    emises: list[str] = []
    serie = {f"2026-09-{j:02d}": 100.0 for j in range(1, 12)}

    def _volumes(query, timespan=None, **k):
        emises.append(f"volumes:{query[:12]}")
        return dict(serie), ""

    def _articles(query, **k):
        emises.append(f"articles:{query[:12]}")
        return []

    monkeypatch.setattr(geopolitics.news, "gdelt_volume_journalier", _volumes)
    monkeypatch.setattr(geopolitics.news, "fetch_gdelt", _articles)
    return emises


def test_seuls_les_dossiers_du_lot_interrogent_gdelt(
    gdelt_compte, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La propriété qui divise la pression GDELT par deux.

    Quatre dossiers configurés, un lot de deux : seuls deux doivent partir sur
    le réseau. Les deux autres n'ont encore rien à reprendre et sont publiés
    comme en attente de leur tour, ce qui reste sans appel réseau.
    """
    monkeypatch.setattr(rotation, "FICHIER_MESURES", tmp_path / "mesures.json")
    resultat = geopolitics.analyser_dossiers(
        dossiers_configures=_QUATRE,
        identifiants_connus={},
        lignes_events=[], motif_events="",
        themes_generiques=[],
        reglages_decouverte={"dossiers_par_execution": 2, "max_candidats": 0, "max_candidats_mesures": 0},
    )
    mesures_volumes = [e for e in gdelt_compte if e.startswith("volumes:")]
    assert len(mesures_volumes) == 2, f"requêtes de volume : {gdelt_compte}"

    par_id = {d["id"]: d for d in resultat["dossiers"]}
    assert sum(1 for d in par_id.values() if d["disponible"]) == 2
    for identifiant in ("trois", "quatre"):
        assert par_id[identifiant]["disponible"] is False
        assert "rotation" in par_id[identifiant]["motif"]


def test_lexecution_suivante_mesure_lautre_moitie(
    gdelt_compte, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le cycle complet : en deux exécutions, les quatre sont passés."""
    fichier = tmp_path / "mesures.json"
    monkeypatch.setattr(rotation, "FICHIER_MESURES", fichier)
    reglages = {"dossiers_par_execution": 2, "max_candidats": 0, "max_candidats_mesures": 0}

    geopolitics.analyser_dossiers(
        dossiers_configures=_QUATRE, identifiants_connus={},
        lignes_events=[], motif_events="", themes_generiques=[],
        reglages_decouverte=reglages,
    )
    premiers = set(rotation.lire(fichier))
    assert len(premiers) == 2

    # Le lendemain : les deux mesurés hier sont les plus récents, donc les deux
    # autres passent. Sans « le moins récemment mesuré d'abord », la rotation
    # remesurerait indéfiniment les deux mêmes.
    gdelt_compte.clear()
    resultat = geopolitics.analyser_dossiers(
        dossiers_configures=_QUATRE, identifiants_connus={},
        lignes_events=[], motif_events="", themes_generiques=[],
        reglages_decouverte=reglages,
    )
    assert set(rotation.lire(fichier)) == {"un", "deux", "trois", "quatre"}
    # Les quatre paraissent, deux mesurés du jour et deux repris et datés.
    par_id = {d["id"]: d for d in resultat["dossiers"]}
    assert all(d["disponible"] for d in par_id.values()), "aucun dossier ne doit disparaître"
    reprises = [d for d in par_id.values() if d["reprise"]]
    assert len(reprises) == 2
    for d in reprises:
        assert d["mesure_du"], "une reprise doit dire de quand elle date"
        assert d["age_mesure_jours"] is not None


# ---------------------------------------------------------------------------
# 7. Le repli sur la série courte, qui débloque un dossier prisonnier du lot
# ---------------------------------------------------------------------------
def test_la_serie_courte_sert_de_repli_a_la_longue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le défaut observé : la série de la mesure d'intensité était jetée.

    La requête sur quatre-vingt-dix jours et celle sur trente jours sont deux
    appels distincts, et GDELT en refuse une bonne part au hasard. Quand la
    longue échouait et la courte passait, le dossier affichait une intensité
    sans série, ne mémorisait rien, et restait indéfiniment dans la file de la
    rotation en consommant un créneau sur quatre. Constaté deux exécutions de
    suite sur le dossier Moyen-Orient.
    """
    courte = {f"2026-09-{j:02d}": 50.0 + j for j in range(1, 12)}

    def _longue_echoue(query, timespan=None, **k):
        # Quatre-vingt-dix jours : refusée. Trente jours : servie.
        if timespan and timespan.startswith("30"):
            return dict(courte), ""
        return {}, "GDELT n'a pas répondu."

    monkeypatch.setattr(geopolitics.news, "gdelt_volume_journalier", _longue_echoue)
    monkeypatch.setattr(geopolitics.news, "fetch_gdelt", lambda *a, **k: [])

    theme = geopolitics.analyser_theme("Essai", "(essai)")
    assert theme.disponible, "l'intensité doit rester calculable"
    assert theme.volumes == courte, "la série courte doit être conservée, pas jetée"


def test_un_dossier_a_serie_courte_sort_de_la_file_de_rotation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Conséquence qui compte : il est mémorisé, donc il cède son créneau.

    Sans le repli, ce dossier revenait dans le lot à chaque exécution sans
    jamais progresser, au détriment des sept autres.
    """
    courte = {f"2026-09-{j:02d}": 50.0 + j for j in range(1, 12)}
    monkeypatch.setattr(rotation, "FICHIER_MESURES", tmp_path / "mesures.json")
    monkeypatch.setattr(
        geopolitics.news, "gdelt_volume_journalier",
        lambda query, timespan=None, **k: (dict(courte), "") if (timespan or "").startswith("30")
        else ({}, "GDELT n'a pas répondu."),
    )
    monkeypatch.setattr(geopolitics.news, "fetch_gdelt", lambda *a, **k: [])

    geopolitics.analyser_dossiers(
        dossiers_configures=[_CFG], identifiants_connus={},
        lignes_events=[], motif_events="", themes_generiques=[],
        reglages_decouverte={"dossiers_par_execution": 1, "max_candidats": 0, "max_candidats_mesures": 0},
    )
    memorisees = rotation.lire(tmp_path / "mesures.json")
    assert "essai" in memorisees, "un dossier à série courte doit être mémorisé"
    assert memorisees["essai"].volumes == courte


def test_gdelt_intensity_rend_la_serie_quelle_a_obtenue(monkeypatch: pytest.MonkeyPatch) -> None:
    """La série est au contrat de sortie : c'est ce qui permet le repli."""
    from dataio import news

    serie = {"2026-09-10": 10.0, "2026-09-11": 30.0}
    monkeypatch.setattr(news, "gdelt_volume_journalier", lambda *a, **k: (dict(serie), ""))
    resultat = news.gdelt_intensity("(essai)")
    assert resultat["volumes"] == serie
    assert resultat["disponible"] is True

    # Série fournie par l'appelant : rendue telle quelle, sans appel.
    resultat = news.gdelt_intensity("(essai)", volumes=serie)
    assert resultat["volumes"] == serie


def test_une_intensite_indisponible_rend_une_serie_vide(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rien à mémoriser, et le champ existe quand même : pas de KeyError."""
    from dataio import news

    monkeypatch.setattr(news, "gdelt_volume_journalier", lambda *a, **k: ({}, "GDELT muet."))
    resultat = news.gdelt_intensity("(essai)")
    assert resultat["volumes"] == {}
    assert resultat["disponible"] is False


# ---------------------------------------------------------------------------
# 8. La série s'accumule au lieu de se faire remplacer
# ---------------------------------------------------------------------------
class _ThemeFactice:
    def __init__(self, volumes): self.volumes = volumes


class _DossierFactice:
    def __init__(self, identifiant, volumes, reprise=False):
        self.id, self.theme, self.reprise = identifiant, _ThemeFactice(volumes), reprise


def test_une_serie_courte_ne_remplace_pas_une_serie_longue() -> None:
    """GDELT sert tantôt 90 jours, tantôt 30 : remplacer ferait perdre l'historique.

    Le défaut observé en production : Israël - Gaza est passé de 86 jours de
    volumes à 30 en une exécution, parce que la requête longue avait échoué et
    que la courte l'avait remplacée. Répété, cela finirait par rendre la
    pertinence marché, qui demande vingt observations, non calculable.
    """
    longue = {f"2026-07-{j:02d}": 10.0 for j in range(1, 32)}
    longue.update({f"2026-08-{j:02d}": 20.0 for j in range(1, 32)})
    connues = {"a": rotation.Mesure("a", date(2026, 9, 1), longue)}
    courte = {f"2026-09-{j:02d}": 30.0 for j in range(1, 12)}

    obtenues = rotation.depuis_dossiers(
        [_DossierFactice("a", courte)], connues, jour=JOUR,
    )
    fusionnee = obtenues["a"].volumes
    assert len(fusionnee) == len(longue) + len(courte)
    assert set(longue) <= set(fusionnee), "l'historique doit survivre"
    assert set(courte) <= set(fusionnee), "la mesure du jour doit entrer"
    assert obtenues["a"].mesure_du == JOUR


def test_la_valeur_du_jour_lemporte_a_date_egale() -> None:
    """GDELT réévalue le volume d'une journée encore en cours."""
    connues = {"a": rotation.Mesure("a", date(2026, 9, 10), {"2026-09-10": 5.0})}
    obtenues = rotation.depuis_dossiers(
        [_DossierFactice("a", {"2026-09-10": 42.0})], connues, jour=JOUR,
    )
    assert obtenues["a"].volumes["2026-09-10"] == 42.0


def test_la_serie_conservee_est_plafonnee() -> None:
    """Sans plafond, le fichier grossirait sans fin sans rien servir de plus."""
    ancienne = {f"2026-{m:02d}-{j:02d}": 1.0 for m in (3, 4, 5, 6, 7) for j in range(1, 29)}
    connues = {"a": rotation.Mesure("a", date(2026, 8, 1), ancienne)}
    obtenues = rotation.depuis_dossiers(
        [_DossierFactice("a", {"2026-09-11": 2.0})], connues, jour=JOUR,
    )
    volumes = obtenues["a"].volumes
    assert len(volumes) == rotation.MAX_JOURS_SERIE
    assert "2026-09-11" in volumes, "le plus récent est toujours gardé"
    assert max(volumes) == "2026-09-11"


def test_un_dossier_repris_ne_touche_pas_a_sa_serie_memorisee() -> None:
    """Une reprise n'apporte rien de neuf : ni date ni série ne bougent."""
    connues = {"a": rotation.Mesure("a", date(2026, 9, 5), {"2026-09-05": 1.0})}
    obtenues = rotation.depuis_dossiers(
        [_DossierFactice("a", {"2026-09-05": 1.0}, reprise=True)], connues, jour=JOUR,
    )
    assert obtenues == {}
