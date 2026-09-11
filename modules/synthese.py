"""Paragraphes de synthèse : ce que les chiffres du jour racontent ensemble.

Ce que ce module produit
------------------------
Un récit par rubrique — or, géopolitique, quantique, crypto — et non une
liste de métriques mises bout à bout. Trois règles de rédaction, appliquées
à chaque phrase :

1. **Chaque chiffre porte sa conséquence.** Un percentile, un écart-type ou
   une variation n'apparaissent jamais seuls : la même phrase (ou la
   suivante) dit ce que cela implique pour le marché et pour le prix de
   l'actif. Définir un indicateur n'apprend rien ; dire ce qu'il change,
   si.
2. **Les faits s'enchaînent par causalité.** Un climat d'aversion au risque
   *soutient* la demande de valeur refuge ; des taux réels en hausse
   *renchérissent* la détention d'un actif sans rendement ; un
   positionnement spéculatif encombré *limite* la place pour de nouveaux
   acheteurs. Le lien est écrit, pas laissé au lecteur.
3. **Le récit a de la profondeur.** L'archive des rapports ne remonte qu'à
   quelques jours ; les séries sous-jacentes remontent à des années. Le
   texte situe donc la position du jour dans une trajectoire de plusieurs
   mois, à partir de ces séries (voir ``dataio.macro.recul_historique`` et
   les horizons de prix de ``modules.gold.run``), jamais à partir d'un
   événement que les données ne montreraient pas.

Le facteur commun
-----------------
Une même donnée macro affecte les trois actifs en même temps, mais pas dans
le même sens : un regain d'appétit pour le risque pèse sur l'or (moins de
demande refuge) et soutient le quantique et la crypto (appétit pour les
actifs spéculatifs). :func:`facteur_commun` extrait cette lecture du rapport
or ; chaque rubrique la reprend et en tire *sa* conséquence.

Les deux garde-fous
-------------------
Inchangés : tout nombre du paragraphe doit exister dans les données de
l'exécution (:func:`modules.gold.explain.verifier_nombres`), et aucune
formulation de recommandation n'est tolérée
(:func:`modules.quantum.moves.verifier_absence_recommandation`). Un
paragraphe qui échoue n'est pas rafistolé : il est refusé, et son motif est
publié à sa place.

Jamais vide
-----------
Une rubrique ne reste jamais sans synthèse. Quand l'indicateur principal
est inexploitable, il est mentionné en une incise et le récit continue avec
ce qui reste : trajectoire de prix, appétit pour le risque, liquidité,
recul historique. Le seul cas où rien n'est publié est un rapport sans
aucune donnée — et alors le motif le dit.

Ce que ce module ne dit jamais
------------------------------
Aucun jugement de valeur sur un actif ou une société — pas de « le plus
solide », pas de « le mieux placé ». Les faits sont exposés, la conclusion
appartient au lecteur.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Final

from modules.gold.explain import verifier_nombres
from modules.quantum.moves import verifier_absence_recommandation

_LOG: Final = logging.getLogger(__name__)

#: Rapport or publié, source du facteur commun pour les autres rubriques.
RAPPORT_OR_DEFAUT: Final = Path(__file__).resolve().parents[1] / "reports" / "gold" / "latest.json"

#: Tournures qui font d'un chiffre une conséquence et non un simple relevé.
#: Un chiffre dont ni la phrase ni la suivante ne contient l'une d'elles est
#: un tableau déguisé en prose — c'est ce que les tests vérifient.
MARQUEURS_CONSEQUENCE: Final[tuple[str, ...]] = (
    "ce qui", "donc", "d'où", "par conséquent", "de sorte que", "autrement dit",
    "soutient", "soutenu", "pèse", "pesé", "limite", "laisse", "réduit", "augmente",
    "favorise", "freine", "fragilise", "renforce", "implique", "signifie", "explique",
    "traduit", "pousse", "attire", "détourne", "renchérit", "allège", "entretient",
    "alimente", "assèche", "nourrit", "protège", "expose", "cantonne", "confirme",
    "relativise", "encombre", "reste peu de place", "marge", "sans excès",
    "pas de levier", "apporte peu", "retomber", "profits lointains", "pression vendeuse",
    "appétit", "sécurité", "refuge", "rotation", "digéré", "escalade", "malgré",
    "cohérent", "va dans le sens", "coexiste", "respiration", "en avance", "rattrapé",
    "prolonge", "à l'intérieur",
)

#: Libellés des composantes du biais or : les identifiants techniques ne
#: s'écrivent jamais tels quels dans une phrase.
_LIBELLE_COMPOSANTE: Final[dict[str, str]] = {
    "ecart_juste_valeur": "l'écart à la juste valeur",
    "positionnement_cot": "le positionnement spéculatif",
    "dynamique_taux_reels": "la dynamique des taux réels",
    "tendance_dollar": "la tendance du dollar",
    "intensite_geopolitique": "l'intensité géopolitique",
    "confirmation_minieres": "la confirmation par les minières",
}

#: Petits nombres en toutes lettres. Un compte calculé ici (« trois dossiers »,
#: « sept jetons ») n'existe pas forcément tel quel dans les données ; l'écrire
#: en chiffres le ferait rejeter par le vérificateur — à raison, puisque ce
#: dernier ne saurait pas d'où il sort. Les valeurs mesurées, elles, restent
#: en chiffres.
_LETTRES: Final[tuple[str, ...]] = (
    "aucun", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix", "onze", "douze",
)

__all__ = [
    "MARQUEURS_CONSEQUENCE",
    "charger_rapport_or",
    "facteur_commun",
    "phrases_sans_consequence",
    "synthetiser_or",
    "synthetiser_geopolitique",
    "synthetiser_quantique",
    "synthetiser_crypto",
    "verifier_synthese",
]


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------
def _fr(valeur: float, decimales: int = 2, signe: bool = False) -> str:
    """Formate un nombre à la française : virgule décimale.

    Jamais plus de deux décimales : « 0,184 » serait lu comme un séparateur
    de milliers par le vérificateur numérique (voir explain._en_flottant).
    """
    decimales = min(decimales, 2)
    texte = f"{valeur:+.{decimales}f}" if signe else f"{valeur:.{decimales}f}"
    return texte.replace(".", ",")


def _lettres(n: int, feminin: bool = False) -> str:
    """Écrit un petit compte en toutes lettres (voir :data:`_LETTRES`)."""
    if not 0 <= n < len(_LETTRES):
        return str(n)
    mot = _LETTRES[n]
    if feminin and n == 0:
        return "aucune"
    if feminin and n == 1:
        return "une"
    return mot


def _pl(n: int, singulier: str, pluriel: str | None = None, feminin: bool = False) -> str:
    """« un dossier », « trois dossiers », « aucune valeur »."""
    pluriel = pluriel or singulier + "s"
    return f"{_lettres(n, feminin)} {singulier if n <= 1 else pluriel}"


def _liste(noms: list[str]) -> str:
    """« A », « A et B », « A, B et C »."""
    noms = [n for n in noms if n]
    if not noms:
        return ""
    if len(noms) == 1:
        return noms[0]
    return ", ".join(noms[:-1]) + " et " + noms[-1]


def _lire(racine: Any, chemin: str, defaut: Any = None) -> Any:
    """Lit une valeur imbriquée sans jamais lever."""
    courant = racine
    for cle in chemin.split("."):
        if not isinstance(courant, dict) or cle not in courant:
            return defaut
        courant = courant[cle]
    return defaut if courant is None else courant


def _phrases(texte: str) -> list[str]:
    """Découpe un texte en phrases non vides."""
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+", texte or "") if p.strip()]


def phrases_sans_consequence(texte: str) -> list[str]:
    """Repère les phrases chiffrées sans conséquence marché à proximité.

    Args:
        texte: paragraphe à contrôler.

    Returns:
        Les phrases contenant un chiffre dont ni elles-mêmes ni la phrase
        suivante ne portent une tournure de :data:`MARQUEURS_CONSEQUENCE`.
    """
    phrases = _phrases(texte)
    fautives: list[str] = []
    for i, phrase in enumerate(phrases):
        if not re.search(r"\d", phrase):
            continue
        voisinage = (phrase + " " + (phrases[i + 1] if i + 1 < len(phrases) else "")).lower()
        if not any(m in voisinage for m in MARQUEURS_CONSEQUENCE):
            fautives.append(phrase)
    return fautives


def verifier_synthese(texte: str, donnees: Any) -> dict[str, Any]:
    """Soumet un paragraphe aux garde-fous du projet.

    Args:
        texte: paragraphe candidat.
        donnees: données de l'exécution, seule source de chiffres autorisée.

    Returns:
        ``{"texte", "publiable", "motif", "nombres_rejetes", "infractions",
        "phrases_sans_consequence"}``. ``publiable`` tombe à ``False`` dès
        qu'un nombre est introuvable ou qu'une recommandation est détectée ;
        une phrase chiffrée sans conséquence est signalée sans bloquer —
        c'est un défaut de rédaction à corriger dans le générateur, pas une
        raison de priver le lecteur de tout le paragraphe.
    """
    if not texte.strip():
        return {
            "texte": "", "publiable": False, "motif": "aucune donnée exploitable ce jour",
            "nombres_rejetes": [], "infractions": [], "phrases_sans_consequence": [],
        }

    conforme, rejetes = verifier_nombres(texte, donnees)
    infractions = verifier_absence_recommandation({"synthese": texte})
    sans_consequence = phrases_sans_consequence(texte)
    if sans_consequence:
        _LOG.warning("Phrase(s) chiffrée(s) sans conséquence : %s", sans_consequence[:3])

    if not conforme:
        motif = (
            "paragraphe écarté : " + ", ".join(f"{n:g}" for n in rejetes[:5])
            + " sans correspondance dans les données du jour"
        )
        _LOG.error("Synthèse rejetée (nombres inventés) : %s", motif)
        return {
            "texte": "", "publiable": False, "motif": motif,
            "nombres_rejetes": rejetes, "infractions": infractions,
            "phrases_sans_consequence": sans_consequence,
        }
    if infractions:
        _LOG.error("Synthèse rejetée (formulation de recommandation) : %s", infractions)
        return {
            "texte": "", "publiable": False,
            "motif": "paragraphe écarté : formulation de recommandation détectée",
            "nombres_rejetes": [], "infractions": infractions,
            "phrases_sans_consequence": sans_consequence,
        }
    return {
        "texte": texte, "publiable": True, "motif": "",
        "nombres_rejetes": [], "infractions": [],
        "phrases_sans_consequence": sans_consequence,
    }


def _refus(rapport: Any, motif: str) -> dict[str, Any]:
    """Un refus explicite, avec son motif."""
    return verifier_synthese("", rapport) | {"motif": motif}


# ---------------------------------------------------------------------------
# Facteur commun aux trois actifs
# ---------------------------------------------------------------------------
def charger_rapport_or(chemin: Path | None = None) -> dict[str, Any]:
    """Relit le rapport or publié, sans jamais lever.

    Args:
        chemin: fichier JSON. ``None`` retient :data:`RAPPORT_OR_DEFAUT`.

    Returns:
        Le rapport, ou un dictionnaire vide s'il manque ou est illisible.
    """
    fichier = chemin or RAPPORT_OR_DEFAUT
    try:
        contenu = json.loads(fichier.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("Rapport or illisible (%s) : pas de facteur commun. %s", fichier, exc)
        return {}
    return contenu if isinstance(contenu, dict) else {}


def facteur_commun(rapport_or: dict[str, Any] | None) -> dict[str, Any]:
    """Extrait du rapport or la lecture macro partagée par les trois actifs.

    Chaque rubrique en tire sa propre conséquence : un même appétit pour le
    risque pèse sur l'or et soutient les actifs spéculatifs. Les valeurs
    sont arrondies à deux décimales pour être citables telles quelles.

    Args:
        rapport_or: contenu de ``reports/gold/latest.json``.

    Returns:
        ``{"disponible", "date", "appetit", "inflation", "liquidite", "credit",
        "taux_reels", "vix"}`` — chaque bloc vaut ``None`` s'il n'est pas
        mesuré.
    """
    axes = _lire(rapport_or or {}, "contexte_macro.regime.axes", {}) or {}
    recul = _lire(rapport_or or {}, "contexte_macro.recul", {}) or {}

    def _axe(nom: str) -> dict[str, Any] | None:
        bloc = axes.get(nom) or {}
        if not bloc.get("disponible") or bloc.get("valeur") is None:
            return None
        return {"niveau": str(bloc.get("niveau", "")), "valeur": round(float(bloc["valeur"]), 2)}

    appetit = _axe("appetit_risque")
    inflation = _axe("inflation")
    liquidite = _axe("liquidite_nette")
    credit = _axe("stress_credit")

    taux = recul.get("taux_reels") if isinstance(recul, dict) else None
    vix = recul.get("vix") if isinstance(recul, dict) else None

    return {
        "disponible": any(x is not None for x in (appetit, inflation, liquidite, credit)),
        "date": _lire(rapport_or or {}, "meta.date"),
        "appetit": appetit,
        "inflation": inflation,
        "liquidite": liquidite,
        "credit": credit,
        "taux_reels": taux if isinstance(taux, dict) else None,
        "vix": vix if isinstance(vix, dict) else None,
    }


def _phrase_appetit(facteur: dict[str, Any] | None, actif: str) -> str | None:
    """Rédige l'effet du climat de risque pour une rubrique donnée.

    Le même fait, quatre conséquences, et chaque rubrique cite les autres :
    c'est le lien entre elles, écrit noir sur blanc.

    Args:
        facteur: sortie de :func:`facteur_commun`.
        actif: ``or``, ``geopolitique``, ``quantique`` ou ``crypto``.

    Returns:
        La phrase, ou ``None`` si l'appétit n'est pas mesuré.
    """
    appetit = (facteur or {}).get("appetit")
    if not appetit:
        return None
    niveau, score = appetit["niveau"], appetit["valeur"]
    if niveau not in ("risk-on", "risk-off"):
        niveau = "neutre"
    score_txt = f"(score d'appétit pour le risque {_fr(score, 2, signe=True)})"

    if actif == "or":
        effets = {
            "risk-off": "les investisseurs cherchent la sécurité, ce qui soutient la demande de valeur refuge et donne à l'or un flux acheteur qu'il n'a pas à mériter par ses propres fondamentaux",
            "risk-on": "les investisseurs cherchent le rendement, ce qui détourne des flux de la valeur refuge — l'or doit alors tenir sur ses seuls fondamentaux",
            "neutre": "ni fuite vers la sécurité ni course au rendement, ce qui laisse l'or sans flux de rotation net et rend ses propres facteurs décisifs",
        }
        return f"Le climat de marché mesuré dans le contexte macro est {niveau} {score_txt} : {effets[niveau]}."

    if actif == "geopolitique":
        effets = {
            "risk-off": "les investisseurs cherchent la sécurité, ce qui soutient la demande de valeur refuge et rend l'or plus réactif à toute escalade",
            "risk-on": "les investisseurs cherchent le rendement, ce qui détourne des flux de la valeur refuge et rend une prime géopolitique plus difficile à installer dans le prix de l'or",
            "neutre": "ni fuite vers la sécurité ni course au rendement, ce qui laisse à la géopolitique seule le soin de déplacer la demande refuge",
        }
        return f"Ce tableau se lit dans un climat de marché {niveau} {score_txt} : {effets[niveau]}."

    if actif == "quantique":
        effets = {
            "risk-off": "les investisseurs se replient, ce qui fragilise d'abord les valeurs à profits lointains — le quantique en est l'archétype",
            "risk-on": "les investisseurs acceptent le risque, ce qui soutient les valeurs spéculatives à profits lointains, quantique compris, indépendamment de leurs résultats",
            "neutre": "aucun flux de rotation ne pousse ni ne freine les valeurs spéculatives, ce qui laisse chaque titre à ses propres nouvelles",
        }
        amorce = (
            "Le climat de marché relevé pour l'or joue ici en sens inverse"
            if niveau != "neutre" else "Le climat de marché relevé pour l'or vaut ici aussi"
        )
        return f"{amorce} : {niveau} {score_txt}, {effets[niveau]}."

    # crypto
    liens = {
        "risk-off": "qui apporte des flux à l'or et en retire au quantique",
        "risk-on": "qui retire des flux à l'or et en apporte au quantique",
        "neutre": "qui ne déplace de flux ni vers l'or ni vers le quantique",
    }
    effets = {
        "risk-off": "assèche ici les flux vers les actifs les plus spéculatifs, ce qui pèse sur les cryptos avant les autres",
        "risk-on": "alimente ici les actifs les plus spéculatifs, ce qui soutient les cryptos avant les autres",
        "neutre": "laisse ici les cryptos à leurs dynamiques internes de levier et d'offre, ce qui rend celles-ci décisives",
    }
    return f"Ce même climat {niveau} {score_txt}, {liens[niveau]}, {effets[niveau]}."


def _lecture_couverture(ratio: float, trajectoire: str) -> tuple[str, str]:
    """Qualifie la couverture d'un dossier et son effet sur la demande refuge.

    Args:
        ratio: intensité rapportée à la moyenne des trente derniers jours.
        trajectoire: ``en accélération``, ``en essoufflement`` ou autre.

    Returns:
        ``(qualificatif, effet)`` — l'effet se lit après « ce qui ».
    """
    eleve, bas = ratio >= 1.5, ratio < 0.9
    if eleve and trajectoire == "en accélération":
        return "une escalade en cours", "nourrit la demande refuge tant qu'elle dure"
    if eleve and trajectoire == "en essoufflement":
        return "un pic d'attention qui retombe", "signale un sujet en voie d'être digéré — la demande refuge y perd son moteur"
    if eleve:
        return "une attention soutenue et stable", "maintient la prime de risque sans la faire grandir"
    if trajectoire == "en accélération":
        return (
            "une attention ordinaire mais qui remonte",
            "signale un sujet qui revient sans avoir encore l'ampleur d'une escalade — la demande refuge n'y trouve pas encore de moteur",
        )
    if bas or trajectoire == "en essoufflement":
        return "une attention en retrait", "prive la demande refuge de moteur géopolitique"
    return "une attention ordinaire", "laisse la demande refuge sans moteur géopolitique net"


# ---------------------------------------------------------------------------
# Or
# ---------------------------------------------------------------------------
def _trajectoire_or(prix: dict[str, Any]) -> list[str]:
    """Situe l'or dans sa trajectoire de plusieurs mois, signe par signe.

    « +21 % sur un an, −16 % sur six mois, +4 % sur trois mois » n'est pas
    une liste : c'est une hausse ancienne, une correction, puis un rebond
    partiel. Le texte le dit.
    """
    phrases: list[str] = []
    v252, v126, v63 = (prix.get(f"variation_{h}j_pct") for h in (252, 126, 63))
    position = prix.get("position_intervalle_252j_pct")

    if v252 is None or v126 is None:
        if prix.get("variation_20j_pct") is not None:
            v20, v5 = prix["variation_20j_pct"], prix.get("variation_5j_pct")
            detail = f" et {_fr(v5, 1, signe=True)} % sur cinq" if v5 is not None else ""
            phrases.append(
                f"L'or a varié de {_fr(v20, 1, signe=True)} % sur vingt séances{detail}, ce qui "
                f"{'confirme une demande présente' if v20 > 0 else 'traduit une demande qui se retire'} "
                "sans que l'historique chargé permette de la situer sur plusieurs mois."
            )
        return phrases

    def _verbe(v: float) -> str:
        return "gagné" if v >= 0 else "perdu"

    constat = f"Sur un an, l'or a {_verbe(v252)} {_fr(abs(v252), 1)} %"
    if (v126 >= 0) == (v252 >= 0):
        constat += f" et {_verbe(v126)} {_fr(abs(v126), 1)} % sur six mois"
    else:
        constat += f", mais il a {_verbe(v126)} {_fr(abs(v126), 1)} % sur six mois"
    if v63 is not None:
        if (v63 >= 0) == (v126 >= 0):
            constat += f", et encore {_fr(abs(v63), 1)} % sur trois mois"
        else:
            constat += f", avant de {'reprendre' if v63 >= 0 else 'rendre'} {_fr(abs(v63), 1)} % sur trois mois"

    signes = (v252 >= 0, v126 >= 0, (v63 if v63 is not None else v126) >= 0)
    lectures = {
        (True, True, True): "la hausse tient sur les trois horizons, ce qui traduit une demande installée plutôt qu'un à-coup",
        (True, False, True): "la hausse annuelle est acquise de longue date, le semestre a été une correction et le trimestre un rebond partiel, ce qui signifie que la demande est revenue sans avoir effacé le repli",
        (True, True, False): "le trimestre entame une hausse jusque-là continue, ce qui signale une demande qui se retire au plus récent",
        (True, False, False): "la hausse annuelle s'érode depuis six mois et le mouvement se poursuit, ce qui traduit une demande en retrait continu",
        (False, False, False): "la baisse tient sur les trois horizons, ce qui traduit un désintérêt installé",
        (False, True, True): "la baisse annuelle est ancienne et les six derniers mois l'ont en partie rattrapée, ce qui signifie que la demande est revenue",
        (False, False, True): "le trimestre rebondit à l'intérieur d'une baisse plus longue, ce qui laisse la tendance de fond intacte tant que le semestre reste négatif",
        (False, True, False): "le rebond semestriel se défait sur le trimestre, ce qui traduit une demande qui n'a pas tenu",
    }
    phrases.append(f"{constat} : {lectures[signes]}.")

    if position is not None:
        if position >= 80:
            lecture = (
                "tout en haut de la fourchette, ce qui signifie que l'essentiel de la hausse est déjà dans "
                "les prix et que chaque nouvelle raison de monter en apporte moins"
            )
        elif position <= 20:
            lecture = (
                "dans le bas de la fourchette, ce qui traduit une demande refuge en retrait et laisse de la "
                "marge à un rebond si un catalyseur se présente"
            )
        else:
            lecture = (
                "à mi-fourchette, ce qui le laisse sans excès dans un sens ni dans l'autre : ni prime d'une "
                "hausse à défendre, ni décote d'un actif délaissé"
            )
        phrases.append(
            f"Il se tient ainsi à {_fr(position, 0)} % de l'intervalle de ses douze derniers mois, {lecture}."
        )
    return phrases


def _macro_accompagnant_or(recul: dict[str, Any]) -> list[str]:
    """Ce que les séries macro ont fait pendant que l'or bougeait."""
    phrases: list[str] = []
    taux = recul.get("taux_reels") if isinstance(recul, dict) else None
    if taux and taux.get("ecart_points_base") is not None:
        ecart = taux["ecart_points_base"]
        if abs(ecart) >= 10:
            sens = "monté" if ecart > 0 else "baissé"
            effet = (
                "renchérit la détention d'un actif sans rendement et retire un soutien à l'or"
                if ecart > 0 else
                "allège le coût de détenir un actif sans rendement et soutient l'or"
            )
            phrases.append(
                f"Pendant ce temps, les taux réels ont {sens} de {_fr(abs(ecart), 0)} points de base sur six mois, "
                f"à {_fr(taux.get('actuel_pct', 0.0), 2)} %, ce qui {effet}."
            )
        else:
            phrases.append(
                f"Pendant ce temps, les taux réels sont restés stables sur six mois, à {_fr(taux.get('actuel_pct', 0.0), 2)} %, "
                "ce qui ne fait ni levier ni frein sur le coût de détenir l'or."
            )

    dollar = recul.get("dollar") if isinstance(recul, dict) else None
    if dollar and dollar.get("variation_6_mois_pct") is not None:
        var = dollar["variation_6_mois_pct"]
        if abs(var) >= 1.0:
            sens = "renforcé" if var > 0 else "affaibli"
            effet = (
                "renchérit l'or pour les acheteurs hors dollar et pèse sur sa demande"
                if var > 0 else "rend l'or moins cher pour les acheteurs hors dollar et soutient sa demande"
            )
            phrases.append(f"Le dollar s'est {sens} de {_fr(abs(var), 1)} % sur la période, ce qui {effet}.")

    inflation = recul.get("inflation") if isinstance(recul, dict) else None
    if inflation and inflation.get("actuel_pct") is not None and inflation.get("il_y_a_6_mois_pct") is not None:
        actuel, passe = inflation["actuel_pct"], inflation["il_y_a_6_mois_pct"]
        if abs(actuel - passe) >= 0.3:
            sens = "remonté" if actuel > passe else "reflué"
            effet = (
                "entretient la contrainte sur la Réserve fédérale et repousse la baisse des taux qui allégerait le coût de l'or"
                if actuel > passe else
                "relâche la contrainte sur la Réserve fédérale et rapproche la baisse des taux qui allégerait le coût de l'or"
            )
            phrases.append(
                f"L'inflation a {sens} de {_fr(passe, 1)} % à {_fr(actuel, 1)} % sur six mois, ce qui {effet}."
            )
    return phrases


