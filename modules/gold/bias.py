"""Agrégation des composantes en un biais quotidien sur l'or.

Principe
--------
Six composantes indépendantes produisent chacune un score entre -1 et +1,
où le signe dit le sens et l'amplitude la force. Elles sont ensuite
combinées avec les pondérations lues dans ``config/gold.yaml`` — jamais
écrites en dur : elles devront être révisées quand la performance réelle du
biais aura été mesurée, et une constante enfouie dans le code ne se révise
pas.

Deux composantes sont **contrariennes**, et c'est délibéré :

* l'**écart de juste valeur** — une prime déjà tendue n'est pas un signal
  d'achat, c'est un risque : le mouvement a déjà eu lieu ;
* le **positionnement COT** — quand tout le monde est déjà acheteur, il ne
  reste plus d'acheteur marginal pour faire monter le prix.

Les quatre autres sont directionnelles : taux réels, dollar, intensité
géopolitique, confirmation par les minières.

Pourquoi le détail par composante est obligatoire
-------------------------------------------------
Un score global sans décomposition ne s'améliore pas : quand il se trompe,
on ne sait pas laquelle des six composantes a fauté, donc on ne sait pas
quoi corriger. Le détail permet aussi de repérer le cas dangereux où un
score modéré recouvre deux composantes fortes et opposées — situation qui
n'a rien à voir avec six composantes tièdes et concordantes, alors que le
score composite est le même.

Renormalisation des poids
-------------------------
Quand une composante est indisponible, son poids est **retiré du
dénominateur** au lieu d'être compté comme un zéro. Un zéro serait un avis
neutre exprimé par une source muette : il tirerait mécaniquement le score
vers le centre et ferait passer une absence d'information pour un signal
d'équilibre. Le taux de couverture est publié à part, et une couverture
faible dégrade la conviction sans falsifier le score.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import numpy as np

_LOG: Final = logging.getLogger(__name__)

#: Ordre de lecture des composantes.
COMPOSANTES: Final[tuple[str, ...]] = (
    "ecart_juste_valeur",
    "positionnement_cot",
    "dynamique_taux_reels",
    "tendance_dollar",
    "intensite_geopolitique",
    "confirmation_minieres",
)

#: Z-score de juste valeur qui sature la composante à -1 ou +1.
Z_SATURATION: Final[float] = 2.0

#: Variation de taux réel, en points de pourcentage sur 20 séances, qui
#: sature la composante. Vingt points de base en un mois est un mouvement
#: franc sur le 10 ans réel.
DELTA_TAUX_SATURATION: Final[float] = 0.20

#: Variation du dollar, en pourcentage sur 20 séances, qui sature.
DELTA_DOLLAR_SATURATION: Final[float] = 2.0

#: Variation du ratio minières/or, en pourcentage sur 20 séances, qui sature.
DELTA_MINIERES_SATURATION: Final[float] = 8.0

#: Excès d'intensité géopolitique qui sature la composante. Le ratio GDELT
#: vaut 1 quand la couverture est normale : un ratio de 2, soit deux fois la
#: normale, est donc l'écart qui porte le score à son maximum.
EXCES_INTENSITE_SATURATION: Final[float] = 1.0

__all__ = ["Composante", "calculer_biais", "enregistrer_biais"]


@dataclass(slots=True, frozen=True)
class Composante:
    """Score d'une composante du biais.

    Attributes:
        nom: identifiant de la composante.
        score: score dans ``[-1, +1]``. ``None`` si indisponible.
        poids_configure: poids brut lu dans la configuration.
        poids_effectif: poids après renormalisation sur les seules
            composantes disponibles.
        contribution: ``score × poids_effectif``, part réelle dans le
            composite.
        valeur_source: valeur brute d'où le score est tiré.
        sens: ``contrarien`` ou ``directionnel``.
        commentaire: lecture en français.
        disponible: ``False`` si la donnée manque.
        motif: raison de l'indisponibilité, vide sinon.
    """

    nom: str
    score: float | None = None
    poids_configure: float = 0.0
    poids_effectif: float = 0.0
    contribution: float = 0.0
    valeur_source: float | None = None
    sens: str = "directionnel"
    commentaire: str = ""
    disponible: bool = False
    motif: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Sérialise la composante pour le rapport JSON."""
        return {
            "nom": self.nom,
            "disponible": self.disponible,
            "motif": self.motif,
            "score": None if self.score is None else round(self.score, 4),
            "poids_configure": round(self.poids_configure, 4),
            "poids_effectif": round(self.poids_effectif, 4),
            "contribution": round(self.contribution, 4),
            "valeur_source": self.valeur_source,
            "sens": self.sens,
            "commentaire": self.commentaire,
        }


