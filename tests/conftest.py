"""Réglages communs à toute la suite de tests.

Aucun test n'accède au réseau : les appels HTTP sont systématiquement
remplacés par des doublures. L'espacement volontaire des appels GDELT
(``dataio.news.INTERVALLE_MIN_GDELT``, 5,5 s en production) n'a donc rien à
protéger ici — il ne ferait qu'ajouter des minutes d'attente à une suite qui
tourne en deux secondes. Il est neutralisé pour les tests, et seulement pour
eux : la valeur de production reste inchangée dans le module.
"""

from __future__ import annotations

import pytest

from dataio import news
from modules import quota_llm
from modules.gold import geopolitics
from modules.gold import rotation_geopolitique


@pytest.fixture(autouse=True)
def _sans_espacement_gdelt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supprime l'attente entre appels GDELT pour la durée d'un test.

    Le budget d'attente du processus est aussi remis à neuf : il est global
    par construction, et toute la suite tourne dans un seul processus. Sans
    cette remise à zéro, un test qui l'épuise ferait échouer les suivants
    selon l'ordre d'exécution, ce qui est la pire espèce de test instable.
    """
    monkeypatch.setattr(news, "INTERVALLE_MIN_GDELT", 0.0)
    monkeypatch.setattr(news, "ATTENTE_429_SECONDES", 0.0)
    monkeypatch.setattr(geopolitics, "ATTENTE_REESSAI_DOSSIER_SECONDES", 0.0)
    news.reinitialiser_budget_gdelt()


@pytest.fixture(autouse=True)
def _quota_llm_isole(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Isole le compteur d'analyses : un test ne touche pas au fichier du dépôt.

    Constaté en écrivant ces tests : ``construire_fil`` écrit le compteur du
    jour, et la suite l'avait déjà porté à 74 analyses fictives dans
    ``reports/quota_llm.json`` — de quoi rogner pour de bon le budget d'une
    vraie journée.
    """
    monkeypatch.setattr(
        quota_llm, "FICHIER_QUOTA", tmp_path_factory.mktemp("quota") / "quota_llm.json"
    )


@pytest.fixture(autouse=True)
def _rotation_geopolitique_isolee(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Isole l'état de rotation des dossiers, pour la même raison que le quota.

    Constaté en écrivant la rotation : ``analyser_dossiers`` enregistre les
    séries de volumes obtenues, et la suite avait déjà rempli le fichier du
    dépôt de mesures fictives. Elles auraient daté d'aujourd'hui des dossiers
    jamais mesurés, qui seraient alors sortis de la file d'attente réelle — une
    rotation faussée, silencieusement, par la suite de tests.
    """
    monkeypatch.setattr(
        rotation_geopolitique,
        "FICHIER_MESURES",
        tmp_path_factory.mktemp("rotation") / "geopolitique_dernieres_mesures.json",
    )
