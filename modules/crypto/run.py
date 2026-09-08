"""Point d'entrée de la veille crypto.

Exécution :
    python -m modules.crypto.run
    python -m modules.crypto.run --date 2026-09-07

Écrit ``reports/crypto/AAAA-MM-JJ.json`` et met à jour
``reports/crypto/latest.json``. Même discipline que les autres moteurs :
chaque bloc porte son ``_meta``, une source muette n'interrompt rien, et le
motif de son indisponibilité est publié.

Deux indicateurs sont structurellement absents et le rapport le dit à chaque
exécution plutôt que de le taire : les **flux des ETF spot** et les
**calendriers de déblocage de jetons**, faute de source gratuite et fiable.
Ils remontent dans ``meta.indicateurs_non_alimentes``, à la racine du
rapport, pour qu'on n'ait pas à fouiller les blocs pour s'en apercevoir.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Final

import pandas as pd
import yaml

from dataio import crypto as crypto_io
from modules.crypto import positioning, regime, rotation

_LOG: Final = logging.getLogger("modules.crypto.run")

#: Racine du dépôt, déduite de l'emplacement de ce fichier.
RACINE: Final = Path(__file__).resolve().parents[2]

#: Configuration de l'univers suivi.
CHEMIN_CONFIG: Final = RACINE / "config" / "universe.yaml"

#: Dossier de publication.
DOSSIER_RAPPORTS: Final = RACINE / "reports" / "crypto"

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
        Dictionnaire de traçabilité.
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


def construire_rapport(
    configuration: dict[str, Any],
    date_rapport: date | None = None,
) -> dict[str, Any]:
    """Collecte, analyse et assemble le rapport crypto.

    Args:
        configuration: contenu de ``config/universe.yaml``.
        date_rapport: date visée. Aujourd'hui par défaut.

    Returns:
        Le rapport, prêt à être sérialisé.
    """
    jour = date_rapport or _maintenant().date()
    echecs: list[str] = []

    watchlist = list(configuration.get("crypto_watchlist") or [])
    reglages_regime = dict(configuration.get("crypto_regime") or {})
    reglages_positionnement = dict(configuration.get("crypto_positioning") or {})

    if not watchlist:
        _LOG.error("crypto_watchlist vide : rien à suivre.")
        echecs.append("watchlist crypto")

    # --- Régime -------------------------------------------------------------
    _LOG.info("Classification du régime de marché...")
    bloc_regime = regime.analyser_regime(reglages_regime)
    bloc_regime["_meta"] = _meta(
        "Coin Metrics (CapMVRVCur) + DefiLlama (offre de stablecoins)", jour, jour
    )
    if not bloc_regime.get("disponible"):
        echecs.append("régime (MVRV)")
    if not (bloc_regime.get("offre_stablecoins") or {}).get("disponible"):
        echecs.append("offre de stablecoins")

    # --- Positionnement -----------------------------------------------------
    _LOG.info("Collecte du positionnement dérivés et des positions...")
    identifiants = [
        str(e.get("coingecko_id", "")) for e in watchlist if e.get("coingecko_id")
    ]
    instantane = crypto_io.get_snapshot(identifiants) if identifiants else pd.DataFrame()
    if instantane is None or instantane.empty:
        echecs.append("instantané CoinGecko")

    bloc_positionnement = positioning.analyser_positionnement(
        reglages_positionnement,
        watchlist,
        instantane=instantane,
        deblocages=dict(configuration.get("deblocages_tokens") or {}),
        aujourd_hui=jour,
    )
    bloc_positionnement["_meta"] = _meta(
        "Binance / Bybit (dérivés) + CoinGecko (positions)", jour, jour
    )
    if not bloc_positionnement["funding"].get("disponible"):
        echecs.append("funding des perpétuels")
    if not bloc_positionnement["open_interest"].get("disponible"):
        echecs.append("open interest")

    # --- Rotation BTC / alts ------------------------------------------------
    _LOG.info("Analyse de la rotation BTC / alts...")
    bloc_rotation = rotation.analyser_rotation(aujourd_hui=jour)
    bloc_rotation["_meta"] = _meta(
        "CoinGecko (dominance, prix, capitalisations) + blockchaincenter", jour, jour
    )
    if not bloc_rotation.get("disponible"):
        echecs.append("rotation BTC/alts")

    # --- Indicateurs structurellement absents -------------------------------
    # Remontés à la racine : enterrés dans leur bloc, ils passeraient pour un
    # oubli plutôt que pour un manque connu et documenté.
    non_alimentes: list[dict[str, Any]] = []
    for actif in ("btc", "eth"):
        flux = (bloc_regime.get(f"regime_{actif}", {}).get("metriques") or {}).get(
            "flux_etf_spot", {}
        )
        if not flux.get("disponible"):
            non_alimentes.append(
                {
                    "indicateur": f"flux nets des ETF spot {actif.upper()}",
                    "bloc": f"regime.regime_{actif}.metriques.flux_etf_spot",
                    "motif": flux.get("motif", "indisponible"),
                }
            )
        detenteurs = (bloc_regime.get(f"regime_{actif}", {}).get("metriques") or {}).get(
            "detenteurs_long_terme", {}
        )
        if not detenteurs.get("disponible"):
            non_alimentes.append(
                {
                    "indicateur": f"comportement des détenteurs de long terme {actif.upper()}",
                    "bloc": f"regime.regime_{actif}.metriques.detenteurs_long_terme",
                    "motif": detenteurs.get("motif", "indisponible"),
                }
            )
    inconnus = [
        j["symbole"]
        for j in bloc_positionnement["deblocages_tokens"].get("jetons", [])
        if j["statut"] in {"inconnu", "absent"}
    ]
    if inconnus:
        non_alimentes.append(
            {
                "indicateur": "calendrier de déblocage de certains jetons",
                "bloc": "positionnement.deblocages_tokens",
                "motif": (
                    f"statut inconnu ou non renseigné pour : {', '.join(inconnus)}. "
                    "Aucune source gratuite ne publie ces calendriers."
                ),
            }
        )

    rapport: dict[str, Any] = {
        "meta": {
            "date": str(jour),
            "horodatage_utc": _maintenant().isoformat(),
            "domaine": "positions crypto",
            "version_moteur": "1.0",
            "jetons_suivis": [str(e.get("symbol", "")) for e in watchlist],
            "sources_en_echec": echecs,
            "donnees_partielles": bool(echecs),
            "indicateurs_non_alimentes": non_alimentes,
            "avertissement": (
                f"{len(echecs)} source(s) indisponible(s) : {', '.join(echecs)}. "
                "Chaque bloc concerné porte son motif."
                if echecs
                else ""
            ),
            "nature_du_rapport": (
                "Ce rapport classe un état de marché et décrit un positionnement. "
                "Un régime n'est pas une prévision de prix, et aucune de ces "
                "informations ne constitue une recommandation."
            ),
        },
        "regime": bloc_regime,
        "rotation": bloc_rotation,
        "positionnement": bloc_positionnement,
    }
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
    """Exécute la veille crypto et publie le rapport.

    Returns:
        0 si le rapport est publié, 1 sinon.
    """
    analyseur = argparse.ArgumentParser(description="Veille sur les positions crypto.")
    analyseur.add_argument("--date", default=None, help="date du rapport, AAAA-MM-JJ.")
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
    rapport = construire_rapport(configuration, date_rapport=jour)

    chemins = publier(rapport)
    if chemins is None:
        return 1

    meta = rapport["meta"]
    print(f"\nVeille crypto du {meta['date']}")
    for actif, bloc in rapport["regime"]["regimes"].items():
        if bloc.get("disponible"):
            print(f"  {actif.upper():5s} régime {bloc['regime']} (MVRV {bloc['mvrv']:.2f})")
        else:
            print(f"  {actif.upper():5s} régime indéterminé : {bloc['motif']}")
    synthese = rapport["rotation"].get("synthese", {})
    if synthese:
        print(f"  Rotation : {synthese.get('etat')} ({synthese.get('n_mesures_exprimees')}/3 mesures)")
    funding = rapport["positionnement"]["funding"]
    if funding.get("disponible"):
        print(
            f"  Funding au {funding['percentile_90j']:.0f}e percentile "
            f"({funding['tension']})"
        )
    print(f"  Indicateurs non alimentés : {len(meta['indicateurs_non_alimentes'])}")
    if meta["sources_en_echec"]:
        print(f"Sources indisponibles : {', '.join(meta['sources_en_echec'])}")
    print(f"Écrit dans {chemins[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