def _borner(valeur: float) -> float:
    """Ramène un score dans ``[-1, +1]``.

    Args:
        valeur: score brut.

    Returns:
        Le score borné.
    """
    return float(np.clip(valeur, -1.0, 1.0))


# ---------------------------------------------------------------------------
# Les six composantes
# ---------------------------------------------------------------------------
def _score_juste_valeur(fair_value: dict[str, Any] | None) -> tuple[float | None, float | None, str, str]:
    """Score contrarien tiré de l'écart de juste valeur.

    Args:
        fair_value: bloc de juste valeur sérialisé.

    Returns:
        Quadruplet ``(score, valeur_source, commentaire, motif)``.
    """
    if not fair_value or not fair_value.get("disponible"):
        return None, None, "", (fair_value or {}).get("motif") or "juste valeur indisponible"
    if not fair_value.get("fiable"):
        r2 = fair_value.get("r2")
        return None, fair_value.get("z_score"), "", (
            f"modèle non fiable (R² = {r2:.2f})" if isinstance(r2, (int, float))
            else "modèle de juste valeur non fiable"
        )

    z = fair_value.get("z_score")
    if z is None:
        return None, None, "", "z-score absent"

    # Contrarien : une prime tendue est un risque, pas une invitation.
    score = _borner(-float(z) / Z_SATURATION)
    if z >= Z_SATURATION:
        commentaire = (
            f"L'or paie une prime de {z:+.2f} écart-type au-dessus de ce que justifient "
            "taux réels et dollar. À ce niveau, la mauvaise nouvelle est déjà payée et "
            "le risque est asymétrique à la baisse."
        )
    elif z <= -Z_SATURATION:
        commentaire = (
            f"L'or cote {z:+.2f} écart-type sous sa juste valeur : le modèle le dit "
            "anormalement bon marché au regard des taux réels et du dollar."
        )
    else:
        commentaire = (
            f"Écart de {z:+.2f} écart-type à la juste valeur : prime ordinaire, "
            "pas de tension particulière."
        )
    return score, float(z), commentaire, ""


def _score_cot(cot: dict[str, Any] | None) -> tuple[float | None, float | None, str, str]:
    """Score contrarien tiré du positionnement des managed money.

    Args:
        cot: bloc de positionnement sérialisé.

    Returns:
        Quadruplet ``(score, valeur_source, commentaire, motif)``.
    """
    if not cot or not cot.get("disponible"):
        return None, None, "", (cot or {}).get("motif") or "positionnement CFTC indisponible"

    percentile = cot.get("percentile_managed_money")
    if percentile is None:
        return None, None, "", "percentile COT non calculé, historique insuffisant"

    # Contrarien : 100e percentile → -1, 0e percentile → +1.
    score = _borner(-(float(percentile) - 50.0) / 50.0)
    age = cot.get("age_jours")
    mention_age = f" Donnée vieille de {age} jour(s)." if age is not None else ""

    if percentile >= 85.0:
        commentaire = (
            f"Positionnement spéculatif au {percentile:.0f}e percentile : le consensus "
            f"acheteur est en place, il reste peu d'acheteurs marginaux.{mention_age}"
        )
    elif percentile <= 15.0:
        commentaire = (
            f"Positionnement spéculatif au {percentile:.0f}e percentile : le marché a "
            f"déjà vendu, un rachat de découvert peut alimenter une hausse.{mention_age}"
        )
    else:
        commentaire = f"Positionnement spéculatif au {percentile:.0f}e percentile, sans excès.{mention_age}"
    return score, float(percentile), commentaire, ""


