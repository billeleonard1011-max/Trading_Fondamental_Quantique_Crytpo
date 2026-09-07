"""Positionnement sur les futures or du COMEX (rapport CFTC).

Ce que mesure ce module
-----------------------
Le rapport *Commitments of Traders* dit qui détient quoi sur le marché à
terme. Deux catégories comptent pour l'or :

* les **managed money** — fonds spéculatifs, CTA, gérants de conviction. Ils
  suivent la tendance. Leur position nette est un baromètre de consensus :
  quand elle est extrême, il ne reste presque personne pour acheter ;
* les **producers / merchants** — mines, raffineurs, industriels. Ils
  couvrent une production physique, ils sont structurellement vendeurs. Leur
  position renseigne sur le comportement de couverture, pas sur une opinion.

Pourquoi un percentile et pas le chiffre brut
---------------------------------------------
« 137 000 contrats nets acheteurs » ne veut rien dire seul : l'open interest
de l'or a doublé en quinze ans, et une même position absolue ne représente
plus la même emprise sur le marché. Le percentile sur cinq ans répond à la
seule question utile : *par rapport à son propre passé récent, ce
positionnement est-il extrême ?*

Fraîcheur de la donnée : point d'attention
------------------------------------------
Le rapport est publié le **vendredi à 15:30 heure de New York** et décrit les
positions du **mardi précédent**. Une donnée « du jour » a donc au minimum
trois jours, et jusqu'à dix le jeudi suivant. Le module expose toujours la
date d'observation et l'âge en jours : présenter un positionnement de mardi
dernier comme la photographie du marché d'aujourd'hui est une erreur, pas
un détail de présentation.

Identifiants vérifiés
---------------------
Rien ici n'est deviné. Les deux identifiants ont été contrôlés contre les
sources elles-mêmes le 7 septembre 2026 :

* **Jeu de données Socrata** ``72hh-3qpy`` — *Commitments of Traders :
  Disaggregated Futures Only*. Confirmé par le catalogue du portail
  ``publicreporting.cftc.gov`` et par une requête réelle renvoyant un code
  HTTP 200.
* **Contrat** ``088691`` — *GOLD - COMMODITY EXCHANGE INC.*, contrats de 100
  onces troy. Obtenu en **énumérant** les codes du jeu de données dont le
  nom de matière contient « GOLD », puis en retenant celui du COMEX. Cette
  vérification a écarté deux pièges : ``088695`` (Micro Gold, contrats de 10
  onces) et ``088LM1`` (Gold 1 once, Coinbase Derivatives). Filtrer sur le
  libellé « GOLD » agrégerait les trois.
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Final

import numpy as np
import pandas as pd
import requests

_LOG: Final = logging.getLogger(__name__)

#: Point d'accès Socrata du rapport désagrégé, futures seules.
URL_SOCRATA: Final = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"

#: Archives annuelles, utilisées quand Socrata ne répond pas.
URL_ARCHIVE: Final = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{annee}.zip"

#: Contrat or 100 onces du COMEX.
CODE_CONTRAT_OR: Final = "088691"

#: Délai maximal, en secondes, accordé à un appel réseau.
TIMEOUT: Final[float] = float(os.environ.get("HTTP_TIMEOUT", "20"))

#: Délai plus long pour l'archive : le fichier pèse quelques mégaoctets.
TIMEOUT_ARCHIVE: Final[float] = float(os.environ.get("HTTP_TIMEOUT_ARCHIVE", "60"))

#: Cinq ans de rapports hebdomadaires.
FENETRE_PERCENTILE: Final[int] = 260

#: En deçà, le percentile n'est pas publié.
MIN_SEMAINES_PERCENTILE: Final[int] = 104

_ENTETES: Final[dict[str, str]] = {
    "User-Agent": "Mozilla/5.0 (compatible; veille-marches/1.0)"
}

#: Correspondance entre les noms de colonnes des deux sources et les noms
#: internes. Les deux sources ne nomment PAS les colonnes de la même façon :
#: l'API Socrata écrit ``prod_merc_positions_long`` là où l'archive annuelle
#: écrit ``Prod_Merc_Positions_Long_All``. Le suffixe ``_All`` disparaît côté
#: API pour les producteurs, mais pas pour les managed money. Sans cette
#: table, le repli renverrait silencieusement des positions producteurs
#: vides. Vérifié en lisant l'en-tête réel des deux sources.
_ALIAS_COLONNES: Final[dict[str, tuple[str, ...]]] = {
    "date_observation": ("report_date_as_yyyy_mm_dd", "report_date_as_yyyy-mm-dd"),
    "mm_long": ("m_money_positions_long_all",),
    "mm_short": ("m_money_positions_short_all",),
    "prod_long": ("prod_merc_positions_long", "prod_merc_positions_long_all"),
    "prod_short": ("prod_merc_positions_short", "prod_merc_positions_short_all"),
    "open_interest": ("open_interest_all",),
    "code_contrat": ("cftc_contract_market_code",),
    "libelle": ("market_and_exchange_names",),
}

__all__ = ["PositionnementCOT", "fetch_cot_or", "get_positionnement_or"]


@dataclass(slots=True, frozen=True)
class PositionnementCOT:
    """Photographie du positionnement sur l'or à une date d'observation.

    Attributes:
        date_observation: mardi décrit par le rapport.
        date_publication_estimee: vendredi de publication, déduit du mardi.
        age_jours: nombre de jours entre l'observation et aujourd'hui.
        net_managed_money: positions longues moins courtes des spéculatifs.
        net_producers: idem pour les producteurs et négociants.
        open_interest: nombre total de contrats ouverts.
        percentile_managed_money: rang du net spéculatif sur cinq ans, en %.
        n_semaines_percentile: profondeur d'historique du percentile.
        variation_hebdo_managed_money: écart avec le rapport précédent.
        source: ``api_socrata`` ou ``archive_annuelle``.
        disponible: ``False`` si aucune donnée n'a pu être obtenue.
        motif: raison de l'indisponibilité, vide sinon.
    """

    date_observation: pd.Timestamp | None = None
    date_publication_estimee: pd.Timestamp | None = None
    age_jours: int | None = None
    net_managed_money: int | None = None
    net_producers: int | None = None
    open_interest: int | None = None
    percentile_managed_money: float | None = None
    n_semaines_percentile: int = 0
    variation_hebdo_managed_money: int | None = None
    source: str = ""
    disponible: bool = False
    motif: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Sérialise le positionnement pour le rapport JSON."""
        return {
            "disponible": self.disponible,
            "motif": self.motif,
            "source": self.source,
            "date_observation": _iso(self.date_observation),
            "date_publication_estimee": _iso(self.date_publication_estimee),
            "age_jours": self.age_jours,
            "net_managed_money": self.net_managed_money,
            "net_producers": self.net_producers,
            "open_interest": self.open_interest,
            "percentile_managed_money": self.percentile_managed_money,
            "n_semaines_percentile": self.n_semaines_percentile,
            "variation_hebdo_managed_money": self.variation_hebdo_managed_money,
            "lecture": self.lecture(),
        }

    def lecture(self) -> str:
        """Phrase de synthèse, âge de la donnée inclus."""
        if not self.disponible:
            return f"Positionnement CFTC indisponible : {self.motif}"

        age = "" if self.age_jours is None else f" Donnée vieille de {self.age_jours} jour(s)."
        if self.percentile_managed_money is None:
            return (
                f"Managed money net à {self.net_managed_money:+,} contrats au "
                f"{_iso(self.date_observation)}, sans percentile faute "
                f"d'historique suffisant ({self.n_semaines_percentile} semaines).{age}"
            )

        p = self.percentile_managed_money
        if p >= 90.0:
            qualite = "positionnement acheteur extrême : le consensus est déjà en place, il reste peu d'acheteurs marginaux"
        elif p >= 70.0:
            qualite = "positionnement acheteur nourri, sans être extrême"
        elif p <= 10.0:
            qualite = "positionnement vendeur extrême : un rachat de découvert peut alimenter une hausse violente"
        elif p <= 30.0:
            qualite = "positionnement dégarni, le marché a déjà vendu"
        else:
            qualite = "positionnement médian, sans tension"
        return (
            f"Managed money net à {self.net_managed_money:+,} contrats, soit le "
            f"{p:.0f}e percentile sur {self.n_semaines_percentile} semaines : {qualite}.{age}"
        )


