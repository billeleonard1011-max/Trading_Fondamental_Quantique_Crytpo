"""Tests du fil d'actualité géopolitique.

Ce fil a une règle de pertinence différente des deux autres : un item n'y
entre que s'il relève d'un thème à canal de transmission connu vers l'or
(voir ``modules/gold/geopolitics.py``), pas simplement parce qu'il parle de
géopolitique. C'est la propriété vérifiée en priorité ici.

Aucun test n'accède au réseau.

Exécution :
    pytest tests/test_geopolitique_feed.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from dataio import news
from modules.geopolitique import feed
from modules.quantum import feed as feed_quantique
from modules.quantum import moves

_CONFIG = {
    "themes": [
        {
            "nom": "Tensions énergétiques",
            "query": '("oil supply" OR "energy crisis" OR "pipeline attack")',
        },
        {
            "nom": "Sanctions",
            "query": '("new sanctions" OR "asset freeze")',
        },
    ],
    "feed": {
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
        _article("Oil supply disruption rattles markets"),
        _article("New sanctions package targets exports"),
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
    assert len(seconds) == 2
    assert not any(i["nouveaute"] for i in seconds)
    assert not any(i["a_une_analyse_interne"] for i in seconds)


def test_historique_en_ajout_seul(tmp_path: Path) -> None:
    """Seuls les items nouveaux sont ajoutés à l'historique."""
    historique = tmp_path / "feed_historique.jsonl"
    premiers = feed.construire_fil(
        _CONFIG, [_article("Oil supply disruption rattles markets")], identifiants_connus=set()
    )
    feed.publier_fil(premiers, dossier=tmp_path, chemin_historique=historique)
    assert len(historique.read_text(encoding="utf-8").strip().split("\n")) == 1

    connus = feed.charger_historique(historique)
    seconds = feed.construire_fil(
        _CONFIG, [_article("Oil supply disruption rattles markets")], identifiants_connus=connus
    )
    feed.publier_fil(seconds, dossier=tmp_path, chemin_historique=historique)
    assert len(historique.read_text(encoding="utf-8").strip().split("\n")) == 1


def test_identifiant_stable_malgre_lurl() -> None:
    """La même dépêche sous deux adresses ne compte qu'une fois."""
    a = feed.identifiant_item("Oil supply disruption rattles markets", "https://a.test/1")
    b = feed.identifiant_item("OIL SUPPLY  disruption rattles  markets!", "https://b.test/2")
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
        [_article("New sanctions package targets exports")],
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
    """Les trois fils partagent exactement le même contrat de sortie."""
    assert feed.CATEGORIES == feed_quantique.CATEGORIES
    assert feed.CLES_ITEM == feed_quantique.CLES_ITEM


def test_analyse_absente_est_nulle_pas_vide() -> None:
    """Un item non analysé porte ``None``, et le drapeau correspondant."""
    items = feed.construire_fil(
        _CONFIG, [_article("New sanctions package targets exports")], identifiants_connus=set()
    )
    connus = {i["id"] for i in items}
    seconds = feed.construire_fil(
        _CONFIG, [_article("New sanctions package targets exports")], connus
    )
    assert seconds[0]["analyse_interne"] is None
    assert seconds[0]["a_une_analyse_interne"] is False


# ---------------------------------------------------------------------------
# 3. Pertinence : seul un canal de transmission connu vers l'or fait entrer
# ---------------------------------------------------------------------------
def test_actualite_sans_canal_de_transmission_ecartee() -> None:
    """Une actualité géopolitique sans rapport avec un thème suivi n'entre pas.

    C'est la propriété qui distingue ce fil d'un fil géopolitique généraliste :
    l'important n'est pas la gravité de l'événement mais l'existence d'un
    canal de transmission déjà mesuré vers l'or.
    """
    items = feed.construire_fil(
        _CONFIG,
        [_article("Local elections held peacefully in a small country")],
        set(),
    )
    assert items == []


def test_theme_a_canal_de_transmission_retenu() -> None:
    """Une actualité qui relève d'un thème suivi entre dans le fil, avec son thème."""
    items = feed.construire_fil(
        _CONFIG, [_article("Oil supply disruption rattles markets")], set()
    )
    assert len(items) == 1
    assert items[0]["tickers_ou_themes_lies"] == ["Tensions énergétiques"]


