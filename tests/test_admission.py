"""Tests de l'admission dans les fils — « est-ce que ça touche mon univers ? »

Ce module vérifie la séparation entre les deux décisions que les mots-clés des
dossiers assuraient auparavant à eux seuls :

* l'**admission** (ici) décide de l'entrée, sur un vocabulaire large ;
* le **rattachement** (``tests/test_geopolitique_feed.py``) décide du rangement,
  sur les dossiers, et a le droit d'être exigeant.

Aucun test n'accède au réseau.

Exécution :
    pytest tests/test_admission.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from dataio import news
from modules.geopolitique import admission
from modules.geopolitique import feed
from modules.gold.geopolitics import charger_dossiers_config

RACINE = Path(__file__).resolve().parents[1]


def _univers() -> admission.Univers:
    """Charge le vocabulaire réellement configuré, pas un jeu d'essai."""
    return admission.charger_univers()


# ---------------------------------------------------------------------------
# 1. Le fichier de configuration tient debout
# ---------------------------------------------------------------------------
def test_le_vocabulaire_reel_se_charge() -> None:
    """Le fichier livré produit un univers exploitable."""
    univers = _univers()
    assert univers, "Univers vide : plus aucun article ne serait admis."
    assert len(univers.domaines) >= 15
    assert univers.magnitude_seisme_min > 0


def test_chaque_domaine_a_une_portee_connue() -> None:
    """Une portée inconnue rétrograderait silencieusement tout un domaine."""
    for domaine in _univers().domaines:
        assert domaine.portee in admission.ORDRE_PORTEE, domaine.identifiant


def test_les_trois_portees_sont_toutes_representees() -> None:
    """Un classement à un seul niveau ne classerait rien."""
    portees = {d.portee for d in _univers().domaines}
    assert portees == set(admission.ORDRE_PORTEE)


def test_chaque_dossier_designe_existe_vraiment() -> None:
    """Verrou : renommer un dossier sans suivre ici casserait le rattachement.

    Le champ ``dossier`` d'un domaine d'admission est le seul point de contact
    entre ``config/univers_admission.yaml`` et
    ``config/geopolitique_dossiers.yaml``. Une référence morte ne lèverait
    aucune erreur en production — l'article irait simplement dans « Autres »,
    sans que personne ne remarque que le dossier a cessé d'être alimenté.
    """
    identifiants = {str(d.get("id")) for d in charger_dossiers_config()}
    for domaine in _univers().domaines:
        if domaine.dossier:
            assert domaine.dossier in identifiants, (
                f"Le domaine {domaine.identifiant} renvoie vers le dossier "
                f"inconnu {domaine.dossier!r}."
            )


def test_le_dossier_matieres_premieres_est_alimente() -> None:
    """Le dossier créé pour les matières premières a bien des domaines qui y mènent."""
    domaines = [d for d in _univers().domaines if d.dossier == "matieres_premieres"]
    assert len(domaines) >= 4, "Filières et régions productrices attendues."
    libelles = " ".join(d.libelle for d in domaines).lower()
    for filiere in ("précieux", "cuivre", "pétrolière"):
        assert filiere in libelles


# ---------------------------------------------------------------------------
# 2. L'univers demandé est bien couvert
# ---------------------------------------------------------------------------
def test_les_actifs_directs_sont_reconnus_comme_tels() -> None:
    """Or, quantique et crypto donnent le rang le plus haut."""
    univers = _univers()
    for titre in (
        "Gold hits a record high in London trading",
        "Quantum computing milestone reached with a 1000-qubit processor",
        "Bitcoin rallies as a spot ETF sees record inflows",
    ):
        verdict = admission.evaluer(titre, univers=univers)
        assert verdict is not None, titre
        assert verdict.portee == "actif_direct", titre


