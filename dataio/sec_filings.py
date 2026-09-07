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

__all__ = [
    "Filing",
    "InsiderTransaction",
    "get_cik",
    "get_recent_filings",
    "estimate_cash_runway",
    "detect_insider_activity",
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
    """Un dépôt Form 4, sans interprétation.

    Le sens d'une transaction d'initié n'est pas lisible depuis le formulaire
    seul : une vente peut être un plan programmé, un achat une souscription
    contractuelle. Le module rapporte donc le fait et sa date, jamais un avis.

    Attributes:
        date_depot: date du dépôt.
        age_jours: ancienneté en jours.
        deposant: nom du déclarant, quand il est lisible.
        role: fonction déclarée, si disponible.
        sens: ``achat``, ``vente`` ou ``indetermine``.
        accession: identifiant du dépôt.
        url: adresse de consultation.
    """

    date_depot: date
    age_jours: int
    deposant: str
    role: str
    sens: str
    accession: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        """Sérialise la transaction pour le rapport JSON."""
        return {
            "date_depot": self.date_depot.strftime("%Y-%m-%d"),
            "age_jours": self.age_jours,
            "deposant": self.deposant,
            "role": self.role,
            "sens": self.sens,
            "accession": self.accession,
            "url": self.url,
        }


def detect_insider_activity(
    cik: str | int,
    days: int = 14,
    aujourd_hui: date | None = None,
    submissions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Relève les Form 4 récents d'une société.

    Le sens de la transaction n'est pas extrait du document lui-même : le
    Form 4 est un XML dont la structure varie, et en lire le détail
    demanderait un analyseur dédié. Le module signale donc l'existence et la
    date des dépôts, et laisse le lien pour consultation. ``sens`` reste
    ``indetermine`` tant que cet analyseur n'existe pas — c'est un manque
    déclaré, pas une valeur devinée.

    Args:
        cik: identifiant de la société.
        days: profondeur de recherche, en jours.
        aujourd_hui: date de référence, pour les tests.
        submissions: charge déjà obtenue, pour les tests.

    Returns:
        Dictionnaire avec ``disponible``, le nombre de dépôts et leur détail.
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
            "n_transactions": 0,
            "transactions": [],
        }

    depots = get_recent_filings(
        identifiant, types=["4"], days=days, aujourd_hui=reference, submissions=submissions
    )
    nom_societe = str(submissions.get("name", ""))

    transactions = [
        InsiderTransaction(
            date_depot=d.date_depot,
            age_jours=d.age_jours,
            # Le nom du déclarant n'est pas dans l'index des dépôts : il est
            # dans le document. Faute de l'analyser, on nomme la société et on
            # laisse le lien plutôt que d'inventer un déposant.
            deposant="",
            role="",
            sens="indetermine",
            accession=d.accession,
            url=d.url,
        ).to_dict()
        for d in depots
    ]

    return {
        "disponible": True,
        "motif": "",
        "cik": identifiant,
        "societe": nom_societe,
        "fenetre_jours": days,
        "n_transactions": len(transactions),
        "transactions": transactions,
        "limite_connue": (
            "Le sens (achat ou vente) et le nom du déclarant ne sont pas extraits : "
            "ils figurent dans le XML du Form 4, dont la structure varie. Les dépôts "
            "sont signalés avec leur lien, sans être interprétés."
        ),
    }