def synthetiser_or(rapport: dict[str, Any]) -> dict[str, Any]:
    """Compose le récit de la rubrique Or.

    Ordre du raisonnement : d'où vient l'or (trajectoire, macro qui l'a
    accompagné), où il en est (valorisation, positionnement), ce qui pourrait
    le faire bouger (géopolitique, climat de risque), et ce que le croisement
    de tout cela donne (le biais).

    Args:
        rapport: contenu de ``reports/gold/latest.json``.

    Returns:
        Résultat de :func:`verifier_synthese`.
    """
    if not rapport:
        return _refus(rapport, "synthèse impossible : rapport or vide")
    phrases: list[str] = []
    facteur = facteur_commun(rapport)

    # 1. D'où vient l'or.
    phrases.extend(_trajectoire_or(_lire(rapport, "prix", {})))
    phrases.extend(_macro_accompagnant_or(_lire(rapport, "contexte_macro.recul", {})))

    # 2. Où il en est : valorisation, ou une incise si le modèle ne tient pas.
    jv = _lire(rapport, "juste_valeur", {})
    incise_jv = ""
    if jv.get("disponible") and jv.get("fiable") and jv.get("z_score") is not None:
        z = jv["z_score"]
        if z >= 1.5:
            lecture = (
                "il est statistiquement cher au regard des taux réels et du dollar, ce qui rend "
                "l'asymétrie défavorable : une bonne nouvelle apporte peu de hausse, une détente peut "
                "faire retomber la prime"
            )
        elif z <= -1.5:
            lecture = (
                "il est statistiquement bon marché au regard de ces facteurs, ce qui laisse de la "
                "marge pour un rattrapage si la demande revient"
            )
        else:
            lecture = "il reste dans la zone que ces facteurs expliquent, ce qui n'offre ni décote ni prime à jouer"
        phrases.append(f"Son écart à la juste valeur ressort à {_fr(z, 2, signe=True)} écart-type : {lecture}.")
    elif jv.get("disponible") and not jv.get("fiable"):
        incise_jv = (
            "Le modèle de juste valeur, sous son seuil de fiabilité ce jour, ne dit pas si ce niveau est "
            "cher ou bon marché : la lecture repose sur le positionnement et le contexte."
        )

    # 3. Positionnement spéculatif.
    cot = _lire(rapport, "positionnement_cot", {})
    if cot.get("disponible") and cot.get("percentile_managed_money") is not None:
        p = cot["percentile_managed_money"]
        variation = cot.get("variation_hebdo_managed_money")
        tendance = ""
        if variation is not None and variation != 0:
            tendance = ", en allègement sur la semaine" if variation < 0 else ", en renforcement sur la semaine"
        if p >= 75:
            lecture = (
                "les gros spéculateurs sont déjà fortement acheteurs, ce qui encombre le marché : "
                "il reste peu de place pour de nouveaux acheteurs, et beaucoup de positions à déboucler "
                "si la tension retombe"
            )
        elif p <= 25:
            lecture = (
                "les gros spéculateurs sont peu engagés côté acheteur, ce qui laisse de la marge à de "
                "nouveaux acheteurs et limite le risque d'un retournement brutal par débouclage"
            )
        else:
            lecture = (
                "les gros spéculateurs sont acheteurs sans excès, ce qui ne fait ni levier ni frein "
                "sur le prix"
            )
        phrases.append(
            f"Côté positionnement, le net spéculatif se situe au {_fr(p, 0)}e percentile sur cinq ans"
            f"{tendance} : {lecture}."
        )
    if incise_jv:
        phrases.append(incise_jv)

    # 4. Ce qui pourrait le faire bouger : géopolitique.
    geo = _lire(rapport, "geopolitique", {})
    dominant = next(
        (d for d in (geo.get("dossiers") or []) if d.get("id") == geo.get("dossier_dominant")), None,
    )
    if dominant and dominant.get("disponible") and dominant.get("intensite_ratio") is not None:
        ratio, traj = float(dominant["intensite_ratio"]), dominant.get("trajectoire") or ""
        qualificatif, effet = _lecture_couverture(ratio, traj)
        phrases.append(
            f"Le dossier géopolitique le plus suivi, {dominant.get('nom_affiche')}, est couvert à "
            f"{_fr(ratio, 1)}× sa normale, trajectoire {traj or 'non qualifiée'} : {qualificatif}, ce qui {effet}."
        )
    deja = _lire(geo, "deja_dans_les_prix", {})
    if deja.get("disponible") and deja.get("z_score_prime") is not None:
        zp = deja["z_score_prime"]
        if deja.get("valeur") is True:
            phrases.append(
                f"La prime de risque géopolitique paraît déjà payée ({_fr(zp, 2, signe=True)} écart-type), "
                "ce qui inverse l'asymétrie : une tension supplémentaire apporte peu de hausse, une détente "
                "ferait retomber la prime."
            )
        else:
            phrases.append(
                f"La prime de risque géopolitique n'est pas encore payée ({_fr(zp, 2, signe=True)} écart-type "
                "mesuré), ce qui laisse à une escalade de la place pour se traduire dans le prix."
            )

    # 5. Facteur commun.
    phrase_appetit = _phrase_appetit(facteur, "or")
    if phrase_appetit:
        phrases.append(phrase_appetit)

    # 6. Ce que tout cela donne.
    biais = _lire(rapport, "biais", {})
    if biais.get("score_composite") is not None:
        composantes = [
            c for c in (biais.get("composantes") or [])
            if c.get("disponible") and c.get("contribution") is not None
        ]
        composantes.sort(key=lambda c: abs(float(c["contribution"])), reverse=True)
        if len(composantes) >= 2:
            a, b = composantes[0], composantes[1]
            ca, cb = float(a["contribution"]), float(b["contribution"])
            noms = (
                _LIBELLE_COMPOSANTE.get(a.get("nom"), a.get("nom", "")),
                _LIBELLE_COMPOSANTE.get(b.get("nom"), b.get("nom", "")),
            )
            relation = "se compensent" if ca * cb < 0 else "s'additionnent"
            detail = (
                f" : {noms[0]} ({_fr(ca, 2, signe=True)}) et {noms[1]} ({_fr(cb, 2, signe=True)}) {relation}, "
                f"ce qui explique {'un score proche de zéro' if abs(float(biais['score_composite'])) < 0.1 else 'le sens du score'}"
            )
        else:
            detail = ""
        phrases.append(
            f"C'est le croisement de ces éléments qui donne un biais mécanique "
            f"{biais.get('biais', 'indéterminé')} (score {_fr(float(biais['score_composite']), 2, signe=True)}, "
            f"conviction {biais.get('conviction', 'inconnue')}, "
            f"{_fr((biais.get('couverture_donnees') or 0.0) * 100, 0)} % des composantes mesurées){detail}."
        )

    prochaine = (_lire(rapport, "calendrier.echeances", []) or [None])[0]
    if prochaine and prochaine.get("nom"):
        phrases.append(
            f"À l'approche de l'échéance « {prochaine['nom']} », cette lecture fondamentale a peu de prise "
            "sur les prochaines séances : c'est l'annonce qui décidera du mouvement immédiat."
        )

    if len(phrases) < 2:
        return _refus(rapport, "synthèse impossible : ni prix, ni valorisation, ni contexte macro ce jour")
    return verifier_synthese(" ".join(phrases), rapport)