def test_les_canaux_dinfluence_sont_reconnus() -> None:
    """Ce qui influence ces actifs entre au rang intermédiaire."""
    univers = _univers()
    cas = {
        "Oil prices slip after the recent surge": "Pétrole et énergie",
        "The dollar weakens against major currencies": "Dollar et devises",
        "Treasury yields fall to a multiyear low": "Taux et obligations",
        "Inflation stays above the target for a third month": "Inflation et prix",
        "Federal Reserve leaves rates unchanged": "Banques centrales",
        "Nasdaq closes higher on tech stocks": "Actions et technologie",
        "New semiconductor export controls announced": "Semi-conducteurs",
        "Copper output falls after a mine strike": "Matières premières",
        "Credit spreads widen amid banking stress": "Banques, crédit et appétit pour le risque",
        "Recession fears grow as payrolls disappoint": "Croissance et emploi",
        "Ransomware shuts down a major pipeline operator":
            "Cyberattaques sur infrastructures et plateformes",
        "Hurricane forces Gulf of Mexico output cuts": "Catastrophes naturelles",
        "New rules agreed for electric vehicle batteries": "Transition énergétique",
    }
    for titre, attendu in cas.items():
        verdict = admission.evaluer(titre, univers=univers)
        assert verdict is not None, titre
        assert attendu in verdict.libelles, f"{titre!r} → {verdict.libelles}"
        assert verdict.rang >= admission.ORDRE_PORTEE["influence"], titre


def test_la_geopolitique_large_entre_au_rang_le_plus_bas() -> None:
    """Conflits, sanctions, accords, élections, commerce : admis, rangés en dernier."""
    univers = _univers()
    for titre in (
        "Ceasefire talks collapse after renewed shelling",
        "New sanctions target the shadow fleet",
        "Leaders sign a treaty at the summit",
        "Presidential election heads to a second round",
        "Tariff threats revive the trade war",
    ):
        verdict = admission.evaluer(titre, univers=univers)
        assert verdict is not None, titre
        assert verdict.portee == "contexte", f"{titre!r} → {verdict.portee}"


def test_hors_univers_rien_nentre() -> None:
    """L'admission est large, pas illimitée."""
    univers = _univers()
    for titre in (
        "Local team wins the regional football cup after extra time",
        "New museum opens with a retrospective on impressionist painting",
    ):
        assert admission.evaluer(titre, univers=univers) is None, titre


def test_les_termes_ajoutes_ne_volent_rien_aux_domaines_voisins() -> None:
    """Chaque collision trouvée à l'ajout des trois derniers domaines.

    Trois pièges, tous mesurés avant d'être écartés : « freeze » aurait fait
    passer « asset freeze » des sanctions aux catastrophes naturelles ; « SWIFT »
    en sigle se déclenche sur l'adjectif anglais ; « hack » ancré au début d'un
    mot attrape « hackathon ». Les trois vocabulaires ont été écrits en
    conséquence, et ce test empêche qu'on les rouvre sans y repenser.
    """
    univers = _univers()

    sanctions = admission.evaluer("Asset freeze targets the shadow fleet", univers=univers)
    assert sanctions is not None
    assert "Catastrophes naturelles" not in sanctions.libelles

    assert admission.evaluer("A swift response from regulators", univers=univers) is None
    assert admission.evaluer("Hackathon draws hundreds of students", univers=univers) is None

    for titre in ("Exchange hacked for 50 million", "Hackers target a grid operator"):
        verdict = admission.evaluer(titre, univers=univers)
        assert verdict is not None, titre
        assert "Cyberattaques sur infrastructures et plateformes" in verdict.libelles


def test_le_nucleaire_iranien_nest_pas_de_la_transition_energetique() -> None:
    """« nuclear » nu est absent du vocabulaire, et c'est voulu.

    Il est déjà le mot-clé du dossier Iran - États-Unis. L'admettre comme
    énergie propre rangerait le programme iranien parmi les renouvelables.
    L'article entre quand même, par l'union avec les mots-clés du dossier.
    """
    verdict = admission.evaluer("Iran nuclear talks resume in Geneva", univers=_univers())
    assert verdict is None or "Transition énergétique" not in verdict.libelles


def test_une_catastrophe_non_sismique_nest_rattachee_a_aucun_dossier() -> None:
    """Le rattachement géographique reste réservé aux séismes, faute de sévérité.

    Une magnitude est une mesure comparable qui autorise un seuil ; un titre
    d'inondation n'en porte aucune. Router « Wildfires force evacuation in
    Chile » vers les matières premières sur la seule foi du pays serait une
    attribution inventée. L'article est admis, il n'est pas attribué.
    """
    verdict = admission.evaluer("Wildfires force evacuation in Chile", univers=_univers())
    assert verdict is not None
    assert "Catastrophes naturelles" in verdict.libelles
    assert "matieres_premieres" not in verdict.dossiers


