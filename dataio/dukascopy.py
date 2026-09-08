"""Téléchargement et mise en cache des ticks Dukascopy.

Pourquoi les ticks et pas les bougies
-------------------------------------
Dukascopy expose bien des fichiers de bougies, mais leurs adresses répondent
503 ou expirent — vérifié le 8 septembre 2026 sur plusieurs formes d'URL.
Les **ticks**, eux, répondent. Le module télécharge donc les ticks et
construit lui-même le M1, ce qui vaut mieux que le contraire : toutes les
unités de temps du backtest descendent alors d'une seule et même série, et
aucun décalage entre sources ne peut fabriquer un signal qui n'a pas existé.

Format des fichiers ``.bi5``
----------------------------
Un fichier par heure, compressé en LZMA. Une fois décompressé, des
enregistrements de vingt octets en gros-boutiste :

* ``uint32`` millisecondes depuis le début de l'heure ;
* ``uint32`` prix demandé, en points entiers ;
* ``uint32`` prix offert, en points entiers ;
* ``float32`` volume demandé, ``float32`` volume offert.

Deux pièges dans les adresses, l'un et l'autre silencieux :

* **le mois est indexé à partir de zéro** : ``2025/05`` désigne juin 2025.
  Se tromper d'un mois ne lève aucune erreur, cela télécharge simplement les
  mauvaises données ;
* un fichier **vide avec un code 200** est la réponse normale pour une heure
  de marché fermé — week-end ou jour férié. Ce n'est pas un échec.

Le facteur de conversion des points en prix dépend du nombre de décimales de
l'instrument, et Dukascopy ne le publie pas dans le fichier. Il est déclaré
par instrument et **vérifié à l'exécution** contre une fourchette de prix
plausible : mieux vaut refuser une série que produire un or à 33 925 $.

Le serveur est capricieux — 503 et expirations de connexion alternent avec
des réponses correctes. D'où les réessais avec attente et, surtout, le cache
sur disque : une heure téléchargée ne l'est jamais deux fois.
"""

from __future__ import annotations

import concurrent.futures
import logging
import lzma
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd
import requests

_LOG: Final = logging.getLogger(__name__)

#: Racine du dépôt, déduite de l'emplacement de ce fichier.
RACINE: Final = Path(__file__).resolve().parents[1]

#: Cache local des données. Ignoré par git : volumineux et reconstructible.
DOSSIER_DONNEES: Final = RACINE / "data"

#: Modèle d'adresse d'un fichier horaire de ticks.
URL_TICKS: Final = (
    "https://datafeed.dukascopy.com/datafeed/{instrument}/"
    "{annee:04d}/{mois:02d}/{jour:02d}/{heure:02d}h_ticks.bi5"
)

#: Taille d'un enregistrement de tick, en octets.
TAILLE_TICK: Final[int] = 20

#: Délai maximal, en secondes, accordé à un appel réseau.
TIMEOUT: Final[float] = float(os.environ.get("DUKASCOPY_TIMEOUT", "30"))

#: Nombre de tentatives par fichier. Volontairement bas : Dukascopy répond
#: 503 pour les heures de marché fermé autant que pour une vraie panne, et
#: insister sur une heure creuse coûte des secondes pour rien. Sur 3 768
#: heures, une attente exponentielle à quatre tentatives ajoutait des dizaines
#: de minutes sans récupérer une seule bougie de plus.
MAX_TENTATIVES: Final[int] = 3

#: Attente initiale entre deux tentatives, en secondes.
ATTENTE_INITIALE: Final[float] = 1.0

#: Téléchargements simultanés. Dukascopy se dégrade nettement quand on le
#: presse : à douze fils, il répond 503 jusque sur des heures de pleine
#: séance, et le taux d'échec grimpe avec la durée de la session. Cinq est
#: le compromis retenu ; descendre à trois améliore encore la fiabilité au
#: prix du débit. Le cache rend ce coût ponctuel.
CONCURRENCE: Final[int] = 5


