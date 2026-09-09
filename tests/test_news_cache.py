"""Tests du cache disque des appels GDELT.

Le fil quantique, les fils crypto et géopolitique, et la chaîne de
transmission géopolitique de l'or interrogent parfois les mêmes requêtes
GDELT dans une même exécution du workflow. Le cache doit éviter l'appel
réseau répété, sans jamais faire échouer l'appelant si le disque est
indisponible.

Exécution :
    pytest tests/test_news_cache.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dataio import news


class _ReponseFactice:
    """Simule ``requests.Response`` pour un test hors ligne."""

    def __init__(self, charge: dict) -> None:
        self.status_code = 200
        self._charge = charge
        self.text = "{}"

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._charge


def test_deuxieme_appel_identique_ne_touche_pas_le_reseau(tmp_path: Path, monkeypatch) -> None:
    """Deux requêtes GDELT identiques ne déclenchent qu'un seul appel réseau."""
    monkeypatch.setattr(news, "CACHE_DIR", tmp_path)
    appels = []

    def _faux_get(url, params=None, timeout=None, headers=None):
        appels.append(params)
        return _ReponseFactice({"articles": [{"title": "Un article", "url": "https://x.test"}]})

    monkeypatch.setattr(news.requests, "get", _faux_get)

    premier = news._appel_gdelt({"query": "or", "mode": "artlist"})
    second = news._appel_gdelt({"query": "or", "mode": "artlist"})

    assert premier == second
    assert len(appels) == 1, "Le second appel aurait dû être servi depuis le cache."


def test_parametres_differents_ne_partagent_pas_le_cache(tmp_path: Path, monkeypatch) -> None:
    """Deux requêtes différentes déclenchent bien deux appels réseau distincts."""
    monkeypatch.setattr(news, "CACHE_DIR", tmp_path)
    appels = []

    def _faux_get(url, params=None, timeout=None, headers=None):
        appels.append(params)
        return _ReponseFactice({"articles": []})

    monkeypatch.setattr(news.requests, "get", _faux_get)

    news._appel_gdelt({"query": "or", "mode": "artlist"})
    news._appel_gdelt({"query": "petrole", "mode": "artlist"})

    assert len(appels) == 2


def test_cache_expire_est_reinterroge(tmp_path: Path, monkeypatch) -> None:
    """Un cache plus vieux que la durée de vie configurée est ignoré."""
    monkeypatch.setattr(news, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(news, "CACHE_TTL_SECONDES", 0.0)
    appels = []

    def _faux_get(url, params=None, timeout=None, headers=None):
        appels.append(params)
        return _ReponseFactice({"articles": []})

    monkeypatch.setattr(news.requests, "get", _faux_get)

    news._appel_gdelt({"query": "or", "mode": "artlist"})
    news._appel_gdelt({"query": "or", "mode": "artlist"})

    assert len(appels) == 2, "Un TTL nul ne doit jamais servir une réponse en cache."


def test_echec_decriture_du_cache_ne_casse_pas_lappel(tmp_path: Path, monkeypatch) -> None:
    """Un dossier de cache inaccessible dégrade sans exception, résultat inchangé."""
    cache_impossible = tmp_path / "un_fichier_pas_un_dossier"
    cache_impossible.write_text("occupe le chemin", encoding="utf-8")
    monkeypatch.setattr(news, "CACHE_DIR", cache_impossible)

    def _faux_get(url, params=None, timeout=None, headers=None):
        return _ReponseFactice({"articles": [{"title": "Un article", "url": "https://x.test"}]})

    monkeypatch.setattr(news.requests, "get", _faux_get)

    resultat = news._appel_gdelt({"query": "or", "mode": "artlist"})
    assert resultat == {"articles": [{"title": "Un article", "url": "https://x.test"}]}
