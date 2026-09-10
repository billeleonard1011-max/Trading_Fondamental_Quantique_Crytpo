"""Point d'entrée de la veille sur les valeurs quantiques.

Exécution :
    python -m modules.quantum.run
    python -m modules.quantum.run --date 2026-09-04
    python -m modules.quantum.run --sans-sec      (aucun appel EDGAR)

Écrit ``reports/quantum/AAAA-MM-JJ.json`` et met à jour
``reports/quantum/latest.json``. Même discipline que le moteur or : chaque
bloc porte son ``_meta``, une source muette n'interrompt rien, et le motif
de son indisponibilité est publié.

Garde-fou avant publication
---------------------------
Le rapport est passé au crible de
:func:`modules.quantum.moves.verifier_absence_recommandation` **avant d'être
écrit**. Si une formulation de recommandation s'est glissée dans un texte, le
rapport n'est pas publié et la commande sort en erreur. Un contrôle qui ne
tourne qu'en test finit par ne plus rien garantir en production.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

import pandas as pd
import yaml

from dataio import macro, market, news
from dataio import sec_filings as sec
from modules import synthese
from modules.quantum import industry, moves

_LOG: Final = logging.getLogger("modules.quantum.run")

#: Racine du dépôt, déduite de l'emplacement de ce fichier.
RACINE: Final = Path(__file__).resolve().parents[2]

#: Configuration de l'univers suivi.
CHEMIN_CONFIG: Final = RACINE / "config" / "universe.yaml"

#: Dossier de publication.
DOSSIER_RAPPORTS: Final = RACINE / "reports" / "quantum"

#: Profondeur d'historique de prix chargée, en jours calendaires.
JOURS_HISTORIQUE: Final[int] = 200

#: Types de dépôts SEC qui comptent pour ces sociétés : levées de fonds,
#: événements marquants, transactions d'initiés et prises de participation.
TYPES_DEPOTS: Final[list[str]] = ["S-3", "424B5", "8-K", "4", "SCHEDULE 13D", "SCHEDULE 13G"]

__all__ = ["construire_rapport", "main"]


def _maintenant() -> datetime:
    """Instant courant en UTC, avec fuseau explicite."""
    return datetime.now(timezone.utc)


def _meta(
    source: str,
    date_donnee: pd.Timestamp | date | str | None = None,
    reference: date | None = None,
) -> dict[str, Any]:
    """Construit le bloc de traçabilité joint à chaque section.

    Args:
        source: origine réelle de la donnée.
        date_donnee: date de l'observation la plus récente utilisée.
        reference: date par rapport à laquelle l'âge est calculé.

    Returns:
        Dictionnaire ``source``, ``horodatage_collecte_utc``, ``date_donnee``
        et ``age_jours``.
    """
    horodatage = _maintenant()
    date_texte: str | None = None
    age: int | None = None
    if date_donnee is not None:
        try:
            jour = pd.Timestamp(date_donnee).normalize()
            date_texte = str(jour.date())
            base = pd.Timestamp(reference or horodatage.date()).normalize()
            age = int((base - jour).days)
        except (ValueError, TypeError):
            _LOG.warning("Date de donnée illisible pour %s : %r", source, date_donnee)
    return {
        "source": source,
        "horodatage_collecte_utc": horodatage.isoformat(),
        "date_donnee": date_texte,
        "age_jours": age,
    }


def charger_configuration(chemin: Path = CHEMIN_CONFIG) -> dict[str, Any]:
    """Lit ``config/universe.yaml``.

    Args:
        chemin: emplacement du fichier.

    Returns:
        La configuration, ou un dictionnaire vide si le fichier est illisible.
    """
    try:
        with chemin.open("r", encoding="utf-8") as fichier:
            return yaml.safe_load(fichier) or {}
    except (OSError, yaml.YAMLError) as exc:
        _LOG.error("Configuration illisible (%s) : %s", chemin, exc)
        return {}


def _charger_prix(tickers: list[str], jour: date) -> dict[str, pd.DataFrame]:
    """Charge l'historique de prix de chaque valeur suivie.

    Args:
        tickers: symboles à charger.
        jour: date de fin de l'historique.

    Returns:
        Historiques par ticker. Les valeurs sans données sont absentes.
    """
    debut = str(jour - timedelta(days=JOURS_HISTORIQUE))
    fin = str(jour)
    prix: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        cadre = market.get_prices(ticker, start=debut, end=fin)
        if cadre.empty:
            _LOG.warning("Aucun prix pour %s.", ticker)
            continue
        prix[ticker] = cadre[cadre.index <= pd.Timestamp(jour)]
    return prix


def construire_rapport(
    configuration: dict[str, Any],
    date_rapport: date | None = None,
    avec_sec: bool = True,
) -> dict[str, Any]:
    """Collecte, analyse et assemble le rapport quantique.

    Args:
        configuration: contenu de ``config/universe.yaml``.
        date_rapport: date visée. Aujourd'hui par défaut.
        avec_sec: ``False`` pour n'effectuer aucun appel EDGAR.

    Returns:
        Le rapport, prêt à être sérialisé.
    """
    jour = date_rapport or _maintenant().date()
    echecs: list[str] = []

    watchlist = list(configuration.get("quantum_watchlist") or [])
    reglages = dict(configuration.get("quantum") or {})
    theme = dict(configuration.get("quantum_industry_watch") or {})

    if not watchlist:
        _LOG.error("quantum_watchlist vide : rien à analyser.")
        echecs.append("watchlist quantique")

    tickers = [str(e.get("ticker", "")).upper() for e in watchlist if e.get("ticker")]

    # --- Prix ---------------------------------------------------------------
    _LOG.info("Chargement des prix des valeurs quantiques...")
    prix = _charger_prix(tickers, jour)
    if not prix:
        echecs.append("prix des valeurs quantiques")

    variations = moves.variations_du_jour(prix)
    date_prix = max(
        (pd.Timestamp(c.index[-1]) for c in prix.values() if not c.empty), default=None
    )
    bloc_prix = {
        "disponible": bool(variations),
        "motif": "" if variations else "aucun historique de prix exploitable",
        "variations_du_jour": {t: round(float(v["variation_pct"]), 3) for t, v in variations.items()},
        "clotures": {t: round(float(v["cloture"]), 4) for t, v in variations.items()},
        "_meta": _meta("yfinance (secours Stooq)", date_prix, jour),
    }

    # --- Contexte macro -----------------------------------------------------
    _LOG.info("Chargement du contexte macro...")
    serie_taux = str(reglages.get("serie_taux_reels", "DFII10"))
    reference_marche = str(reglages.get("reference_marche", "QQQ"))

    series_fred = macro.get_many([serie_taux], start=str(jour - timedelta(days=90)), end=str(jour))
    variation_taux: float | None = None
    if not series_fred.empty and serie_taux in series_fred.columns:
        variation_taux = moves.variation_derniere_seance(series_fred[serie_taux], en_points=True)
    else:
        echecs.append(f"série {serie_taux}")

    prix_marche = market.get_prices(
        reference_marche, start=str(jour - timedelta(days=30)), end=str(jour)
    )
    variation_marche: float | None = None
    if not prix_marche.empty:
        variation_marche = moves.variation_derniere_seance(prix_marche["close"])
    else:
        echecs.append(f"indice {reference_marche}")

    bloc_macro = {
        "disponible": variation_taux is not None or variation_marche is not None,
        "motif": "" if (variation_taux is not None or variation_marche is not None)
        else "ni les taux réels ni l'indice de référence n'ont pu être chargés",
        "serie_taux_reels": serie_taux,
        "variation_taux_reels_points": variation_taux,
        "reference_marche": reference_marche,
        "variation_marche_pct": variation_marche,
        "_meta": _meta(f"FRED {serie_taux} + {reference_marche} (yfinance)", jour, jour),
    }

    # --- Dépôts SEC et trésorerie ------------------------------------------
    runways: dict[str, dict[str, Any]] = {}
    depots_par_ticker: dict[str, list[dict[str, Any]]] = {}
    inities_par_ticker: dict[str, dict[str, Any]] = {}

    if avec_sec:
        _LOG.info("Interrogation d'EDGAR...")
        for entree in watchlist:
            ticker = str(entree.get("ticker", "")).upper()
            cik = str(entree.get("cik", "")).strip()
            if not cik:
                _LOG.warning("Pas de CIK configuré pour %s : EDGAR ignoré.", ticker)
                continue
            try:
                depots = sec.get_recent_filings(cik, types=TYPES_DEPOTS, days=30, aujourd_hui=jour)
                depots_par_ticker[ticker] = [d.to_dict() for d in depots]
                inities_par_ticker[ticker] = sec.detect_insider_activity(
                    cik, days=14, aujourd_hui=jour
                )
                runways[ticker] = sec.estimate_cash_runway(cik)
            except Exception as exc:  # noqa: BLE001 - EDGAR ne doit rien interrompre
                _LOG.warning("EDGAR en échec pour %s : %s", ticker, exc)
                echecs.append(f"EDGAR/{ticker}")
    else:
        _LOG.info("Appels EDGAR désactivés par l'appelant.")

    if avec_sec and not any(r.get("disponible") for r in runways.values()):
        echecs.append("trésorerie (XBRL)")

    # --- Actualité par valeur ----------------------------------------------
    _LOG.info("Collecte de l'actualité par valeur...")
    actualites_par_ticker: dict[str, list[dict[str, Any]]] = {}
    for entree in watchlist:
        ticker = str(entree.get("ticker", "")).upper()
        mots = list(entree.get("keywords") or [])
        if not mots:
            continue
        requete = news.construire_requete_gdelt(mots)
        if not requete:
            continue
        articles = news.fetch_gdelt(requete, timespan="24h", max_records=20, tags=["quantique"])
        actualites_par_ticker[ticker] = [a.to_dict() for a in articles]

    # --- Mouvements ---------------------------------------------------------
    _LOG.info("Détection des mouvements...")
    mouvements = moves.detecter_mouvements(
        prix,
        watchlist,
        reglages,
        variation_taux_reels=variation_taux,
        variation_marche_pct=variation_marche,
        actualites_par_ticker=actualites_par_ticker,
        depots_par_ticker=depots_par_ticker,
        inities_par_ticker=inities_par_ticker,
    )
    bloc_mouvements = {
        "disponible": bool(variations),
        "motif": "" if variations else "aucune variation calculable",
        "n_mouvements": len(mouvements),
        "seuils_appliques": {
            str(e.get("ticker")).upper(): float(e.get("seuil_mouvement_pct", 8.0))
            for e in watchlist
        },
        "mouvements": [m.to_dict() for m in mouvements],
        "_meta": _meta("analyse interne (prix + FRED + EDGAR + GDELT)", date_prix, jour),
    }

    # --- Secteur ------------------------------------------------------------
    _LOG.info("Analyse du secteur...")
    intensite = None
    if theme.get("query"):
        intensite = news.gdelt_intensity(str(theme["query"]))
        if not intensite.get("disponible"):
            echecs.append("intensité GDELT du secteur")

    articles_secteur: list[dict[str, Any]] = []
    if theme.get("query"):
        articles_secteur = [
            a.to_dict()
            for a in news.fetch_gdelt(
                str(theme["query"]), timespan="30d", max_records=250, tags=["quantique"]
            )
        ]

    bloc_secteur = industry.analyser_secteur(
        watchlist, reglages, prix, runways,
        intensite_financements=intensite,
        articles_secteur=articles_secteur,
    )
    bloc_secteur["_meta"] = _meta("EDGAR XBRL + GDELT + prix", jour, jour)

    # --- Assemblage ---------------------------------------------------------
    rapport: dict[str, Any] = {
        "meta": {
            "date": str(jour),
            "horodatage_utc": _maintenant().isoformat(),
            "domaine": "valeurs quantiques cotées",
            "version_moteur": "1.0",
            "valeurs_suivies": tickers,
            "sources_en_echec": echecs,
            "donnees_partielles": bool(echecs),
            "avertissement": (
                f"{len(echecs)} source(s) indisponible(s) : {', '.join(echecs)}. "
                "Chaque bloc concerné porte son motif."
                if echecs
                else ""
            ),
            "nature_du_rapport": (
                "Ce rapport décrit des faits de marché et leur contexte. Il ne "
                "contient aucune recommandation d'achat, de vente ou de "
                "positionnement, et n'a pas vocation à en contenir."
            ),
        },
        "prix": bloc_prix,
        "contexte_macro": bloc_macro,
        "mouvements": bloc_mouvements,
        "secteur": bloc_secteur,
    }
    # Synthèse composée en dernier, à partir des blocs déjà calculés.
    rapport["synthese"] = synthese.synthetiser_quantique(rapport)

    return rapport


def publier(rapport: dict[str, Any], dossier: Path = DOSSIER_RAPPORTS) -> tuple[Path, Path] | None:
    """Écrit le rapport du jour et met à jour ``latest.json``.

    Args:
        rapport: rapport complet.
        dossier: dossier de publication.

    Returns:
        Couple des chemins écrits, ou ``None`` en cas d'échec.
    """
    jour = rapport.get("meta", {}).get("date", str(date.today()))
    try:
        dossier.mkdir(parents=True, exist_ok=True)
        chemin_jour = dossier / f"{jour}.json"
        chemin_latest = dossier / "latest.json"
        contenu = json.dumps(rapport, ensure_ascii=False, indent=2, default=str)
        chemin_jour.write_text(contenu, encoding="utf-8")
        chemin_latest.write_text(contenu, encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        _LOG.error("Publication impossible : %s", exc)
        return None
    _LOG.info("Rapport publié : %s et %s", chemin_jour, chemin_latest)
    return chemin_jour, chemin_latest


def main(argv: list[str] | None = None) -> int:
    """Exécute la veille quantique et publie le rapport.

    Returns:
        0 si le rapport est publié, 1 en cas d'échec d'écriture ou si le
        contrôle anti-recommandation a détecté une infraction.
    """
    analyseur = argparse.ArgumentParser(description="Veille sur les valeurs quantiques cotées.")
    analyseur.add_argument("--date", default=None, help="date du rapport, AAAA-MM-JJ.")
    analyseur.add_argument("--sans-sec", action="store_true", help="n'interroge pas EDGAR.")
    analyseur.add_argument("--verbeux", action="store_true", help="journalisation détaillée.")
    arguments = analyseur.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if arguments.verbeux else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s : %(message)s",
        datefmt="%H:%M:%S",
    )

    jour: date | None = None
    if arguments.date:
        try:
            jour = datetime.strptime(arguments.date, "%Y-%m-%d").date()
        except ValueError:
            _LOG.error("Date « %s » invalide : format attendu AAAA-MM-JJ.", arguments.date)
            return 1

    configuration = charger_configuration()
    rapport = construire_rapport(configuration, date_rapport=jour, avec_sec=not arguments.sans_sec)

    # --- Garde-fou : aucune recommandation ne doit sortir d'ici ------------
    infractions = moves.verifier_absence_recommandation(rapport)
    rapport["meta"]["controle_anti_recommandation"] = {
        "effectue": True,
        "n_infractions": len(infractions),
        "infractions": infractions,
        "n_motifs_verifies": len(moves.MOTIFS_RECOMMANDATION),
    }
    if infractions:
        _LOG.error(
            "Publication refusée : %d formulation(s) de recommandation détectée(s).",
            len(infractions),
        )
        for infraction in infractions[:5]:
            _LOG.error("  %s → %s", infraction["chemin"], infraction["extrait"][:80])
        return 1

    chemins = publier(rapport)
    if chemins is None:
        return 1

    meta = rapport["meta"]
    n_mouvements = rapport["mouvements"]["n_mouvements"]
    print(f"\nVeille quantique du {meta['date']} : {n_mouvements} mouvement(s) au-delà du seuil")
    for mouvement in rapport["mouvements"]["mouvements"]:
        print(
            f"  {mouvement['ticker']:6s} {mouvement['variation_pct']:+7.2f} %  "
            f"{mouvement['classification']}"
        )
    correlation = rapport["secteur"]["correlation_positions"]
    if correlation.get("correlation_elevee"):
        print(f"  {correlation['avertissement']}")
    if meta["sources_en_echec"]:
        print(f"Sources indisponibles : {', '.join(meta['sources_en_echec'])}")
    print(f"Écrit dans {chemins[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