@dataclass(slots=True, frozen=True)
class Instrument:
    """Description d'un instrument Dukascopy.

    Attributes:
        nom: symbole tel que l'attend l'adresse, par exemple ``XAUUSD``.
        diviseur: facteur de conversion des points entiers en prix.
        prix_min: borne basse de plausibilité, pour le contrôle de cohérence.
        prix_max: borne haute de plausibilité.
    """

    nom: str
    diviseur: float
    prix_min: float
    prix_max: float


#: Instruments connus. Les bornes de plausibilité ne servent qu'à détecter
#: une erreur de diviseur : elles sont larges à dessein, il ne s'agit pas de
#: filtrer des prix mais d'attraper un facteur mille.
INSTRUMENTS: Final[dict[str, Instrument]] = {
    "XAUUSD": Instrument("XAUUSD", 1000.0, 200.0, 20_000.0),
    "EURUSD": Instrument("EURUSD", 100_000.0, 0.5, 2.0),
}

_ENTETES: Final[dict[str, str]] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

__all__ = [
    "Instrument",
    "INSTRUMENTS",
    "decoder_bi5",
    "telecharger_heure",
    "charger_m1",
]


# ---------------------------------------------------------------------------
# Décodage
# ---------------------------------------------------------------------------
def decoder_bi5(
    contenu: bytes, instrument: Instrument, debut_heure: datetime
) -> pd.DataFrame:
    """Décode un fichier horaire de ticks.

    Args:
        contenu: octets bruts du fichier ``.bi5``.
        instrument: instrument concerné, pour le diviseur et les bornes.
        debut_heure: instant de début de l'heure, en UTC.

    Returns:
        DataFrame indexé par horodatage, colonnes ``ask`` et ``bid``. Vide
        pour un fichier vide, ce qui est le cas normal hors séance.

    Raises:
        ValueError: si les prix décodés sortent des bornes de plausibilité,
            ce qui trahit un diviseur erroné plutôt qu'un prix aberrant.
    """
    if not contenu:
        return pd.DataFrame(columns=["ask", "bid"])

    try:
        brut = lzma.LZMADecompressor().decompress(contenu)
    except lzma.LZMAError as exc:
        _LOG.warning("Fichier de ticks illisible (%s) : %s", debut_heure, exc)
        return pd.DataFrame(columns=["ask", "bid"])

    n = len(brut) // TAILLE_TICK
    if n == 0:
        return pd.DataFrame(columns=["ask", "bid"])

    # Lecture vectorisée : un tableau structuré gros-boutiste vaut mieux
    # qu'une boucle Python sur des dizaines de milliers d'enregistrements.
    type_tick = np.dtype(
        [(">ms", ">u4"), (">ask", ">u4"), (">bid", ">u4"), (">va", ">f4"), (">vb", ">f4")]
    )
    tableau = np.frombuffer(brut[: n * TAILLE_TICK], dtype=type_tick)

    ask = tableau[">ask"].astype("float64") / instrument.diviseur
    bid = tableau[">bid"].astype("float64") / instrument.diviseur

    # Contrôle de cohérence : un diviseur faux ne lève aucune erreur, il
    # produit simplement un instrument méconnaissable. On refuse plutôt que
    # de laisser un or à 33 925 $ entrer dans un backtest.
    mediane = float(np.median(bid[bid > 0])) if (bid > 0).any() else 0.0
    if mediane and not (instrument.prix_min <= mediane <= instrument.prix_max):
        raise ValueError(
            f"{instrument.nom} : prix médian décodé de {mediane:.4f}, hors de la "
            f"fourchette plausible [{instrument.prix_min}, {instrument.prix_max}]. "
            f"Le diviseur {instrument.diviseur:g} est probablement erroné."
        )

    horodatages = pd.to_datetime(
        debut_heure, utc=True
    ) + pd.to_timedelta(tableau[">ms"].astype("int64"), unit="ms")

    return pd.DataFrame(
        {"ask": ask, "bid": bid}, index=pd.DatetimeIndex(horodatages, name="horodatage")
    )