def _iso(horodatage: pd.Timestamp | None) -> str | None:
    """Convertit un horodatage en date ISO, en tolérant ``None``."""
    return None if horodatage is None else str(pd.Timestamp(horodatage).date())


def _colonne(cadre: pd.DataFrame, interne: str) -> pd.Series | None:
    """Retrouve une colonne quelle que soit la source qui l'a produite.

    Args:
        cadre: données brutes, colonnes déjà mises en minuscules.
        interne: nom interne recherché, clé de :data:`_ALIAS_COLONNES`.

    Returns:
        La série correspondante, ou ``None`` si aucun alias ne correspond.
    """
    for alias in _ALIAS_COLONNES[interne]:
        if alias in cadre.columns:
            return cadre[alias]
    return None


def _normaliser(brut: pd.DataFrame, source: str) -> pd.DataFrame:
    """Ramène les données d'une source quelconque au format commun.

    Args:
        brut: données telles que reçues.
        source: nom de la source, pour la journalisation.

    Returns:
        DataFrame indexé par date d'observation, colonnes ``net_managed_money``,
        ``net_producers``, ``open_interest``. Vide si une colonne essentielle
        manque.
    """
    if brut is None or brut.empty:
        return pd.DataFrame()

    cadre = brut.copy()
    cadre.columns = [str(c).strip().lower() for c in cadre.columns]

    requis = ("date_observation", "mm_long", "mm_short", "open_interest")
    extraites: dict[str, pd.Series] = {}
    for interne in (*requis, "prod_long", "prod_short"):
        serie = _colonne(cadre, interne)
        if serie is None:
            if interne in requis:
                _LOG.warning(
                    "Colonne « %s » absente des données %s : source inutilisable.",
                    interne,
                    source,
                )
                return pd.DataFrame()
            _LOG.warning(
                "Colonne « %s » absente des données %s : net producteurs non calculé.",
                interne,
                source,
            )
            continue
        extraites[interne] = serie

    dates = pd.to_datetime(extraites["date_observation"], errors="coerce", utc=False)
    # L'API renvoie un horodatage complet (« 2026-09-01T00:00:00.000 ») et
    # l'archive une date sèche : on ne garde que le jour dans les deux cas.
    dates = pd.to_datetime(dates).dt.normalize()

    def _entier(nom: str) -> np.ndarray:
        """Convertit une colonne en nombres, valeurs illisibles écartées.

        Le résultat est renvoyé en tableau nu, pas en Series : les colonnes
        extraites portent l'index d'origine (0, 1, 2...) alors que le cadre
        final est indexé par date. Assembler des Series d'index différent
        déclencherait un réalignement silencieux de pandas, et toutes les
        valeurs deviendraient manquantes sans le moindre message.
        """
        if nom not in extraites:
            return np.full(len(cadre), np.nan, dtype="float64")
        return pd.to_numeric(
            extraites[nom].astype(str).str.replace(",", "", regex=False),
            errors="coerce",
        ).to_numpy(dtype="float64")

    resultat = pd.DataFrame(
        {
            "net_managed_money": _entier("mm_long") - _entier("mm_short"),
            "net_producers": _entier("prod_long") - _entier("prod_short"),
            "open_interest": _entier("open_interest"),
        },
        index=pd.DatetimeIndex(dates, name="date_observation"),
    )

    resultat = resultat[resultat.index.notna()]
    resultat = resultat.dropna(subset=["net_managed_money", "open_interest"])
    resultat = resultat[~resultat.index.duplicated(keep="last")].sort_index()
    _LOG.info("COT %s : %d rapport(s) hebdomadaire(s).", source, len(resultat))
    return resultat