# ---------------------------------------------------------------------------
# Géopolitique
# ---------------------------------------------------------------------------
_CHAINE: Final = "pétrole → inflation anticipée → taux réels → or"


#: Sens attendu de chaque maillon (voir geopolitics.chaine_de_transmission)
#: et son libellé court pour la prose.
_MAILLONS: Final[tuple[tuple[str, int, str], ...]] = (
    ("2_petrole", 1, "le pétrole"),
    ("3_inflation_anticipee", 1, "les anticipations d'inflation"),
    ("4_taux_reels", -1, "les taux réels"),
    ("5_or", 1, "l'or"),
)


def _maillons_en_rupture(maillons: dict[str, Any]) -> list[str]:
    """Nomme les maillons mesurés qui ne vont pas dans le sens attendu.

    Args:
        maillons: bloc ``chaine_de_transmission.maillons`` d'un dossier.

    Returns:
        Libellés avec leur variation, prêts à être insérés dans une phrase.
    """
    ruptures: list[str] = []
    for cle, sens, libelle in _MAILLONS:
        bloc = maillons.get(cle) or {}
        variation = bloc.get("variation")
        if not bloc.get("disponible") or variation is None:
            continue
        if float(variation) * sens <= 0:
            # L'unité est celle du moteur (« % » ou « points de base ») : on
            # ne la devine pas. Des points de base s'écrivent sans décimale.
            unite = str(bloc.get("unite_variation") or "").strip()
            decimales = 0 if "base" in unite else 2
            ruptures.append(f"{libelle} ({_fr(float(variation), decimales, signe=True)} {unite})".rstrip())
    return ruptures


