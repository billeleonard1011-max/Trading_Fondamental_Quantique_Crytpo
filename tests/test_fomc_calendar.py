"""Tests du calendrier FOMC : analyse de la page, cache et filet de sécurité.

Ce que ces tests protègent
--------------------------
Un calendrier de réunions ne tombe jamais en panne franchement. Les dates
connues restent exactes jusqu'au jour où il n'en reste plus, et le compte à
rebours cesse alors d'exister sans qu'aucune exception ne soit levée. Le
risque n'est donc pas le plantage, c'est le **silence**.

Trois propriétés sont vérifiées :

1. l'analyse de la page de la Réserve fédérale, sur un extrait figé, avec
   tous les cas tordus rencontrés sur la page réelle ;
2. le repli sur le cache quand la collecte échoue ;
3. l'alerte de renouvellement quand les deux sources sont muettes — et le
   fait que le rapport continue de s'exécuter malgré tout.

Aucun test n'accède au réseau : la page est lue depuis
``tests/fixtures/fomccalendars_extrait.html``, et les appels sortants sont
remplacés par des doublures.

Exécution :
    pytest tests/test_fomc_calendar.py -v
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import requests

from dataio import calendar as cal

FIXTURE = Path(__file__).parent / "fixtures" / "fomccalendars_extrait.html"


@pytest.fixture(scope="module")
def html_fed() -> str:
    """Extrait figé de la page de la Réserve fédérale."""
    return FIXTURE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Analyse de la page
# ---------------------------------------------------------------------------
def test_parsing_extrait_fige(html_fed: str) -> None:
    """L'extrait figé donne les réunions attendues, sans réseau."""
    reunions = cal.parser_calendrier_fomc(html_fed)
    assert reunions, "Aucune réunion extraite de l'extrait figé."

    dates = {r.date_decision for r in reunions}
    # Réunions de 2026, vérifiées à la main contre la page officielle.
    for attendue in (
        date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
        date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
    ):
        assert attendue in dates, f"Réunion du {attendue} absente."

    assert reunions == sorted(reunions, key=lambda r: r.date_decision)


def test_date_retenue_est_le_second_jour(html_fed: str) -> None:
    """La décision tombe le second jour : c'est cette date qui compte.

    « September 15-16 » doit donner le 16, pas le 15. Retenir le premier jour
    déclencherait la fenêtre de silence vingt-quatre heures trop tôt et la
    lèverait juste avant l'annonce.
    """
    reunions = {r.libelle: r.date_decision for r in cal.parser_calendrier_fomc(html_fed)}
    assert reunions["September 15-16*"] == date(2026, 9, 16)
    assert reunions["January 27-28"] == date(2026, 1, 28)


def test_reunion_a_cheval_sur_deux_mois(html_fed: str) -> None:
    """« Jan/Feb 31-1 » se termine le 1er février, pas le 1er janvier."""
    reunions = {r.libelle: r.date_decision for r in cal.parser_calendrier_fomc(html_fed)}
    assert reunions["Jan/Feb 31-1"] == date(2023, 2, 1)
    assert reunions["Oct/Nov 31-1"] == date(2023, 11, 1)


def test_projections_economiques_detectees(html_fed: str) -> None:
    """L'astérisque marque les réunions avec projections trimestrielles."""
    reunions = {r.libelle: r.avec_projections for r in cal.parser_calendrier_fomc(html_fed)}
    assert reunions["September 15-16*"] is True
    assert reunions["October 27-28"] is False


def test_vote_par_notation_ecarte(html_fed: str) -> None:
    """Un « notation vote » n'est pas une décision de taux : il est écarté.

    L'entrée « August 22 (notation vote) » figure dans l'extrait de 2025. La
    compter produirait un compte à rebours vers un événement qui ne déplace
    pas le marché.
    """
    reunions = cal.parser_calendrier_fomc(html_fed)
    assert date(2025, 8, 22) not in {r.date_decision for r in reunions}
    assert all("notation" not in r.libelle for r in reunions)