# ---------------------------------------------------------------------------
# Source 1 : API Socrata
# ---------------------------------------------------------------------------
def _fetch_socrata(code_contrat: str, limite: int) -> pd.DataFrame:
    """Interroge l'API Socrata de la CFTC.

    Le filtre porte sur le **code de contrat**, pas sur le libellé : le jeu de
    données contient trois contrats dont le nom comprend « GOLD », et un filtre
    textuel les mélangerait.

    Un jeton applicatif Socrata (``CFTC_APP_TOKEN``) est accepté s'il est
    présent dans l'environnement : il ne sert qu'à relever la limite de débit,
    l'accès reste public et anonyme sans lui.

    Args:
        code_contrat: code CFTC du contrat.
        limite: nombre de rapports hebdomadaires demandés.

    Returns:
        Données brutes, vides en cas d'échec.
    """
    parametres = {
        "$select": (
            "report_date_as_yyyy_mm_dd,cftc_contract_market_code,"
            "market_and_exchange_names,open_interest_all,"
            "m_money_positions_long_all,m_money_positions_short_all,"
            "prod_merc_positions_long,prod_merc_positions_short"
        ),
        "$where": f"cftc_contract_market_code='{code_contrat}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(int(limite)),
    }
    entetes = dict(_ENTETES)
    jeton = os.environ.get("CFTC_APP_TOKEN", "").strip()
    if jeton:
        entetes["X-App-Token"] = jeton

    try:
        reponse = requests.get(URL_SOCRATA, params=parametres, timeout=TIMEOUT, headers=entetes)
        reponse.raise_for_status()
        charge = reponse.json()
    except requests.RequestException as exc:
        _LOG.warning("API CFTC injoignable : %s", exc)
        return pd.DataFrame()
    except ValueError as exc:
        _LOG.warning("Réponse CFTC illisible : %s", exc)
        return pd.DataFrame()

    if not isinstance(charge, list) or not charge:
        _LOG.warning("L'API CFTC n'a renvoyé aucun enregistrement pour le contrat %s.", code_contrat)
        return pd.DataFrame()
    return pd.DataFrame(charge)


