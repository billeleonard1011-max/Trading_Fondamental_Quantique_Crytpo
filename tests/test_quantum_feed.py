"""Tests du fil d'actualité quantique et de l'extraction des Form 4.

Trois propriétés sont vérifiées, et la première est la raison d'être du fil.

1. **Rien n'est réexpliqué deux fois.** Un module conçu pour tourner toutes
   les quinze minutes qui réanalyserait à chaque passage les mêmes dépêches
   coûterait cher et publierait des doublons. Deux exécutions successives sur
   les mêmes données doivent donner zéro nouvelle analyse à la seconde.
2. **Le format de sortie est un contrat.** Les fils crypto et géopolitique
   reprendront cette structure : un champ en trop ou en moins doit faire
   échouer la suite, pas se découvrir à l'affichage.
3. **Pasqal ne paraît pas.** Le titre sert d'exemple de calibration dans la
   documentation ; le voir surgir dans le fil laisserait croire qu'il est
   suivi alors qu'il n'est pas dans la watchlist.

Aucun test n'accède au réseau.

Exécution :
    pytest tests/test_quantum_feed.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from dataio import news, sec_filings as sec
from modules.quantum import feed, moves

_CONFIG = {
    "quantum_watchlist": [
        {"ticker": "RGTI", "name": "Rigetti Computing", "keywords": ["Rigetti"]},
        {"ticker": "IONQ", "name": "IonQ", "keywords": ["IonQ"]},
    ],
    "quantum": {"incumbents": ["IBM", "Quantinuum", "Pasqal"]},
    "feed_quantique": {
        "exclusions": ["Pasqal", "PSQL"],
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
        _article("Rigetti announces a new processor"),
        _article("IonQ signs a government contract"),
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
        _CONFIG, [_article("Rigetti raises funds")], identifiants_connus=set()
    )
    feed.publier_fil(premiers, dossier=tmp_path, chemin_historique=historique)
    assert len(historique.read_text(encoding="utf-8").strip().split("\n")) == 1

    # Republier les mêmes items connus n'allonge pas le fichier.
    connus = feed.charger_historique(historique)
    seconds = feed.construire_fil(
        _CONFIG, [_article("Rigetti raises funds")], identifiants_connus=connus
    )
    feed.publier_fil(seconds, dossier=tmp_path, chemin_historique=historique)
    assert len(historique.read_text(encoding="utf-8").strip().split("\n")) == 1

def test_identifiant_stable_malgre_lurl(tmp_path: Path) -> None:
    """La même dépêche sous deux adresses ne compte qu'une fois.

    Se fier à l'URL rendrait un article éternellement « nouveau » dès qu'un
    agrégateur le republie sous une autre adresse.
    """
    a = feed.identifiant_item("Rigetti announces a new processor", "https://a.test/1")
    b = feed.identifiant_item("RIGETTI  announces a  new processor!", "https://b.test/2")
    assert a == b

def test_historique_corrompu_ne_perd_pas_tout(tmp_path: Path) -> None:
    """Une ligne illisible est ignorée, le reste de l'historique survit."""
    historique = tmp_path / "feed_historique.jsonl"
    historique.write_text(
        '{"id": "aaa"}\nligne corrompue\n{"id": "bbb"}\n', encoding="utf-8"
    )
    assert feed.charger_historique(historique) == {"aaa", "bbb"}

def test_historique_absent_est_une_premiere_execution(tmp_path: Path) -> None:
    """Sans fichier, l'ensemble est vide et rien n'échoue."""
    assert feed.charger_historique(tmp_path / "jamais_ecrit.jsonl") == set()

