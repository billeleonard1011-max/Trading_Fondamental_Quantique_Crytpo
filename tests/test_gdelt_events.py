"""Tests du client GDELT Events, entièrement hors ligne.

Exécution :
    pytest tests/test_gdelt_events.py -v
"""

from __future__ import annotations

import io
import zipfile

import requests

from dataio import gdelt_events as ge


def _ligne_evenement(acteur1: str, acteur2: str, code: str = "040",
                      goldstein: str = "1.0", tonalite: str = "-2.5",
                      url: str = "https://example.org/article") -> list[str]:
    """Fabrique une ligne d'export Events, avec ses 61 colonnes minimales."""
    ligne = ["0"] * ge._NB_COLONNES_MIN
    ligne[ge._COL_ACTOR1_PAYS] = acteur1
    ligne[ge._COL_ACTOR2_PAYS] = acteur2
    ligne[ge._COL_CODE_EVENEMENT] = code
    ligne[ge._COL_GOLDSTEIN] = goldstein
    ligne[ge._COL_TONALITE] = tonalite
    ligne[ge._COL_SOURCE_URL] = url
    return ligne


class _ReponseFactice:
    """Simule ``requests.Response`` pour un test hors ligne."""

    def __init__(self, texte: str = "", contenu: bytes = b"", ok: bool = True,
                 statut: int = 200) -> None:
        self.text = texte
        self.content = contenu
        self.status_code = statut
        self._ok = ok

    def raise_for_status(self) -> None:
        if not self._ok:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _zip_evenements(lignes: list[list[str]]) -> bytes:
    """Compresse des lignes en une archive équivalente à un export réel."""
    texte = "\n".join("\t".join(l) for l in lignes)
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        archive.writestr("20260910000000.export.CSV", texte)
    return tampon.getvalue()


def _fetch_factice(index_texte: str, zip_bytes: bytes | None = None,
                    echec_index: bool = False, echec_zip: bool = False):
    """Fabrique une implémentation de ``requests.get`` injectable."""
    def get(url: str, timeout: float, headers: dict) -> _ReponseFactice:
        if url == ge.URL_DERNIERE_MISE_A_JOUR:
            if echec_index:
                raise requests.ConnectionError("réseau coupé")
            return _ReponseFactice(texte=index_texte)
        if echec_zip:
            raise requests.ConnectionError("réseau coupé")
        return _ReponseFactice(contenu=zip_bytes or b"")
    return get


# ---------------------------------------------------------------------------
# recuperer_dernier_export
# ---------------------------------------------------------------------------
def test_recupere_et_decompresse_un_export_reel() -> None:
    lignes_attendues = [_ligne_evenement("ISR", "PSE")]
    index = f"1234 abcd http://data.gdeltproject.org/gdeltv2/20260910000000.export.CSV.zip\n"
    get = _fetch_factice(index, _zip_evenements(lignes_attendues))

    lignes, motif = ge.recuperer_dernier_export(get)
    assert motif == ""
    assert len(lignes) == 1
    assert lignes[0][ge._COL_ACTOR1_PAYS] == "ISR"


def test_index_injoignable_degrade_sans_exception() -> None:
    get = _fetch_factice("", echec_index=True)
    lignes, motif = ge.recuperer_dernier_export(get)
    assert lignes == []
    assert "injoignable" in motif


def test_index_sans_fichier_events_est_signale() -> None:
    get = _fetch_factice("un index sans le bon format\n")
    lignes, motif = ge.recuperer_dernier_export(get)
    assert lignes == []
    assert "sans fichier" in motif


def test_export_injoignable_degrade_sans_exception() -> None:
    index = "1234 abcd http://data.gdeltproject.org/gdeltv2/20260910000000.export.CSV.zip\n"
    get = _fetch_factice(index, echec_zip=True)
    lignes, motif = ge.recuperer_dernier_export(get)
    assert lignes == []
    assert "injoignable" in motif


def test_archive_corrompue_degrade_sans_exception() -> None:
    index = "1234 abcd http://data.gdeltproject.org/gdeltv2/20260910000000.export.CSV.zip\n"
    get = _fetch_factice(index, b"pas une archive zip")
    lignes, motif = ge.recuperer_dernier_export(get)
    assert lignes == []
    assert "corrompue" in motif


# ---------------------------------------------------------------------------
# compter_evenements_par_acteurs
# ---------------------------------------------------------------------------
def test_compte_seulement_les_evenements_bilateraux() -> None:
    """Un acteur seul, sans son homologue, ne doit pas compter.

    Reproduit le cas trouvé sur un export réel : un événement Russie-Vietnam
    ne doit pas compter pour le dossier Russie-Ukraine.
    """
    lignes = [
        _ligne_evenement("RUS", "UKR"),
        _ligne_evenement("UKR", "RUS"),  # ordre inverse, doit compter aussi
        _ligne_evenement("RUS", "VNM"),  # acteur seul : ne doit pas compter
        _ligne_evenement("USA", "CHN"),  # sans rapport
    ]
    resultat = ge.compter_evenements_par_acteurs(lignes, ["RUS", "UKR"])
    assert resultat["n_evenements"] == 2


def test_exemple_reprend_les_vrais_champs_de_la_ligne() -> None:
    lignes = [_ligne_evenement("ISR", "PSE", code="172", goldstein="-5.0",
                                tonalite="-3.3", url="https://exemple.test/a")]
    resultat = ge.compter_evenements_par_acteurs(lignes, ["ISR", "PSE"])
    assert resultat["n_evenements"] == 1
    assert resultat["exemple"] == {
        "code_evenement": "172", "goldstein": -5.0, "tonalite": -3.3,
        "source_url": "https://exemple.test/a",
    }


def test_aucun_evenement_bilateral_rend_un_exemple_absent_pas_invente() -> None:
    lignes = [_ligne_evenement("USA", "CHN")]
    resultat = ge.compter_evenements_par_acteurs(lignes, ["ISR", "PSE"])
    assert resultat == {"n_evenements": 0, "exemple": None}


def test_ligne_trop_courte_est_ignoree_sans_exception() -> None:
    resultat = ge.compter_evenements_par_acteurs([["ISR", "PSE"]], ["ISR", "PSE"])
    assert resultat["n_evenements"] == 0


def test_valeurs_non_numeriques_ne_levent_pas_dexception() -> None:
    ligne = _ligne_evenement("ISR", "PSE", goldstein="", tonalite="n/d")
    resultat = ge.compter_evenements_par_acteurs([ligne], ["ISR", "PSE"])
    assert resultat["exemple"]["goldstein"] is None
    assert resultat["exemple"]["tonalite"] is None