# ---------------------------------------------------------------------------
# Source 2 : archives annuelles
# ---------------------------------------------------------------------------
def _fetch_archive(code_contrat: str, annees: int) -> pd.DataFrame:
    """Télécharge et concatène les archives annuelles de la CFTC.

    Chaque archive est un ZIP contenant un unique fichier texte séparé par des
    virgules, couvrant une année entière. Une année manquante est ignorée : le
    repli reste utile même partiel.

    Args:
        code_contrat: code CFTC du contrat.
        annees: nombre d'années à remonter depuis l'année courante.

    Returns:
        Données brutes concaténées, vides si aucune archive n'a pu être lue.
    """
    annee_courante = date.today().year
    morceaux: list[pd.DataFrame] = []

    for annee in range(annee_courante, annee_courante - max(int(annees), 1), -1):
        url = URL_ARCHIVE.format(annee=annee)
        try:
            reponse = requests.get(url, timeout=TIMEOUT_ARCHIVE, headers=_ENTETES)
            reponse.raise_for_status()
        except requests.RequestException as exc:
            _LOG.warning("Archive CFTC %d indisponible : %s", annee, exc)
            continue

        try:
            with zipfile.ZipFile(io.BytesIO(reponse.content)) as archive:
                noms = archive.namelist()
                if not noms:
                    _LOG.warning("Archive CFTC %d vide.", annee)
                    continue
                with archive.open(noms[0]) as fichier:
                    cadre = pd.read_csv(fichier, low_memory=False)
        except (zipfile.BadZipFile, ValueError, pd.errors.ParserError) as exc:
            _LOG.warning("Archive CFTC %d illisible : %s", annee, exc)
            continue

        cadre.columns = [str(c).strip().lower() for c in cadre.columns]
        colonne_code = _ALIAS_COLONNES["code_contrat"][0]
        if colonne_code not in cadre.columns:
            _LOG.warning("Archive CFTC %d sans colonne de code contrat.", annee)
            continue

        # Le code est parfois lu comme un entier (« 88691 ») : la comparaison
        # se fait sur la chaîne rembourrée à six caractères.
        codes = cadre[colonne_code].astype(str).str.strip().str.zfill(len(code_contrat))
        retenu = cadre[codes == code_contrat]
        if retenu.empty:
            _LOG.warning("Archive CFTC %d : aucun enregistrement pour le contrat %s.", annee, code_contrat)
            continue
        morceaux.append(retenu)
        _LOG.debug("Archive CFTC %d : %d ligne(s) retenue(s).", annee, len(retenu))

    if not morceaux:
        return pd.DataFrame()
    return pd.concat(morceaux, ignore_index=True)


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------
def fetch_cot_or(
    code_contrat: str = CODE_CONTRAT_OR,
    semaines: int = FENETRE_PERCENTILE,
    autoriser_archive: bool = True,
) -> tuple[pd.DataFrame, str]:
    """Récupère l'historique de positionnement sur l'or.

    L'API Socrata est essayée d'abord ; l'archive annuelle prend le relais si
    elle échoue ou renvoie trop peu de rapports pour calculer un percentile.

    Args:
        code_contrat: code CFTC du contrat.
        semaines: profondeur d'historique souhaitée.
        autoriser_archive: mettre à ``False`` pour interdire le repli, par
            exemple dans un test.

    Returns:
        Couple ``(historique, source)``. L'historique est vide et la source
        est ``"aucune"`` si les deux canaux ont échoué.
    """
    brut = _fetch_socrata(code_contrat, limite=semaines + 10)
    historique = _normaliser(brut, "api_socrata")
    if len(historique) >= MIN_SEMAINES_PERCENTILE:
        return historique, "api_socrata"

    if not autoriser_archive:
        return historique, ("api_socrata" if not historique.empty else "aucune")

    _LOG.warning(
        "API CFTC insuffisante (%d rapport(s)) : repli sur les archives annuelles.",
        len(historique),
    )
    # Une année d'archive contient environ 52 rapports.
    annees = max(2, int(np.ceil(semaines / 52.0)) + 1)
    brut_archive = _fetch_archive(code_contrat, annees=annees)
    historique_archive = _normaliser(brut_archive, "archive_annuelle")

    if historique_archive.empty:
        return historique, ("api_socrata" if not historique.empty else "aucune")
    if historique.empty:
        return historique_archive, "archive_annuelle"

    # Les deux canaux se recouvrent : on fusionne en gardant l'API, plus fraîche.
    fusion = pd.concat([historique_archive, historique])
    fusion = fusion[~fusion.index.duplicated(keep="last")].sort_index()
    return fusion, "api_socrata+archive_annuelle"