def test_changement_dannee_sur_reunion_a_cheval() -> None:
    """« Dec/Jan 30-1 » place la décision en janvier de l'année suivante."""
    html = (
        '<h4><a id="1">2030 FOMC Meetings</a></h4>'
        '<div class="row fomc-meeting">'
        '<div class="fomc-meeting__month"><strong>Dec/Jan</strong></div>'
        '<div class="fomc-meeting__date">30-1</div></div>'
    )
    reunions = cal.parser_calendrier_fomc(html)
    assert len(reunions) == 1
    assert reunions[0].date_decision == date(2031, 1, 1)


def test_html_non_reconnu_ne_leve_pas_dexception() -> None:
    """Une refonte du site donne une liste vide, pas un plantage."""
    assert cal.parser_calendrier_fomc("") == []
    assert cal.parser_calendrier_fomc("<html><body>Rien à voir</body></html>") == []
    # Les marqueurs sont là mais la structure a changé : liste vide également.
    assert cal.parser_calendrier_fomc('<div class="fomc-meeting">sans panneau</div>') == []


def test_date_impossible_ignoree() -> None:
    """Un 31 février sur la page ne fait pas tomber le module."""
    html = (
        '<h4><a id="1">2026 FOMC Meetings</a></h4>'
        '<div class="row fomc-meeting">'
        '<div class="fomc-meeting__month"><strong>February</strong></div>'
        '<div class="fomc-meeting__date">30-31</div></div>'
    )
    assert cal.parser_calendrier_fomc(html) == []


# ---------------------------------------------------------------------------
# 2. Cache
# ---------------------------------------------------------------------------
def _reunions_factices(jours: list[date]) -> list[cal.ReunionFOMC]:
    """Fabrique des réunions pour les tests de cache."""
    return [cal.ReunionFOMC(date_decision=j, avec_projections=False, libelle=str(j)) for j in jours]


def test_aller_retour_par_le_cache(tmp_path: Path) -> None:
    """Ce qui est écrit dans le cache est relu à l'identique."""
    chemin = tmp_path / "cache.json"
    reunions = _reunions_factices([date(2027, 1, 27), date(2027, 3, 17)])

    assert cal.ecrire_cache_fomc(reunions, chemin)
    relues, collecte, motif = cal.lire_cache_fomc(chemin)

    assert motif == ""
    assert collecte == date.today()
    assert [r.date_decision for r in relues] == [date(2027, 1, 27), date(2027, 3, 17)]


def test_cache_absent_ou_corrompu(tmp_path: Path) -> None:
    """Un cache manquant ou illisible donne un motif, pas une exception."""
    reunions, collecte, motif = cal.lire_cache_fomc(tmp_path / "inexistant.json")
    assert reunions == [] and collecte is None and "aucun cache" in motif

    corrompu = tmp_path / "corrompu.json"
    corrompu.write_text("{ ceci n'est pas du JSON", encoding="utf-8")
    reunions, _, motif = cal.lire_cache_fomc(corrompu)
    assert reunions == [] and "illisible" in motif