def _score_taux_reels(delta_taux: float | None) -> tuple[float | None, float | None, str, str]:
    """Score directionnel tiré de la dynamique des taux réels.

    Args:
        delta_taux: variation du taux réel 10 ans sur 20 séances, en points
            de pourcentage.

    Returns:
        Quadruplet ``(score, valeur_source, commentaire, motif)``.
    """
    if delta_taux is None:
        return None, None, "", "série DFII10 indisponible ou trop courte"

    # Une baisse des taux réels réduit le coût de portage : favorable à l'or.
    score = _borner(-float(delta_taux) / DELTA_TAUX_SATURATION)
    points_de_base = float(delta_taux) * 100.0
    sens = "baissent" if delta_taux < 0 else ("montent" if delta_taux > 0 else "sont stables")
    effet = "réduit" if delta_taux < 0 else ("alourdit" if delta_taux > 0 else "laisse inchangé")
    commentaire = (
        f"Les taux réels {sens} de {abs(points_de_base):.0f} points de base sur 20 séances, "
        f"ce qui {effet} le coût de détention de l'or."
    )
    return score, float(delta_taux), commentaire, ""


def _score_dollar(delta_dollar: float | None) -> tuple[float | None, float | None, str, str]:
    """Score directionnel tiré de la tendance du dollar.

    Args:
        delta_dollar: variation de l'indice large du dollar sur 20 séances,
            en pourcentage.

    Returns:
        Quadruplet ``(score, valeur_source, commentaire, motif)``.
    """
    if delta_dollar is None:
        return None, None, "", "série DTWEXBGS indisponible ou trop courte"

    # L'or est coté en dollars : un dollar qui s'affaiblit le soutient.
    score = _borner(-float(delta_dollar) / DELTA_DOLLAR_SATURATION)
    sens = "s'affaiblit" if delta_dollar < 0 else ("se renforce" if delta_dollar > 0 else "est stable")
    effet = "soutient" if delta_dollar < 0 else ("pèse sur" if delta_dollar > 0 else "n'influence pas")
    commentaire = (
        f"Le dollar {sens} de {abs(float(delta_dollar)):.1f} % sur 20 séances, "
        f"ce qui {effet} l'or."
    )
    return score, float(delta_dollar), commentaire, ""


def _score_geopolitique(geo: dict[str, Any] | None) -> tuple[float | None, float | None, str, str]:
    """Score directionnel tiré de l'intensité géopolitique, amorti si déjà payée.

    L'amortissement est le cœur de la thèse du système : une intensité forte
    dont la prime est *déjà* dans le prix n'est plus un signal haussier. Le
    score est alors divisé par deux, puis son signe est inversé si le thème
    est installé — parce que ce qui reste à jouer, c'est la détente.

    Args:
        geo: bloc géopolitique sérialisé.

    Returns:
        Quadruplet ``(score, valeur_source, commentaire, motif)``.
    """
    if not geo or not geo.get("disponible"):
        return None, None, "", (geo or {}).get("motif") or "GDELT indisponible"

    intensite = geo.get("intensite_max")
    if intensite is None:
        return None, None, "", "aucune intensité mesurée"

    # Ratio 1 = normale, 2 = deux fois la normale. On centre sur 1.
    brut = _borner((float(intensite) - 1.0) / EXCES_INTENSITE_SATURATION)
    deja = (geo.get("deja_dans_les_prix") or {}).get("valeur")

    if deja is True:
        # Déjà payé : le signal haussier s'efface et s'inverse.
        score = _borner(-abs(brut) * 0.5)
        commentaire = (
            f"Couverture géopolitique à {float(intensite):.1f}× la normale, mais la prime "
            "est déjà dans le prix : le potentiel restant est celui d'une détente, "
            "pas d'une escalade."
        )
    else:
        score = brut
        commentaire = (
            f"Couverture géopolitique à {float(intensite):.1f}× la normale"
            + (
                ", prime pas encore intégrée : soutien possible."
                if brut > 0
                else ", rien d'anormal."
            )
        )
    return score, float(intensite), commentaire, ""