def synthetiser_geopolitique(rapport: dict[str, Any]) -> dict[str, Any]:
    """Compose le récit de la rubrique Géopolitique.

    Deux sources se croisent : la couverture médiatique (intensité,
    trajectoire — mesurée dossier par dossier, parfois pour aucun) et le
    relevé d'événements bilatéraux, qui existe même quand la couverture
    n'est pas mesurable. Le texte les distingue, et ne fait jamais dire à
    un instantané de quinze minutes plus qu'il ne peut.

    Args:
        rapport: contenu de ``reports/gold/latest.json``.

    Returns:
        Résultat de :func:`verifier_synthese`.
    """
    if not rapport:
        return _refus(rapport, "synthèse impossible : rapport or vide")
    phrases: list[str] = []
    facteur = facteur_commun(rapport)
    geo = _lire(rapport, "geopolitique", {})
    tous = [d for d in (geo.get("dossiers") or []) if isinstance(d, dict)]
    mesures = [d for d in tous if d.get("disponible") and d.get("intensite_ratio") is not None]
    non_mesures = [d for d in tous if d not in mesures]

    def _nom(d: dict[str, Any]) -> str:
        return str(d.get("nom_affiche") or d.get("id") or "")

    if mesures:
        ordonnes = sorted(mesures, key=lambda d: float(d["intensite_ratio"]), reverse=True)
        tete = ordonnes[0]
        ratio, traj = float(tete["intensite_ratio"]), tete.get("trajectoire") or "non qualifiée"
        qualificatif, effet = _lecture_couverture(ratio, traj)
        phrases.append(
            f"Sur les {_pl(len(tous), 'dossier')} suivis, {_nom(tete)} est le plus couvert, à "
            f"{_fr(ratio, 1)}× sa moyenne des trente derniers jours, trajectoire {traj} : {qualificatif}, ce qui {effet}."
        )
        autres = ordonnes[1:]
        if autres:
            descriptions = [f"{_nom(d)} ({d.get('trajectoire') or 'trajectoire non qualifiée'})" for d in autres]
            phrases.append(
                f"Les autres dossiers mesurés, {_liste(descriptions)}, restent en retrait de celui-ci, ce qui "
                "concentre sur un seul sujet ce que la géopolitique peut apporter à la demande refuge."
            )
        if non_mesures:
            phrases.append(
                f"Pour {_liste([_nom(d) for d in non_mesures])}, la couverture n'est pas mesurable ce jour, "
                "ce qui laisse leur trajectoire hors de cette lecture."
            )
    elif tous:
        motif = geo.get("motif") or "couverture non mesurable"
        phrases.append(
            f"Aucun des {_pl(len(tous), 'dossier')} suivis n'a de couverture mesurable ce jour ({motif}), "
            "ce qui prive la lecture de sa trajectoire médiatique : le reste se déduit des événements "
            "relevés et des chiffres de marché."
        )
    else:
        motif = geo.get("motif") or "aucun dossier mesurable"
        phrases.append(
            f"Aucun dossier de conflit n'est mesurable ce jour ({motif}), ce qui prive la lecture "
            "géopolitique de son narratif : le reste se déduit des chiffres de marché."
        )

    # Événements bilatéraux : une source distincte de la couverture, qui existe
    # même pour les dossiers dont l'intensité n'est pas mesurable.
    # Événements : les dossiers bilatéraux se comparent entre eux ; un dossier
    # régional agrège des dizaines de paires et se cite à part.
    evenements = [d for d in tous if isinstance(d.get("n_evenements_bilateraux"), (int, float))]
    bilateraux = [d for d in evenements if d.get("type", "conflit") == "conflit"]
    regionaux = [d for d in evenements if d.get("type") == "regional" and int(d["n_evenements_bilateraux"]) > 0]
    if bilateraux:
        plus_actif = max(bilateraux, key=lambda d: d["n_evenements_bilateraux"])
        n = int(plus_actif["n_evenements_bilateraux"])
        region = ""
        if regionaux:
            r = max(regionaux, key=lambda d: d["n_evenements_bilateraux"])
            region = f", et {int(r['n_evenements_bilateraux'])} à l'intérieur de la région {_nom(r)} toutes paires confondues"
        if n >= 5:
            phrases.append(
                f"Le relevé d'événements GDELT des dernières heures en compte {n} pour {_nom(plus_actif)}{region}, ce qui "
                "confirme une activité soutenue entre les deux parties, indépendamment de ce que la presse en dit."
            )
        elif n >= 1:
            phrases.append(
                f"Le relevé d'événements GDELT des dernières heures en compte {n} pour {_nom(plus_actif)}{region}, ce qui "
                "relativise l'intensité médiatique : peu d'actes entre les deux parties sur la période relevée."
            )
        else:
            phrases.append(
                "Le relevé d'événements GDELT des dernières heures est vide pour les dossiers bilatéraux, ce qui ne pèse "
                "pas : rien n'y contredit la couverture, rien ne la confirme non plus."
            )
    elif regionaux:
        r = max(regionaux, key=lambda d: d["n_evenements_bilateraux"])
        phrases.append(
            f"Le relevé d'événements GDELT des dernières heures en compte {int(r['n_evenements_bilateraux'])} à "
            f"l'intérieur de la région {_nom(r)}, toutes paires confondues, ce qui confirme une activité soutenue "
            "dans la région, indépendamment de ce que la presse en dit."
        )

    # Développements nouveaux, dossier par dossier (les comptes sont ceux des données).
    nouveaux = [(d, int(d.get("n_nouveaux_developpements") or 0)) for d in tous]
    avec = [(d, n) for d, n in nouveaux if n > 0]
    if avec:
        detail = _liste([f"{_nom(d)} {n}" for d, n in avec])
        phrases.append(
            f"Des développements nouveaux sont apparus depuis la dernière vérification ({detail}), ce qui "
            "renouvelle la matière de ces dossiers et justifie d'en relire la trajectoire."
        )
    elif tous:
        phrases.append(
            "Aucun développement nouveau depuis la dernière vérification, ce qui laisse la lecture "
            "d'hier valable : rien n'est venu la contredire."
        )

    # Chaîne de transmission : le moteur écrit ``chaine_rompue`` à ``None``
    # quand aucune série n'est mesurable, ``False`` quand tous les maillons
    # vont dans le sens attendu, ``True`` sinon. Les maillons qui rompent
    # sont nommés avec leur variation — elle est dans les données.
    mesurables = [d for d in tous if _lire(d, "chaine_de_transmission.chaine_rompue") is not None]
    completes = [d for d in mesurables if _lire(d, "chaine_de_transmission.chaine_rompue") is False]
    rompues = [d for d in mesurables if _lire(d, "chaine_de_transmission.chaine_rompue") is True]
    if completes:
        phrases.append(
            f"Pour {_liste([_nom(d) for d in completes])}, la chaîne {_CHAINE} est cohérente, ce qui donne "
            "au mouvement de l'or une explication vérifiable en amont : il ne tient pas à la seule peur."
        )
    if rompues:
        qui = "l'ensemble des dossiers" if len(rompues) == len(tous) and len(tous) > 1 else _liste([_nom(d) for d in rompues])
        nom_chaine = "cette chaîne" if completes else f"la chaîne {_CHAINE}"
        ruptures = _maillons_en_rupture(_lire(rompues[0], "chaine_de_transmission.maillons", {}) or {})
        verbe = "ne va pas" if len(ruptures) == 1 else "ne vont pas"
        detail = f" — {_liste(ruptures)} {verbe} dans le sens attendu —" if ruptures else ""
        phrases.append(
            f"Pour {qui}, {nom_chaine} est rompue{detail}, ce qui signifie que la tension ne se transmet pas "
            "par les canaux mesurés : si l'or y réagit, c'est sur la peur, et cela tient rarement longtemps."
        )
    if tous and not mesurables:
        phrases.append(
            f"La chaîne {_CHAINE} n'est mesurable pour aucun dossier ce jour, ce qui laisse ouverte la "
            "question de savoir si la tension atteint l'or par les canaux économiques ou par la seule peur."
        )

    # Classement par pertinence marché : qui bouge encore les prix, qui ne
    # bouge plus rien, et pour qui on ne peut pas conclure.
    classement = [c for c in (geo.get("classement") or []) if isinstance(c, dict)]
    if classement:
        actifs = [c for c in classement if c.get("statut") == "actif"]
        veille = [c for c in classement if c.get("statut") == "veille"]
        insuffisants = [c for c in classement if not c.get("donnees_suffisantes")]
        if actifs:
            tete = actifs[0]
            score = _lire(tete, "pertinence.score")
            phrases.append(
                f"Parmi les sujets classés par pertinence marché, {tete.get('nom')} ressort en tête"
                + (f" : ses jours de pic de couverture voient les actifs bouger {_fr(float(score), 2)} fois plus que les autres jours" if score is not None else "")
                + ", ce qui en fait le sujet dont l'actualité se lit encore dans les prix."
            )
        inertes = [c for c in veille if _lire(c, "pertinence.lecture") == "inerte"]
        neutres = [c for c in veille if _lire(c, "pertinence.lecture") != "inerte"]
        if inertes:
            noms = _liste([str(c.get("nom")) for c in inertes[:3]])
            phrases.append(
                f"{noms} {'sont' if len(inertes) > 1 else 'est'} en veille et inerte{'s' if len(inertes) > 1 else ''} : "
                "la couverture est mesurée mais le marché n'y réagit plus, ce qui signifie que le sujet est intégré "
                "dans les prix — gardé, car il peut se réactiver."
            )
        if neutres:
            detail = _liste([
                f"{c.get('nom')} ({_fr(float(_lire(c, 'pertinence.score')), 2)})" if _lire(c, "pertinence.score") is not None else str(c.get("nom"))
                for c in neutres[:3]
            ])
            seuil = _lire(geo, "criteres_pertinence.reagit")
            repere = f" — il faudrait dépasser {_fr(float(seuil), 1)} pour parler de réaction" if seuil is not None else ""
            phrases.append(
                f"{detail} {'sont' if len(neutres) > 1 else 'est'} en veille : la couverture est mesurée, mais "
                f"{'leurs' if len(neutres) > 1 else 'ses'} jours de pic ne se distinguent pas nettement des autres "
                f"séances{repere}, ce qui ne permet pas de dire que cette actualité déplace encore les prix."
            )
        if insuffisants:
            noms = _liste([str(c.get("nom")) for c in insuffisants[:3]])
            phrases.append(
                f"Pour {noms}, le classement n'est pas encore possible faute d'observations suffisantes, "
                "ce qui interdit d'en tirer une lecture de marché aujourd'hui."
            )
        # Le temps long : ce que l'historique du classement ajoute à la photo du jour.
        promus = [c for c in classement if _lire(c, "historique.changement") == "promu"]
        retrogrades = [c for c in classement if _lire(c, "historique.changement") == "rétrogradé"]
        if promus or retrogrades:
            morceaux = []
            if promus:
                morceaux.append(f"{_liste([str(c.get('nom')) for c in promus[:3]])} {'montent' if len(promus) > 1 else 'monte'} d'un cran")
            if retrogrades:
                morceaux.append(f"{_liste([str(c.get('nom')) for c in retrogrades[:3]])} {'reculent' if len(retrogrades) > 1 else 'recule'}")
            phrases.append(
                f"Par rapport au dernier classement, {' et '.join(morceaux)}, ce qui déplace l'attention "
                "sans qu'un seul jour suffise à la fixer : c'est la durée qui confirme."
            )
        durables = sorted(
            (c for c in classement if int(_lire(c, "historique.inerte_depuis_jours", 0) or 0) >= 5),
            key=lambda c: -int(_lire(c, "historique.inerte_depuis_jours", 0) or 0),
        )
        if durables:
            c = durables[0]
            n = int(_lire(c, "historique.inerte_depuis_jours", 0))
            phrases.append(
                f"{c.get('nom')} est inerte depuis {n} jours de classement consécutifs, ce qui n'est plus une "
                "photo du jour : le marché a cessé d'y réagir de façon durable."
            )

    deja = _lire(geo, "deja_dans_les_prix", {})
    if deja.get("disponible") and deja.get("z_score_prime") is not None:
        zp = deja["z_score_prime"]
        if deja.get("valeur") is True:
            phrases.append(
                f"La prime de risque paraît déjà payée par le marché de l'or ({_fr(zp, 2, signe=True)} "
                "écart-type), ce qui signifie qu'une tension supplémentaire apporte peu de hausse tandis "
                "qu'une détente ferait retomber la prime."
            )
        else:
            phrases.append(
                f"La prime de risque n'est pas encore payée ({_fr(zp, 2, signe=True)} écart-type mesuré), "
                "ce qui laisse à une escalade de la place pour se traduire dans le prix de l'or."
            )
    elif tous:
        phrases.append(
            "Savoir si cette prime est déjà payée n'est pas évaluable ce jour, le résidu de juste valeur "
            "n'étant pas fiable, ce qui interdit de dire si une escalade aurait encore de la place dans le prix."
        )

    phrase_appetit = _phrase_appetit(facteur, "geopolitique")
    if phrase_appetit:
        phrases.append(phrase_appetit)

    if len(phrases) < 2:
        return _refus(rapport, "synthèse impossible : ni dossier ni contexte macro ce jour")
    return verifier_synthese(" ".join(phrases), rapport)