# ---------------------------------------------------------------------------
# Téléchargement
# ---------------------------------------------------------------------------
def _chemin_cache(instrument: str, moment: datetime) -> Path:
    """Emplacement du fichier brut mis en cache.

    Args:
        instrument: symbole.
        moment: heure concernée, en UTC.

    Returns:
        Chemin du fichier, dossiers non créés.
    """
    return (
        DOSSIER_DONNEES
        / "dukascopy"
        / instrument
        / f"{moment:%Y}"
        / f"{moment:%m}"
        / f"{moment:%d}"
        / f"{moment:%H}h.bi5"
    )


def telecharger_heure(
    instrument: str, moment: datetime, dossier_cache: Path | None = None
) -> bytes | None:
    """Récupère un fichier horaire, depuis le cache ou depuis Dukascopy.

    Args:
        instrument: symbole, par exemple ``XAUUSD``.
        moment: heure concernée, en UTC.
        dossier_cache: racine du cache. :data:`DOSSIER_DONNEES` par défaut.

    Returns:
        Octets bruts, éventuellement vides pour une heure hors séance.
        ``None`` si le téléchargement a échoué après tous les réessais.
    """
    racine = dossier_cache or DOSSIER_DONNEES
    chemin = (
        racine
        / "dukascopy"
        / instrument
        / f"{moment:%Y}"
        / f"{moment:%m}"
        / f"{moment:%d}"
        / f"{moment:%H}h.bi5"
    )
    if chemin.exists():
        try:
            return chemin.read_bytes()
        except OSError as exc:
            _LOG.warning("Cache illisible (%s) : %s", chemin, exc)

    # Le mois est indexé à partir de zéro dans les adresses Dukascopy.
    url = URL_TICKS.format(
        instrument=instrument,
        annee=moment.year,
        mois=moment.month - 1,
        jour=moment.day,
        heure=moment.hour,
    )

    attente = ATTENTE_INITIALE
    for tentative in range(1, MAX_TENTATIVES + 1):
        try:
            reponse = requests.get(url, timeout=TIMEOUT, headers=_ENTETES)
        except requests.RequestException as exc:
            if tentative == MAX_TENTATIVES:
                _LOG.warning("Dukascopy injoignable (%s) : %s", moment, type(exc).__name__)
                return None
            time.sleep(attente)
            attente *= 2.0
            continue

        if reponse.status_code == 404:
            # Heure absente de l'historique : légitime, on la traite comme vide.
            contenu = b""
        elif reponse.status_code >= 500 or reponse.status_code == 429:
            if tentative == MAX_TENTATIVES:
                _LOG.warning(
                    "Dukascopy en erreur %d sur %s après %d tentatives.",
                    reponse.status_code, moment, MAX_TENTATIVES,
                )
                return None
            time.sleep(attente)
            attente *= 2.0
            continue
        elif reponse.status_code != 200:
            _LOG.warning("Dukascopy a répondu %d sur %s.", reponse.status_code, moment)
            return None
        else:
            contenu = reponse.content

        try:
            chemin.parent.mkdir(parents=True, exist_ok=True)
            chemin.write_bytes(contenu)
        except OSError as exc:
            _LOG.warning("Cache non écrit (%s) : %s", chemin, exc)
        return contenu

    return None


def _heures_a_couvrir(debut: date, fin: date) -> list[datetime]:
    """Énumère les heures UTC d'une période.

    Les week-ends sont écartés d'emblée : le marché de l'or est fermé du
    vendredi soir au dimanche soir, et demander ces fichiers ne rapporterait
    que des réponses vides au prix d'autant de requêtes.

    Args:
        debut: premier jour inclus.
        fin: dernier jour inclus.

    Returns:
        Heures à télécharger, dans l'ordre chronologique.
    """
    heures: list[datetime] = []
    jour = debut
    while jour <= fin:
        jourdesemaine = jour.weekday()
        if jourdesemaine == 5:
            # Samedi : marché fermé toute la journée.
            jour += timedelta(days=1)
            continue
        for heure in range(24):
            # Le marché rouvre le dimanche en soirée, vers 21:00 UTC, et
            # ferme le vendredi vers 21:00 UTC. Demander les heures creuses
            # ne rapporte que des 503, au prix d'autant de requêtes.
            if jourdesemaine == 6 and heure < 21:
                continue
            if jourdesemaine == 4 and heure >= 21:
                continue
            heures.append(datetime(jour.year, jour.month, jour.day, heure, tzinfo=timezone.utc))
        jour += timedelta(days=1)
    return heures