# ---------------------------------------------------------------------------
# 3. Les règles de comparaison des termes
# ---------------------------------------------------------------------------
def test_un_terme_court_qui_nest_pas_un_sigle_est_ignore() -> None:
    """« or » ne peut pas être un terme : c'est la conjonction anglaise."""
    assert admission._motif("or", exact=False, longueur_min=4) is None
    assert admission._motif("OR", exact=False, longueur_min=4) is not None


def test_un_sigle_est_compare_au_mot_entier() -> None:
    """« WTO » ne doit pas sortir de « wtoxyz », ni « TIPS » de « tips ».

    C'est la raison d'être de la règle : un sigle en minuscules devient un mot
    ordinaire, et beaucoup de sigles financiers ont un homonyme courant. Le
    cas mesuré qui a fait retirer « TIPS » du vocabulaire : « Investment tips
    for retirement » était admis comme relevant des taux et obligations.
    """
    motif = admission._motif("WTO", exact=False, longueur_min=4)
    assert motif is not None
    assert motif.search("the wto ruling")
    assert not motif.search("wtoxyz filings")


def test_un_terme_ordinaire_est_ancre_au_debut_dun_mot() -> None:
    """La règle du reste du projet : « Iran » retrouve « Iranian », pas « Tirana »."""
    motif = admission._motif("iran", exact=False, longueur_min=4)
    assert motif is not None
    assert motif.search("iranian officials")
    assert not motif.search("tirana hosts a summit")


def test_un_terme_exact_ne_se_declenche_pas_au_milieu_dun_mot() -> None:
    """Sans comparaison au mot entier, « Mali » sortirait de « Malibu »."""
    motif = admission._motif("Mali", exact=True, longueur_min=4)
    assert motif is not None
    assert motif.search("a quake struck mali")
    assert not motif.search("a fire near malibu")


def test_la_normalisation_rapproche_les_deux_cotes() -> None:
    """Texte et terme passent par la même fonction, sinon rien ne se rencontre."""
    assert admission.normaliser("L'Or à Paris — 3 000 $") == "l or a paris 3 000"


# ---------------------------------------------------------------------------
# 4. Les événements localisés, qui rendent le flux USGS exploitable
# ---------------------------------------------------------------------------
def test_la_magnitude_est_lue_sur_le_titre_brut() -> None:
    """La normalisation effacerait le point décimal : la lecture précède."""
    assert admission.magnitude_sismique("M 6.1 - 40 km W of Calama, Chile") == 6.1
    assert admission.magnitude_sismique("M 5,9 - 253 km ENE of Lospalos") == 5.9
    assert admission.magnitude_sismique("Copper output falls in Chile") is None


def test_un_seisme_suffisant_sous_une_region_productrice_entre() -> None:
    """Le cas qui motivait toute la séparation : USGS devient exploitable.

    Le titre ne contient ni « cuivre », ni « mine », ni aucun mot-clé de
    dossier — seulement un lieu et une magnitude. L'ancienne règle le
    collectait puis l'écartait intégralement.
    """
    verdict = admission.evaluer("M 6.3 - 40 km W of Calama, Chile", univers=_univers())
    assert verdict is not None
    assert "matieres_premieres" in verdict.dossiers
    assert "cuivre" in " ".join(verdict.libelles).lower()


def test_un_seisme_trop_faible_nentre_pas() -> None:
    """Sous le seuil, le flux USGS ne produirait que du bruit quotidien."""
    assert admission.evaluer("M 4.7 - 66 km WSW of Zhaotong, China", univers=_univers()) is None


def test_un_seisme_hors_region_productrice_nentre_pas() -> None:
    """La magnitude seule ne suffit pas : il faut une production à perturber."""
    assert admission.evaluer("M 7.0 - 30 km E of Hokkaido, Japan", univers=_univers()) is None