# ---------------------------------------------------------------------------
# Quantique
# ---------------------------------------------------------------------------
def _trajectoire_quantique(longues: dict[str, dict[str, Any]]) -> tuple[list[str], str]:
    """Raconte trois et six mois ensemble, sans confondre accélération et rechute.

    Un titre à −35 % sur trois mois et +13 % sur six n'a pas « accéléré » :
    il a rendu en un trimestre l'essentiel de ce qu'il avait gagné avant. Le
    trimestre précédent se déduit des deux chiffres, et sa direction seule
    est citée — sa valeur n'est pas dans les données.

    Args:
        longues: ``prix.variations_longues`` du rapport quantique.

    Returns:
        ``(phrases, direction_3_mois)`` où la direction vaut ``baisse``,
        ``hausse``, ``mixte`` ou ``""``.
    """
    phrases: list[str] = []
    trois = {t: float(v["variation_3_mois_pct"]) for t, v in longues.items() if v.get("variation_3_mois_pct") is not None}
    six = {t: float(v["variation_6_mois_pct"]) for t, v in longues.items() if v.get("variation_6_mois_pct") is not None}
    direction = ""

    if trois:
        details3 = ", ".join(f"{t} {_fr(v, 1, signe=True)} %" for t, v in sorted(trois.items()))
        if all(v < 0 for v in trois.values()):
            direction, lecture = "baisse", (
                "un recul commun à tout le secteur, ce qui désigne un désengagement d'ensemble plutôt qu'une "
                "déception propre à une société"
            )
        elif all(v > 0 for v in trois.values()):
            direction, lecture = "hausse", (
                "une hausse commune à tout le secteur, ce qui traduit un intérêt qui s'étend au secteur "
                "plutôt qu'à une société"
            )
        else:
            direction, lecture = "mixte", (
                "des trajectoires qui divergent, ce qui signifie que le secteur ne bouge plus d'un bloc : "
                "les nouvelles propres à chaque société pèsent davantage"
            )
        phrases.append(f"Sur trois mois, {details3} : {lecture}.")

    if six:
        details6 = ", ".join(f"{t} {_fr(v, 1, signe=True)} %" for t, v in sorted(six.items()))
        communs = [t for t in six if t in trois and trois[t] > -100.0]
        if communs:
            anterieur = {t: (1 + six[t] / 100.0) / (1 + trois[t] / 100.0) - 1.0 for t in communs}
            ant_pos = all(anterieur[t] > 0 for t in communs)
            ant_neg = all(anterieur[t] < 0 for t in communs)
            en_avance = sorted(t for t in six if six[t] > 0)
            en_retrait = sorted(t for t in six if six[t] <= 0)
            if direction == "baisse" and ant_pos:
                large = all(abs(trois[t]) >= anterieur[t] * 100.0 / 2.0 for t in communs)
                suite = (
                    "ce qui implique que le trimestre précédent les avait portés bien plus haut : le secteur "
                    f"a rendu en trois mois {'une large part' if large else 'une partie'} de ses gains antérieurs"
                )
                if en_avance and en_retrait:
                    suite += (
                        f", ce qui laisse {_liste(en_avance)} encore en avance sur le semestre et "
                        f"{_liste(en_retrait)} sous leur niveau de départ"
                    )
                elif en_avance:
                    suite += ", sans effacer l'avance prise sur le semestre"
                else:
                    suite += ", et tous sont repassés sous leur niveau de départ"
            elif direction == "hausse" and ant_neg:
                suite = "ce qui implique que le trimestre a rattrapé un recul antérieur : la hausse est récente, pas installée"
            elif (direction == "baisse" and ant_neg) or (direction == "hausse" and ant_pos):
                suite = "ce qui prolonge le mouvement du trimestre précédent : la tendance est continue sur tout le semestre"
            else:
                suite = "ce qui mêle des trajectoires différentes selon les titres : aucun mouvement commun ne se dégage sur le semestre"
        else:
            en_hausse = sum(1 for v in six.values() if v > 0)
            if en_hausse == len(six):
                suite = "ce qui traduit un intérêt qui s'est étendu à tout le secteur plutôt qu'à une société"
            elif en_hausse == 0:
                suite = "ce qui traduit un désengagement du secteur entier, pas une déception propre à une société"
            else:
                suite = "ce qui signifie que le secteur ne bouge plus d'un bloc : les nouvelles propres à chaque société pèsent davantage"
        phrases.append(f"Sur six mois, le bilan est {details6}, {suite}.")
    return phrases, direction