def charger_m1(
    instrument: str,
    debut: date,
    fin: date,
    dossier_cache: Path | None = None,
    concurrence: int = CONCURRENCE,
) -> pd.DataFrame:
    """Télécharge une période et en construit les bougies d'une minute.

    Les bougies sont bâties sur le **prix offert**, convention usuelle pour
    un historique de référence. Le coût d'exécution — écart et glissement —
    est appliqué séparément par le moteur de backtest, ce qui permet de le
    faire varier sans retélécharger quoi que ce soit.

    Args:
        instrument: symbole, présent dans :data:`INSTRUMENTS`.
        debut: premier jour inclus.
        fin: dernier jour inclus.
        dossier_cache: racine du cache.
        concurrence: nombre de téléchargements simultanés.

    Returns:
        DataFrame indexé par minute UTC, colonnes ``open``, ``high``,
        ``low``, ``close``, ``volume`` et ``spread_moyen``. Vide si rien
        n'a pu être récupéré.
    """
    description = INSTRUMENTS.get(instrument.upper())
    if description is None:
        _LOG.error("Instrument %s inconnu : diviseur non déclaré.", instrument)
        return pd.DataFrame()

    heures = _heures_a_couvrir(debut, fin)
    _LOG.info(
        "%s : %d heure(s) à couvrir du %s au %s.", instrument, len(heures), debut, fin
    )

    morceaux: list[pd.DataFrame] = []
    echecs = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(int(concurrence), 1)) as executeur:
        futurs = {
            executeur.submit(telecharger_heure, description.nom, moment, dossier_cache): moment
            for moment in heures
        }
        for futur in concurrent.futures.as_completed(futurs):
            moment = futurs[futur]
            try:
                contenu = futur.result()
            except Exception as exc:  # noqa: BLE001 - un fichier ne doit rien casser
                _LOG.warning("Heure %s en échec : %s", moment, exc)
                echecs += 1
                continue
            if contenu is None:
                echecs += 1
                continue
            try:
                ticks = decoder_bi5(contenu, description, moment)
            except ValueError as exc:
                # Un diviseur erroné invalide toute la série : on s'arrête.
                _LOG.error("Décodage incohérent : %s", exc)
                raise
            if not ticks.empty:
                morceaux.append(ticks)

    if echecs:
        _LOG.warning(
            "%s : %d heure(s) non récupérée(s) sur %d. La série comporte des trous.",
            instrument, echecs, len(heures),
        )
    if not morceaux:
        _LOG.error("%s : aucune donnée récupérée.", instrument)
        return pd.DataFrame()

    ticks = pd.concat(morceaux).sort_index()
    ticks = ticks[~ticks.index.duplicated(keep="first")]

    # Agrégation en bougies d'une minute. Le prix offert fait la bougie,
    # l'écart moyen est conservé à titre documentaire.
    groupes = ticks.resample("1min")
    m1 = pd.DataFrame(
        {
            "open": groupes["bid"].first(),
            "high": groupes["bid"].max(),
            "low": groupes["bid"].min(),
            "close": groupes["bid"].last(),
            "volume": groupes["bid"].count(),
            "spread_moyen": (ticks["ask"] - ticks["bid"]).resample("1min").mean(),
        }
    ).dropna(subset=["open", "high", "low", "close"])

    _LOG.info(
        "%s : %d bougie(s) M1, du %s au %s.",
        instrument, len(m1),
        m1.index.min() if len(m1) else "—",
        m1.index.max() if len(m1) else "—",
    )
    return m1
