"""Flux et tensions physiques du complexe métaux précieux.

Trois indicateurs, indépendants les uns des autres. Chacun peut échouer
seul : une source muette ne renvoie jamais une valeur inventée, elle renvoie
``None`` accompagné du motif.

1. **Encours du GLD** — combien d'or le plus gros ETF adossé au métal
   détient réellement. C'est la mesure de la demande d'investissement
   occidentale. *Voir l'avertissement ci-dessous : cet indicateur n'est pas
   alimenté de façon fiable.*
2. **Ratio or / argent** — combien d'onces d'argent vaut une once d'or.
   L'argent est à la fois métal précieux et métal industriel : il monte plus
   vite que l'or quand la hausse est spéculative et large, il décroche quand
   seule la peur porte l'or. Un ratio qui monte signale une hausse défensive
   et étroite ; un ratio qui baisse, un vrai appétit pour le complexe.
3. **Ratio GDX / or** — les mines d'or sont un pari à effet de levier sur le
   métal : leurs coûts sont fixes, leur revenu suit le cours. Quand l'or
   monte sans les minières, le marché ne croit pas à la durabilité du
   mouvement. C'est un signal de confirmation, ou de divergence.

Avertissement sur l'encours du GLD
----------------------------------
Le fichier quotidien de SPDR **n'est pas exploitable par programme**. La
vérification a été faite, elle est reproduite dans :func:`get_encours_gld` :
l'adresse d'archive redirige vers un PDF (la liste nominative des lingots),
et le classeur de positions de SSGA renvoie une erreur 404. Aucune des deux
ne fournit de série de tonnage.

Le repli utilise ``yfinance``, qui donne l'actif net **du jour uniquement**.
La limite est majeure et doit être comprise : ce qui a une valeur de signal,
c'est le *flux* — l'ETF a-t-il créé ou détruit des parts cette semaine ? —
et ce flux exige un historique que le repli ne fournit pas. L'indicateur est
donc publié comme un niveau documentaire, marqué approximation, et sa
variation reste explicitement ``None``. Il n'alimente pas le biais quotidien.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Final

import pandas as pd
import requests

from dataio import market

_LOG: Final = logging.getLogger(__name__)

#: Délai maximal, en secondes, accordé à un appel réseau.
TIMEOUT: Final[float] = float(os.environ.get("HTTP_TIMEOUT", "20"))

#: Adresses officielles de SPDR, testées puis écartées. Conservées pour que
#: la vérification soit rejouable et pour détecter le jour où elles
#: redeviendraient exploitables.
URLS_SPDR: Final[tuple[str, ...]] = (
    "https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive_EN.csv",
    "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-gld.xlsx",
)

#: Symboles utilisés pour les ratios.
SYMBOLE_OR: Final = "GC=F"
SYMBOLE_ARGENT: Final = "SI=F"
SYMBOLE_MINIERES: Final = "GDX"
SYMBOLE_ETF_OR: Final = "GLD"

#: Onces troy dans une tonne métrique, pour convertir un actif net en tonnage.
ONCES_PAR_TONNE: Final[float] = 32150.7465

#: Fenêtre des percentiles de ratio : cinq ans de jours ouvrés.
FENETRE_PERCENTILE: Final[int] = 1260

#: Horizon de comparaison des tendances, en séances.
HORIZON_TENDANCE: Final[int] = 20

_ENTETES: Final[dict[str, str]] = {
    "User-Agent": "Mozilla/5.0 (compatible; veille-marches/1.0)"
}

__all__ = [
    "IndicateurFlux",
    "get_encours_gld",
    "get_ratio_or_argent",
    "get_ratio_gdx_or",
    "get_flux_or",
]


@dataclass(slots=True, frozen=True)
class IndicateurFlux:
    """Un indicateur de flux ou de tension, disponible ou non.

    Attributes:
        nom: identifiant court de l'indicateur.
        valeur: valeur du jour. ``None`` si la source a échoué.
        unite: unité de la valeur, pour l'affichage.
        date_valeur: date de l'observation.
        percentile: rang de la valeur dans son historique, en pourcentage.
        variation_pct: variation sur l'horizon de tendance, en pourcentage.
        tendance: ``en hausse``, ``en baisse`` ou ``stable``.
        source: origine réelle de la donnée.
        est_approximation: ``True`` quand la valeur n'est pas la grandeur
            demandée mais un substitut. Le lecteur doit le savoir.
        limite: description de l'approximation, vide si la valeur est directe.
        disponible: ``False`` si la valeur n'a pas pu être obtenue.
        motif: raison de l'indisponibilité, vide sinon.
        details: informations complémentaires propres à l'indicateur.
    """

    nom: str
    valeur: float | None = None
    unite: str = ""
    date_valeur: pd.Timestamp | None = None
    percentile: float | None = None
    variation_pct: float | None = None
    tendance: str = ""
    source: str = ""
    est_approximation: bool = False
    limite: str = ""
    disponible: bool = False
    motif: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Sérialise l'indicateur pour le rapport JSON."""
        return {
            "nom": self.nom,
            "disponible": self.disponible,
            "motif": self.motif,
            "valeur": self.valeur,
            "unite": self.unite,
            "date_valeur": None if self.date_valeur is None else str(pd.Timestamp(self.date_valeur).date()),
            "percentile": self.percentile,
            "variation_pct": self.variation_pct,
            "tendance": self.tendance,
            "source": self.source,
            "est_approximation": self.est_approximation,
            "limite": self.limite,
            "details": dict(self.details),
        }