def synthetiser_quantique(
    rapport: dict[str, Any], facteur: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Compose le récit de la rubrique Quantique.

    N'expose que des faits observables — trajectoires, mouvements,
    corrélation, trésorerie, contexte de taux. Aucun jugement comparatif
    entre les trois valeurs : ce serait franchir la ligne entre expliquer et
    conseiller.

    Args:
        rapport: contenu de ``reports/quantum/latest.json``.
        facteur: sortie de :func:`facteur_commun`. ``None`` lit
            ``rapport["facteur_commun"]`` s'il est présent.

    Returns:
        Résultat de :func:`verifier_synthese`.
    """
    if not rapport:
        return _refus(rapport, "synthèse impossible : rapport quantique vide")
    facteur = facteur if facteur is not None else rapport.get("facteur_commun")
    phrases: list[str] = []

    # 1. Trajectoire du secteur.
    trajectoire, direction = _trajectoire_quantique(_lire(rapport, "prix.variations_longues", {}) or {})
    phrases.extend(trajectoire)

    # 2. La séance.
    mouvements = _lire(rapport, "mouvements", {})
    n = int(mouvements.get("n_mouvements") or 0)
    if n:
        details = ", ".join(
            f"{m.get('ticker')} {_fr(m.get('variation_pct') or 0.0, 1, signe=True)} %"
            for m in (mouvements.get("mouvements") or [])[:3]
        )
        sectoriels = [m for m in (mouvements.get("mouvements") or []) if m.get("classification") == "sectoriel"]
        if sectoriels:
            lecture = (
                "ce qui, pour la part classée sectorielle, signifie que d'autres valeurs bougent dans le même "
                "sens avec une amplitude comparable : le mouvement n'est pas propre à la société"
            )
        else:
            lecture = "ce qui, aucune autre valeur ne suivant, désigne un mouvement propre à la société concernée"
        phrases.append(
            f"Sur la séance, {_pl(n, 'valeur dépasse', 'valeurs dépassent', feminin=True)} leur seuil de mouvement ({details}), {lecture}."
        )
    elif mouvements:
        phrases.append(
            "Sur la séance, aucune valeur suivie ne dépasse son seuil de mouvement, ce qui signifie que "
            "rien de propre au secteur ne s'est joué aujourd'hui et que sa direction reste celle des "
            "flux généraux."
        )

    # 3. Concentration du risque.
    correlation = _lire(rapport, "secteur.correlation_positions", {})
    if correlation.get("disponible") and correlation.get("correlation_max") is not None:
        c = float(correlation["correlation_max"])
        if correlation.get("correlation_elevee") or c >= 0.7:
            effet = (
                "ce qui signifie que les détenir ensemble revient largement à détenir la même position : "
                "la diversification apparente entre elles est trompeuse"
            )
        else:
            effet = "ce qui laisse une diversification réelle entre elles"
        phrases.append(
            f"La corrélation la plus forte entre deux des valeurs atteint {_fr(c, 2)} sur "
            f"{correlation.get('n_seances_effectives', 0)} séances, {effet}."
        )

    # 4. Contexte de taux propre au secteur.
    macro = _lire(rapport, "contexte_macro", {})
    var_taux = macro.get("variation_taux_reels_points")
    if var_taux is not None:
        var_taux = float(var_taux)
        if abs(var_taux) >= 0.03:
            sens = "monté" if var_taux > 0 else "baissé"
            effet = (
                "ce qui pèse sur les valeurs à profits lointains, dont la valeur actualisée diminue quand le taux monte"
                if var_taux > 0 else
                "ce qui soutient les valeurs à profits lointains, dont la valeur actualisée augmente quand le taux baisse"
            )
            phrases.append(f"Les taux réels ont {sens} de {_fr(abs(var_taux), 2)} point sur la séance, {effet}.")
        else:
            phrases.append(
                f"Les taux réels n'ont bougé que de {_fr(var_taux, 2, signe=True)} point sur la séance, "
                "ce qui n'explique aucun mouvement du secteur par le coût de l'argent."
            )
    taux_recul = (facteur or {}).get("taux_reels")
    if taux_recul and taux_recul.get("ecart_points_base") is not None and abs(taux_recul["ecart_points_base"]) >= 10:
        ecart = float(taux_recul["ecart_points_base"])
        phrases.append(
            f"Sur six mois, ils ont {'monté' if ecart > 0 else 'baissé'} de {_fr(abs(ecart), 0)} points de base, "
            f"ce qui {'a renchéri' if ecart > 0 else 'a allégé'} durablement le financement des sociétés qui "
            "brûlent du cash et se refinancent par émission d'actions."
        )

    # 5. Trésorerie, quand elle est mesurée.
    tresorerie = _lire(rapport, "secteur.tresorerie", {}) or _lire(rapport, "tresorerie", {})
    runways = [
        (cle, float(bloc["runway_trimestres"]))
        for cle, bloc in (tresorerie.items() if isinstance(tresorerie, dict) else [])
        if isinstance(bloc, dict) and bloc.get("runway_trimestres") is not None
    ]
    if runways:
        details = ", ".join(f"{t} {_fr(v, 1)} trimestres" for t, v in runways[:3])
        courts = [t for t, v in runways if v < 4]
        effet = (
            f"ce qui expose {_liste(courts)} à une émission d'actions dilutive à court terme"
            if courts else "ce qui éloigne pour l'instant le besoin d'une émission dilutive"
        )
        phrases.append(f"Trésorerie estimée en trimestres d'activité : {details}, {effet}.")

    # 6. Facteur commun, puis ce que la trajectoire du secteur en dit.
    phrase_appetit = _phrase_appetit(facteur, "quantique")
    if phrase_appetit:
        phrases.append(phrase_appetit)
        niveau = (facteur or {}).get("appetit", {}).get("niveau")
        contrastes = {
            ("baisse", "risk-on"): "Ce recul trimestriel coexiste aujourd'hui avec un climat porteur, ce qui désigne des causes propres au secteur plutôt qu'un retrait général du risque.",
            ("baisse", "risk-off"): "Ce recul trimestriel est cohérent avec ce climat, ce qui empêche de l'attribuer au seul secteur.",
            ("hausse", "risk-on"): "La hausse trimestrielle va dans le sens de ce climat, ce qui la rend dépendante de sa persistance.",
            ("hausse", "risk-off"): "La hausse trimestrielle coexiste avec un climat défavorable, ce qui la rattache à des nouvelles propres au secteur.",
        }
        contraste = contrastes.get((direction, niveau))
        if contraste:
            phrases.append(contraste)

    if len(phrases) < 2:
        return _refus(rapport, "synthèse impossible : ni prix, ni mouvement, ni contexte ce jour")
    return verifier_synthese(" ".join(phrases), rapport)


# ---------------------------------------------------------------------------
# Crypto
# ---------------------------------------------------------------------------
_LIBELLE_ROTATION: Final[dict[str, str]] = {
    "rotation_alts": "orientée vers les alternatives",
    "dominance_btc": "orientée vers le bitcoin",
    "indetermine": "indéterminée",
    "indéterminé": "indéterminée",
    "aucune": "sans direction mesurable",
}


def synthetiser_crypto(
    rapport: dict[str, Any], facteur: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Compose le récit de la rubrique Crypto.

    La profondeur disponible est celle des trois horizons de prix (vingt-
    quatre heures, sept jours, trente jours) : le texte les emboîte — un
    repli de séance à l'intérieur d'une hausse mensuelle n'est pas un
    retournement — et confronte l'écart ether/bitcoin à la mesure de
    rotation.

    Args:
        rapport: contenu de ``reports/crypto/latest.json``.
        facteur: sortie de :func:`facteur_commun`. ``None`` lit
            ``rapport["facteur_commun"]`` s'il est présent.

    Returns:
        Résultat de :func:`verifier_synthese`.
    """
    if not rapport:
        return _refus(rapport, "synthèse impossible : rapport crypto vide")
    facteur = facteur if facteur is not None else rapport.get("facteur_commun")
    phrases: list[str] = []

    bloc_positions = _lire(rapport, "positionnement.positions", {})
    positions = {p.get("symbole"): p for p in (bloc_positions.get("positions") or []) if isinstance(p, dict)}
    btc, eth = positions.get("BTC") or {}, positions.get("ETH") or {}

    # 1. Trente jours : le mouvement de fond.
    direction_mois = ""
    trente = [(nom, float(p["variation_30j_pct"])) for nom, p in (("le bitcoin", btc), ("l'ether", eth)) if p.get("variation_30j_pct") is not None]
    if trente:
        details = " et ".join(f"{nom} {_fr(v, 1, signe=True)} %" for nom, v in trente)
        if all(v > 0 for _, v in trente):
            direction_mois, effet = "hausse", "ce qui décrit un marché qui monte d'un bloc : les flux entrent sans discriminer"
        elif all(v < 0 for _, v in trente):
            direction_mois, effet = "baisse", "ce qui décrit un marché qui se retire d'un bloc : les flux sortent sans discriminer"
        else:
            direction_mois, effet = "mixte", "ce qui signifie que les deux références divergent : les flux choisissent, ils ne suivent pas le marché entier"
        phrases.append(f"Sur trente jours, {details}, {effet}.")

    # 2. Sept jours et vingt-quatre heures, emboîtés dans le mois.
    sept = btc.get("variation_7j_pct")
    jour = btc.get("variation_24h_pct")
    mesures_24h = [p for p in positions.values() if p.get("variation_24h_pct") is not None]
    en_baisse_24h = sum(1 for p in mesures_24h if float(p["variation_24h_pct"]) < 0)
    if sept is not None and direction_mois in ("hausse", "baisse"):
        sept = float(sept)
        detail_jour = f" et {_fr(float(jour), 1, signe=True)} % sur la séance" if jour is not None else ""
        largeur = ""
        if mesures_24h:
            largeur = (
                f", et {_pl(en_baisse_24h, 'des ' + _lettres(len(mesures_24h)) + ' jetons suivis recule', 'des ' + _lettres(len(mesures_24h)) + ' jetons suivis reculent')} sur vingt-quatre heures"
            )
        if (sept < 0) == (direction_mois == "hausse"):
            sens = "repli" if direction_mois == "hausse" else "rebond"
            phrases.append(
                f"Sur sept jours en revanche, le bitcoin fait {_fr(sept, 1, signe=True)} %{detail_jour}{largeur}, "
                f"ce qui fait du moment présent un {sens} général à l'intérieur "
                f"{"d'une hausse" if direction_mois == 'hausse' else "d'une baisse"} mensuelle : une respiration commune à tout le marché, "
                "pas un mouvement propre à un jeton."
            )
        else:
            phrases.append(
                f"Sur sept jours, le bitcoin fait {_fr(sept, 1, signe=True)} %{detail_jour}{largeur}, dans le même sens que le mois, "
                "ce qui montre que le mouvement mensuel se poursuit sans pause."
            )

    # 3. Ether contre bitcoin, confronté à la mesure de rotation.
    rotation = _lire(rapport, "rotation.synthese", {})
    ecart_eth = None
    if btc.get("variation_30j_pct") is not None and eth.get("variation_30j_pct") is not None:
        ecart_eth = float(eth["variation_30j_pct"]) - float(btc["variation_30j_pct"])
    if rotation.get("etat"):
        etat_brut = str(rotation["etat"])
        etat = _LIBELLE_ROTATION.get(etat_brut, etat_brut)
        exprimees = rotation.get("n_mesures_exprimees")
        total = rotation.get("n_mesures_total")
        if exprimees is None:
            exprimees = len([c for c in (rotation.get("contributions") or []) if not c.get("abstention")])
        compte = f"sur {_lettres(int(exprimees), feminin=True)} mesure{'s' if int(exprimees) > 1 else ''} exploitable{'s' if int(exprimees) > 1 else ''}"
        if total:
            compte += f" sur {_lettres(int(total))}"
        if etat_brut == "rotation_alts":
            effet = "ce qui signifie que l'argent quitte le bitcoin pour des jetons plus risqués : l'appétit s'élargit"
        elif etat_brut == "dominance_btc":
            effet = "ce qui signifie que l'argent se replie sur l'actif le plus liquide : l'appétit se rétrécit"
        else:
            effet = "ce qui laisse sans réponse la question de savoir si l'appétit s'élargit ou se rétrécit"
        amorce = ""
        if ecart_eth is not None and abs(ecart_eth) >= 3.0:
            if ecart_eth > 0:
                amorce = "L'ether fait mieux que le bitcoin sur trente jours, ce qui va dans le sens d'un appétit qui s'élargit aux jetons plus risqués ; "
            else:
                amorce = "Le bitcoin fait mieux que l'ether sur trente jours, ce qui va dans le sens d'un repli vers l'actif le plus liquide ; "
            phrases.append(
                f"{amorce}la mesure de rotation, elle, est {etat} {compte}, {effet}"
                + (" — ce signal reste donc sans confirmation." if etat_brut not in ("rotation_alts", "dominance_btc") else ".")
            )
        else:
            phrases.append(f"La rotation entre bitcoin et alternatives est {etat} {compte}, {effet}.")

    # 4. Régimes de détention.
    regimes = []
    for cle, nom in (("regime.regime_btc", "le bitcoin"), ("regime.regime_eth", "l'ether")):
        bloc = _lire(rapport, cle, {})
        if bloc.get("disponible") and bloc.get("mvrv") is not None:
            regimes.append((nom, bloc.get("regime", "indéterminé"), float(bloc["mvrv"])))
    if regimes:
        constat = " et ".join(f"{nom} en régime {reg} (MVRV {_fr(mvrv, 2)})" for nom, reg, mvrv in regimes)
        mvrv_max = max(m for _, _, m in regimes)
        if mvrv_max >= 3.0:
            effet = (
                "ce qui signifie que le détenteur moyen porte une plus-value telle que la tentation de "
                "prendre ses gains grandit : la pression vendeuse potentielle est élevée"
            )
        elif mvrv_max < 1.0:
            effet = (
                "ce qui signifie que le détenteur moyen est en perte latente : ceux qui restent n'ont pas "
                "cédé à perte, et la pression vendeuse est faible"
            )
        else:
            effet = (
                "ce qui signifie une plus-value latente modérée : peu d'incitation à céder en masse, "
                "et donc une pression vendeuse contenue"
            )
        phrases.append(f"Côté détention, {constat}, {effet}.")

    # 5. Levier — ou une incise quand il n'est pas mesurable.
    funding = _lire(rapport, "positionnement.funding", {})
    if funding.get("disponible") and funding.get("percentile") is not None:
        pct = float(funding["percentile"])
        if pct >= 90:
            effet = "ce qui signifie que le levier est tendu du côté acheteur : un repli déclencherait des liquidations en chaîne"
        elif pct <= 10:
            effet = "ce qui signifie que le levier est tendu du côté vendeur : une hausse déclencherait des rachats forcés"
        else:
            effet = "ce qui ne fait pas du levier un amplificateur dans un sens ni dans l'autre"
        phrases.append(
            f"Le financement des positions à levier se situe au {_fr(pct, 0)}e percentile de son historique récent, {effet}."
        )
    elif funding:
        phrases.append(
            "Le financement des positions à levier n'est pas mesurable ce jour, ce qui laisse la question du "
            "levier sans réponse : aucun amplificateur identifié, ni à la hausse ni à la baisse."
        )

    # 6. Facteur commun, confronté au mois, puis la liquidité.
    phrase_appetit = _phrase_appetit(facteur, "crypto")
    if phrase_appetit:
        phrases.append(phrase_appetit)
        niveau = (facteur or {}).get("appetit", {}).get("niveau")
        contrastes = {
            ("hausse", "risk-on"): "La hausse mensuelle va dans le sens de ce climat, ce qui la rend dépendante de sa persistance.",
            ("hausse", "risk-off"): "La hausse mensuelle coexiste avec un climat défavorable, ce qui désigne des causes internes au marché crypto plutôt qu'un appétit général.",
            ("baisse", "risk-on"): "Le repli mensuel coexiste avec un climat porteur, ce qui désigne des causes internes au marché crypto — levier, offre — plutôt qu'un retrait général du risque.",
            ("baisse", "risk-off"): "Le repli mensuel est cohérent avec ce climat, ce qui empêche de l'attribuer au seul marché crypto.",
        }
        contraste = contrastes.get((direction_mois, niveau))
        if contraste:
            phrases.append(contraste)
    liquidite = (facteur or {}).get("liquidite")
    if liquidite and liquidite.get("niveau"):
        niveau_liq = str(liquidite["niveau"])
        if "hausse" in niveau_liq or "expansion" in niveau_liq:
            effet = "ce qui alimente les actifs qui n'ont ni rendement ni flux de trésorerie, cryptos en tête"
        elif "baisse" in niveau_liq or "contraction" in niveau_liq:
            effet = "ce qui assèche en premier les actifs qui n'ont ni rendement ni flux de trésorerie, cryptos en tête"
        else:
            effet = "ce qui ne fournit ni carburant ni frein aux actifs sans rendement"
        phrases.append(f"La liquidité nette, relevée dans le contexte macro de l'or, est {niveau_liq}, {effet}.")

    if len(phrases) < 2:
        return _refus(rapport, "synthèse impossible : ni régime, ni rotation, ni prix ce jour")
    return verifier_synthese(" ".join(phrases), rapport)
