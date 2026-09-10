"""Point d'entrée du moteur d'analyse fondamentale de l'or.

Exécution :
    python -m modules.gold.run
    python -m modules.gold.run --sans-explication   (aucun appel OpenAI)
    python -m modules.gold.run --date 2026-09-04    (rejoue une date passée)

Le module orchestre la collecte, le calcul et la publication. Il écrit
``reports/gold/AAAA-MM-JJ.json`` et met à jour ``reports/gold/latest.json``.

Principe de dégradation
-----------------------
Chaque source est isolée : si elle échoue, son bloc est marqué indisponible
avec son motif, et le reste s'exécute quand même. Le biais final est alors
publié en signalant qu'il repose sur des données partielles, avec la liste
de ce qui manque.

Un rapport incomplet et honnête vaut mieux qu'une absence de rapport, et
infiniment mieux qu'un rapport complet dont on ignore qu'une source était
muette.

Horodatage par bloc
-------------------
Chaque bloc porte son propre ``_meta`` : la source réelle, l'instant de
collecte, la date de la donnée elle-même et son âge en jours. Le COT du
mardi précédent et le prix d'il y a une heure n'ont pas la même fraîcheur,
et la page web doit pouvoir l'afficher au lieu de les présenter côte à côte
comme s'ils étaient contemporains.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Final

import pandas as pd
import yaml

from dataio import calendar as calendrier_macro
from dataio import cot as cot_io
from dataio import gold_flows, macro, market
from modules import synthese
from modules.gold import analogues, bias, explain, fair_value, geopolitics

_LOG: Final = logging.getLogger("modules.gold.run")

#: Racine du dépôt, déduite de l'emplacement de ce fichier.
RACINE: Final = Path(__file__).resolve().parents[2]

#: Configuration du moteur.
CHEMIN_CONFIG: Final = RACINE / "config" / "gold.yaml"

#: Dossier de publication.
DOSSIER_RAPPORTS: Final = RACINE / "reports" / "gold"

#: Symbole du prix de l'or, en dollars par once.
SYMBOLE_OR: Final = "GC=F"

#: Repli si les contrats à terme sont indisponibles. Attention : le GLD ne
#: cote pas en dollars par once mais en parts d'ETF (environ un dixième de
#: l'once, érodé par les frais de gestion). Les écarts en dollars ne sont
#: alors plus des dollars par once, et le champ ``unite_prix`` le signale.
SYMBOLE_OR_REPLI: Final = "GLD"

#: Séries FRED nécessaires au moteur.
SERIES_FRED: Final[tuple[str, ...]] = (
    "DFII10", "DTWEXBGS", "DCOILWTICO", "T10YIE",
    # Contexte macro en prose (dataio.macro.rediger_contexte_macro) : les
    # quatre premières servaient déjà à la juste valeur et à la chaîne de
    # transmission, celles-ci s'y ajoutent pour l'inflation, l'emploi, la
    # courbe, le crédit et l'appétit pour le risque.
    "CPIAUCSL", "UNRATE", "VIXCLS", "BAMLH0A0HYM2", "T10Y2Y", "DGS10", "DGS2",
    "WALCL", "RRPONTSYD",
)

#: ETF sectoriels servant à mesurer la rotation cyclique/défensif, l'un des
#: trois signaux d'appétit pour le risque. Repris de config/universe.yaml
#: (bloc ``groupes``) via dataio.macro, pour n'avoir qu'une définition.
TICKERS_SECTEURS: Final[tuple[str, ...]] = macro.GROUPE_CYCLIQUES + macro.GROUPE_DEFENSIFS

#: Horizon des variations utilisées par le biais, en séances.
HORIZON_BIAIS: Final[int] = 20

#: Profondeur de l'historique COT rapatrié : de quoi remonter à 2010 en
#: rapports hebdomadaires, pour la base des précédents.
SEMAINES_COT_HISTORIQUE: Final[int] = 900

__all__ = ["construire_rapport", "main"]


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------
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
            _LOG.warning("Date de donnée illisible pour la source %s : %r", source, date_donnee)

    return {
        "source": source,
        "horodatage_collecte_utc": horodatage.isoformat(),
        "date_donnee": date_texte,
        "age_jours": age,
    }


def _avertissement(echecs: list[str], alertes: list[dict[str, str]]) -> str:
    """Rassemble en une phrase ce qui doit sauter aux yeux du lecteur.

    Deux natures de problème y coexistent, et il serait trompeur de les
    confondre : une **source en échec** a rendu un bloc indisponible, tandis
    qu'une **alerte** vient d'un bloc disponible mais qui réclame une
    intervention — typiquement un calendrier FOMC dont les dates s'épuisent.
    Le second cas ne dégrade rien aujourd'hui et cesserait de fonctionner
    demain sans prévenir : c'est justement pour cela qu'il doit remonter ici
    plutôt que rester enterré dans son bloc.

    Args:
        echecs: sources indisponibles.
        alertes: alertes levées par des blocs par ailleurs disponibles.

    Returns:
        L'avertissement, vide si tout va bien.
    """
    morceaux: list[str] = []
    if echecs:
        morceaux.append(
            f"{len(echecs)} source(s) indisponible(s) : {', '.join(echecs)}. "
            "Chaque bloc concerné porte son motif."
        )
    for alerte in alertes:
        morceaux.append(
            f"ALERTE {alerte['sujet']} — {alerte['motif']} "
            f"(bloc « {alerte['bloc']} »)."
        )
    return " ".join(morceaux)


def charger_configuration(chemin: Path = CHEMIN_CONFIG) -> dict[str, Any]:
    """Lit ``config/gold.yaml``.

    Args:
        chemin: emplacement du fichier.

    Returns:
        La configuration, ou un dictionnaire vide si le fichier est illisible.
        Les modules appelés appliquent alors leurs valeurs par défaut.
    """
    try:
        with chemin.open("r", encoding="utf-8") as fichier:
            return yaml.safe_load(fichier) or {}
    except (OSError, yaml.YAMLError) as exc:
        _LOG.error("Configuration illisible (%s) : %s. Valeurs par défaut appliquées.", chemin, exc)
        return {}


def _variation_serie(cadre: pd.DataFrame, colonne: str, horizon: int, en_pct: bool) -> float | None:
    """Variation d'une série FRED sur un horizon donné.

    Args:
        cadre: DataFrame des séries FRED.
        colonne: nom de la série.
        horizon: recul en observations.
        en_pct: ``True`` pour une variation relative, ``False`` pour une
            différence en niveau (cas des taux).

    Returns:
        La variation, ou ``None`` si la série est absente ou trop courte.
    """
    if cadre is None or colonne not in cadre.columns:
        return None
    serie = cadre[colonne].dropna()
    if len(serie) <= horizon:
        return None

    courant, precedent = float(serie.iloc[-1]), float(serie.iloc[-1 - horizon])
    if en_pct:
        return None if precedent == 0.0 else (courant / precedent - 1.0) * 100.0
    return courant - precedent


# ---------------------------------------------------------------------------
# Collecte
# ---------------------------------------------------------------------------
def _charger_prix_or(debut: str, fin: str | None) -> tuple[pd.Series, str, str]:
    """Charge le prix de l'or, avec repli documenté.

    Args:
        debut: début de l'historique, au format ISO.
        fin: fin de l'historique.

    Returns:
        Triplet ``(serie, source, unite)``. La série est vide si les deux
        sources ont échoué.
    """
    prix = market.get_prices(SYMBOLE_OR, start=debut, end=fin)
    if not prix.empty:
        return prix["close"].rename("or"), f"{SYMBOLE_OR} (yfinance)", "USD par once"

    _LOG.warning("Contrats à terme or indisponibles : repli sur %s.", SYMBOLE_OR_REPLI)
    prix = market.get_prices(SYMBOLE_OR_REPLI, start=debut, end=fin)
    if not prix.empty:
        return (
            prix["close"].rename("or"),
            f"{SYMBOLE_OR_REPLI} (yfinance, repli)",
            "parts d'ETF — PAS des dollars par once",
        )

    return pd.Series(dtype="float64", name="or"), "aucune", "inconnue"


def construire_rapport(
    configuration: dict[str, Any],
    date_rapport: date | None = None,
    avec_explication: bool = True,
    client_openai: Any | None = None,
) -> dict[str, Any]:
    """Collecte, calcule et assemble le rapport complet.

    Args:
        configuration: contenu de ``config/gold.yaml``.
        date_rapport: date visée. Aujourd'hui par défaut.
        avec_explication: ``False`` pour n'effectuer aucun appel OpenAI.
        client_openai: client déjà construit, pour les tests.

    Returns:
        Le rapport, prêt à être sérialisé en JSON.
    """
    jour = date_rapport or _maintenant().date()
    echecs: list[str] = []
    # Alertes distinctes des échecs de source : un bloc peut être disponible
    # et néanmoins réclamer une intervention, comme un calendrier FOMC qui
    # s'épuise. Sans remontée jusqu'à meta, personne ne les verrait.
    alertes: list[dict[str, str]] = []

    cfg_fv = dict(configuration.get("juste_valeur") or {})
    cfg_cot = dict(configuration.get("cot") or {})
    cfg_cal = dict(configuration.get("calendrier") or {})
    cfg_geo = dict(configuration.get("geopolitique") or {})
    cfg_ana = dict(configuration.get("analogues") or {})
    cfg_biais = dict(configuration.get("biais") or {})
    cfg_exp = dict(configuration.get("explication") or {})

    debut = str(cfg_fv.get("debut_historique", "2010-01-01"))
    fin = str(jour)

    # --- Prix de l'or ------------------------------------------------------
    _LOG.info("Chargement du prix de l'or...")
    prix_or, source_prix, unite_prix = _charger_prix_or(debut, fin)
    if prix_or.empty:
        echecs.append("prix de l'or")
    else:
        prix_or = prix_or[prix_or.index <= pd.Timestamp(jour)]

    bloc_prix: dict[str, Any] = {
        "disponible": not prix_or.empty,
        "motif": "" if not prix_or.empty else "ni les contrats à terme ni l'ETF n'ont répondu",
        "prix": None if prix_or.empty else float(prix_or.iloc[-1]),
        "unite_prix": unite_prix,
        "variation_5j_pct": None,
        "variation_20j_pct": None,
        "_meta": _meta(source_prix, None if prix_or.empty else prix_or.index[-1], jour),
    }
    for horizon in (5, 20):
        if len(prix_or) > horizon:
            precedent = float(prix_or.iloc[-1 - horizon])
            if precedent:
                bloc_prix[f"variation_{horizon}j_pct"] = (float(prix_or.iloc[-1]) / precedent - 1.0) * 100.0

    # --- Séries FRED -------------------------------------------------------
    _LOG.info("Chargement des séries FRED...")
    series_fred = macro.get_many(list(SERIES_FRED), start=debut, end=fin)
    if series_fred.empty:
        echecs.append("séries FRED")
        _LOG.warning("Aucune série FRED : juste valeur et chaîne de transmission dégradées.")
    else:
        series_fred = series_fred[series_fred.index <= pd.Timestamp(jour)]

    serie_taux = str(cfg_fv.get("serie_taux_reel", "DFII10"))
    serie_dollar = str(cfg_fv.get("serie_dollar", "DTWEXBGS"))

    # --- Juste valeur ------------------------------------------------------
    _LOG.info("Estimation de la juste valeur...")
    historique_fv = pd.DataFrame()
    if prix_or.empty or series_fred.empty or serie_taux not in series_fred.columns or serie_dollar not in series_fred.columns:
        manquant = "prix de l'or" if prix_or.empty else f"séries {serie_taux} / {serie_dollar}"
        lecture_fv = fair_value.FairValue(date=None, motif=f"{manquant} indisponible(s)")
        echecs.append("juste valeur")
    else:
        donnees_fv = fair_value.preparer_donnees(
            prix_or, series_fred[serie_taux], series_fred[serie_dollar]
        )
        historique_fv = fair_value.fair_value_history(
            donnees_fv,
            fenetre=int(cfg_fv.get("fenetre_jours", fair_value.FENETRE_DEFAUT)),
            min_observations=int(cfg_fv.get("min_observations", fair_value.MIN_OBSERVATIONS)),
        )
        lecture_fv = fair_value.estimate_fair_value(
            donnees_fv,
            date=pd.Timestamp(jour),
            fenetre=int(cfg_fv.get("fenetre_jours", fair_value.FENETRE_DEFAUT)),
            min_observations=int(cfg_fv.get("min_observations", fair_value.MIN_OBSERVATIONS)),
            seuil_r2=float(cfg_fv.get("seuil_r2_fiable", fair_value.SEUIL_R2_DEFAUT)),
            ecarts_historiques=None if historique_fv.empty else historique_fv["ecart_pct"],
        )
        if not lecture_fv.disponible:
            echecs.append("juste valeur")

    bloc_fv = lecture_fv.to_dict()
    bloc_fv["unite_prix"] = unite_prix
    bloc_fv["_meta"] = _meta(
        f"{source_prix} + FRED {serie_taux}/{serie_dollar}", lecture_fv.date, jour
    )

    # --- Positionnement COT ------------------------------------------------
    # L'historique est téléchargé une seule fois, assez profond pour servir à
    # la fois la lecture du jour et la base des précédents : deux appels
    # rapatrieraient neuf cents rapports en double.
    _LOG.info("Chargement du positionnement CFTC...")
    historique_cot, source_cot = cot_io.fetch_cot_or(
        code_contrat=str(cfg_cot.get("code_contrat", cot_io.CODE_CONTRAT_OR)),
        semaines=SEMAINES_COT_HISTORIQUE,
    )
    positionnement = cot_io.get_positionnement_or(
        code_contrat=str(cfg_cot.get("code_contrat", cot_io.CODE_CONTRAT_OR)),
        semaines=int(cfg_cot.get("fenetre_percentile_semaines", cot_io.FENETRE_PERCENTILE)),
        min_semaines=int(cfg_cot.get("min_semaines_percentile", cot_io.MIN_SEMAINES_PERCENTILE)),
        aujourd_hui=jour,
        historique=historique_cot if not historique_cot.empty else None,
    )
    if not historique_cot.empty:
        positionnement = replace(positionnement, source=source_cot)
    if not positionnement.disponible:
        echecs.append("positionnement CFTC")
    bloc_cot = positionnement.to_dict()
    bloc_cot["_meta"] = _meta(
        positionnement.source or "CFTC", positionnement.date_observation, jour
    )

    # --- Flux --------------------------------------------------------------
    _LOG.info("Chargement des flux et ratios...")
    flux = gold_flows.get_flux_or(debut=debut, fin=fin)
    bloc_flux: dict[str, Any] = {"_meta": _meta("yfinance + SPDR (tentative)", None, jour)}
    for nom, indicateur in flux.items():
        bloc_flux[nom] = indicateur.to_dict()
        if not indicateur.disponible:
            echecs.append(f"flux/{nom}")

    # --- Calendrier --------------------------------------------------------
    _LOG.info("Chargement du calendrier macro...")
    bloc_calendrier = calendrier_macro.get_calendrier(cfg_cal)
    bloc_calendrier["_meta"] = _meta(
        f"FRED releases + FOMC ({bloc_calendrier.get('fomc', {}).get('source', 'inconnue')})",
        jour,
        jour,
    )
    if not bloc_calendrier.get("disponible"):
        echecs.append("calendrier macro")
    if bloc_calendrier.get("alerte_renouvellement"):
        alertes.append(
            {
                "bloc": "calendrier",
                "sujet": "calendrier FOMC",
                "motif": str(bloc_calendrier.get("motif") or "motif non précisé"),
            }
        )

    # --- Contexte macro en prose -------------------------------------------
    # Placé avant la géopolitique : le décor général d'abord, les
    # développements spécifiques ensuite — c'est aussi l'ordre d'affichage
    # dans l'onglet Géopolitique du site.
    _LOG.info("Lecture du contexte macro...")
    prix_secteurs = pd.DataFrame()
    try:
        univers = market.get_universe(list(TICKERS_SECTEURS), start=debut, end=fin)
        if univers:
            prix_secteurs = pd.DataFrame(
                {ticker: cadre["close"] for ticker, cadre in univers.items()}
            )
    except Exception as exc:  # noqa: BLE001 - une source muette ne casse rien
        _LOG.warning("Prix sectoriels indisponibles (%s) : rotation non mesurée.", exc)

    regime_macro = macro.compute_macro_regime(
        series_fred if not series_fred.empty else pd.DataFrame(),
        prix_secteurs if not prix_secteurs.empty else None,
    )
    bloc_contexte = macro.rediger_contexte_macro(
        regime_macro, series_fred if not series_fred.empty else None
    )
    bloc_contexte["regime"] = regime_macro.to_dict()
    bloc_contexte["_meta"] = _meta(
        "FRED (séries macro) + ETF sectoriels (rotation cyclique/défensif)",
        regime_macro.date_lecture,
        jour,
    )
    if not bloc_contexte.get("disponible"):
        echecs.append("contexte macro")

    # --- Géopolitique (dossiers de conflits nommés) -------------------------
    _LOG.info("Mesure des dossiers géopolitiques...")
    z_prime = lecture_fv.z_score if (lecture_fv.disponible and lecture_fv.fiable) else None
    bloc_geo = geopolitics.analyser_dossiers(
        series_macro=series_fred if not series_fred.empty else None,
        prix_or=prix_or if not prix_or.empty else None,
        z_score_prime=z_prime,
        fenetre=int(cfg_geo.get("fenetre_trajectoire_jours", geopolitics.FENETRE_TRAJECTOIRE)),
        seuil_acceleration=float(cfg_geo.get("seuil_acceleration", 1.15)),
        seuil_essoufflement=float(cfg_geo.get("seuil_essoufflement", 0.85)),
    )
    bloc_geo["_meta"] = _meta(bloc_geo.get("source", "GDELT"), jour, jour)
    if not bloc_geo.get("disponible"):
        echecs.append("géopolitique (GDELT)")
    # bloc_geo["_dossiers_objets"] (objets Dossier, non sérialisables tels
    # quels) traverse volontairement jusqu'à rapport["geopolitique"] : main()
    # le retire juste avant publier() et s'en sert pour mettre à jour
    # l'historique des développements — seulement si le rapport est
    # réellement publié, jamais sur un essai à blanc (--sans-explication ou
    # un test qui appelle construire_rapport() sans publier).

    # --- Précédents historiques -------------------------------------------
    _LOG.info("Recherche des précédents historiques...")
    horizons = [int(h) for h in cfg_ana.get("horizons_jours", analogues.HORIZONS)]
    serie_cot_historique = (
        historique_cot["net_managed_money"] if not historique_cot.empty else None
    )

    base, variables_absentes = analogues.construire_base(
        historique_fv,
        prix_or,
        cot=serie_cot_historique,
        stress_geopolitique=None,  # GDELT ne remonte pas jusqu'en 2010 par l'API publique
        debut=str(cfg_ana.get("debut_historique", "2010-01-01")),
        horizons=horizons,
    )

    etat_du_jour: dict[str, float] = {}
    if lecture_fv.disponible and lecture_fv.z_score is not None:
        etat_du_jour["z_fair_value"] = float(lecture_fv.z_score)
    if positionnement.percentile_managed_money is not None:
        etat_du_jour["percentile_cot"] = float(positionnement.percentile_managed_money)
    if not series_fred.empty and serie_taux in series_fred.columns:
        derniers_taux = series_fred[serie_taux].dropna()
        if not derniers_taux.empty:
            etat_du_jour["regime_taux_reels"] = float(derniers_taux.iloc[-1])

    bloc_analogues = analogues.find_analogues(
        etat_du_jour,
        base,
        n=int(cfg_ana.get("n_voisins", analogues.N_VOISINS)),
        separation_min_jours=int(cfg_ana.get("separation_min_jours", analogues.SEPARATION_MIN_JOURS)),
        min_cas=int(cfg_ana.get("min_cas", analogues.MIN_CAS)),
        horizons=horizons,
    )
    bloc_analogues["etat_du_jour"] = etat_du_jour
    bloc_analogues["variables_sans_historique"] = variables_absentes
    bloc_analogues["_meta"] = _meta(
        "reconstruction interne (juste valeur + COT + FRED)",
        None if base.empty else base.index.max(),
        jour,
    )
    if not bloc_analogues.get("disponible"):
        echecs.append("précédents historiques")

    # --- Biais -------------------------------------------------------------
    _LOG.info("Agrégation du biais...")
    delta_taux = _variation_serie(series_fred, serie_taux, HORIZON_BIAIS, en_pct=False)
    delta_dollar = _variation_serie(series_fred, serie_dollar, HORIZON_BIAIS, en_pct=True)

    bloc_biais = bias.calculer_biais(
        cfg_biais,
        fair_value=bloc_fv,
        cot=bloc_cot,
        geopolitique=bloc_geo,
        flux=bloc_flux,
        delta_taux_reels=delta_taux,
        delta_dollar=delta_dollar,
        analogues=bloc_analogues,
    )
    bloc_biais["_meta"] = _meta("agrégation interne", jour, jour)

    # --- Assemblage --------------------------------------------------------
    rapport: dict[str, Any] = {
        "meta": {
            "date": str(jour),
            "horodatage_utc": _maintenant().isoformat(),
            "instrument": "XAUUSD",
            "version_moteur": "2.1",
            "sources_en_echec": echecs,
            "donnees_partielles": bool(echecs),
            "alertes": alertes,
            "avertissement": _avertissement(echecs, alertes),
        },
        "prix": bloc_prix,
        "juste_valeur": bloc_fv,
        "positionnement_cot": bloc_cot,
        "flux": bloc_flux,
        "calendrier": bloc_calendrier,
        "contexte_macro": bloc_contexte,
        "geopolitique": bloc_geo,
        "analogues": bloc_analogues,
        "biais": bloc_biais,
    }

    # --- Explications ------------------------------------------------------
    if avec_explication:
        _LOG.info("Génération des explications...")
        rapport["explications"] = explain.expliquer_rapport(
            rapport, configuration=cfg_exp, client=client_openai
        )
    else:
        rapport["explications"] = {
            "explications": {},
            "n_openai": 0,
            "n_gabarit": 0,
            "toutes_verifiees": False,
            "motif": "explications désactivées par l'appelant (--sans-explication)",
        }

    # --- Historique d'auto-évaluation --------------------------------------
    chemin_historique = cfg_biais.get("fichier_historique")
    if chemin_historique:
        bias.enregistrer_biais(
            bloc_biais,
            RACINE / str(chemin_historique),
            prix_or=bloc_prix.get("prix"),
            date_rapport=str(jour),
        )

    # --- Synthèses de rubrique ---------------------------------------------
    # Composées en dernier : elles relient des blocs déjà calculés, et ne
    # valent donc que si tout le reste du rapport est en place.
    rapport["synthese"] = synthese.synthetiser_or(rapport)
    rapport["geopolitique"]["synthese"] = synthese.synthetiser_geopolitique(rapport)

    return rapport


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------
def publier(rapport: dict[str, Any], dossier: Path = DOSSIER_RAPPORTS) -> tuple[Path, Path] | None:
    """Écrit le rapport du jour et met à jour ``latest.json``.

    Args:
        rapport: rapport complet.
        dossier: dossier de publication.

    Returns:
        Couple des deux chemins écrits, ou ``None`` en cas d'échec d'écriture.
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
    """Exécute le moteur et publie le rapport.

    Args:
        argv: arguments de ligne de commande.

    Returns:
        Code de sortie : 0 si le rapport est publié, 1 sinon. Un rapport
        partiel reste un succès — c'est le mode de fonctionnement prévu quand
        une source est muette.
    """
    analyseur = argparse.ArgumentParser(
        description="Moteur d'analyse fondamentale de l'or (XAUUSD)."
    )
    analyseur.add_argument("--date", default=None, help="date du rapport, AAAA-MM-JJ.")
    analyseur.add_argument(
        "--sans-explication",
        action="store_true",
        help="n'effectue aucun appel OpenAI ; les explications passent en mode gabarit.",
    )
    analyseur.add_argument(
        "--verbeux", action="store_true", help="journalisation détaillée."
    )
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
    rapport = construire_rapport(
        configuration,
        date_rapport=jour,
        avec_explication=not arguments.sans_explication,
    )

    # Retiré avant publication : ce sont des objets Python (geopolitics.Dossier),
    # pas des données à sérialiser. Conservé le temps de mettre à jour
    # l'historique des développements géopolitiques, seulement une fois le
    # rapport effectivement écrit.
    dossiers_mesures = rapport.get("geopolitique", {}).pop("_dossiers_objets", [])

    chemins = publier(rapport)
    if chemins is None:
        return 1
    if dossiers_mesures:
        geopolitics.publier_historique_dossiers(dossiers_mesures)

    meta = rapport["meta"]
    biais_final = rapport["biais"]
    print(
        f"\nRapport or du {meta['date']} : biais {biais_final['biais']} "
        f"(score {biais_final['score_composite']:+.3f}, conviction {biais_final['conviction']}, "
        f"couverture {biais_final['couverture_donnees']:.0%})"
    )
    if meta["sources_en_echec"]:
        print(f"Sources indisponibles : {', '.join(meta['sources_en_echec'])}")
    print(f"Écrit dans {chemins[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