def _indisponible(nom: str, motif: str) -> IndicateurFlux:
    """Construit un indicateur marqué indisponible.

    Args:
        nom: identifiant de l'indicateur.
        motif: raison, reprise telle quelle dans la sortie.

    Returns:
        Indicateur neutre.
    """
    _LOG.warning("Indicateur « %s » indisponible : %s", nom, motif)
    return IndicateurFlux(nom=nom, motif=motif)


def _qualifier_tendance(variation_pct: float, seuil: float = 1.0) -> str:
    """Traduit une variation en mot.

    Args:
        variation_pct: variation en pourcentage sur l'horizon.
        seuil: amplitude en deçà de laquelle la variation est jugée nulle.

    Returns:
        ``en hausse``, ``en baisse`` ou ``stable``.
    """
    if variation_pct > seuil:
        return "en hausse"
    if variation_pct < -seuil:
        return "en baisse"
    return "stable"


def _lire_serie_ratio(
    nom: str,
    numerateur: str,
    denominateur: str,
    debut: str,
    fin: str | None,
) -> tuple[pd.Series | None, str]:
    """Charge deux séries de prix et en forme le ratio.

    Args:
        nom: nom de l'indicateur, pour les messages.
        numerateur: symbole du numérateur.
        denominateur: symbole du dénominateur.
        debut: date de début au format ISO.
        fin: date de fin au format ISO.

    Returns:
        Couple ``(ratio, motif)``. Le ratio est ``None`` si l'une des deux
        séries manque, et le motif dit laquelle.
    """
    prix_num = market.get_prices(numerateur, start=debut, end=fin)
    prix_den = market.get_prices(denominateur, start=debut, end=fin)

    absents = [s for s, d in ((numerateur, prix_num), (denominateur, prix_den)) if d.empty]
    if absents:
        return None, f"série de prix indisponible pour {', '.join(absents)}"

    # Jointure interne : seules les séances communes aux deux instruments
    # entrent dans le ratio. Compléter vers l'avant fabriquerait un ratio
    # entre le prix du jour et celui de la veille.
    cadre = pd.DataFrame(
        {"num": prix_num["close"], "den": prix_den["close"]}
    ).dropna()
    cadre = cadre[cadre["den"] > 0.0]
    if cadre.empty:
        return None, f"aucune séance commune entre {numerateur} et {denominateur}"

    ratio = (cadre["num"] / cadre["den"]).sort_index()
    ratio.name = nom
    return ratio, ""


def _mettre_en_forme_ratio(
    nom: str,
    ratio: pd.Series,
    unite: str,
    source: str,
    horizon: int = HORIZON_TENDANCE,
    fenetre_percentile: int = FENETRE_PERCENTILE,
    details: dict[str, Any] | None = None,
) -> IndicateurFlux:
    """Transforme une série de ratio en indicateur lisible.

    Args:
        nom: identifiant de l'indicateur.
        ratio: série du ratio, triée.
        unite: unité affichée.
        source: origine des prix.
        horizon: nombre de séances de la comparaison de tendance.
        fenetre_percentile: profondeur du percentile.
        details: champs supplémentaires à joindre.

    Returns:
        L'indicateur renseigné.
    """
    courant = float(ratio.iloc[-1])
    fenetre = ratio.iloc[-int(fenetre_percentile):]

    percentile: float | None = None
    if len(fenetre) >= 250:
        percentile = float((fenetre.to_numpy() <= courant).mean() * 100.0)
    else:
        _LOG.debug("%s : percentile ignoré, %d séance(s).", nom, len(fenetre))

    variation: float | None = None
    tendance = ""
    if len(ratio) > horizon:
        precedent = float(ratio.iloc[-1 - horizon])
        if precedent != 0.0:
            variation = (courant / precedent - 1.0) * 100.0
            tendance = _qualifier_tendance(variation)

    return IndicateurFlux(
        nom=nom,
        valeur=courant,
        unite=unite,
        date_valeur=pd.Timestamp(ratio.index[-1]),
        percentile=percentile,
        variation_pct=variation,
        tendance=tendance,
        source=source,
        disponible=True,
        details={"n_seances": len(ratio), "horizon_tendance_seances": horizon, **(details or {})},
    )


