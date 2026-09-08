"""Règles de la société de financement, et survie du compte par Monte Carlo.

FTMO One Step, 10 000 €. Deux règles, et leur différence est le piège :

* **perte journalière de 300 €** — mesurée sur la journée, elle se remet à
  zéro chaque jour ;
* **perte totale de 1 000 €, en trailing** — le seuil monte avec le solde,
  mais **seulement sur le solde de clôture journalier**, une fois par jour en
  fin de séance, et il ne redescend jamais. Le recalculer sur un pic de gain
  latent en cours de journée durcirait la règle bien au-delà de ce que la
  société impose, et ferait échouer des comptes qui passent en réalité.

Pourquoi du Monte Carlo
-----------------------
Un unique enchaînement de trades ne dit rien de la survie d'un compte : le
même ensemble de résultats, dans un autre ordre, passe ou casse. La question
n'est pas « ce chemin a-t-il tenu ? » mais « quelle part des chemins
tient ? ». Les trades sont donc rééchantillonnés, et la sortie est une
distribution.

Le rééchantillonnage porte sur **l'ordre** des trades, sans remise : il
conserve exactement les résultats observés et ne fait varier que leur
séquence. Tirer avec remise reviendrait à inventer un historique qui n'a pas
eu lieu ; c'est défendable, mais ce n'est pas ce qui est demandé ici.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final

import numpy as np

_LOG: Final = logging.getLogger(__name__)

#: Capital initial du compte, en euros.
CAPITAL_INITIAL: Final[float] = 10_000.0

#: Perte journalière maximale, en euros.
PERTE_JOURNALIERE_MAX: Final[float] = 300.0

#: Perte totale maximale, en euros, appliquée en trailing.
PERTE_TOTALE_MAX: Final[float] = 1_000.0

#: Objectif de gain retenu par défaut, en euros.
OBJECTIF_GAIN: Final[float] = 1_000.0

#: Nombre de tirages Monte Carlo.
N_TIRAGES: Final[int] = 5_000

#: Motifs de fin de scénario.
BREACH_JOURNALIER: Final = "perte_journaliere"
BREACH_TOTAL: Final = "perte_totale_trailing"
OBJECTIF_ATTEINT: Final = "objectif_atteint"
NI_LUN_NI_LAUTRE: Final = "aucun"

__all__ = [
    "ConfigPropFirm",
    "simuler_compte",
    "monte_carlo",
    "BREACH_JOURNALIER",
    "BREACH_TOTAL",
    "OBJECTIF_ATTEINT",
]


@dataclass(slots=True, frozen=True)
class ConfigPropFirm:
    """Paramètres du compte financé.

    Attributes:
        capital_initial: solde de départ, en euros.
        perte_journaliere_max: seuil de perte sur une journée.
        perte_totale_max: seuil de perte totale, en trailing.
        objectif_gain: gain visé pour valider le compte.
    """

    capital_initial: float = CAPITAL_INITIAL
    perte_journaliere_max: float = PERTE_JOURNALIERE_MAX
    perte_totale_max: float = PERTE_TOTALE_MAX
    objectif_gain: float = OBJECTIF_GAIN


def simuler_compte(
    resultats: list[float],
    jours: list[Any] | None = None,
    config: ConfigPropFirm | None = None,
) -> dict[str, Any]:
    """Rejoue une séquence de trades contre les règles du compte.

    Les deux règles sont vérifiées dans l'ordre où elles se déclenchent, et
    la première franchie clôt le scénario : c'est elle qui invalide le
    compte, et savoir laquelle est plus utile que de savoir qu'il a échoué.

    Le seuil de perte totale ne se recalcule qu'**en fin de journée**, sur le
    solde de clôture, et il ne redescend jamais. Le recalculer en intraday
    reviendrait à durcir la règle.

    Args:
        resultats: résultats des trades, en euros, dans l'ordre.
        jours: jour de chaque trade, pour la règle journalière. Sans lui,
            chaque trade est traité comme une journée distincte.
        config: paramètres du compte.

    Returns:
        Dictionnaire décrivant l'issue : ``issue``, solde final, nombre de
        trades joués, et le détail du franchissement le cas échéant.
    """
    reglages = config or ConfigPropFirm()
    solde = reglages.capital_initial
    # Le seuil de départ est fixé sur le capital initial : il ne montera
    # qu'avec les clôtures journalières supérieures.
    plus_haut_journalier = reglages.capital_initial
    seuil_total = reglages.capital_initial - reglages.perte_totale_max

    if jours is None:
        jours = list(range(len(resultats)))

    jour_courant: Any = None
    solde_debut_jour = solde
    trades_joues = 0

    for i, gain in enumerate(resultats):
        jour = jours[i] if i < len(jours) else jour_courant

        if jour != jour_courant:
            # Clôture de la journée précédente : c'est le seul moment où le
            # seuil trailing se recalcule.
            if jour_courant is not None:
                plus_haut_journalier = max(plus_haut_journalier, solde)
                seuil_total = max(
                    seuil_total, plus_haut_journalier - reglages.perte_totale_max
                )
            jour_courant = jour
            solde_debut_jour = solde

        solde += gain
        trades_joues += 1

        perte_du_jour = solde_debut_jour - solde
        if perte_du_jour >= reglages.perte_journaliere_max:
            return {
                "issue": BREACH_JOURNALIER,
                "solde_final": solde,
                "trades_joues": trades_joues,
                "perte_du_jour": perte_du_jour,
                "seuil_total_courant": seuil_total,
                "detail": (
                    f"Perte de {perte_du_jour:.2f} € sur la journée, seuil "
                    f"{reglages.perte_journaliere_max:.0f} €."
                ),
            }

        if solde <= seuil_total:
            return {
                "issue": BREACH_TOTAL,
                "solde_final": solde,
                "trades_joues": trades_joues,
                "perte_du_jour": perte_du_jour,
                "seuil_total_courant": seuil_total,
                "detail": (
                    f"Solde de {solde:.2f} € sous le seuil trailing de "
                    f"{seuil_total:.2f} €."
                ),
            }

        if solde - reglages.capital_initial >= reglages.objectif_gain:
            return {
                "issue": OBJECTIF_ATTEINT,
                "solde_final": solde,
                "trades_joues": trades_joues,
                "perte_du_jour": perte_du_jour,
                "seuil_total_courant": seuil_total,
                "detail": (
                    f"Objectif de {reglages.objectif_gain:.0f} € atteint, solde "
                    f"{solde:.2f} €."
                ),
            }

    return {
        "issue": NI_LUN_NI_LAUTRE,
        "solde_final": solde,
        "trades_joues": trades_joues,
        "perte_du_jour": 0.0,
        "seuil_total_courant": seuil_total,
        "detail": "Séquence épuisée sans franchissement ni objectif atteint.",
    }


def monte_carlo(
    resultats: list[float],
    n_tirages: int = N_TIRAGES,
    config: ConfigPropFirm | None = None,
    graine: int = 20260908,
    trades_par_jour: int = 2,
) -> dict[str, Any]:
    """Rééchantillonne l'ordre des trades pour distribuer les issues.

    Args:
        resultats: résultats des trades, en euros.
        n_tirages: nombre de chemins simulés.
        config: paramètres du compte.
        graine: graine du générateur, pour un résultat reproductible.
        trades_par_jour: nombre de trades regroupés dans une même journée.
            Le regroupement compte : la règle journalière ne mord que si
            plusieurs pertes tombent le même jour, et supposer un trade par
            jour la rendrait presque inopérante.

    Returns:
        Dictionnaire des probabilités et de la distribution des soldes.
    """
    reglages = config or ConfigPropFirm()
    if not resultats:
        return {
            "disponible": False,
            "motif": "aucun trade à rééchantillonner",
            "n_tirages": 0,
        }
    if int(n_tirages) <= 0:
        # Zéro tirage est une façon légitime de demander à sauter la
        # simulation — la comparaison de sensibilité s'en sert. Le dire vaut
        # mieux que de diviser par zéro.
        return {
            "disponible": False,
            "motif": "simulation désactivée : aucun tirage demandé",
            "n_tirages": 0,
        }

    alea = np.random.default_rng(graine)
    tableau = np.asarray(resultats, dtype="float64")
    n = tableau.size
    jours = [i // max(int(trades_par_jour), 1) for i in range(n)]

    comptes = {
        OBJECTIF_ATTEINT: 0,
        BREACH_JOURNALIER: 0,
        BREACH_TOTAL: 0,
        NI_LUN_NI_LAUTRE: 0,
    }
    soldes: list[float] = []

    for _ in range(int(n_tirages)):
        ordre = alea.permutation(n)
        issue = simuler_compte(list(tableau[ordre]), jours=jours, config=reglages)
        comptes[issue["issue"]] += 1
        soldes.append(issue["solde_final"])

    total = float(n_tirages)
    distribution = np.asarray(soldes, dtype="float64")
    n_breach = comptes[BREACH_JOURNALIER] + comptes[BREACH_TOTAL]

    # Un échantillon trop court rend le résultat vrai mais sans portée : si la
    # somme des pertes possibles n'atteint pas le seuil, aucune permutation ne
    # peut casser le compte, et une probabilité de rupture nulle ne dit rien
    # de la stratégie — seulement de la taille de l'échantillon. Le signaler
    # vaut mieux que de laisser lire un zéro rassurant.
    perte_totale_possible = float(-tableau[tableau < 0].sum()) if (tableau < 0).any() else 0.0
    echantillon_suffisant = perte_totale_possible >= reglages.perte_totale_max
    avertissement = (
        ""
        if echantillon_suffisant
        else (
            f"Échantillon trop court pour que la question ait un sens : la somme de "
            f"toutes les pertes observées vaut {perte_totale_possible:.0f} €, en deçà du "
            f"seuil de {reglages.perte_totale_max:.0f} €. Aucune permutation ne peut "
            "donc casser le compte, et la probabilité de rupture nulle mesure la "
            "brièveté de l'historique, pas la solidité de la stratégie."
        )
    )

    return {
        "disponible": True,
        "motif": "",
        "n_tirages": int(n_tirages),
        "n_trades_par_chemin": int(n),
        "trades_par_jour": int(trades_par_jour),
        "probabilite_objectif": comptes[OBJECTIF_ATTEINT] / total,
        "probabilite_breach": n_breach / total,
        "probabilite_ni_lun_ni_lautre": comptes[NI_LUN_NI_LAUTRE] / total,
        "premiere_regle_violee": {
            "perte_journaliere": comptes[BREACH_JOURNALIER] / total,
            "perte_totale_trailing": comptes[BREACH_TOTAL] / total,
        },
        "part_des_breaches_par_regle": (
            {
                "perte_journaliere": comptes[BREACH_JOURNALIER] / n_breach,
                "perte_totale_trailing": comptes[BREACH_TOTAL] / n_breach,
            }
            if n_breach
            else {}
        ),
        "echantillon_suffisant": echantillon_suffisant,
        "avertissement": avertissement,
        "perte_cumulee_possible_eur": perte_totale_possible,
        "solde_median": float(np.median(distribution)),
        "solde_moyen": float(distribution.mean()),
        "solde_p05": float(np.percentile(distribution, 5)),
        "solde_p95": float(np.percentile(distribution, 95)),
        "objectif_gain_eur": reglages.objectif_gain,
        "methode": (
            "Rééchantillonnage de l'ordre des trades sans remise : les résultats "
            "observés sont conservés, seule leur séquence varie. La question posée "
            "n'est pas « ce chemin a-t-il tenu ? » mais « quelle part des chemins "
            "tient ? »."
        ),
    }
