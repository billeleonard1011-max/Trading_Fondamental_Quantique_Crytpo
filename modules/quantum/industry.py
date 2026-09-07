"""Vue d'ensemble du secteur quantique, indépendante des positions détenues.

Quatre lectures, et ce qu'elles servent à voir
----------------------------------------------
1. **Trésorerie et autonomie.** Ces sociétés ne dégagent pas de bénéfices :
   elles vivent de levées de fonds successives. Le nombre de trimestres de
   trésorerie restante ne dit pas qu'une société va faire faillite — elle
   lèvera —, il dit *quand* elle devra le faire, et donc quand une émission
   d'actions nouvelles deviendra probable.
2. **Financements et contrats publics.** L'essentiel du chiffre d'affaires du
   secteur vient de commandes publiques et de programmes de recherche. Le
   volume de couverture sur ce thème mesure l'intensité de ce flux.
3. **Nouveaux entrants.** Un nom qui apparaît plusieurs fois en un mois sans
   figurer parmi les acteurs connus est une information : la concurrence se
   déplace.
4. **Corrélation entre les positions.** Trois lignes fortement corrélées ne
   forment pas un portefeuille diversifié, elles forment une seule position
   en trois morceaux. C'est un fait de structure, qui mérite d'être dit.

Ce module décrit. Il ne conseille rien, et sa sortie passe le même contrôle
que celle de :mod:`modules.quantum.moves`.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Final

import numpy as np
import pandas as pd

_LOG: Final = logging.getLogger(__name__)

#: Longueur minimale d'un nom candidat, pour écarter les sigles courants.
LONGUEUR_MIN_ENTITE: Final[int] = 4

#: Mots à ne jamais retenir comme nom d'entité, malgré leur majuscule : ils
#: ouvrent une phrase ou désignent un pays, pas une entreprise.
_MOTS_VIDES: Final[frozenset[str]] = frozenset(
    {
        "The", "This", "That", "These", "Those", "There", "Their", "They",
        "With", "From", "Into", "Over", "After", "Before", "While", "When",
        "What", "Which", "Where", "Here", "Have", "Will", "Would", "Could",
        "Should", "About", "Because", "However", "Quantum", "Computing",
        "Company", "Companies", "Technology", "Technologies", "Research",
        "University", "Institute", "National", "Federal", "Government",
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
        "Sunday", "January", "February", "March", "April", "June", "July",
        "August", "September", "October", "November", "December",
        "United", "States", "China", "Europe", "Japan", "Korea", "India",
        "News", "Report", "Reports", "Market", "Markets", "Stock", "Stocks",
        "Shares", "Analyst", "Analysts", "Read", "More", "Best", "First",
    }
)

#: Motif d'un nom propre : un ou plusieurs mots capitalisés à la suite.
_MOTIF_ENTITE: Final = re.compile(r"\b([A-Z][a-zA-Z0-9&.\-]{2,}(?:\s+[A-Z][a-zA-Z0-9&.\-]{2,}){0,2})\b")

__all__ = [
    "analyser_tresorerie",
    "detecter_nouveaux_entrants",
    "analyser_correlation",
    "analyser_secteur",
]


# ---------------------------------------------------------------------------
# 1. Trésorerie
# ---------------------------------------------------------------------------
def analyser_tresorerie(
    watchlist: list[dict[str, Any]],
    runways: dict[str, dict[str, Any]],
    seuil_trimestres: float = 4.0,
) -> dict[str, Any]:
    """Rassemble les estimations de trésorerie restante.

    Args:
        watchlist: entrées ``quantum_watchlist``.
        runways: sortie de ``estimate_cash_runway``, par ticker.
        seuil_trimestres: seuil d'alerte, en trimestres.

    Returns:
        Bloc sérialisable, une entrée par valeur.
    """
    societes: list[dict[str, Any]] = []
    alertes: list[str] = []

    for entree in watchlist:
        ticker = str(entree.get("ticker", "")).upper()
        estimation = dict(runways.get(ticker) or {})
        bloc: dict[str, Any] = {
            "ticker": ticker,
            "nom": str(entree.get("name", ticker)),
            "disponible": bool(estimation.get("disponible", False)),
            "motif": str(estimation.get("motif", "estimation non fournie")),
            "tresorerie_usd": estimation.get("tresorerie_usd"),
            "date_tresorerie": estimation.get("date_tresorerie"),
            "consommation_trimestrielle_usd": estimation.get("consommation_trimestrielle_usd"),
            "trimestres_restants": estimation.get("trimestres_restants"),
            "seuil_alerte_trimestres": seuil_trimestres,
            "sous_le_seuil": False,
            "commentaire": str(estimation.get("commentaire", "")),
        }

        trimestres = estimation.get("trimestres_restants")
        if bloc["disponible"] and trimestres is not None:
            bloc["sous_le_seuil"] = float(trimestres) < seuil_trimestres
            if bloc["sous_le_seuil"]:
                alertes.append(ticker)
                bloc["commentaire"] = (
                    f"{bloc['commentaire']} Au rythme observé, l'autonomie passe sous "
                    f"{seuil_trimestres:.0f} trimestres : une émission d'actions nouvelles "
                    "devient probable à cet horizon, ce qui dilue les actionnaires existants."
                ).strip()
        societes.append(bloc)

    disponibles = [s for s in societes if s["disponible"]]
    return {
        "disponible": bool(disponibles),
        "motif": "" if disponibles else "aucune estimation XBRL exploitable",
        "seuil_alerte_trimestres": seuil_trimestres,
        "n_societes": len(societes),
        "n_estimations_disponibles": len(disponibles),
        "societes_sous_le_seuil": alertes,
        "societes": societes,
    }


# ---------------------------------------------------------------------------
# 2. Nouveaux entrants
# ---------------------------------------------------------------------------
def _normaliser(texte: str) -> str:
    """Réduit un nom à une forme comparable : minuscules, sans accent."""
    decompose = unicodedata.normalize("NFKD", texte.lower())
    return "".join(c for c in decompose if not unicodedata.combining(c)).strip()


def detecter_nouveaux_entrants(
    articles: list[dict[str, Any]],
    incumbents: list[str],
    min_mentions: int = 3,
) -> dict[str, Any]:
    """Repère les entités récurrentes absentes de la liste des acteurs connus.

    L'extraction repose sur la capitalisation : dans un titre en anglais, les
    noms propres portent une majuscule. C'est grossier et le module l'assume —
    une liste de mots vides écarte les débuts de phrase, les pays et les mois,
    et le seuil de récurrence élimine l'essentiel du reste. Un nom cité une
    seule fois n'est pas retenu.

    Args:
        articles: articles collectés, sérialisés comme ``NewsItem.to_dict``.
        incumbents: acteurs déjà connus, ignorés.
        min_mentions: nombre d'occurrences à partir duquel une entité est
            signalée.

    Returns:
        Bloc sérialisable listant les candidats et leur nombre de mentions.
    """
    if not articles:
        return {
            "disponible": False,
            "motif": "aucun article collecté sur la période",
            "min_mentions": min_mentions,
            "candidats": [],
        }

    connus = {_normaliser(i) for i in incumbents}
    comptes: dict[str, int] = {}
    exemples: dict[str, str] = {}

    for article in articles:
        titre = str(article.get("titre", ""))
        vus: set[str] = set()
        for correspondance in _MOTIF_ENTITE.finditer(titre):
            nom = correspondance.group(1).strip()
            if len(nom) < LONGUEUR_MIN_ENTITE:
                continue
            # Un nom composé uniquement de mots vides n'est pas une entité.
            if all(mot in _MOTS_VIDES for mot in nom.split()):
                continue
            normalise = _normaliser(nom)
            # Un acteur connu, ou un nom qui contient un acteur connu.
            if any(c in normalise or normalise in c for c in connus):
                continue
            if normalise in vus:
                continue
            vus.add(normalise)
            comptes[nom] = comptes.get(nom, 0) + 1
            exemples.setdefault(nom, titre)

    candidats = [
        {"nom": nom, "mentions": n, "exemple_titre": exemples.get(nom, "")}
        for nom, n in sorted(comptes.items(), key=lambda kv: kv[1], reverse=True)
        if n >= min_mentions
    ]

    if candidats:
        _LOG.info(
            "Nouveaux entrants possibles : %s",
            ", ".join(f"{c['nom']} ({c['mentions']})" for c in candidats[:5]),
        )
    return {
        "disponible": True,
        "motif": "",
        "min_mentions": min_mentions,
        "n_articles_analyses": len(articles),
        "n_candidats": len(candidats),
        "candidats": candidats,
        "methode": (
            "Extraction par capitalisation dans les titres, filtrée par une liste de "
            "mots vides et un seuil de récurrence. Méthode volontairement simple : "
            "elle signale des pistes à vérifier, elle n'identifie pas des sociétés."
        ),
    }


# ---------------------------------------------------------------------------
# 3. Corrélation entre positions
# ---------------------------------------------------------------------------
def analyser_correlation(
    prix: dict[str, pd.DataFrame],
    fenetre: int = 60,
    seuil: float = 0.70,
) -> dict[str, Any]:
    """Mesure la corrélation des rendements quotidiens entre valeurs suivies.

    La corrélation porte sur les **rendements**, pas sur les prix : deux
    séries de prix qui montent toutes deux dans le temps sont corrélées par
    construction, ce qui ne dit rien de leur comportement conjoint au jour le
    jour.

    Args:
        prix: historiques par ticker.
        fenetre: nombre de séances retenues.
        seuil: corrélation au-delà de laquelle l'avertissement est émis.

    Returns:
        Bloc sérialisable avec la matrice, la paire la plus corrélée et, le
        cas échéant, l'avertissement de diversification.
    """
    series: dict[str, pd.Series] = {}
    for ticker, cadre in (prix or {}).items():
        if cadre is None or cadre.empty or "close" not in cadre.columns:
            continue
        serie = cadre["close"].dropna()
        if len(serie) >= 20:
            series[ticker] = serie

    if len(series) < 2:
        return {
            "disponible": False,
            "motif": f"{len(series)} valeur(s) exploitable(s), deux au minimum requises",
            "fenetre_seances": fenetre,
        }

    cadre_prix = pd.DataFrame(series).sort_index()
    rendements = cadre_prix.pct_change().dropna().iloc[-int(fenetre):]
    if len(rendements) < 20:
        return {
            "disponible": False,
            "motif": f"{len(rendements)} séance(s) communes, 20 au minimum requises",
            "fenetre_seances": fenetre,
        }

    matrice = rendements.corr()

    paires: list[dict[str, Any]] = []
    tickers = list(matrice.columns)
    for i, a in enumerate(tickers):
        for b in tickers[i + 1 :]:
            valeur = matrice.loc[a, b]
            if pd.notna(valeur):
                paires.append({"paire": f"{a}/{b}", "correlation": round(float(valeur), 4)})
    paires.sort(key=lambda p: abs(p["correlation"]), reverse=True)

    correlation_max = paires[0]["correlation"] if paires else None
    correlation_moyenne = (
        float(np.mean([p["correlation"] for p in paires])) if paires else None
    )
    elevee = correlation_max is not None and correlation_max >= seuil

    avertissement = ""
    if elevee:
        concernees = [p["paire"] for p in paires if p["correlation"] >= seuil]
        avertissement = (
            f"Corrélation de {correlation_max:.2f} sur {len(rendements)} séances entre "
            f"{paires[0]['paire']} ({len(concernees)} paire(s) au-dessus de {seuil:.2f}). "
            "Ces positions se comportent comme une seule et même position : la "
            "diversification apparente est limitée, et un choc commun les touche ensemble."
        )
        _LOG.warning("Corrélation élevée entre valeurs quantiques : %.2f.", correlation_max)

    return {
        "disponible": True,
        "motif": "",
        "fenetre_seances": int(fenetre),
        "n_seances_effectives": len(rendements),
        "seuil_correlation_elevee": seuil,
        "correlation_max": correlation_max,
        "correlation_moyenne": None if correlation_moyenne is None else round(correlation_moyenne, 4),
        "correlation_elevee": elevee,
        "paires": paires,
        "avertissement": avertissement,
    }


# ---------------------------------------------------------------------------
# Assemblage
# ---------------------------------------------------------------------------
def analyser_secteur(
    watchlist: list[dict[str, Any]],
    configuration: dict[str, Any],
    prix: dict[str, pd.DataFrame],
    runways: dict[str, dict[str, Any]],
    intensite_financements: dict[str, Any] | None = None,
    articles_secteur: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble la vue d'ensemble du secteur.

    Args:
        watchlist: entrées ``quantum_watchlist``.
        configuration: bloc ``quantum`` de la configuration.
        prix: historiques par ticker.
        runways: estimations de trésorerie par ticker.
        intensite_financements: sortie de ``gdelt_intensity`` sur le thème
            des financements et contrats.
        articles_secteur: articles collectés sur le secteur.

    Returns:
        Bloc sérialisable.
    """
    tresorerie = analyser_tresorerie(
        watchlist, runways,
        seuil_trimestres=float(configuration.get("seuil_runway_trimestres", 4.0)),
    )
    nouveaux = detecter_nouveaux_entrants(
        list(articles_secteur or []),
        list(configuration.get("incumbents") or []),
        min_mentions=int(configuration.get("min_mentions_for_alert", 3)),
    )
    correlation = analyser_correlation(
        prix,
        fenetre=int(configuration.get("fenetre_correlation_seances", 60)),
        seuil=float(configuration.get("seuil_correlation_elevee", 0.70)),
    )

    financements = dict(intensite_financements or {})
    bloc_financements = {
        "disponible": bool(financements.get("disponible", False)),
        "motif": "" if financements.get("disponible") else (
            financements.get("commentaire") or "intensité GDELT indisponible"
        ),
        "ratio_couverture": financements.get("ratio"),
        "volume_24h": financements.get("volume_24h"),
        "moyenne_journaliere_30j": financements.get("moyenne_journaliere_30j"),
        "alerte": bool(financements.get("alerte", False)),
        "lecture": (
            f"Couverture des financements et contrats du secteur à "
            f"{float(financements['ratio']):.1f} fois sa normale sur 24 heures."
            if financements.get("ratio") is not None
            else "Intensité de couverture non mesurée."
        ),
    }

    return {
        "disponible": any(
            b.get("disponible") for b in (tresorerie, nouveaux, correlation, bloc_financements)
        ),
        "tresorerie": tresorerie,
        "financements_et_contrats": bloc_financements,
        "nouveaux_entrants": nouveaux,
        "correlation_positions": correlation,
    }