# ---------------------------------------------------------------------------
# 1. Encours du GLD
# ---------------------------------------------------------------------------
def _tenter_fichier_spdr() -> tuple[None, str]:
    """Tente les fichiers officiels de SPDR et documente leur échec.

    La tentative est réelle : les adresses sont interrogées, et le motif
    renvoyé décrit ce que le serveur a répondu. Le jour où SPDR publiera un
    format exploitable, ce diagnostic le signalera au lieu de le masquer.

    Returns:
        Couple ``(None, motif)``. Le premier élément est toujours ``None`` :
        aucune de ces adresses ne fournit de série de tonnage exploitable.
    """
    constats: list[str] = []
    for url in URLS_SPDR:
        try:
            reponse = requests.get(
                url, timeout=TIMEOUT, headers=_ENTETES, allow_redirects=True, stream=True
            )
            type_contenu = reponse.headers.get("Content-Type", "inconnu").split(";")[0]
            constats.append(f"{url.split('/')[-1]} → HTTP {reponse.status_code}, {type_contenu}")
            reponse.close()
        except requests.RequestException as exc:
            constats.append(f"{url.split('/')[-1]} → injoignable ({type(exc).__name__})")

    return None, (
        "SPDR ne publie pas de série de tonnage exploitable par programme ("
        + " ; ".join(constats)
        + "). L'archive redirige vers un PDF de liste de lingots."
    )


def get_encours_gld(essayer_spdr: bool = True) -> IndicateurFlux:
    """Encours du GLD : tentative officielle, puis repli documenté.

    Args:
        essayer_spdr: mettre à ``False`` pour sauter la tentative officielle,
            par exemple quand on sait déjà qu'elle échoue et qu'on veut
            épargner deux appels réseau.

    Returns:
        L'indicateur. Il est marqué ``est_approximation`` dès que la valeur
        provient du repli, et sa variation reste ``None`` : le repli ne donne
        aucun historique, donc aucun flux.
    """
    nom = "encours_gld"
    motif_spdr = "tentative officielle non effectuée"
    if essayer_spdr:
        _, motif_spdr = _tenter_fichier_spdr()
        _LOG.warning("Encours GLD : source officielle écartée. %s", motif_spdr)

    # --- Repli : actif net du jour, via yfinance --------------------------
    try:
        import yfinance as yf
    except ImportError:
        return _indisponible(nom, f"{motif_spdr} ; et yfinance n'est pas installé pour le repli")

    try:
        informations = yf.Ticker(SYMBOLE_ETF_OR).get_info() or {}
    except Exception as exc:  # noqa: BLE001 - yfinance remonte des erreurs très variées
        return _indisponible(nom, f"{motif_spdr} ; repli yfinance en échec : {type(exc).__name__}")

    actif_net = informations.get("totalAssets") or informations.get("netAssets")
    parts = informations.get("sharesOutstanding")
    if actif_net is None:
        return _indisponible(nom, f"{motif_spdr} ; yfinance n'expose pas l'actif net du GLD")

    try:
        actif_net = float(actif_net)
    except (TypeError, ValueError):
        return _indisponible(nom, f"{motif_spdr} ; actif net yfinance illisible")

    # Tonnage estimé à partir du prix de l'once. C'est une reconstitution, pas
    # une mesure : elle suppose que l'actif net est intégralement de l'or, ce
    # qui néglige la trésorerie du fonds et les frais courus.
    tonnage: float | None = None
    prix_or = market.get_prices(
        SYMBOLE_OR,
        start=str(date.today() - timedelta(days=15)),
        end=str(date.today()),
    )
    if not prix_or.empty:
        once = float(prix_or["close"].iloc[-1])
        if once > 0.0:
            tonnage = actif_net / once / ONCES_PAR_TONNE

    return IndicateurFlux(
        nom=nom,
        valeur=actif_net,
        unite="USD",
        date_valeur=pd.Timestamp(date.today()),
        percentile=None,
        # Le flux est la seule information qui aurait une valeur de signal, et
        # il n'est pas calculable : aucune série d'encours n'est accessible.
        variation_pct=None,
        tendance="",
        source="yfinance (repli)",
        est_approximation=True,
        limite=(
            "Niveau du jour seulement, sans historique : la variation d'encours "
            "— la seule grandeur qui porterait un signal — n'est pas calculable. "
            f"Source officielle écartée : {motif_spdr}"
        ),
        disponible=True,
        details={
            "parts_en_circulation": None if parts is None else float(parts),
            "tonnage_estime": tonnage,
            "tonnage_est_reconstitue": True,
            "alimente_le_biais": False,
        },
    )