# ---------------------------------------------------------------------------
# 2. Format unifié
# ---------------------------------------------------------------------------
def test_chaque_item_respecte_exactement_le_format() -> None:
    """Les clés d'un item sont exactement celles du contrat, ni plus ni moins."""
    items = feed.construire_fil(
        _CONFIG,
        [_article("IonQ and IBM announce a partnership")],
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

def test_categorie_fermee_aux_trois_valeurs() -> None:
    """La catégorie est un ensemble fermé, que les prochains fils partageront."""
    assert feed.CATEGORIES == ("quantique", "crypto", "geopolitique")

def test_analyse_absente_est_nulle_pas_vide() -> None:
    """Un item non analysé porte ``None``, et le drapeau correspondant."""
    items = feed.construire_fil(
        _CONFIG, [_article("Rigetti news")], identifiants_connus=set(),
    )
    connus = {i["id"] for i in items}
    seconds = feed.construire_fil(_CONFIG, [_article("Rigetti news")], connus)
    assert seconds[0]["analyse_interne"] is None
    assert seconds[0]["a_une_analyse_interne"] is False

# ---------------------------------------------------------------------------
# 3. Pasqal reste hors du fil
# ---------------------------------------------------------------------------
def test_pasqal_absent_du_fil() -> None:
    """Aucune mention de Pasqal ni de PSQL tant qu'il n'est pas suivi.

    Pasqal figure pourtant parmi les acteurs connus du secteur, ce qui
    suffirait à rendre l'item pertinent : l'exclusion est donc explicite.
    """
    articles = [
        _article("Pasqal shares fall after SPAC listing"),
        _article("PSQL drops 58% in a week"),
        _article("Rigetti announces a new processor"),
    ]
    items = feed.construire_fil(_CONFIG, articles, identifiants_connus=set())

    assert len(items) == 1
    assert items[0]["tickers_ou_themes_lies"] == ["RGTI"]

    serialise = str(items).lower()
    assert "pasqal" not in serialise
    assert "psql" not in serialise

def test_item_sans_lien_avec_le_secteur_ecarte() -> None:
    """Un article qui ne cite aucune valeur ni acteur connu n'entre pas."""
    items = feed.construire_fil(
        _CONFIG, [_article("Une dépêche sans rapport avec le secteur")], set()
    )
    assert items == []

def test_aucune_recommandation_dans_le_fil() -> None:
    """Le fil produit passe le contrôle anti-recommandation."""
    items = feed.construire_fil(
        _CONFIG,
        [_article("IonQ wins a contract"), _article("Rigetti raises capital")],
        identifiants_connus=set(),
    )
    assert moves.verifier_absence_recommandation(items) == []

def test_gabarit_signale_un_mouvement_sectoriel() -> None:
    """Quand le prix a bougé pour tout le secteur, l'explication le dit."""
    items = feed.construire_fil(
        _CONFIG,
        [_article("Rigetti falls with the sector")],
        identifiants_connus=set(),
        mouvements_par_ticker={
            "RGTI": {
                "ticker": "RGTI",
                "variation_pct": -9.2,
                "classification": moves.SECTORIEL,
            }
        },
    )
    analyse = items[0]["analyse_interne"]
    assert analyse is not None
    assert "n'est pas propre à cette société" in analyse
    assert "-9.2" in analyse

# ---------------------------------------------------------------------------
# 4. Form 4 : le sens est toujours présent
# ---------------------------------------------------------------------------
_FORM4 = """<?xml version="1.0"?>
<ownershipDocument>
  <periodOfReport>2026-09-03</periodOfReport>
  <issuer><issuerTradingSymbol>RGTI</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Bertelsen Jeffrey A.</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>0</isDirector><isOfficer>1</isOfficer><isTenPercentOwner>0</isTenPercentOwner>
      <officerTitle>CHIEF FINANCIAL OFFICER</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-09-03</value></transactionDate>
      <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>25000</value></transactionShares>
        <transactionPricePerShare><value>0.60</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-09-03</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>25000</value></transactionShares>
        <transactionPricePerShare><value>15.0004</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""

def test_form4_extraction_complete() -> None:
    """Identité, rôle, titre, sens et montants sont extraits."""
    operations, motif = sec.parser_form4(_FORM4, url_depot="https://exemple.test/depot")
    assert motif == ""
    assert len(operations) == 2

    vente = next(o for o in operations if o.sens == "vente")
    assert vente.identite["nom"] == "Bertelsen Jeffrey A."
    assert vente.identite["role"] == "dirigeant"
    assert vente.identite["titre_fonction"] == "CHIEF FINANCIAL OFFICER"
    assert vente.nombre_titres == 25_000
    assert vente.prix_unitaire == pytest.approx(15.0004)
    assert vente.valeur_totale_usd == pytest.approx(375_010.0)
    assert vente.ticker == "RGTI"

def test_form4_sens_toujours_present() -> None:
    """Chaque opération porte un ``sens``, fût-il indéterminé.

    Un champ absent obligerait chaque consommateur à gérer le cas ; un champ
    à « indetermine » se lit tout seul.
    """
    operations, _ = sec.parser_form4(_FORM4)
    for operation in operations:
        assert operation.sens in {"achat", "vente", "indetermine"}
        assert operation.to_dict()["sens"]

    # Un exercice d'options n'est ni un achat ni une vente de marché.
    exercice = next(o for o in operations if o.code_transaction == "M")
    assert exercice.sens == "indetermine"
    assert "instrument dérivé" in exercice.libelle_code
    assert "pas une décision d'achat ou de vente" in exercice.motif

def test_form4_identite_manquante_degrade_sans_perdre_loperation() -> None:
    """Palier 1 : sans identité, l'opération reste publiée avec son motif."""
    sans_identite = _FORM4.replace(
        "<reportingOwnerId><rptOwnerName>Bertelsen Jeffrey A.</rptOwnerName></reportingOwnerId>",
        "<reportingOwnerId></reportingOwnerId>",
    )
    operations, _ = sec.parser_form4(sans_identite)
    assert len(operations) == 2
    for operation in operations:
        assert operation.identite is None
        assert "nom du déclarant absent" in operation.motif
    # Les montants restent lisibles.
    assert any(o.valeur_totale_usd for o in operations)

def test_form4_sans_operation_publie_la_reference() -> None:
    """Palier 3 : un dépôt sans opération reste visible avec son lien."""
    minimal = """<?xml version="1.0"?>
<ownershipDocument>
  <periodOfReport>2026-09-03</periodOfReport>
  <issuer><issuerTradingSymbol>RGTI</issuerTradingSymbol></issuer>
  <reportingOwner><reportingOwnerId><rptOwnerName>X</rptOwnerName></reportingOwnerId></reportingOwner>
</ownershipDocument>
"""
    operations, motif = sec.parser_form4(minimal, url_depot="https://exemple.test/d")
    assert len(operations) == 1
    assert operations[0].sens == "indetermine"
    assert operations[0].url_depot == "https://exemple.test/d"
    assert "aucune opération exploitable" in motif

def test_form4_enveloppe_sgml_traitee() -> None:
    """Le XML enveloppé dans une soumission complète est bien isolé.

    Tous les dépôts ne publient pas de fichier XML nu : certains ne sont
    disponibles que dans la soumission complète, un conteneur SGML dont
    ElementTree ne veut pas.
    """
    enveloppe = (
        "<SEC-DOCUMENT>0001-26-000012.txt : 20260826\n<SEC-HEADER>en-tetes\n"
        "</SEC-HEADER>\n<TYPE>4\n<FILENAME>ownership.xml\n<XML>\n"
        + _FORM4
        + "\n</XML>\n</SEC-DOCUMENT>\n"
    )
    operations, motif = sec.parser_form4(enveloppe)
    assert motif == ""
    assert len(operations) == 2
    assert any(o.sens == "vente" for o in operations)

def test_form4_xml_illisible_ne_leve_pas_dexception() -> None:
    """Un contenu inexploitable donne un motif, pas une exception."""
    operations, motif = sec.parser_form4("ceci n'est pas du XML")
    assert operations == []
    assert "illisible" in motif