def _score_minieres(ratio_gdx: dict[str, Any] | None) -> tuple[float | None, float | None, str, str]:
    """Score directionnel tiré du ratio minières / or.

    Args:
        ratio_gdx: indicateur ``ratio_gdx_or`` sérialisé.

    Returns:
        Quadruplet ``(score, valeur_source, commentaire, motif)``.
    """
    if not ratio_gdx or not ratio_gdx.get("disponible"):
        return None, None, "", (ratio_gdx or {}).get("motif") or "ratio minières/or indisponible"

    variation = ratio_gdx.get("variation_pct")
    if variation is None:
        return None, None, "", "variation du ratio minières/or non calculable"

    score = _borner(float(variation) / DELTA_MINIERES_SATURATION)
    if variation > 0:
        commentaire = (
            f"Les minières surperforment l'or de {float(variation):.1f} % sur 20 séances : "
            "elles confirment le mouvement du métal."
        )
    elif variation < 0:
        commentaire = (
            f"Les minières sous-performent l'or de {abs(float(variation)):.1f} % sur "
            "20 séances : le levier ne suit pas, le marché doute de la durabilité du mouvement."
        )
    else:
        commentaire = "Les minières évoluent au même rythme que l'or : aucune information."
    return score, float(variation), commentaire, ""


# ---------------------------------------------------------------------------
# Agrégation
# ---------------------------------------------------------------------------
def _invalidations(
    fair_value: dict[str, Any] | None,
    cot: dict[str, Any] | None,
    biais: str,
    delta_taux: float | None,
) -> list[dict[str, Any]]:
    """Exprime les conditions qui rendraient le biais caduc.

    Les conditions sont données en **niveaux**, pas en intentions : un seuil
    que l'on peut poser sur un graphique et vérifier soi-même.

    Args:
        fair_value: bloc de juste valeur.
        cot: bloc de positionnement.
        biais: sens du biais retenu.
        delta_taux: variation des taux réels sur 20 séances.

    Returns:
        Liste de conditions, chacune avec son seuil et sa lecture.
    """
    conditions: list[dict[str, Any]] = []

    if fair_value and fair_value.get("disponible") and fair_value.get("fiable"):
        z = fair_value.get("z_score")
        prix_theorique = fair_value.get("prix_theorique")
        prix = fair_value.get("prix_observe")
        if z is not None and prix is not None and prix_theorique is not None:
            # Le prix qui ramènerait le z-score à zéro est le prix théorique.
            conditions.append(
                {
                    "variable": "prix de l'or",
                    "seuil": round(float(prix_theorique), 2),
                    "operateur": "retour vers",
                    "lecture": (
                        f"La prime disparaît si l'or revient vers {float(prix_theorique):,.0f} $, "
                        f"son prix théorique. Écart actuel : {float(prix) - float(prix_theorique):+,.0f} $."
                    ),
                }
            )
        r2 = fair_value.get("r2")
        seuil_r2 = fair_value.get("seuil_r2")
        if r2 is not None and seuil_r2 is not None:
            conditions.append(
                {
                    "variable": "R² du modèle de juste valeur",
                    "seuil": float(seuil_r2),
                    "operateur": "<",
                    "lecture": (
                        f"Tout le raisonnement tombe si le R² passe sous {float(seuil_r2):.2f} "
                        f"(actuellement {float(r2):.2f}) : le modèle n'expliquerait plus l'or."
                    ),
                }
            )

    if cot and cot.get("disponible") and cot.get("percentile_managed_money") is not None:
        percentile = float(cot["percentile_managed_money"])
        cible = 50.0
        conditions.append(
            {
                "variable": "percentile COT managed money",
                "seuil": cible,
                "operateur": "traverse",
                "lecture": (
                    f"Le positionnement cesse d'être un argument si le percentile "
                    f"revient vers {cible:.0f} (actuellement {percentile:.0f})."
                ),
            }
        )

    if delta_taux is not None:
        sens = "remontent" if biais == "haussier" else "rebaissent"
        conditions.append(
            {
                "variable": "taux réel 10 ans (DFII10), variation 20 séances",
                "seuil": 0.0,
                "operateur": "change de signe",
                "lecture": (
                    f"Le moteur principal s'inverse si les taux réels {sens} "
                    f"(variation actuelle : {float(delta_taux) * 100:+.0f} points de base)."
                ),
            }
        )

    return conditions