# ---------------------------------------------------------------------------
# 2. Ratio or / argent
# ---------------------------------------------------------------------------
def get_ratio_or_argent(debut: str = "2010-01-01", fin: str | None = None) -> IndicateurFlux:
    """Ratio or / argent, calculé depuis les prix à terme.

    Les contrats à terme sont préférés aux ETF : ``GLD`` et ``SLV`` ne
    détiennent pas le même nombre d'onces par part, et leur rapport n'est
    donc pas le ratio or/argent mais un multiple arbitraire de celui-ci. Sur
    contrats, le rapport est directement le nombre d'onces d'argent qu'achète
    une once d'or — le chiffre que les tables historiques publient.

    Args:
        debut: début de l'historique, au format ISO.
        fin: fin de l'historique. Aujourd'hui par défaut.

    Returns:
        L'indicateur, éventuellement marqué indisponible.
    """
    nom = "ratio_or_argent"
    ratio, motif = _lire_serie_ratio(nom, SYMBOLE_OR, SYMBOLE_ARGENT, debut, fin)
    if ratio is None:
        return _indisponible(nom, motif)

    indicateur = _mettre_en_forme_ratio(
        nom,
        ratio,
        unite="onces d'argent par once d'or",
        source=f"{SYMBOLE_OR} / {SYMBOLE_ARGENT} (yfinance)",
        details={"interpretation": "un ratio qui monte traduit une hausse défensive, portée par l'or seul"},
    )
    if indicateur.percentile is not None and indicateur.percentile >= 80.0:
        _LOG.info("Ratio or/argent au %ie percentile : hausse étroite.", round(indicateur.percentile))
    return indicateur


# ---------------------------------------------------------------------------
# 3. Ratio minières / or
# ---------------------------------------------------------------------------
def get_ratio_gdx_or(debut: str = "2010-01-01", fin: str | None = None) -> IndicateurFlux:
    """Ratio GDX / GLD : les minières confirment-elles le métal ?

    Le rapport se calcule entre deux ETF cotés à la même bourse et aux mêmes
    heures, ce qui évite le décalage de séance qu'introduirait un contrat à
    terme. Seule sa *variation* est interprétée : le niveau absolu dépend des
    frais et des divisions de parts de chaque fonds, il n'a pas de sens
    propre.

    Args:
        debut: début de l'historique, au format ISO.
        fin: fin de l'historique. Aujourd'hui par défaut.

    Returns:
        L'indicateur, éventuellement marqué indisponible.
    """
    nom = "ratio_gdx_or"
    ratio, motif = _lire_serie_ratio(nom, SYMBOLE_MINIERES, SYMBOLE_ETF_OR, debut, fin)
    if ratio is None:
        return _indisponible(nom, motif)

    indicateur = _mettre_en_forme_ratio(
        nom,
        ratio,
        unite="sans dimension",
        source=f"{SYMBOLE_MINIERES} / {SYMBOLE_ETF_OR} (yfinance)",
        details={
            "interpretation": (
                "les minières sont un pari à effet de levier sur l'or : un ratio "
                "qui monte confirme la hausse du métal, un ratio qui baisse la dément"
            )
        },
    )
    return indicateur


def get_flux_or(
    debut: str = "2010-01-01",
    fin: str | None = None,
    essayer_spdr: bool = True,
) -> dict[str, IndicateurFlux]:
    """Assemble les trois indicateurs de flux.

    Chaque indicateur est calculé indépendamment : l'échec de l'un n'empêche
    pas les autres.

    Args:
        debut: début de l'historique des ratios.
        fin: fin de l'historique.
        essayer_spdr: transmis à :func:`get_encours_gld`.

    Returns:
        Dictionnaire des trois indicateurs, indexé par leur nom.
    """
    indicateurs = {
        "encours_gld": get_encours_gld(essayer_spdr=essayer_spdr),
        "ratio_or_argent": get_ratio_or_argent(debut=debut, fin=fin),
        "ratio_gdx_or": get_ratio_gdx_or(debut=debut, fin=fin),
    }
    indisponibles = [n for n, i in indicateurs.items() if not i.disponible]
    if indisponibles:
        _LOG.warning("Flux or : %d indicateur(s) indisponible(s) : %s", len(indisponibles), ", ".join(indisponibles))
    return indicateurs
