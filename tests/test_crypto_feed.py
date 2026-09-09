"""Tests du fil d'actualité crypto.

Mêmes propriétés que le fil quantique (voir ``tests/test_quantum_feed.py``),
appliquées aux dix jetons de ``crypto_watchlist`` : rien n'est réexpliqué
deux fois, le format de sortie est un contrat partagé avec les deux autres
fils, et un item sans jeton suivi n'entre pas dans le fil.

Aucun test n'accède au réseau.

Exécution :
    pytest tests/test_crypto_feed.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from dataio import news
from modules.crypto import feed
from modules.quantum import feed as feed_quantique
from modules.quantum import moves

_CONFIG = {
    "crypto_watchlist": [
        {"symbol": "BTC", "keywords": ["Bitcoin"]},
        {"symbol": "ETH", "keywords": ["Ethereum", "Ether"]},
    ],
    "feed_crypto": {
        "exclusions": [],
        "max_analyses_par_execution": 8,
    },
}


def _article(titre: str, source: str = "Test Feed", url: str = "") -> news.NewsItem:
    """Fabrique un article de test."""
    return news.NewsItem(
        titre=titre,
        url=url or f"https://exemple.test/{abs(hash(titre)) % 10**8}",
        source=source,
        date=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# 1. Ne rien réexpliquer deux fois
# ---------------------------------------------------------------------------
def test_deuxieme_execution_ne_reexplique_rien(tmp_path: Path) -> None:
    """Deux passages sur les mêmes données : la seconde n'analyse plus rien."""
    articles = [
        _article("Bitcoin rebounds after a volatile week"),
        _article("Ethereum upgrade goes live on mainnet"),
    ]

    premiers = feed.construire_fil(_CONFIG, articles, identifiants_connus=set())
    assert len(premiers) == 2
    assert all(i["nouveaute"] for i in premiers)
    assert all(i["a_une_analyse_interne"] for i in premiers)

    historique = tmp_path / "feed_historique.jsonl"
    feed.publier_fil(premiers, dossier=tmp_path, chemin_historique=historique)

    connus = feed.charger_historique(historique)
    assert len(connus) == 2

    seconds = feed.construire_fil(_CONFIG, articles, identifiants_connus=connus)
    assert len(seconds) == 2, "Les items doivent rester au fil, sans être réanalysés."
    assert not any(i["nouveaute"] for i in seconds)
    assert not any(i["a_une_analyse_interne"] for i in seconds)


def test_historique_en_ajout_seul(tmp_path: Path) -> None:
    """Seuls les items nouveaux sont ajoutés à l'historique."""
    historique = tmp_path / "feed_historique.jsonl"
    premiers = feed.construire_fil(
        _CONFIG, [_article("Bitcoin ETF sees new inflows")], identifiants_connus=set()
    )
    feed.publier_fil(premiers, dossier=tmp_path, chemin_historique=historique)
    assert len(historique.read_text(encoding="utf-8").strip().split("\n")) == 1

    connus = feed.charger_historique(historique)
    seconds = feed.construire_fil(
        _CONFIG, [_article("Bitcoin ETF sees new inflows")], identifiants_connus=connus
    )
    feed.publier_fil(seconds, dossier=tmp_path, chemin_historique=historique)
    assert len(historique.read_text(encoding="utf-8").strip().split("\n")) == 1


def test_identifiant_stable_malgre_lurl() -> None:
    """La même dépêche sous deux adresses ne compte qu'une fois."""
    a = feed.identifiant_item("Bitcoin rebounds after a volatile week", "https://a.test/1")
    b = feed.identifiant_item("BITCOIN  rebounds after a  volatile week!", "https://b.test/2")
    assert a == b


def test_historique_corrompu_ne_perd_pas_tout(tmp_path: Path) -> None:
    """Une ligne illisible est ignorée, le reste de l'historique survit."""
    historique = tmp_path / "feed_historique.jsonl"
    historique.write_text('{"id": "aaa"}\nligne corrompue\n{"id": "bbb"}\n', encoding="utf-8")
    assert feed.charger_historique(historique) == {"aaa", "bbb"}


def test_historique_absent_est_une_premiere_execution(tmp_path: Path) -> None:
    """Sans fichier, l'ensemble est vide et rien n'échoue."""
    assert feed.charger_historique(tmp_path / "jamais_ecrit.jsonl") == set()


# ---------------------------------------------------------------------------
# 2. Format unifié, partagé avec les deux autres fils
# ---------------------------------------------------------------------------
def test_chaque_item_respecte_exactement_le_format() -> None:
    """Les clés d'un item sont exactement celles du contrat, ni plus ni moins."""
    items = feed.construire_fil(
        _CONFIG,
        [_article("Ethereum and Bitcoin both rally on ETF news")],
        identifiants_connus=set(),
    )
    assert items, "Aucun item produit."
    for item in items:
        assert set(item) == set(feed.CLES_ITEM), (
            f"Clés inattendues : {set(item) ^ set(feed.CLES_ITEM)}"
        )
        assert item["categorie"] in feed.CATEGORIES
        assert isinstance(item["a_une_analyse_interne"], bool)
        assert isinstance(item["nouveaute"], bool)
        assert isinstance(item["tickers_ou_themes_lies"], list)
        assert item["id"] and item["titre_affiche"] and item["horodatage_utc"]


def test_contrat_identique_au_fil_quantique() -> None:
    """Les trois fils partagent exactement le même contrat de sortie.

    Un des deux modules qui dérive sans que l'autre suive casserait la page
    web sans qu'aucun test unitaire isolé ne le remarque.
    """
    assert feed.CATEGORIES == feed_quantique.CATEGORIES
    assert feed.CLES_ITEM == feed_quantique.CLES_ITEM


def test_analyse_absente_est_nulle_pas_vide() -> None:
    """Un item non analysé porte ``None``, et le drapeau correspondant."""
    items = feed.construire_fil(_CONFIG, [_article("Bitcoin news")], identifiants_connus=set())
    connus = {i["id"] for i in items}
    seconds = feed.construire_fil(_CONFIG, [_article("Bitcoin news")], connus)
    assert seconds[0]["analyse_interne"] is None
    assert seconds[0]["a_une_analyse_interne"] is False


# ---------------------------------------------------------------------------
# 3. Pertinence : seuls les jetons suivis entrent dans le fil
# ---------------------------------------------------------------------------
def test_item_sans_jeton_suivi_ecarte() -> None:
    """Un article qui ne cite aucun jeton suivi n'entre pas."""
    items = feed.construire_fil(
        _CONFIG, [_article("Une dépêche sans rapport avec la crypto")], set()
    )
    assert items == []


def test_aucune_recommandation_dans_le_fil() -> None:
    """Le fil produit passe le contrôle anti-recommandation, réutilisé du fil quantique."""
    items = feed.construire_fil(
        _CONFIG,
        [_article("Bitcoin gains"), _article("Ethereum rallies")],
        identifiants_connus=set(),
    )
    assert moves.verifier_absence_recommandation(items) == []


def test_gabarit_cite_la_variation_du_jeton() -> None:
    """Quand une variation 24 h est fournie, le gabarit la cite telle quelle."""
    items = feed.construire_fil(
        _CONFIG,
        [_article("Bitcoin drops sharply overnight")],
        identifiants_connus=set(),
        variations_par_symbole={
            "BTC": {"symbole": "BTC", "variation_24h_pct": -5.3},
        },
    )
    analyse = items[0]["analyse_interne"]
    assert analyse is not None
    assert "-5.3" in analyse
    assert "BTC" in analyse
