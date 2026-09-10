"""Paragraphes de synthèse : ce que les chiffres du jour racontent ensemble.

Le problème que ce module résout
--------------------------------
Chaque rubrique du site aligne des métriques justes et documentées, sans
jamais dire ce qu'elles racontent *ensemble*. Un écart à la juste valeur de
+1,8 écart-type, une prime géopolitique installée depuis trois semaines et
une couverture médiatique qui s'essouffle sont trois faits ; leur mise bout
à bout est une lecture, et c'est elle qui manquait.

Ce que ce module n'est pas
--------------------------
Ce n'est pas un habillage rédactionnel écrit une fois pour toutes. Chaque
paragraphe est recomposé à chaque exécution à partir des champs déjà
calculés par les moteurs — jamais recalculés ici — et il ne contient que des
chiffres présents dans ces données. Deux garde-fous, tous deux déjà en place
ailleurs dans le projet, sont appliqués systématiquement :

* :func:`modules.gold.explain.verifier_nombres` — tout nombre du paragraphe
  doit exister dans les données de cette exécution ;
* :func:`modules.quantum.moves.verifier_absence_recommandation` — aucun
  paragraphe ne recommande d'acheter, de vendre ni de se positionner.

Un paragraphe qui échoue à l'un des deux n'est pas corrigé à la volée : il
est refusé, et le motif est publié à sa place. Un texte faux vaut moins que
pas de texte.

Ce que ce module ne dit jamais
------------------------------
Aucun jugement de valeur sur un actif ou une société — pas de « le plus
solide », pas de « le meilleur positionné ». Les faits sont exposés
(contrats, trésorerie, financements, chiffres de marché), la conclusion
appartient au lecteur. C'est la même ligne que le reste du projet tient
entre expliquer et conseiller.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from modules.gold.explain import verifier_nombres
from modules.quantum.moves import verifier_absence_recommandation

_LOG: Final = logging.getLogger(__name__)

__all__ = [
    "synthetiser_or",
    "synthetiser_geopolitique",
    "synthetiser_quantique",
    "synthetiser_crypto",
    "verifier_synthese",
]


def _fr(valeur: float, decimales: int = 2, signe: bool = False) -> str:
    """Formate un nombre à la française, virgule décimale comprise."""
    texte = f"{valeur:+.{decimales}f}" if signe else f"{valeur:.{decimales}f}"
    return texte.replace(".", ",")


def _lire(racine: Any, chemin: str, defaut: Any = None) -> Any:
    """Lit une valeur imbriquée sans jamais lever.

    Args:
        racine: structure source.
        chemin: chemin pointé, par exemple ``juste_valeur.z_score``.
        defaut: valeur rendue si le chemin n'existe pas.

    Returns:
        La valeur trouvée, ou ``defaut``.
    """
    courant = racine
    for cle in chemin.split("."):
        if not isinstance(courant, dict) or cle not in courant:
            return defaut
        courant = courant[cle]
    return defaut if courant is None else courant


def verifier_synthese(texte: str, donnees: Any) -> dict[str, Any]:
    """Soumet un paragraphe aux deux garde-fous du projet.

    Args:
        texte: paragraphe candidat.
        donnees: données de l'exécution, seule source de chiffres autorisée.

    Returns:
        ``{"texte", "publiable", "motif", "nombres_rejetes", "infractions"}``.
        ``publiable`` est ``False`` dès qu'un nombre est introuvable dans les
        données ou qu'une formulation de recommandation est détectée ; le
        texte n'est alors pas corrigé, il est écarté avec son motif.
    """
    if not texte.strip():
        return {
            "texte": "", "publiable": False, "motif": "aucune donnée exploitable ce jour",
            "nombres_rejetes": [], "infractions": [],
        }

    conforme, rejetes = verifier_nombres(texte, donnees)
    infractions = verifier_absence_recommandation({"synthese": texte})

    if not conforme:
        motif = (
            "paragraphe écarté : "
            + ", ".join(f"{n:g}" for n in rejetes[:5])
            + " sans correspondance dans les données du jour"
        )
        _LOG.error("Synthèse rejetée (nombres inventés) : %s", motif)
        return {
            "texte": "", "publiable": False, "motif": motif,
            "nombres_rejetes": rejetes, "infractions": infractions,
        }

    if infractions:
        _LOG.error("Synthèse rejetée (formulation de recommandation) : %s", infractions)
        return {
            "texte": "", "publiable": False,
            "motif": "paragraphe écarté : formulation de recommandation détectée",
            "nombres_rejetes": [], "infractions": infractions,
        }

    return {
        "texte": texte, "publiable": True, "motif": "",
        "nombres_rejetes": [], "infractions": [],
    }


# ---------------------------------------------------------------------------
# Or
# ---------------------------------------------------------------------------
def synthetiser_or(rapport: dict[str, Any]) -> dict[str, Any]:
    """Compose la synthèse de la rubrique Or.

    Relie l'écart à la juste valeur, ce que la géopolitique en explique déjà,
    et le climat macro — trois blocs que le rapport calcule séparément et que
    personne ne rapprochait.

    Args:
        rapport: contenu de ``reports/gold/latest.json``.

    Returns:
        Résultat de :func:`verifier_synthese`.
    """
    phrases: list[str] = []

    prix = _lire(rapport, "prix", {})
    if prix.get("disponible") and prix.get("variation_20j_pct") is not None:
        phrases.append(
            f"L'or a varié de {_fr(prix['variation_20j_pct'], 1, signe=True)} % "
            f"sur vingt séances et de {_fr(prix.get('variation_5j_pct') or 0.0, 1, signe=True)} % "
            "sur cinq."
        )

    jv = _lire(rapport, "juste_valeur", {})
    if jv.get("disponible") and jv.get("fiable") and jv.get("z_score") is not None:
        z = jv["z_score"]
        if z >= 1.5:
            lecture = (
                "il est statistiquement cher au regard des taux réels et du dollar, "
                "sans que cela indique une correction imminente"
            )
        elif z <= -1.5:
            lecture = "il est statistiquement bon marché au regard de ces mêmes facteurs"
        else:
            lecture = "il reste dans la zone que ces facteurs expliquent"
        phrases.append(
            f"Son écart à la juste valeur ressort à {_fr(z, 2, signe=True)} écart-type : {lecture}."
        )
    elif jv.get("disponible") and not jv.get("fiable"):
        phrases.append(
            f"L'écart à la juste valeur n'est pas interprétable ce jour : le modèle "
            f"n'explique que {_fr(jv.get('r2') or 0.0, 2)} de la variance, sous le seuil "
            f"de {_fr(jv.get('seuil_r2') or 0.0, 2)} exigé."
        )

    deja = _lire(rapport, "geopolitique.deja_dans_les_prix", {})
    if deja.get("disponible") and deja.get("z_score_prime") is not None:
        if deja.get("valeur") is True:
            phrases.append(
                f"La prime de risque géopolitique paraît déjà payée "
                f"({_fr(deja['z_score_prime'], 2, signe=True)} écart-type) : une tension "
                "supplémentaire ferait peu monter le prix, une détente le ferait retomber."
            )
        else:
            phrases.append(
                f"La prime de risque géopolitique n'apparaît pas encore payée "
                f"({_fr(deja['z_score_prime'], 2, signe=True)} écart-type de prime mesurée)."
            )

    intensite = _lire(rapport, "geopolitique.intensite_max")
    dominant = _lire(rapport, "geopolitique.dossier_dominant", "")
    if intensite is not None and dominant:
        nom = dominant
        for dossier in _lire(rapport, "geopolitique.dossiers", []) or []:
            if dossier.get("id") == dominant:
                nom = dossier.get("nom_affiche") or dominant
                traj = dossier.get("trajectoire") or ""
                break
        else:
            traj = ""
        suite = f", trajectoire {traj}" if traj else ""
        phrases.append(
            f"Le dossier géopolitique le plus actif est {nom}, à "
            f"{_fr(intensite, 1)}× sa couverture habituelle{suite}."
        )

    appetit = _lire(rapport, "contexte_macro.regime.axes.appetit_risque", {})
    if appetit.get("disponible") and appetit.get("valeur") is not None:
        phrases.append(
            f"Le climat de marché est {appetit.get('niveau', 'non qualifié')} "
            f"(score {_fr(appetit['valeur'], 2, signe=True)}), ce qui pèse sur la demande "
            "de valeur refuge dans un sens ou dans l'autre."
        )

    biais = _lire(rapport, "biais", {})
    if biais.get("score_composite") is not None:
        phrases.append(
            f"Au total, le biais mécanique ressort {biais.get('biais', 'indéterminé')} "
            f"(score {_fr(biais['score_composite'], 2, signe=True)}, conviction "
            f"{biais.get('conviction', 'inconnue')}), sur "
            f"{_fr((biais.get('couverture_donnees') or 0.0) * 100, 0)} % des composantes mesurées."
        )

    return verifier_synthese(" ".join(phrases), rapport)


# ---------------------------------------------------------------------------
# Géopolitique
# ---------------------------------------------------------------------------
def synthetiser_geopolitique(rapport: dict[str, Any]) -> dict[str, Any]:
    """Compose la synthèse de la rubrique Géopolitique.

    Args:
        rapport: contenu de ``reports/gold/latest.json`` (le bloc
            géopolitique y est embarqué).

    Returns:
        Résultat de :func:`verifier_synthese`.
    """
    phrases: list[str] = []
    geo = _lire(rapport, "geopolitique", {})
    dossiers = [d for d in (geo.get("dossiers") or []) if d.get("disponible")]

    if not dossiers:
        motif = geo.get("motif") or "aucun dossier mesurable"
        return verifier_synthese("", rapport) | {"motif": f"synthèse impossible : {motif}"}

    ordonnes = sorted(dossiers, key=lambda d: d.get("intensite_ratio") or 0.0, reverse=True)
    tete = ordonnes[0]
    phrases.append(
        f"Sur les {len(geo.get('dossiers') or [])} dossiers suivis, "
        f"{tete.get('nom_affiche')} est le plus couvert, à "
        f"{_fr(tete.get('intensite_ratio') or 0.0, 1)}× sa moyenne des trente derniers jours "
        f"(trajectoire {tete.get('trajectoire') or 'non qualifiée'})."
    )

    accelere = [d for d in ordonnes if d.get("trajectoire") == "en accélération"]
    essouffle = [d for d in ordonnes if d.get("trajectoire") == "en essoufflement"]
    if accelere:
        phrases.append(
            f"{len(accelere)} dossier(s) voient leur couverture s'intensifier : "
            + ", ".join(d.get("nom_affiche", "") for d in accelere) + "."
        )
    if essouffle:
        phrases.append(
            f"{len(essouffle)} dossier(s) s'essoufflent : "
            + ", ".join(d.get("nom_affiche", "") for d in essouffle)
            + " — un sujet déjà digéré fait moins bouger les prix qu'un sujet naissant."
        )

    nouveaux = sum(int(d.get("n_nouveaux_developpements") or 0) for d in dossiers)
    if nouveaux:
        phrases.append(
            f"{nouveaux} développement(s) nouveau(x) depuis la dernière vérification."
        )
    else:
        phrases.append("Aucun développement nouveau depuis la dernière vérification.")

    rompues = [d for d in dossiers
               if _lire(d, "chaine_de_transmission.chaine_rompue") is True]
    completes = [d for d in dossiers
                 if _lire(d, "chaine_de_transmission.chaine_rompue") is False]
    if completes:
        phrases.append(
            f"Pour {len(completes)} dossier(s), la chaîne pétrole → inflation anticipée → "
            "taux réels → or est cohérente : le mouvement de l'or y a une explication "
            "vérifiable en amont."
        )
    if rompues:
        # Formulée pour tenir seule : la phrase sur les chaînes cohérentes
        # peut ne pas avoir été écrite, « elle » n'aurait alors pas
        # d'antécédent.
        phrases.append(
            f"Pour {len(rompues)} dossier(s), cette chaîne est rompue : la tension ne se "
            "transmet pas par les canaux mesurés."
        )

    return verifier_synthese(" ".join(phrases), rapport)


# ---------------------------------------------------------------------------
# Quantique
# ---------------------------------------------------------------------------
def synthetiser_quantique(rapport: dict[str, Any]) -> dict[str, Any]:
    """Compose la synthèse de la rubrique Quantique.

    N'expose que des faits observables — mouvements mesurés, corrélation,
    trésorerie, activité d'initiés quand elle est publiée. Aucun jugement
    comparatif entre les trois valeurs suivies : ce serait franchir la ligne
    entre expliquer et conseiller.

    Args:
        rapport: contenu de ``reports/quantum/latest.json``.

    Returns:
        Résultat de :func:`verifier_synthese`.
    """
    phrases: list[str] = []
    mouvements = _lire(rapport, "mouvements", {})
    if not mouvements:
        return verifier_synthese("", rapport) | {
            "motif": "synthèse impossible : aucun relevé de mouvements ce jour",
        }
    n = int(mouvements.get("n_mouvements") or 0)

    if n:
        details = ", ".join(
            f"{m.get('ticker')} {_fr(m.get('variation_pct') or 0.0, 1, signe=True)} %"
            for m in (mouvements.get("mouvements") or [])[:3]
        )
        phrases.append(
            f"{n} valeur(s) suivie(s) dépassent leur seuil de mouvement du jour : {details}."
        )
        sectoriels = [m for m in (mouvements.get("mouvements") or [])
                      if m.get("classification") == "sectoriel"]
        if sectoriels:
            phrases.append(
                f"{len(sectoriels)} de ces mouvements sont classés sectoriels : d'autres "
                "valeurs du secteur bougent dans le même sens avec une amplitude comparable, "
                "le mouvement n'est donc pas propre à la société."
            )
    else:
        phrases.append(
            "Aucune des valeurs suivies ne dépasse son seuil de mouvement du jour."
        )

    correlation = _lire(rapport, "secteur.correlation_positions", {})
    if correlation.get("disponible") and correlation.get("correlation_max") is not None:
        phrases.append(
            f"La corrélation la plus forte entre deux d'entre elles atteint "
            f"{_fr(correlation['correlation_max'], 2)} sur "
            f"{correlation.get('n_seances_effectives', 0)} séances : au-delà du seuil de forte "
            "corrélation, "
            "les détenir ensemble revient largement à détenir la même position."
        )

    tresorerie = _lire(rapport, "tresorerie", {})
    runways = [
        (cle, bloc.get("runway_trimestres"))
        for cle, bloc in (tresorerie.items() if isinstance(tresorerie, dict) else [])
        if isinstance(bloc, dict) and bloc.get("runway_trimestres") is not None
    ]
    if runways:
        details = ", ".join(f"{t} {_fr(v, 1)} trimestre(s)" for t, v in runways[:3])
        phrases.append(
            f"Trésorerie estimée en trimestres d'activité au rythme actuel : {details}. "
            "Ces sociétés se refinancent par émission d'actions : un horizon court "
            "annonce une dilution, pas une faillite."
        )

    industrie = _lire(rapport, "industrie", {})
    if industrie.get("n_articles") is not None:
        phrases.append(
            f"L'actualité du secteur compte {industrie['n_articles']} article(s) "
            "sur les financements et contrats de la période."
        )

    return verifier_synthese(" ".join(phrases), rapport)


# ---------------------------------------------------------------------------
# Crypto
# ---------------------------------------------------------------------------
def synthetiser_crypto(rapport: dict[str, Any]) -> dict[str, Any]:
    """Compose la synthèse de la rubrique Crypto.

    Args:
        rapport: contenu de ``reports/crypto/latest.json``.

    Returns:
        Résultat de :func:`verifier_synthese`.
    """
    phrases: list[str] = []

    regimes = []
    for cle, nom in (("regime.regime_btc", "le bitcoin"), ("regime.regime_eth", "l'ether")):
        bloc = _lire(rapport, cle, {})
        if bloc.get("disponible") and bloc.get("mvrv") is not None:
            regimes.append(
                f"{nom} en régime {bloc.get('regime', 'indéterminé')} "
                f"(MVRV {_fr(bloc['mvrv'], 2)})"
            )
    if regimes:
        # Le MVRV n'est explicité qu'une fois : le répéter par actif alourdit
        # sans rien apprendre de plus.
        phrases.append(
            "Côté régimes, " + " et ".join(regimes) + " — le MVRV rapporte la valeur de "
            "marché au prix moyen d'achat réel des détenteurs, et mesure donc une "
            "pression vendeuse potentielle, pas une prévision."
        )

    #: Les états de rotation sont des identifiants techniques : ils passent
    #: par ce dictionnaire avant d'entrer dans une phrase, jamais bruts.
    lisible = {
        "rotation_alts": "orientée vers les alternatives",
        "dominance_btc": "orientée vers le bitcoin",
        "indetermine": "indéterminée",
        "indéterminé": "indéterminée",
        "aucune": "sans direction mesurable",
    }
    rotation = _lire(rapport, "rotation.synthese", {})
    if rotation.get("etat"):
        votants = [c for c in (rotation.get("contributions") or []) if not c.get("abstention")]
        phrases.append(
            f"La rotation entre bitcoin et alternatives est "
            f"{lisible.get(rotation['etat'], rotation['etat'])} sur "
            f"{len(votants)} mesure(s) exploitable(s)."
        )

    positions = _lire(rapport, "positionnement.positions.positions", []) or []
    mesurees = [p for p in positions if p.get("variation_24h_pct") is not None]
    if mesurees:
        hausse = [p for p in mesurees if (p.get("variation_24h_pct") or 0) > 0]
        phrases.append(
            f"Sur les {len(mesurees)} jetons suivis dont le prix est mesuré, "
            f"{len(hausse)} progressent sur 24 heures."
        )

    funding = _lire(rapport, "positionnement.funding", {})
    if funding.get("disponible") and funding.get("percentile") is not None:
        phrases.append(
            f"Le financement des positions à effet de levier se situe au "
            f"{_fr(funding['percentile'], 0)}e percentile de son historique récent : dans le haut de cette échelle, "
            "le levier est tendu d'un côté du marché."
        )

    return verifier_synthese(" ".join(phrases), rapport)
