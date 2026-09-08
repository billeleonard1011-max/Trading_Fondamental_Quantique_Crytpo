"""Client EDGAR : dépôts réglementaires, trésorerie et activité d'initiés.

Les données de la SEC sont publiques, gratuites et sans clé. Elles ont en
revanche une exigence stricte : **tout appel doit porter un User-Agent
identifiant l'appelant, avec une adresse de contact**. Sans lui, la SEC
répond 403 — vérifié : un ``python-requests/2.31`` par défaut est refusé, le
même appel avec un agent nommé passe. L'adresse est lue dans
``SEC_CONTACT_EMAIL`` et n'apparaît jamais en dur.

Ce que ce module sert à voir
----------------------------
Les sociétés quantiques cotées brûlent de la trésorerie et se refinancent en
émettant des actions. Trois choses se lisent donc dans leurs dépôts, et nulle
part ailleurs :

* un **S-3** ou un **424B5** annoncent une levée, donc une dilution ;
* la **trésorerie restante**, qui dit combien de temps la société peut tenir
  avant d'y être contrainte ;
* les **Form 4**, qui montrent ce que font les dirigeants et gros
  actionnaires de leurs propres titres.

Ce module rapporte ces faits. Il ne les interprète pas, et surtout il ne
qualifie jamais un achat d'initié de bon ou de mauvais signe : un dirigeant
achète pour des raisons qu'aucune donnée publique ne révèle.

Lecture des Form 4 : extraction complète, puis dégradation par paliers
----------------------------------------------------------------------
Le schéma ``ownershipDocument`` expose l'identité et le rôle du déclarant
(``rptOwnerName``, ``isDirector``, ``isOfficer``, ``isTenPercentOwner``,
``officerTitle``) et le détail de chaque opération (``transactionCode``,
``transactionShares``, ``transactionPricePerShare``). Le module tente
l'extraction complète, puis dégrade sans jamais rien fabriquer :

1. identité illisible mais opération lisible → ``identite`` à ``None``,
   opération conservée, motif renseigné ;
2. sens de l'opération illisible → ``sens`` à ``indetermine``, la référence
   et le lien du dépôt restent publiés ;
3. rien d'exploitable → aucune transaction, motif explicite.

Un dépôt porte souvent **plusieurs** opérations : l'exercice d'options suivi
de la revente des titres en est le cas courant. Elles sont toutes renvoyées.

Sur le code d'opération, et pourquoi ``sens`` reste souvent indéterminé
----------------------------------------------------------------------
Seuls ``P`` (achat sur le marché) et ``S`` (vente sur le marché) traduisent
une décision discrétionnaire. Les autres codes n'en sont pas : ``M`` est
l'exercice d'un instrument dérivé, ``A`` une attribution, ``F`` une retenue
fiscale, ``G`` une donation. Les classer en « achat » parce que des titres
sont acquis serait un contresens — un exercice d'options programmé n'a pas
le sens d'un achat en séance. Ces opérations sortent donc en
``indetermine``, avec leur ``code_transaction`` et son libellé, pour que le
lecteur juge sur pièces.

Sur le *runway* : pourquoi seulement XBRL
------------------------------------------
Le calcul de trésorerie restante ne s'appuie que sur les données structurées
XBRL. Quand elles manquent pour une société, le module renvoie
``disponible: false`` avec le motif, et s'arrête là. Il ne tente pas de lire
le texte du dépôt en repli : extraire un chiffre de trésorerie d'un PDF ou
d'un HTML de 200 pages marche sur trois sociétés et se trompe sur la
quatrième, sans prévenir. Un trou déclaré vaut mieux qu'un nombre faux.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final

import requests

_LOG: Final = logging.getLogger(__name__)

#: Correspondance ticker vers CIK, publiée par la SEC.
URL_TICKERS: Final = "https://www.sec.gov/files/company_tickers.json"

#: Historique des dépôts d'une société.
URL_SUBMISSIONS: Final = "https://data.sec.gov/submissions/CIK{cik}.json"

#: Données structurées XBRL d'une société.
URL_COMPANYFACTS: Final = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

#: Recherche plein texte dans les dépôts.
URL_FULLTEXT: Final = "https://efts.sec.gov/LATEST/search-index"

#: Consultation classique des dépôts par société.
URL_BROWSE: Final = "https://www.sec.gov/cgi-bin/browse-edgar"

#: Racine des documents déposés.
URL_ARCHIVES: Final = "https://www.sec.gov/Archives/edgar/data"

#: Délai maximal, en secondes, accordé à un appel réseau.
TIMEOUT: Final[float] = float(os.environ.get("HTTP_TIMEOUT", "20"))

#: Adresse de contact par défaut, si l'environnement n'en fournit aucune.
#: La SEC exige une adresse joignable ; celle-ci est un repli explicite et
#: non une vraie boîte, d'où l'avertissement journalisé.
CONTACT_DEFAUT: Final = "contact-non-configure@example.invalid"

#: Concepts XBRL de trésorerie, par ordre de préférence. Le premier est le
#: plus strict ; les suivants incluent la trésorerie soumise à restriction,
#: qui n'est pas librement utilisable mais reste mieux que rien.
CONCEPTS_TRESORERIE: Final[tuple[str, ...]] = (
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations",
)

#: Concepts XBRL de consommation de trésorerie d'exploitation.
CONCEPTS_CONSOMMATION: Final[tuple[str, ...]] = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
)

#: Durée d'un trimestre, en jours, pour ramener un flux à une cadence.
JOURS_PAR_TRIMESTRE: Final[float] = 91.25

#: Libellé des codes d'opération des Form 4, tels que la SEC les définit.
#: Seuls P et S sont des décisions de marché ; les autres sont mécaniques.
CODES_TRANSACTION: Final[dict[str, str]] = {
    "P": "achat sur le marché",
    "S": "vente sur le marché",
    "M": "exercice d'un instrument dérivé",
    "A": "attribution ou octroi par l'émetteur",
    "F": "titres retenus pour l'impôt",
    "G": "donation",
    "D": "cession à l'émetteur",
    "C": "conversion d'un instrument dérivé",
    "X": "exercice d'une option d'achat",
    "J": "opération de nature autre",
    "V": "opération déclarée volontairement par anticipation",
}

#: Codes traduisibles en un sens de marché. Les autres restent indéterminés.
SENS_PAR_CODE: Final[dict[str, str]] = {"P": "achat", "S": "vente"}

__all__ = [
    "Filing",
    "InsiderTransaction",
    "CODES_TRANSACTION",
    "get_cik",
    "get_recent_filings",
    "estimate_cash_runway",
    "detect_insider_activity",
    "parser_form4",
]


def _entetes() -> dict[str, str]:
    """Construit les en-têtes exigés par la SEC.

    La SEC demande un User-Agent nommant l'appelant et une adresse de
    contact. Ce n'est pas une politesse : sans lui les requêtes reçoivent un
    403.

    Returns:
        En-têtes prêts pour ``requests``.
    """
    contact = os.environ.get("SEC_CONTACT_EMAIL", "").strip()
    if not contact:
        _LOG.warning(
            "SEC_CONTACT_EMAIL absente de l'environnement : la SEC exige une "
            "adresse de contact et peut refuser les requêtes (403)."
        )
        contact = CONTACT_DEFAUT
    return {
        "User-Agent": f"Veille-Marches/1.0 ({contact})",
        "Accept-Encoding": "gzip, deflate",
        "Accept": "application/json",
    }


def _appeler(url: str, parametres: dict[str, Any] | None = None) -> Any | None:
    """Appelle un point d'accès de la SEC.

    Args:
        url: adresse complète.
        parametres: paramètres de requête.

    Returns:
        Charge JSON décodée, ou ``None`` en cas d'échec.
    """
    try:
        reponse = requests.get(url, params=parametres, timeout=TIMEOUT, headers=_entetes())
        if reponse.status_code == 403:
            _LOG.warning(
                "SEC a refusé la requête (403) sur %s : User-Agent probablement "
                "jugé non identifiant. Renseigner SEC_CONTACT_EMAIL.",
                url,
            )
            return None
        reponse.raise_for_status()
        return reponse.json()
    except requests.RequestException as exc:
        _LOG.warning("SEC injoignable sur %s : %s", url, exc)
        return None
    except ValueError as exc:
        _LOG.warning("Réponse SEC illisible sur %s : %s", url, exc)
        return None


def _normaliser_cik(cik: str | int) -> str:
    """Ramène un CIK à dix chiffres, comme l'exigent les URL de data.sec.gov.

    Args:
        cik: identifiant, avec ou sans zéros de tête, avec ou sans préfixe.

    Returns:
        Le CIK sur dix chiffres.
    """
    chiffres = re.sub(r"\D", "", str(cik))
    return chiffres.zfill(10)


def get_cik(ticker: str) -> str | None:
    """Retrouve le CIK d'une société depuis son ticker.

    Args:
        ticker: symbole boursier.

    Returns:
        Le CIK sur dix chiffres, ou ``None`` si le ticker est introuvable.
    """
    charge = _appeler(URL_TICKERS)
    if not isinstance(charge, dict):
        return None

    cible = ticker.strip().upper()
    for entree in charge.values():
        if str(entree.get("ticker", "")).upper() == cible:
            return _normaliser_cik(entree.get("cik_str", ""))

    _LOG.warning("Ticker %s absent du répertoire de la SEC.", ticker)
    return None


# ---------------------------------------------------------------------------
# Dépôts récents
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class Filing:
    """Un dépôt réglementaire.

    Attributes:
        type: type de formulaire (``8-K``, ``S-3``, ``4``...).
        date_depot: date de dépôt.
        age_jours: ancienneté en jours.
        accession: numéro d'accession, identifiant unique du dépôt.
        url: adresse de consultation.
        description: éléments déclarés, quand le formulaire en porte.
    """

    type: str
    date_depot: date
    age_jours: int
    accession: str
    url: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Sérialise le dépôt pour le rapport JSON."""
        return {
            "type": self.type,
            "date_depot": self.date_depot.strftime("%Y-%m-%d"),
            "age_jours": self.age_jours,
            "accession": self.accession,
            "url": self.url,
            "description": self.description,
        }


