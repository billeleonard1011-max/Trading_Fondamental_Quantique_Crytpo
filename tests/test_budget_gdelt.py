"""Tests du budget d'attente GDELT, qui rend le rapport publiable sous limitation.

Le défaut corrigé, mesuré sur trois exécutions CI du 11 septembre 2026 : GDELT
refuse presque toutes les requêtes venues d'un exécuteur GitHub, dont les
adresses sont partagées et durement limitées. Le repli par requête, à quatre
tentatives espacées de 15, 30 puis 60 secondes, coûtait alors vingt et une
minutes de pure temporisation. Le job a été tué à son délai d'expiration de
quarante-cinq minutes, à la même étape, trois fois de suite, sans jamais rien
publier.

Un rapport partiel qui paraît vaut mieux qu'un rapport complet qui n'existe
pas. C'est la règle de dégradation gracieuse que le reste du projet applique,
et le budget la rend mécanique.

Aucun test n'accède au réseau.

Exécution :
    pytest tests/test_budget_gdelt.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dataio import news


class _ReponseRefus:
    """Simule le refus pour dépassement de débit que GDELT renvoie en CI."""

    status_code = 429
    text = ""

    def raise_for_status(self) -> None:
        raise news.requests.HTTPError("429 Client Error: Too Many Requests")

    def json(self) -> dict:
        return {}


class _ReponseOk:
    """Simule une réponse aboutie."""

    status_code = 200
    text = "{}"

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"articles": []}


@pytest.fixture
def gdelt_qui_refuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Remplace le réseau par un GDELT qui refuse tout, et compte les attentes."""
    monkeypatch.setattr(news, "CACHE_DIR", tmp_path)
    # Une attente réelle de dix secondes, mais jamais dormie : le budget se
    # mesure sur les secondes demandées, pas sur celles vraiment passées.
    monkeypatch.setattr(news, "ATTENTE_429_SECONDES", 10.0)
    monkeypatch.setattr(news.time, "sleep", lambda _: None)

    appels: list[dict] = []

    def _faux_get(url, params=None, timeout=None, headers=None):
        appels.append(dict(params or {}))
        return _ReponseRefus()

    monkeypatch.setattr(news.requests, "get", _faux_get)
    news.reinitialiser_budget_gdelt()
    return appels


def test_le_budget_neuf_laisse_une_requete_epuiser_ses_tentatives(
    gdelt_qui_refuse, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une limitation passagère doit encore avoir droit à tout le repli.

    Le budget ne remplace pas le repli, il le borne. Tant qu'il reste de la
    marge, rien ne change par rapport au comportement d'avant.
    """
    monkeypatch.setattr(news, "BUDGET_ATTENTE_GDELT", 300.0)
    assert news._appel_gdelt({"query": "or", "mode": "artlist"}, essais=4) is None
    assert len(gdelt_qui_refuse) == 4, "les quatre tentatives doivent avoir lieu"


def test_une_fois_le_budget_epuise_les_requetes_echouent_du_premier_coup(
    gdelt_qui_refuse, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C'est le cœur du correctif : cesser de payer pour un refus systématique.

    Avec 10 s d'attente de base, une requête qui épuise ses quatre tentatives
    consomme 10 + 20 + 40 = 70 s. Un budget de 70 s en laisse donc passer
    exactement une, et pas une seconde de plus.
    """
    monkeypatch.setattr(news, "BUDGET_ATTENTE_GDELT", 70.0)

    news._appel_gdelt({"query": "premiere", "mode": "artlist"}, essais=4)
    assert len(gdelt_qui_refuse) == 4, "la première garde tout son repli"
    assert news.budget_gdelt_restant() == 0.0

    for numero in range(5):
        news._appel_gdelt({"query": f"suivante{numero}", "mode": "artlist"}, essais=4)

    # Cinq requêtes de plus, une seule tentative chacune : le budget est vide.
    assert len(gdelt_qui_refuse) == 4 + 5


def test_le_budget_epuise_ne_fait_pas_lever(gdelt_qui_refuse, monkeypatch: pytest.MonkeyPatch) -> None:
    """L'appelant reçoit None et publie un dossier indisponible, il n'explose pas."""
    monkeypatch.setattr(news, "BUDGET_ATTENTE_GDELT", 0.0)
    assert news._appel_gdelt({"query": "or", "mode": "artlist"}, essais=4) is None


def test_le_budget_ne_se_consomme_pas_quand_gdelt_repond(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une exécution où tout passe doit finir avec son budget intact."""
    monkeypatch.setattr(news, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(news, "BUDGET_ATTENTE_GDELT", 300.0)
    monkeypatch.setattr(news.requests, "get", lambda *a, **k: _ReponseOk())
    news.reinitialiser_budget_gdelt()

    for numero in range(5):
        assert news._appel_gdelt({"query": f"q{numero}", "mode": "artlist"}) == {"articles": []}
    assert news.budget_gdelt_restant() == 300.0


def test_le_budget_se_remet_a_neuf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un processus neuf repart entier ; c'est ce que la remise à zéro imite."""
    monkeypatch.setattr(news, "BUDGET_ATTENTE_GDELT", 120.0)
    news.reinitialiser_budget_gdelt()
    assert news.budget_gdelt_restant() == 120.0


def test_le_budget_borne_le_temps_total_dune_execution(
    gdelt_qui_refuse, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Propriété qui compte vraiment : le temps d'attente total est plafonné.

    C'est ce qui empêche l'exécution de dépasser son délai. Quinze requêtes
    toutes refusées ne doivent pas coûter plus que le budget, là où l'ancien
    comportement aurait demandé quinze fois 70 secondes.
    """
    monkeypatch.setattr(news, "BUDGET_ATTENTE_GDELT", 150.0)
    attentes: list[float] = []
    monkeypatch.setattr(news.time, "sleep", attentes.append)

    for numero in range(15):
        news._appel_gdelt({"query": f"dossier{numero}", "mode": "artlist"}, essais=4)

    assert sum(attentes) <= 150.0, f"{sum(attentes)} s d'attente pour un budget de 150 s"
    assert sum(attentes) < 15 * 70, "l'ancien comportement aurait demandé 1050 s"