def calculer_biais(
    configuration: dict[str, Any],
    fair_value: dict[str, Any] | None = None,
    cot: dict[str, Any] | None = None,
    geopolitique: dict[str, Any] | None = None,
    flux: dict[str, Any] | None = None,
    delta_taux_reels: float | None = None,
    delta_dollar: float | None = None,
    analogues: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine les six composantes en un biais quotidien.

    Args:
        configuration: bloc ``biais`` de ``config/gold.yaml``.
        fair_value: bloc de juste valeur sérialisé.
        cot: bloc de positionnement sérialisé.
        geopolitique: bloc géopolitique sérialisé.
        flux: dictionnaire des indicateurs de flux sérialisés.
        delta_taux_reels: variation du taux réel 10 ans sur 20 séances, en
            points de pourcentage.
        delta_dollar: variation de l'indice dollar sur 20 séances, en %.
        analogues: bloc des précédents historiques, joint à la sortie pour
            information mais **non intégré au score** : les précédents
            décrivent ce qui a suivi des configurations semblables, ils ne
            sont pas une septième opinion sur la configuration elle-même.

    Returns:
        Dictionnaire prêt à être sérialisé.
    """
    poids_config = dict(configuration.get("ponderations") or {})
    manquants = [c for c in COMPOSANTES if c not in poids_config]
    if manquants:
        _LOG.warning(
            "Pondération absente de la configuration pour : %s. Poids nul appliqué.",
            ", ".join(manquants),
        )

    ratio_gdx = (flux or {}).get("ratio_gdx_or")

    calculs = {
        "ecart_juste_valeur": (_score_juste_valeur(fair_value), "contrarien"),
        "positionnement_cot": (_score_cot(cot), "contrarien"),
        "dynamique_taux_reels": (_score_taux_reels(delta_taux_reels), "directionnel"),
        "tendance_dollar": (_score_dollar(delta_dollar), "directionnel"),
        "intensite_geopolitique": (_score_geopolitique(geopolitique), "directionnel"),
        "confirmation_minieres": (_score_minieres(ratio_gdx), "directionnel"),
    }

    # --- Renormalisation sur les seules composantes disponibles ------------
    poids_total_configure = sum(float(poids_config.get(c, 0.0)) for c in COMPOSANTES)
    poids_disponibles = sum(
        float(poids_config.get(nom, 0.0))
        for nom, ((score, _, _, _), _) in calculs.items()
        if score is not None
    )
    couverture = (
        poids_disponibles / poids_total_configure if poids_total_configure > 0.0 else 0.0
    )

    composantes: list[Composante] = []
    score_composite = 0.0

    for nom in COMPOSANTES:
        (score, valeur, commentaire, motif), sens = calculs[nom]
        poids_configure = float(poids_config.get(nom, 0.0))

        if score is None or poids_disponibles <= 0.0:
            composantes.append(
                Composante(
                    nom=nom,
                    poids_configure=poids_configure,
                    valeur_source=valeur,
                    sens=sens,
                    disponible=False,
                    motif=motif or "composante indisponible",
                )
            )
            continue

        poids_effectif = poids_configure / poids_disponibles
        contribution = score * poids_effectif
        score_composite += contribution
        composantes.append(
            Composante(
                nom=nom,
                score=score,
                poids_configure=poids_configure,
                poids_effectif=poids_effectif,
                contribution=contribution,
                valeur_source=valeur,
                sens=sens,
                commentaire=commentaire,
                disponible=True,
            )
        )

    disponibles = [c for c in composantes if c.disponible]

    # --- Sens du biais -----------------------------------------------------
    seuil_haussier = float(configuration.get("seuil_haussier", 0.20))
    seuil_vendeur = float(configuration.get("seuil_vendeur", -0.20))
    if not disponibles:
        biais = "indeterminé"
    elif score_composite >= seuil_haussier:
        biais = "haussier"
    elif score_composite <= seuil_vendeur:
        biais = "vendeur"
    else:
        biais = "neutre"

    # --- Conviction --------------------------------------------------------
    # Trois facteurs se multiplient : l'amplitude du score, la part des
    # composantes réellement disponibles, et leur concordance. Un score fort
    # obtenu par deux composantes qui se contredisent violemment n'a pas la
    # même valeur qu'un score identique obtenu par six composantes alignées.
    if disponibles and score_composite != 0.0:
        poids_concordants = sum(
            c.poids_effectif for c in disponibles
            if c.score is not None and c.score * score_composite > 0.0
        )
        concordance = poids_concordants / sum(c.poids_effectif for c in disponibles)
    else:
        concordance = 0.0

    conviction_brute = abs(score_composite) * couverture * concordance
    if conviction_brute >= 0.35:
        conviction = "forte"
    elif conviction_brute >= 0.15:
        conviction = "moyenne"
    else:
        conviction = "faible"

    couverture_min = float(configuration.get("couverture_min_pour_conviction", 0.60))
    donnees_partielles = couverture < couverture_min
    if donnees_partielles:
        _LOG.warning(
            "Biais calculé sur données partielles : couverture %.0f %% (< %.0f %%).",
            couverture * 100.0,
            couverture_min * 100.0,
        )

    indisponibles = [c.nom for c in composantes if not c.disponible]

    return {
        "horodatage_utc": datetime.now(timezone.utc).isoformat(),
        "biais": biais,
        "score_composite": round(score_composite, 4),
        "conviction": conviction,
        "conviction_brute": round(conviction_brute, 4),
        "concordance": round(concordance, 4),
        "couverture_donnees": round(couverture, 4),
        "couverture_min_requise": couverture_min,
        "donnees_partielles": donnees_partielles,
        "avertissement": (
            f"Biais calculé sur données partielles : {len(indisponibles)} composante(s) "
            f"indisponible(s) ({', '.join(indisponibles)}). La couverture est de "
            f"{couverture:.0%}, sous le seuil de {couverture_min:.0%}."
            if donnees_partielles
            else ""
        ),
        "seuils": {"haussier": seuil_haussier, "vendeur": seuil_vendeur},
        "n_composantes_disponibles": len(disponibles),
        "n_composantes_totales": len(COMPOSANTES),
        "composantes_indisponibles": indisponibles,
        "composantes": [c.to_dict() for c in composantes],
        "invalidations": _invalidations(fair_value, cot, biais, delta_taux_reels),
        "precedents_historiques": (analogues or {}).get("agregation", {}),
    }


# ---------------------------------------------------------------------------
# Historique, pour l'auto-évaluation ultérieure
# ---------------------------------------------------------------------------
def enregistrer_biais(
    biais: dict[str, Any],
    chemin: str | Path,
    prix_or: float | None,
    date_rapport: str,
) -> bool:
    """Ajoute le biais du jour à l'historique d'auto-évaluation.

    Le format est le JSON par lignes : un objet par ligne, ajouté sans
    relire le fichier. Un rapport quotidien ne doit pas charger quinze ans
    d'historique en mémoire pour y ajouter une ligne.

    Le prix de l'or au moment du biais est enregistré avec lui : c'est ce qui
    permettra, dans un mois, de noter le biais à 1, 5 et 20 jours sans avoir
    à reconstituer quel prix il regardait. Les champs d'évaluation sont créés
    vides, prêts à être remplis par le module de notation à venir.

    Args:
        biais: sortie de :func:`calculer_biais`.
        chemin: fichier d'historique, au format JSONL.
        prix_or: prix de l'or à la date du rapport.
        date_rapport: date du rapport, au format ISO.

    Returns:
        ``True`` si la ligne a été écrite.
    """
    fichier = Path(chemin)
    ligne = {
        "date": date_rapport,
        "horodatage_utc": biais.get("horodatage_utc"),
        "biais": biais.get("biais"),
        "score_composite": biais.get("score_composite"),
        "conviction": biais.get("conviction"),
        "couverture_donnees": biais.get("couverture_donnees"),
        "donnees_partielles": biais.get("donnees_partielles"),
        "prix_or_au_moment_du_biais": prix_or,
        "contributions": {
            c["nom"]: c["contribution"] for c in biais.get("composantes", []) if c.get("disponible")
        },
        # À remplir plus tard par le module de notation : c'est ce qui
        # permettra au système de se noter lui-même.
        "evaluation": {"rendement_1j": None, "rendement_5j": None, "rendement_20j": None, "evalue_le": None},
    }

    try:
        fichier.parent.mkdir(parents=True, exist_ok=True)
        with fichier.open("a", encoding="utf-8") as flux:
            flux.write(json.dumps(ligne, ensure_ascii=False) + "\n")
    except OSError as exc:
        _LOG.warning("Historique des biais non écrit (%s) : %s", fichier, exc)
        return False

    _LOG.info("Biais du %s ajouté à %s.", date_rapport, fichier)
    return True