def _url_depot(cik: str, accession: str) -> str:
    """Construit l'adresse de consultation d'un dépôt.

    Args:
        cik: CIK sur dix chiffres.
        accession: numéro d'accession avec tirets.

    Returns:
        L'adresse du dossier du dépôt.
    """
    sans_tirets = accession.replace("-", "")
    return f"{URL_ARCHIVES}/{int(cik)}/{sans_tirets}/{accession}-index.htm"


def get_recent_filings(
    ticker_or_cik: str,
    types: list[str] | None = None,
    days: int = 30,
    aujourd_hui: date | None = None,
    submissions: dict[str, Any] | None = None,
) -> list[Filing]:
    """Liste les dépôts récents d'une société.

    Args:
        ticker_or_cik: ticker (``RGTI``) ou CIK. Un argument tout en chiffres
            est traité comme un CIK, sinon comme un ticker.
        types: types de formulaires à retenir (``S-3``, ``424B5``, ``8-K``,
            ``4``, ``SCHEDULE 13D``...). Tous si ``None``. La comparaison est
            insensible à la casse et tolère les suffixes : ``S-3`` retient
            ``S-3/A``, une modification de la même levée.
        days: profondeur de recherche, en jours.
        aujourd_hui: date de référence, pour les tests.
        submissions: charge ``submissions`` déjà obtenue, pour éviter un appel
            réseau — c'est ce qui rend ce module testable hors ligne.

    Returns:
        Dépôts triés du plus récent au plus ancien. Liste vide en cas d'échec.
    """
    if submissions is None:
        cik = (
            _normaliser_cik(ticker_or_cik)
            if str(ticker_or_cik).strip().isdigit()
            else get_cik(str(ticker_or_cik))
        )
        if cik is None:
            return []
        submissions = _appeler(URL_SUBMISSIONS.format(cik=cik))
        if not isinstance(submissions, dict):
            return []
    else:
        cik = _normaliser_cik(submissions.get("cik", ticker_or_cik))

    recents = (submissions.get("filings") or {}).get("recent") or {}
    formulaires = recents.get("form") or []
    dates = recents.get("filingDate") or []
    accessions = recents.get("accessionNumber") or []
    elements = recents.get("items") or []

    if not (len(formulaires) == len(dates) == len(accessions)):
        _LOG.warning(
            "Dépôts SEC incohérents pour %s : %d formulaires, %d dates, %d accessions.",
            ticker_or_cik, len(formulaires), len(dates), len(accessions),
        )
        return []

    reference = aujourd_hui or date.today()
    limite = reference - timedelta(days=max(int(days), 1))
    voulus = {t.strip().upper() for t in (types or [])}

    resultats: list[Filing] = []
    for i, formulaire in enumerate(formulaires):
        try:
            jour = datetime.strptime(str(dates[i]), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if jour < limite:
            # Les dépôts sont classés du plus récent au plus ancien : au-delà
            # de la limite, tout le reste est plus ancien encore.
            break

        type_normalise = str(formulaire).strip().upper()
        if voulus and not any(
            type_normalise == v or type_normalise.startswith(f"{v}/") for v in voulus
        ):
            continue

        resultats.append(
            Filing(
                type=str(formulaire).strip(),
                date_depot=jour,
                age_jours=(reference - jour).days,
                accession=str(accessions[i]),
                url=_url_depot(cik, str(accessions[i])),
                description=str(elements[i]) if i < len(elements) else "",
            )
        )

    _LOG.info(
        "SEC %s : %d dépôt(s) sur %d jours%s.",
        ticker_or_cik, len(resultats), days,
        f" (types {', '.join(sorted(voulus))})" if voulus else "",
    )
    return resultats


# ---------------------------------------------------------------------------
# Trésorerie restante
# ---------------------------------------------------------------------------
def _points_xbrl(faits: dict[str, Any], concepts: tuple[str, ...]) -> list[dict[str, Any]]:
    """Extrait les points d'un des concepts, le premier qui existe.

    Args:
        faits: bloc ``facts.us-gaap`` de ``companyfacts``.
        concepts: concepts candidats, par ordre de préférence.

    Returns:
        Points en dollars issus des 10-Q et 10-K, triés par date de fin.
    """
    for concept in concepts:
        bloc = faits.get(concept)
        if not bloc:
            continue
        points = [
            p for p in (bloc.get("units") or {}).get("USD", [])
            if p.get("form") in ("10-Q", "10-K") and p.get("val") is not None
        ]
        if points:
            points.sort(key=lambda p: str(p.get("end", "")))
            return points
    return []


def estimate_cash_runway(
    cik: str | int,
    companyfacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Estime le nombre de trimestres de trésorerie restante.

    Méthode, et le piège qu'elle évite
    ----------------------------------
    ``NetCashProvidedByUsedInOperatingActivities`` est déclaré **en cumul
    depuis le début de l'exercice**, pas par trimestre. Chez Rigetti, le
    T1 2026 porte -16,2 M$ sur trois mois et le T2 -32,0 M$ sur *six* mois :
    lire la valeur du T2 comme un trimestre doublerait la consommation et
    diviserait le runway par deux.

    La consommation est donc ramenée à une cadence journalière — la valeur
    divisée par la durée réellement couverte, ``end - start`` — puis
    multipliée par la durée d'un trimestre. Le calcul est alors juste, que le
    fait couvre trois mois, six mois ou un exercice entier.

    Args:
        cik: identifiant de la société.
        companyfacts: charge ``companyfacts`` déjà obtenue, pour les tests.

    Returns:
        Dictionnaire avec ``disponible``, et en cas de succès la trésorerie,
        la consommation trimestrielle et le nombre de trimestres restants.
        ``disponible`` est ``False`` avec un motif si l'extraction XBRL
        échoue : aucun repli sur le texte des dépôts n'est tenté.
    """
    identifiant = _normaliser_cik(cik)
    echec = {
        "disponible": False,
        "cik": identifiant,
        "tresorerie_usd": None,
        "consommation_trimestrielle_usd": None,
        "trimestres_restants": None,
        "date_tresorerie": None,
        "source": "XBRL companyfacts",
    }

    if companyfacts is None:
        companyfacts = _appeler(URL_COMPANYFACTS.format(cik=identifiant))
    if not isinstance(companyfacts, dict):
        return {**echec, "motif": "companyfacts XBRL injoignable ou illisible"}

    faits = (companyfacts.get("facts") or {}).get("us-gaap") or {}
    if not faits:
        return {**echec, "motif": "aucune donnée us-gaap dans companyfacts"}

    tresorerie = _points_xbrl(faits, CONCEPTS_TRESORERIE)
    if not tresorerie:
        return {
            **echec,
            "motif": (
                "aucun concept de trésorerie exploitable "
                f"({', '.join(CONCEPTS_TRESORERIE[:2])}...)"
            ),
        }

    consommation = _points_xbrl(faits, CONCEPTS_CONSOMMATION)
    if not consommation:
        return {
            **echec,
            "motif": f"aucun concept de flux d'exploitation ({CONCEPTS_CONSOMMATION[0]})",
        }

    dernier_cash = tresorerie[-1]
    montant = float(dernier_cash["val"])

    # Consommation ramenée à une cadence journalière, pour absorber le cumul.
    dernier_flux = consommation[-1]
    try:
        debut = datetime.strptime(str(dernier_flux["start"]), "%Y-%m-%d").date()
        fin = datetime.strptime(str(dernier_flux["end"]), "%Y-%m-%d").date()
    except (KeyError, TypeError, ValueError):
        return {**echec, "motif": "période du flux d'exploitation illisible"}

    jours = (fin - debut).days
    if jours <= 0:
        return {**echec, "motif": "période du flux d'exploitation nulle ou négative"}

    flux_par_jour = float(dernier_flux["val"]) / jours
    flux_trimestriel = flux_par_jour * JOURS_PAR_TRIMESTRE

    if flux_trimestriel >= 0.0:
        # Une société qui génère de la trésorerie n'a pas de runway à calculer.
        return {
            **echec,
            "disponible": True,
            "tresorerie_usd": montant,
            "consommation_trimestrielle_usd": flux_trimestriel,
            "trimestres_restants": None,
            "date_tresorerie": str(dernier_cash.get("end")),
            "motif": "",
            "commentaire": (
                "La société dégage de la trésorerie sur la dernière période "
                "publiée : la notion de trimestres restants ne s'applique pas."
            ),
        }

    consommation_trimestrielle = abs(flux_trimestriel)
    trimestres = montant / consommation_trimestrielle if consommation_trimestrielle else None

    return {
        "disponible": True,
        "motif": "",
        "cik": identifiant,
        "tresorerie_usd": montant,
        "date_tresorerie": str(dernier_cash.get("end")),
        "concept_tresorerie": dernier_cash.get("frame") or "",
        "consommation_trimestrielle_usd": consommation_trimestrielle,
        "periode_flux": {
            "debut": str(debut),
            "fin": str(fin),
            "jours_couverts": jours,
            "valeur_brute_usd": float(dernier_flux["val"]),
            "remarque": (
                "Valeur déclarée en cumul depuis le début de l'exercice : "
                "ramenée à une cadence trimestrielle par la durée réellement couverte."
            ),
        },
        "trimestres_restants": trimestres,
        "source": "XBRL companyfacts",
        "commentaire": (
            f"Trésorerie de {montant / 1e6:.1f} M$ au {dernier_cash.get('end')}, "
            f"consommation de {consommation_trimestrielle / 1e6:.1f} M$ par trimestre "
            f"au rythme observé, soit environ {trimestres:.1f} trimestre(s). "
            "Estimation au rythme courant, qui ne préjuge d'aucune levée à venir."
            if trimestres is not None
            else ""
        ),
    }


# ---------------------------------------------------------------------------
# Activité d'initiés
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class InsiderTransaction:
    """Une opération déclarée dans un Form 4.

    Le sens d'une opération d'initié n'est pas interprété : une vente peut
    être un plan programmé, un achat une souscription contractuelle. Le
    module rapporte le fait, sa date et son montant, jamais un avis.

    Attributes:
        ticker: symbole de l'émetteur.
        date_transaction: date de l'opération, à défaut celle du dépôt.
        identite: nom et rôle du déclarant. ``None`` si illisible.
        sens: ``achat``, ``vente`` ou ``indetermine``.
        code_transaction: code brut de la SEC (``P``, ``S``, ``M``...).
        libelle_code: traduction du code en français.
        nombre_titres: quantité déclarée.
        prix_unitaire: prix par titre.
        valeur_totale_usd: produit des deux précédents, quand ils existent.
        url_depot: adresse de consultation du dépôt.
        motif: raison d'une extraction partielle, vide sinon.
    """

    ticker: str
    date_transaction: str
    identite: dict[str, Any] | None
    sens: str
    url_depot: str
    code_transaction: str = ""
    libelle_code: str = ""
    nombre_titres: int | None = None
    prix_unitaire: float | None = None
    valeur_totale_usd: float | None = None
    motif: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Sérialise l'opération pour le rapport JSON."""
        return {
            "ticker": self.ticker,
            "date_transaction": self.date_transaction,
            "identite": None if self.identite is None else dict(self.identite),
            "sens": self.sens,
            "code_transaction": self.code_transaction,
            "libelle_code": self.libelle_code,
            "nombre_titres": self.nombre_titres,
            "prix_unitaire": self.prix_unitaire,
            "valeur_totale_usd": self.valeur_totale_usd,
            "url_depot": self.url_depot,
            "motif": self.motif,
        }


def _texte_xml(noeud: Any, chemin: str) -> str | None:
    """Lit le texte d'un sous-élément, en tolérant son absence.

    Le schéma des Form 4 enveloppe la plupart des champs dans un sous-élément
    ``<value>``, mais pas tous, et pas dans toutes les versions du schéma. La
    fonction essaie donc le chemin tel quel puis suffixé de ``/value``.

    Args:
        noeud: élément XML de départ.
        chemin: chemin relatif recherché.

    Returns:
        Le texte, ou ``None`` s'il est absent ou vide.
    """
    if noeud is None:
        return None
    for candidat in (chemin, f"{chemin}/value"):
        element = noeud.find(candidat)
        if element is not None and element.text and element.text.strip():
            return element.text.strip()
    return None


def _identite_declarant(racine: Any) -> tuple[dict[str, Any] | None, str]:
    """Extrait le nom et le rôle du déclarant.

    Args:
        racine: racine du document ``ownershipDocument``.

    Returns:
        Couple ``(identite, motif)``. ``identite`` est ``None`` si le nom est
        introuvable, et le motif dit alors pourquoi.
    """
    proprietaire = racine.find("reportingOwner")
    if proprietaire is None:
        return None, "bloc reportingOwner absent du dépôt"

    nom = _texte_xml(proprietaire, "reportingOwnerId/rptOwnerName")
    if not nom:
        return None, "nom du déclarant absent du dépôt"

    relation = proprietaire.find("reportingOwnerRelationship")
    role: str | None = None
    titre: str | None = None
    if relation is not None:
        def _vrai(champ: str) -> bool:
            """Un drapeau du schéma vaut « 1 » ou « true » selon les dépôts."""
            valeur = _texte_xml(relation, champ)
            return str(valeur).strip().lower() in {"1", "true"} if valeur else False

        # Ordre de priorité : le rôle le plus engageant l'emporte quand
        # plusieurs drapeaux sont levés — un dirigeant qui siège au conseil
        # est d'abord un administrateur au regard de la déclaration.
        if _vrai("isDirector"):
            role = "administrateur"
        elif _vrai("isOfficer"):
            role = "dirigeant"
        elif _vrai("isTenPercentOwner"):
            role = "actionnaire_10pct"
        titre = _texte_xml(relation, "officerTitle")

    return {"nom": nom, "role": role, "titre_fonction": titre}, ""


def _nombre(texte: str | None) -> float | None:
    """Convertit un champ numérique du dépôt, en tolérant les séparateurs.

    Args:
        texte: valeur brute.

    Returns:
        La valeur, ou ``None`` si elle n'est pas lisible.
    """
    if texte is None:
        return None
    try:
        return float(str(texte).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parser_form4(
    xml_brut: str | bytes,
    ticker: str = "",
    url_depot: str = "",
    date_depot: str = "",
) -> tuple[list[InsiderTransaction], str]:
    """Extrait les opérations d'un Form 4, avec dégradation par paliers.

    Args:
        xml_brut: contenu du fichier ``form4.xml``.
        ticker: symbole de l'émetteur, repris dans chaque opération.
        url_depot: adresse de consultation, reprise dans chaque opération.
        date_depot: date du dépôt, utilisée si l'opération n'en porte pas.

    Returns:
        Couple ``(transactions, motif)``. La liste est vide quand rien n'est
        exploitable, et le motif dit alors pourquoi. Elle contient une entrée
        par opération déclarée : un même dépôt en porte souvent plusieurs.
    """
    import xml.etree.ElementTree as ET

    texte = xml_brut.decode("utf-8", "replace") if isinstance(xml_brut, bytes) else str(xml_brut)

    # Tous les dépôts ne publient pas un fichier XML nu. Quand le document
    # séparé est absent, on récupère la soumission complète, qui est un
    # conteneur SGML : en-têtes EDGAR, puis le XML entre balises <XML>.
    # ElementTree refuse ce préambule. On isole donc le document lui-même,
    # ce qui traite d'un coup les deux formes rencontrées sur EDGAR.
    debut = texte.find("<ownershipDocument")
    if debut != -1:
        fin = texte.rfind("</ownershipDocument>")
        if fin != -1:
            texte = texte[debut : fin + len("</ownershipDocument>")]

    try:
        racine = ET.fromstring(texte)
    except ET.ParseError as exc:
        return [], f"XML du Form 4 illisible ({exc})"

    identite, motif_identite = _identite_declarant(racine)
    symbole = ticker or _texte_xml(racine, "issuer/issuerTradingSymbol") or ""
    date_document = _texte_xml(racine, "periodOfReport") or date_depot

    transactions: list[InsiderTransaction] = []
    for balise in ("nonDerivativeTransaction", "derivativeTransaction"):
        for operation in racine.iter(balise):
            code = (_texte_xml(operation, "transactionCoding/transactionCode") or "").strip().upper()
            titres = _nombre(_texte_xml(operation, "transactionAmounts/transactionShares"))
            prix = _nombre(_texte_xml(operation, "transactionAmounts/transactionPricePerShare"))
            date_operation = _texte_xml(operation, "transactionDate") or date_document or ""

            # Palier 2 : sans code lisible, l'opération est publiée mais son
            # sens reste indéterminé plutôt que deviné.
            sens = SENS_PAR_CODE.get(code, "indetermine")
            valeur = titres * prix if (titres is not None and prix) else None

            motifs: list[str] = []
            if motif_identite:
                motifs.append(motif_identite)
            if not code:
                motifs.append("code d'opération absent : sens indéterminé")
            elif sens == "indetermine":
                motifs.append(
                    f"code {code} — {CODES_TRANSACTION.get(code, 'code non répertorié')} : "
                    "ce n'est pas une décision d'achat ou de vente sur le marché"
                )
            if titres is None:
                motifs.append("nombre de titres illisible")

            transactions.append(
                InsiderTransaction(
                    ticker=symbole,
                    date_transaction=date_operation,
                    identite=identite,
                    sens=sens,
                    code_transaction=code,
                    libelle_code=CODES_TRANSACTION.get(code, "code non répertorié" if code else ""),
                    nombre_titres=None if titres is None else int(round(titres)),
                    prix_unitaire=prix,
                    valeur_totale_usd=valeur,
                    url_depot=url_depot,
                    motif=" ; ".join(motifs),
                )
            )

    if not transactions:
        # Palier 3 : le dépôt existe mais aucune opération n'en sort. On
        # renvoie tout de même une entrée de référence, pour que le dépôt
        # reste visible avec son lien.
        return (
            [
                InsiderTransaction(
                    ticker=symbole,
                    date_transaction=date_document or date_depot,
                    identite=identite,
                    sens="indetermine",
                    url_depot=url_depot,
                    motif=(
                        "aucune opération exploitable dans le dépôt"
                        + (f" ; {motif_identite}" if motif_identite else "")
                    ),
                )
            ],
            "aucune opération exploitable : seule la référence du dépôt est publiée",
        )

    return transactions, ""


def _telecharger_form4(cik: str, accession: str) -> tuple[str | None, str]:
    """Télécharge le XML d'un Form 4 depuis les archives EDGAR.

    Args:
        cik: CIK sur dix chiffres.
        accession: numéro d'accession avec tirets.

    Returns:
        Couple ``(contenu, motif)``.
    """
    sans_tirets = accession.replace("-", "")
    base = f"{URL_ARCHIVES}/{int(cik)}/{sans_tirets}"
    # Le nom du document varie : « form4.xml » est le cas courant, mais
    # certains déposants nomment le fichier autrement. On lit donc l'index
    # du dépôt pour retrouver le XML réellement présent.
    # « form4.xml » et « ownership.xml » sont les deux noms courants ; la
    # soumission complète .txt sert de dernier recours et sera dégagée de son
    # enveloppe SGML par parser_form4.
    for url in (
        f"{base}/form4.xml",
        f"{base}/ownership.xml",
        f"{base}/{accession}.txt",
    ):
        try:
            reponse = requests.get(url, timeout=TIMEOUT, headers=_entetes())
            if reponse.status_code == 200 and "<ownershipDocument" in reponse.text:
                return reponse.text, ""
        except requests.RequestException as exc:
            _LOG.debug("Form 4 injoignable sur %s : %s", url, exc)
            continue

    try:
        index = requests.get(f"{base}/", timeout=TIMEOUT, headers=_entetes())
        index.raise_for_status()
        noms = re.findall(r'href="[^"]*?/([A-Za-z0-9_.\-]+\.xml)"', index.text)
        for nom in dict.fromkeys(noms):
            reponse = requests.get(f"{base}/{nom}", timeout=TIMEOUT, headers=_entetes())
            if reponse.status_code == 200 and "<ownershipDocument" in reponse.text:
                return reponse.text, ""
    except requests.RequestException as exc:
        return None, f"index du dépôt injoignable ({type(exc).__name__})"

    return None, "aucun document XML de Form 4 trouvé dans le dépôt"


def detect_insider_activity(
    cik: str | int,
    days: int = 14,
    aujourd_hui: date | None = None,
    submissions: dict[str, Any] | None = None,
    ticker: str = "",
    xml_par_accession: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Relève et détaille les Form 4 récents d'une société.

    Args:
        cik: identifiant de la société.
        days: profondeur de recherche, en jours.
        aujourd_hui: date de référence, pour les tests.
        submissions: charge déjà obtenue, pour les tests.
        ticker: symbole, repris dans les opérations.
        xml_par_accession: contenus XML déjà obtenus, indexés par numéro
            d'accession. Fournis, aucun appel réseau n'est effectué — c'est
            ce qui rend l'analyse testable hors ligne.

    Returns:
        Dictionnaire avec ``disponible``, le nombre d'opérations et leur
        détail. Chaque opération porte toujours un ``sens``, fût-il
        ``indetermine``.
    """
    identifiant = _normaliser_cik(cik)
    reference = aujourd_hui or date.today()

    if submissions is None:
        submissions = _appeler(URL_SUBMISSIONS.format(cik=identifiant))
    if not isinstance(submissions, dict):
        return {
            "disponible": False,
            "motif": "historique des dépôts SEC injoignable",
            "cik": identifiant,
            "n_depots": 0,
            "n_transactions": 0,
            "transactions": [],
        }

    depots = get_recent_filings(
        identifiant, types=["4"], days=days, aujourd_hui=reference, submissions=submissions
    )
    nom_societe = str(submissions.get("name", ""))
    symbole = ticker or (submissions.get("tickers") or [""])[0]

    transactions: list[dict[str, Any]] = []
    motifs_depots: list[str] = []

    for depot in depots:
        if xml_par_accession is not None:
            xml_brut = xml_par_accession.get(depot.accession)
            motif = "" if xml_brut else "XML non fourni pour ce dépôt"
        else:
            xml_brut, motif = _telecharger_form4(identifiant, depot.accession)

        if not xml_brut:
            # Palier 3 : le dépôt reste visible même sans son contenu.
            motifs_depots.append(f"{depot.accession} : {motif}")
            transactions.append(
                InsiderTransaction(
                    ticker=symbole,
                    date_transaction=depot.date_depot.strftime("%Y-%m-%d"),
                    identite=None,
                    sens="indetermine",
                    url_depot=depot.url,
                    motif=motif,
                ).to_dict()
            )
            continue

        operations, motif_analyse = parser_form4(
            xml_brut,
            ticker=symbole,
            url_depot=depot.url,
            date_depot=depot.date_depot.strftime("%Y-%m-%d"),
        )
        if motif_analyse:
            motifs_depots.append(f"{depot.accession} : {motif_analyse}")

        if not operations:
            # Palier 3 au niveau de l'appelant : une analyse en échec ne doit
            # pas faire disparaître le dépôt de la sortie. Le lien reste
            # publié, avec le motif, plutôt qu'un silence.
            transactions.append(
                InsiderTransaction(
                    ticker=symbole,
                    date_transaction=depot.date_depot.strftime("%Y-%m-%d"),
                    identite=None,
                    sens="indetermine",
                    url_depot=depot.url,
                    motif=motif_analyse or "dépôt non analysable",
                ).to_dict()
            )
            continue

        transactions.extend(o.to_dict() for o in operations)

    achats = [t for t in transactions if t["sens"] == "achat"]
    ventes = [t for t in transactions if t["sens"] == "vente"]

    return {
        "disponible": True,
        "motif": "",
        "cik": identifiant,
        "societe": nom_societe,
        "ticker": symbole,
        "fenetre_jours": days,
        "n_depots": len(depots),
        "n_transactions": len(transactions),
        "n_achats_marche": len(achats),
        "n_ventes_marche": len(ventes),
        "transactions": transactions,
        "motifs_par_depot": motifs_depots,
        "limite_connue": (
            "Seuls les codes P (achat sur le marché) et S (vente sur le marché) "
            "donnent un sens. Les autres opérations — exercices d'options, "
            "attributions, retenues fiscales — sortent en « indetermine » avec leur "
            "code : les compter comme des achats serait un contresens."
        ),
    }