def get_positionnement_or(
    code_contrat: str = CODE_CONTRAT_OR,
    semaines: int = FENETRE_PERCENTILE,
    min_semaines: int = MIN_SEMAINES_PERCENTILE,
    aujourd_hui: date | None = None,
    historique: pd.DataFrame | None = None,
) -> PositionnementCOT:
    """Lit le dernier rapport COT sur l'or et le situe dans son historique.

    Args:
        code_contrat: code CFTC du contrat.
        semaines: fenêtre du percentile, en rapports hebdomadaires.
        min_semaines: profondeur minimale sous laquelle le percentile est
            refusé plutôt que publié sur trop peu de points.
        aujourd_hui: date de référence pour le calcul de l'âge. Utile aux
            tests ; date du jour par défaut.
        historique: historique déjà chargé. S'il est fourni, aucun appel
            réseau n'est effectué — c'est ce qui rend ce module testable
            hors ligne.

    Returns:
        Le positionnement, éventuellement marqué indisponible.
    """
    source = "fourni"
    if historique is None:
        historique, source = fetch_cot_or(code_contrat=code_contrat, semaines=semaines)

    if historique is None or historique.empty:
        return PositionnementCOT(
            source=source,
            motif="ni l'API Socrata ni les archives annuelles n'ont fourni de données",
        )

    historique = historique.sort_index()
    derniere = historique.iloc[-1]
    date_obs = pd.Timestamp(historique.index[-1])

    reference = aujourd_hui or datetime.now(timezone.utc).date()
    age = int((pd.Timestamp(reference).normalize() - date_obs.normalize()).days)

    # Le rapport du mardi paraît le vendredi suivant, soit trois jours après.
    publication = date_obs + pd.Timedelta(days=3)

    fenetre = historique["net_managed_money"].dropna().iloc[-int(semaines):]
    percentile: float | None = None
    if len(fenetre) >= min_semaines:
        courant = float(fenetre.iloc[-1])
        percentile = float((fenetre.to_numpy() <= courant).mean() * 100.0)
    else:
        _LOG.warning(
            "Percentile COT refusé : %d semaine(s) d'historique, %d requises.",
            len(fenetre),
            min_semaines,
        )

    variation: int | None = None
    if len(historique) >= 2:
        precedent = historique["net_managed_money"].iloc[-2]
        if pd.notna(precedent):
            variation = int(round(float(derniere["net_managed_money"] - precedent)))

    net_producers = derniere.get("net_producers")
    return PositionnementCOT(
        date_observation=date_obs,
        date_publication_estimee=publication,
        age_jours=age,
        net_managed_money=int(round(float(derniere["net_managed_money"]))),
        net_producers=None if pd.isna(net_producers) else int(round(float(net_producers))),
        open_interest=int(round(float(derniere["open_interest"]))),
        percentile_managed_money=percentile,
        n_semaines_percentile=len(fenetre),
        variation_hebdo_managed_money=variation,
        source=source,
        disponible=True,
    )