def test_entree_de_cache_corrompue_ne_perd_pas_les_autres(tmp_path: Path) -> None:
    """Une ligne abîmée est écartée, le reste du cache survit."""
    chemin = tmp_path / "cache.json"
    chemin.write_text(
        json.dumps(
            {
                "derniere_collecte_reussie": "2026-09-07",
                "reunions": [
                    {"date_decision": "2027-01-27", "avec_projections": False, "libelle": "ok"},
                    {"date_decision": "pas une date", "avec_projections": False},
                    {"avec_projections": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    reunions, collecte, motif = cal.lire_cache_fomc(chemin)
    assert motif == ""
    assert len(reunions) == 1
    assert reunions[0].date_decision == date(2027, 1, 27)
    assert collecte == date(2026, 9, 7)


def test_repli_sur_le_cache_quand_le_reseau_echoue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Collecte impossible : le cache prend le relais et le dit."""
    chemin = tmp_path / "cache.json"
    cal.ecrire_cache_fomc(_reunions_factices([date(2027, 1, 27), date(2027, 3, 17)]), chemin)

    def _injoignable(*args: Any, **kwargs: Any) -> Any:
        raise requests.ConnectionError("réseau coupé")

    monkeypatch.setattr(cal.requests, "get", _injoignable)

    reunions, diagnostic = cal.charger_reunions_fomc(chemin_cache=chemin)
    assert len(reunions) == 2
    assert diagnostic["collecte_reussie"] is False
    assert diagnostic["source"] == "cache local"
    assert "injoignable" in diagnostic["motif"]


def test_collecte_reussie_rafraichit_le_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, html_fed: str
) -> None:
    """Une collecte réussie réécrit le cache avec la date du jour."""
    chemin = tmp_path / "cache.json"

    class _Reponse:
        text = html_fed
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(cal.requests, "get", lambda *a, **k: _Reponse())

    reunions, diagnostic = cal.charger_reunions_fomc(chemin_cache=chemin)
    assert reunions and diagnostic["collecte_reussie"] is True
    assert diagnostic["jours_depuis_collecte"] == 0
    assert chemin.exists()

    contenu = json.loads(chemin.read_text(encoding="utf-8"))
    assert contenu["derniere_collecte_reussie"] == date.today().strftime("%Y-%m-%d")
    assert contenu["n_reunions"] == len(reunions)


# ---------------------------------------------------------------------------
# 3. Filet de sécurité
# ---------------------------------------------------------------------------
_CONFIG_CAL = {
    "fuseau_publication": "America/New_York",
    "publications": [],          # pas de clé FRED dans les tests
    "fomc": {"heure_locale": "14:00"},
    "fenetre_silence_minutes": 120,
}


def _sans_reseau(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rend tout appel sortant impossible."""
    def _injoignable(*args: Any, **kwargs: Any) -> Any:
        raise requests.ConnectionError("réseau coupé")

    monkeypatch.setattr(cal.requests, "get", _injoignable)


def test_alerte_quand_les_deux_sources_echouent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Réseau muet et cache absent : l'alerte se lève, rien ne plante.

    C'est le scénario que le filet de sécurité existe pour rendre visible.
    """
    _sans_reseau(monkeypatch)

    bloc = cal.get_calendrier(
        _CONFIG_CAL,
        maintenant=datetime(2026, 9, 7, 18, tzinfo=timezone.utc),
        chemin_cache=tmp_path / "cache_absent.json",
    )

    assert bloc["alerte_renouvellement"] is True
    assert bloc["n_reunions_a_venir_connues"] == 0
    assert bloc["horizon_couvert_jusquau"] is None
    assert bloc["motif"], "L'alerte doit être motivée."
    assert "aucune réunion connue" in bloc["motif"]
    assert bloc["echeances"] == []
    assert bloc["disponible"] is False
    # Le bloc reste exploitable : aucune clé ne manque.
    assert set(bloc["fomc"]) >= {"collecte_reussie", "n_reunions_a_venir_connues", "alerte_renouvellement"}


def test_alerte_quand_moins_de_deux_reunions_futures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une seule réunion future restante suffit à lever l'alerte."""
    _sans_reseau(monkeypatch)
    chemin = tmp_path / "cache.json"
    cal.ecrire_cache_fomc(_reunions_factices([date(2026, 9, 16)]), chemin)

    bloc = cal.get_calendrier(
        _CONFIG_CAL,
        maintenant=datetime(2026, 9, 7, 18, tzinfo=timezone.utc),
        chemin_cache=chemin,
    )
    assert bloc["alerte_renouvellement"] is True
    assert bloc["n_reunions_a_venir_connues"] == 1
    assert "moins de 2 réunions futures" in bloc["motif"]
    # L'échéance connue reste publiée : l'alerte n'efface pas l'information.
    assert len(bloc["echeances"]) == 1


def test_pas_dalerte_quand_le_calendrier_est_fourni(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cache récent et plusieurs réunions à venir : aucune alerte."""
    _sans_reseau(monkeypatch)
    chemin = tmp_path / "cache.json"
    cal.ecrire_cache_fomc(
        _reunions_factices([date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9)]),
        chemin,
    )

    bloc = cal.get_calendrier(
        _CONFIG_CAL,
        maintenant=datetime(2026, 9, 7, 18, tzinfo=timezone.utc),
        chemin_cache=chemin,
    )
    assert bloc["alerte_renouvellement"] is False
    assert bloc["n_reunions_a_venir_connues"] == 3
    assert bloc["horizon_couvert_jusquau"] == "2026-12-09"
    # Hors alerte, aucun motif : un texte affiché à côté d'un drapeau à false
    # se lirait comme un avertissement alors que tout va bien.
    assert bloc["motif"] == ""
    # Le recours au cache reste néanmoins lisible, sans dramatiser.
    assert bloc["fomc"]["source"] == "cache local"
    assert bloc["fomc"]["collecte_reussie"] is False


def test_cache_tres_ancien_leve_lalerte(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un cache périmé alerte même s'il reste des réunions.

    Le calendrier a pu être révisé entre-temps sans qu'on le sache : des dates
    anciennes mais nombreuses inspirent une fausse confiance.
    """
    _sans_reseau(monkeypatch)
    chemin = tmp_path / "cache.json"
    chemin.write_text(
        json.dumps(
            {
                "derniere_collecte_reussie": "2020-01-01",
                "reunions": [
                    {"date_decision": "2026-09-16", "avec_projections": True, "libelle": "a"},
                    {"date_decision": "2026-10-28", "avec_projections": False, "libelle": "b"},
                    {"date_decision": "2026-12-09", "avec_projections": True, "libelle": "c"},
                ],
            }
        ),
        encoding="utf-8",
    )

    bloc = cal.get_calendrier(
        _CONFIG_CAL,
        maintenant=datetime(2026, 9, 7, 18, tzinfo=timezone.utc),
        chemin_cache=chemin,
    )
    assert bloc["alerte_renouvellement"] is True
    assert bloc["n_reunions_a_venir_connues"] == 3
    assert "à vérifier, source indisponible depuis" in bloc["motif"]


def test_reunion_avec_projections_nommee_dans_lecheance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Les réunions à projections sont signalées : elles bougent plus l'or."""
    _sans_reseau(monkeypatch)
    chemin = tmp_path / "cache.json"
    cal.ecrire_cache_fomc(
        [
            cal.ReunionFOMC(date(2026, 9, 16), True, "September 15-16*"),
            cal.ReunionFOMC(date(2026, 10, 28), False, "October 27-28"),
        ],
        chemin,
    )

    bloc = cal.get_calendrier(
        _CONFIG_CAL,
        maintenant=datetime(2026, 9, 7, 18, tzinfo=timezone.utc),
        chemin_cache=chemin,
    )
    noms = [e["nom"] for e in bloc["echeances"]]
    assert any("projections économiques" in n for n in noms)
    assert any(n.endswith("(FOMC)") for n in noms)
    # L'heure reste conventionnelle : la Fed ne publie pas d'horaire machine.
    assert all(e["heure_conventionnelle"] for e in bloc["echeances"])
    assert all(e["heure_locale_new_york"] == "14:00" for e in bloc["echeances"])


# ---------------------------------------------------------------------------
# 4. Remontée jusqu'au rapport
# ---------------------------------------------------------------------------
def test_rapport_complet_survit_a_lechec_des_deux_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FRED muet et page FOMC injoignable : le rapport aboutit et alerte.

    Le test vise l'exigence de fond du moteur : une panne de collecte ne doit
    jamais interrompre la production du rapport, ni disparaître dans un bloc
    que personne ne lit. Elle doit remonter dans ``meta.avertissement``, à
    l'endroit exact où un lecteur pressé regarde.
    """
    import pandas as pd

    from dataio import gold_flows, macro, market, news
    from dataio import cot as cot_io
    from modules.gold import geopolitics, run as moteur

    # --- Toutes les sources externes sont coupées -------------------------
    def _injoignable(*args: Any, **kwargs: Any) -> Any:
        raise requests.ConnectionError("réseau coupé")

    monkeypatch.setattr(cal.requests, "get", _injoignable)
    monkeypatch.setenv("FRED_API_KEY", "")          # FRED hors service
    monkeypatch.setattr(macro, "get_many", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(moteur.macro, "get_many", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(
        market, "get_prices", lambda *a, **k: pd.DataFrame(columns=list(market.COLONNES))
    )
    monkeypatch.setattr(
        moteur.market, "get_prices", lambda *a, **k: pd.DataFrame(columns=list(market.COLONNES))
    )
    monkeypatch.setattr(cot_io, "fetch_cot_or", lambda *a, **k: (pd.DataFrame(), "aucune"))
    monkeypatch.setattr(moteur.cot_io, "fetch_cot_or", lambda *a, **k: (pd.DataFrame(), "aucune"))
    monkeypatch.setattr(gold_flows, "get_flux_or", lambda *a, **k: {})
    monkeypatch.setattr(moteur.gold_flows, "get_flux_or", lambda *a, **k: {})
    monkeypatch.setattr(news, "gdelt_volume_journalier", lambda *a, **k: ({}, "coupé"))
    monkeypatch.setattr(
        news, "gdelt_intensity", lambda *a, **k: {"disponible": False, "commentaire": "coupé"}
    )
    # Narratif (GDELT DOC) et activité par acteur (GDELT Events) des dossiers
    # géopolitiques : deux dépendances réseau propres à la partie B, coupées
    # comme le reste plutôt qu'oubliées — sans quoi ce test, qui prétend
    # couper toutes les sources externes, en laisserait deux passer.
    monkeypatch.setattr(news, "fetch_gdelt", lambda *a, **k: [])
    monkeypatch.setattr(
        geopolitics.gdelt_events, "recuperer_dernier_export",
        lambda *a, **k: ([], "coupé"),
    )
    monkeypatch.setattr(
        geopolitics.gdelt_events, "recuperer_exports_recents",
        lambda *a, **k: ([], {"n_exports_lus": 0, "n_exports_attendus": 96, "heures": 24, "motif": "coupé"}),
    )
    # Le cache FOMC pointe vers un fichier qui n'existe pas.
    monkeypatch.setattr(cal, "CACHE_FOMC", tmp_path / "cache_absent.json")

    configuration = {
        "juste_valeur": {"debut_historique": "2024-01-01"},
        "cot": {},
        "calendrier": _CONFIG_CAL,
        "geopolitique": {},
        "analogues": {},
        "biais": {"ponderations": {}},
        "explication": {"activee": False},
    }

    rapport = moteur.construire_rapport(
        configuration, date_rapport=date(2026, 9, 7), avec_explication=False
    )

    # 1. Le rapport existe malgré tout.
    assert rapport["meta"]["date"] == "2026-09-07"
    assert rapport["biais"]["biais"] == "indeterminé"

    # 2. L'alerte est levée dans le bloc calendrier.
    assert rapport["calendrier"]["alerte_renouvellement"] is True
    assert rapport["calendrier"]["n_reunions_a_venir_connues"] == 0

    # 3. Et surtout, elle est remontée jusqu'à meta.
    assert rapport["meta"]["alertes"], "L'alerte doit figurer dans meta.alertes."
    assert any(a["sujet"] == "calendrier FOMC" for a in rapport["meta"]["alertes"])
    assert "ALERTE calendrier FOMC" in rapport["meta"]["avertissement"]
    assert rapport["meta"]["donnees_partielles"] is True


def test_avertissement_meta_distingue_echecs_et_alertes() -> None:
    """Une source muette et un calendrier à renouveler ne sont pas la même chose."""
    from modules.gold.run import _avertissement

    assert _avertissement([], []) == ""

    seul_echec = _avertissement(["séries FRED"], [])
    assert "indisponible" in seul_echec and "ALERTE" not in seul_echec

    seule_alerte = _avertissement([], [{"bloc": "calendrier", "sujet": "calendrier FOMC", "motif": "épuisé"}])
    assert "ALERTE calendrier FOMC" in seule_alerte and "indisponible" not in seule_alerte

    les_deux = _avertissement(
        ["séries FRED"], [{"bloc": "calendrier", "sujet": "calendrier FOMC", "motif": "épuisé"}]
    )
    assert "indisponible" in les_deux and "ALERTE calendrier FOMC" in les_deux