def test_mot_seul_trop_generique_nest_pas_un_canal_de_transmission() -> None:
    """Un mot isolé et générique du thème ne suffit pas à faire entrer un item.

    Cas réel observé lors d'une exécution en direct : la requête GDELT du
    thème « Conflits majeurs » contient « invasion » nu (reprise telle quelle
    de modules/gold/geopolitics.py, où elle sert à mesurer un volume
    d'articles, pas à filtrer des titres un par un). Appliqué au seul titre
    d'un flux RSS, ce mot a fait remonter un fait divers de cambriolage et un
    débat migratoire, sans aucun rapport avec un conflit majeur.
    """
    config = {
        "themes": [
            {"nom": "Conflits majeurs", "query": '("military strike" OR "invasion")'},
        ],
        "feed": {"exclusions": [], "max_analyses_par_execution": 8},
    }
    items = feed.construire_fil(
        config,
        [_article("Inmate loses bid for new trial in Huntingdon County home invasion")],
        set(),
    )
    assert items == []


def test_aucune_recommandation_dans_le_fil() -> None:
    """Le fil produit passe le contrôle anti-recommandation, réutilisé du fil quantique."""
    items = feed.construire_fil(
        _CONFIG,
        [_article("Oil supply disruption rattles markets"),
         _article("New sanctions package targets exports")],
        identifiants_connus=set(),
    )
    assert moves.verifier_absence_recommandation(items) == []


def test_gabarit_cite_la_mesure_du_theme() -> None:
    """Quand une mesure d'intensité est fournie, le gabarit la cite telle quelle."""
    items = feed.construire_fil(
        _CONFIG,
        [_article("Oil supply disruption rattles markets")],
        identifiants_connus=set(),
        mesures_par_theme={
            "Tensions énergétiques": {
                "disponible": True,
                "intensite_ratio": 2.4,
                "trajectoire": "en accélération",
            },
        },
    )
    analyse = items[0]["analyse_interne"]
    assert analyse is not None
    assert "2.4" in analyse
    assert "en accélération" in analyse


# ---------------------------------------------------------------------------
# 4. Réutilisation des thèmes de modules.gold.geopolitics
# ---------------------------------------------------------------------------
def test_extraction_des_termes_reprend_la_syntaxe_gdelt_des_themes() -> None:
    """Les termes d'un thème sont extraits de sa requête GDELT sans réécriture."""
    termes = feed._termes_theme(
        '("oil supply" OR "energy crisis" OR "pipeline attack" OR "strait of hormuz")'
    )
    assert termes == ["oil supply", "energy crisis", "pipeline attack", "strait of hormuz"]


def test_themes_reels_de_gold_yaml_sont_exploitables() -> None:
    """Les quatre thèmes réellement configurés pour l'or produisent des termes.

    Garantit que ce module reste synchronisé avec
    ``modules.gold.geopolitics`` : si la syntaxe des requêtes changeait sans
    que ce module suive, ce test le remarquerait avant la production.
    """
    import yaml

    from pathlib import Path as _Path

    racine = _Path(__file__).resolve().parents[1]
    configuration = yaml.safe_load((racine / "config" / "gold.yaml").read_text(encoding="utf-8"))
    themes = configuration["geopolitique"]["themes"]
    assert len(themes) == 4
    for theme in themes:
        termes = feed._termes_theme(theme["query"])
        assert termes, f"Aucun terme extrait pour {theme['nom']!r}"


# ---------------------------------------------------------------------------
# Le filtre de pertinence : titre ET chapô, mots-clés des dossiers
# ---------------------------------------------------------------------------
_DOSSIERS = [
    {"id": "israel_gaza", "nom_affiche": "Israël - Gaza", "mots_cles": ["Israël", "Gaza", "Hamas"]},
    {"id": "moyen_orient", "nom_affiche": "Moyen-Orient (région)", "mots_cles": ["Houthi", "mer Rouge", "Red Sea"]},
    {"id": "politique_monetaire", "nom_affiche": "Politique monétaire (Fed, BCE)",
     "mots_cles": ["Federal Reserve", "FOMC", "Powell"]},
]


def _item(titre: str, resume: str = "") -> news.NewsItem:
    return news.NewsItem(
        titre=titre, url=f"https://exemple.test/{abs(hash(titre))}", source="Reuters",
        date=datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc), resume=resume,
    )


def _fil(articles, connus=None):
    return feed.construire_fil(
        _CONFIG, articles, identifiants_connus=connus or set(), dossiers=_DOSSIERS,
    )


def test_un_item_reconnu_par_son_seul_chapo_est_retenu() -> None:
    """Le défaut corrigé : le chapô était rempli par fetch_rss et ignoré."""
    articles = [_item("Spokesperson to make a statement at 9 AM ET",
                      "The Houthi movement said it would address shipping in the Red Sea.")]
    items = _fil(articles)
    assert len(items) == 1
    assert "Moyen-Orient (région)" in items[0]["tickers_ou_themes_lies"]