def test_un_pays_nadmet_rien_sans_magnitude() -> None:
    """Sans cette condition, toute l'actualité chilienne entrerait ici."""
    verdict = admission.evaluer("Chile holds presidential election", univers=_univers())
    assert verdict is not None                      # admis par les élections
    assert "matieres_premieres" not in verdict.dossiers


def test_un_pays_de_deux_filieres_ressort_avec_les_deux() -> None:
    """Le Pérou produit des métaux précieux et du cuivre : les deux sont dits."""
    verdict = admission.evaluer("M 6.8 - 120 km S of Lima, Peru", univers=_univers())
    assert verdict is not None
    libelles = " ".join(verdict.libelles).lower()
    assert "précieux" in libelles and "cuivre" in libelles


# ---------------------------------------------------------------------------
# 5. Dégradation gracieuse
# ---------------------------------------------------------------------------
def test_un_fichier_absent_rend_un_univers_vide_sans_lever(tmp_path: Path) -> None:
    """Le fil se vide, ce qui se voit — une exception en pleine collecte, non."""
    univers = admission.charger_univers(tmp_path / "jamais_ecrit.yaml")
    assert not univers
    assert admission.evaluer("Gold hits a record high", univers=univers) is None


def test_un_fichier_illisible_rend_un_univers_vide_sans_lever(tmp_path: Path) -> None:
    """Un YAML cassé ne doit pas faire tomber la collecte."""
    fichier = tmp_path / "casse.yaml"
    fichier.write_text("domaines: [ ceci n'est pas\n  du yaml", encoding="utf-8")
    assert not admission.charger_univers(fichier)


def test_une_portee_inconnue_retrograde_au_lieu_de_promouvoir(tmp_path: Path) -> None:
    """Une erreur de configuration ne doit pas passer devant un actif suivi."""
    fichier = tmp_path / "univers.yaml"
    fichier.write_text(
        yaml.safe_dump({"domaines": [
            {"id": "x", "libelle": "X", "portee": "inventee", "termes": ["quelquechose"]},
        ]}, allow_unicode=True),
        encoding="utf-8",
    )
    univers = admission.charger_univers(fichier)
    assert univers.domaines[0].portee == admission.PORTEE_DEFAUT


# ---------------------------------------------------------------------------
# 6. L'élargissement ne peut rien retirer
# ---------------------------------------------------------------------------
def test_tout_ce_quun_dossier_reconnaissait_entre_encore() -> None:
    """Propriété structurelle : l'admission est une union, jamais un remplacement.

    Chaque mot-clé de chaque dossier réellement configuré est mis dans un
    titre, et l'item doit entrer. Sans cette garantie, élargir l'admission
    aurait pu en même temps faire disparaître des sujets suivis — le contraire
    exact de ce qui était demandé.
    """
    dossiers = charger_dossiers_config()
    configuration = yaml.safe_load((RACINE / "config" / "gold.yaml").read_text(encoding="utf-8"))
    geo = configuration["geopolitique"]
    univers = _univers()

    for dossier in dossiers:
        for mot in dossier.get("mots_cles") or []:
            article = news.NewsItem(
                titre=f"Report on {mot} published today",
                url=f"https://exemple.test/{abs(hash(mot))}",
                source="Test",
                date=datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc),
            )
            items = feed.construire_fil(
                geo, [article], identifiants_connus=set(),
                dossiers=dossiers, univers=univers,
                configuration_explication={"activee": False},
            )
            assert items, f"Le mot-clé {mot!r} du dossier {dossier['id']} n'entre plus."


def test_chaque_item_porte_une_portee_valide() -> None:
    """Le champ est au contrat partagé : il doit toujours être renseigné."""
    dossiers = charger_dossiers_config()
    articles = [
        news.NewsItem(titre=t, url=f"https://exemple.test/{i}", source="Test",
                      date=datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc))
        for i, t in enumerate([
            "Gold hits a record high",
            "Oil prices slip after the surge",
            "Presidential election heads to a second round",
        ])
    ]
    items = feed.construire_fil(
        {"themes": [], "feed": {}}, articles, identifiants_connus=set(),
        dossiers=dossiers, univers=_univers(),
        configuration_explication={"activee": False},
    )
    assert len(items) == 3
    assert {i["portee"] for i in items} == {"actif_direct", "influence", "contexte"}