def test_le_titre_seul_suffit_toujours() -> None:
    items = _fil([_item("Powell signals a pause in rate decisions")])
    assert len(items) == 1
    assert "Politique monétaire (Fed, BCE)" in items[0]["tickers_ou_themes_lies"]


def test_le_fait_divers_qui_avait_motive_le_durcissement_reste_ecarte() -> None:
    """« home invasion » : le mot nu « invasion » n'est le mot-clé d'aucun dossier."""
    ecartes = [
        _item("Police investigate a home invasion in a quiet suburb",
              "Two suspects fled after the home invasion, local police said."),
        _item("Migration debate divides parliament",
              "Lawmakers clashed over an invasion of migrants, one member said."),
    ]
    assert _fil(ecartes) == []


def test_un_terme_ne_se_declenche_pas_au_milieu_dun_mot() -> None:
    """« Iran » ne doit pas sortir de « Tirana », mais doit sortir d'« Iranian »."""
    dossiers = [{"id": "iran_etats_unis", "nom_affiche": "Iran - États-Unis", "mots_cles": ["Iran"]}]
    faux = feed.construire_fil(_CONFIG, [_item("Tirana hosts a regional summit")], set(), dossiers=dossiers)
    assert faux == []
    vrai = feed.construire_fil(_CONFIG, [_item("Iranian officials meet negotiators")], set(), dossiers=dossiers)
    assert len(vrai) == 1 and "Iran - États-Unis" in vrai[0]["tickers_ou_themes_lies"]


def test_un_theme_generique_retenu_sans_dossier_releve_de_autres() -> None:
    """Rattaché à un thème, à aucun dossier : l'onglet « Autres » est sa place."""
    items = _fil([_item("Traders weigh the outlook", "A disruption to oil supply tightened the market.")])
    assert len(items) == 1
    noms_dossiers = {d["nom_affiche"] for d in _DOSSIERS}
    assert not noms_dossiers & set(items[0]["tickers_ou_themes_lies"])
    assert items[0]["tickers_ou_themes_lies"]


def test_un_mot_cle_dun_seul_caractere_ne_filtre_rien() -> None:
    """Un mot-clé trop court est ignoré plutôt que d'ouvrir la porte à tout."""
    dossiers = [{"id": "x", "nom_affiche": "X", "mots_cles": ["or"]}]
    assert feed.construire_fil(_CONFIG, [_item("New order book rules")], set(), dossiers=dossiers) == []


def test_les_doublons_sont_ecartes_comme_pour_les_autres_sources() -> None:
    """Même titre via deux sources : un seul item, la déduplication est commune."""
    a = news.NewsItem(titre="Hamas responds to the latest proposal", url="https://a.test/1",
                      source="Reuters", date=datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc))
    b = news.NewsItem(titre="Hamas responds to the latest proposal!", url="https://b.test/2",
                      source="GDELT", date=datetime(2026, 9, 11, 10, 5, tzinfo=timezone.utc))
    assert len(_fil(news.dedupe([a, b]))) == 1


def test_un_item_deja_traite_nest_jamais_retraite() -> None:
    articles = [_item("Gaza talks resume", "Negotiators returned to the table.")]
    premiers = _fil(articles)
    assert premiers[0]["nouveaute"] is True
    connus = {premiers[0]["id"]}
    seconds = _fil(articles, connus)
    assert seconds[0]["nouveaute"] is False
    assert seconds[0]["analyse_interne"] is None


def test_dossiers_illisibles_degradent_sans_casser_le_fil() -> None:
    """Sans dossiers, les thèmes génériques tiennent encore le fil."""
    items = feed.construire_fil(
        _CONFIG, [_item("Oil supply disruption widens"), _item("Gaza talks resume")],
        set(), dossiers=[],
    )
    assert len(items) == 1                       # le thème énergie reste, Gaza n'a plus de dossier
    assert "Gaza" not in " ".join(items[0]["tickers_ou_themes_lies"])


def test_les_exclusions_lisent_aussi_le_chapo() -> None:
    config = {**_CONFIG, "feed": {**_CONFIG.get("feed", {}), "exclusions": ["home invasion"]}}
    articles = [_item("Hamas statement", "Unrelated mention of a home invasion in the same bulletin.")]
    assert feed.construire_fil(config, articles, set(), dossiers=_DOSSIERS) == []
