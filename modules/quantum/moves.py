"""Détection et explication des mouvements de prix des valeurs quantiques.

La question à laquelle ce module répond
---------------------------------------
« RGTI a perdu 16 % » n'apprend rien. Les deux questions qui comptent sont :
*le reste du secteur a-t-il bougé pareil ?* et *si oui, qu'est-ce qui a bougé
en amont ?* Un titre qui chute seul et un titre qui chute avec ses cinq
comparables ne racontent pas la même histoire, et la seconde a le plus
souvent une cause macro qu'on peut nommer.

L'épisode qui calibre ce module
-------------------------------
Fin août 2026, Pasqal entre au Nasdaq par fusion SPAC, bondit d'environ 60 %
le premier jour, atteint 24,69 $ le 31 août, puis perd 58,4 % en une semaine
pour finir vers 7,96 $ le 4 septembre. Lu seul, le titre paraît sinistré.

Or le mouvement n'a rien de propre à Pasqal : les taux longs américains
remontaient, ce qui pénalise mécaniquement toutes les valeurs à bêta élevé et
flux de trésorerie négatifs — quantique, nucléaire, spatial ensemble. La
valeur d'une société sans bénéfices tient dans des flux lointains ; quand le
taux qui les actualise monte, leur valeur présente baisse, et ce
raisonnement ne dit rien sur la société elle-même.

Fait notable en sens inverse le même jour : un administrateur, Bpifrance
Investissement, achetait plus de 1,3 million d'actions, visible dans un
Form 4. Le module doit faire remonter ce genre d'élément **sans trancher**
entre lui et la baisse. C'est l'objet du champ ``signaux_contradictoires``.

D'où la discipline : ne jamais s'arrêter à la variation, toujours comparer au
secteur, chercher une cause macro avant une cause spécifique, et montrer les
éléments discordants au lieu de les arbitrer.

Interdiction de recommander
---------------------------
Aucun texte produit ici ne doit suggérer une action. La contrainte est
vérifiée par :func:`verifier_absence_recommandation`, qui parcourt la sortie
et signale tout motif interdit. Le point d'entrée l'appelle avant publication.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Final, Iterable

import pandas as pd

_LOG: Final = logging.getLogger(__name__)

#: Motifs de recommandation interdits dans tout texte produit par ce paquet.
#:
#: La liste vise les formulations qui poussent à agir, en français et en
#: anglais. Elle est volontairement large : un faux positif coûte une
#: reformulation, un faux négatif laisse passer un conseil d'investissement
#: dans un outil qui n'a pas le droit d'en donner.
MOTIFS_RECOMMANDATION: Final[tuple[str, ...]] = (
    r"\bachet(?:er|ez|ons|é|ée|és)\b",
    r"\bvend(?:re|ez|ons|u|ue)\b",
    r"\bposition(?:ner|nez|nons)\b",
    r"\binvesti(?:r|ssez|ssons)\b",
    r"\brenforce(?:r|z|ons)\b",
    r"\balléger\b|\ballege(?:r|z)\b",
    r"\bopportunité\b",
    r"\bà l'achat\b|\bà la vente\b",
    r"\bpoint d'entrée\b|\bniveau d'entrée\b",
    r"\bprendre position\b|\bprise de position\b",
    r"\bmérite\b",
    r"\bintéressant(?:e|s|es)?\b",
    r"\battractif(?:ve|s|ves)?\b",
    r"\bsous-évalué(?:e|s|es)?\b|\bsurévalué(?:e|s|es)?\b",
    r"\brecommand(?:er|ation|é|ée|ons|ez)\b",
    r"\bconseill(?:er|é|ée|ons|ez)\b",
    r"\bbuy\b|\bsell\b|\bhold\b",
    r"\bstrong buy\b|\bprice target\b",
    r"\bshould (?:buy|sell|invest)\b",
)

#: Clés dont le contenu est repris **verbatim** d'une source externe et n'est
#: donc pas écrit par ce paquet.
#:
#: Un titre de presse peut parfaitement contenir « buy » ou « acheter » sans
#: que le module recommande quoi que ce soit : c'est une citation, pas un
#: avis. Les exclure évite un faux positif qui, à force, ferait désactiver le
#: garde-fou — ce qui serait bien pire que de laisser passer une citation.
#:
#: ``titre_affiche`` et ``url_source`` sont les mêmes citations que ``titre``
#: et ``url`` ci-dessus, sous les noms qu'elles portent dans le format de
#: sortie unifié des fils (``feed.CLES_ITEM``) plutôt que dans le contexte
#: interne d'analyse : un fil entier serait sinon bloqué par le titre d'une
#: seule dépêche externe, aperçu en pratique sur un communiqué crypto
#: (« Best Crypto To Buy Now »).
CLES_CITATION: Final[frozenset[str]] = frozenset(
    {
        "titre", "url", "resume", "description", "source", "query", "keywords",
        "libelle_source", "titre_affiche", "url_source",
    }
)

#: Clés portant les avertissements du système sur sa propre nature.
#:
#: Ces textes sont le cas paradoxal du contrôle : pour affirmer qu'un rapport
#: ne contient aucune recommandation d'achat ou de vente, il faut bien écrire
#: les mots « recommandation », « achat » et « vente ». La phrase déclenche
#: donc les motifs qu'elle sert justement à garantir. Les exclure n'affaiblit
#: rien — ce sont des dénégations, pas des conseils —, et ne pas le faire
#: rendrait toute publication impossible.
#:
#: ``infractions`` et ``extrait`` sont exclus pour la même raison : ils citent
#: les formulations fautives détectées, et recontrôler un rapport déjà
#: contrôlé les signalerait en boucle.
CLES_AVERTISSEMENT: Final[frozenset[str]] = frozenset(
    {
        "nature_du_rapport",
        "avertissement",
        "avertissement_heures",
        "limite_connue",
        "infractions",
        "extrait",
        "motifs_interdits",
    }
)

#: Ensemble des clés soustraites au contrôle, pour les deux raisons ci-dessus.
CLES_EXCLUES: Final[frozenset[str]] = CLES_CITATION | CLES_AVERTISSEMENT

#: Classifications possibles d'un mouvement.
SECTORIEL: Final = "sectoriel"
SPECIFIQUE: Final = "specifique"

__all__ = [
    "Mouvement",
    "MOTIFS_RECOMMANDATION",
    "CLES_EXCLUES",
    "verifier_absence_recommandation",
    "variations_du_jour",
    "variation_derniere_seance",
    "detecter_mouvements",
]


# ---------------------------------------------------------------------------
# Garde-fou : aucune recommandation
# ---------------------------------------------------------------------------
_MOTIFS_COMPILES: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(m, flags=re.IGNORECASE) for m in MOTIFS_RECOMMANDATION
)


def verifier_absence_recommandation(
    objet: Any,
    chemin: str = "",
    cles_exclues: Iterable[str] = CLES_EXCLUES,
) -> list[dict[str, str]]:
    """Parcourt une structure et signale tout motif de recommandation.

    Le parcours est récursif et s'applique à toutes les chaînes de la sortie,
    sauf celles dont la clé figure dans ``cles_exclues``, pour deux raisons
    distinctes : soit leur contenu est cité d'une source externe et n'engage
    pas le module (:data:`CLES_CITATION`), soit il s'agit d'un avertissement
    du système sur sa propre nature, qui doit nommer ce qu'il exclut pour
    pouvoir l'exclure (:data:`CLES_AVERTISSEMENT`).

    Args:
        objet: structure à contrôler, typiquement un rapport sérialisé.
        chemin: chemin courant, utilisé pour localiser une infraction.
        cles_exclues: clés soustraites au contrôle.

    Returns:
        Liste d'infractions, chacune avec son ``chemin``, le ``motif``
        déclenché et l'``extrait`` fautif. Liste vide si la sortie est saine.
    """
    exclues = set(cles_exclues)
    infractions: list[dict[str, str]] = []

    def _parcourir(noeud: Any, ou: str) -> None:
        """Descend récursivement dans la structure."""
        if isinstance(noeud, str):
            for motif in _MOTIFS_COMPILES:
                trouve = motif.search(noeud)
                if trouve:
                    debut = max(0, trouve.start() - 40)
                    infractions.append(
                        {
                            "chemin": ou or "(racine)",
                            "motif": motif.pattern,
                            "extrait": noeud[debut : trouve.end() + 40].strip(),
                        }
                    )
        elif isinstance(noeud, dict):
            for cle, valeur in noeud.items():
                if str(cle) in exclues:
                    continue
                _parcourir(valeur, f"{ou}.{cle}" if ou else str(cle))
        elif isinstance(noeud, (list, tuple)):
            for i, element in enumerate(noeud):
                _parcourir(element, f"{ou}[{i}]")

    _parcourir(objet, chemin)
    if infractions:
        _LOG.error(
            "Formulation de recommandation détectée dans la sortie : %s",
            "; ".join(f"{i['chemin']} → {i['extrait'][:60]}" for i in infractions[:3]),
        )
    return infractions


# ---------------------------------------------------------------------------
# Variations
# ---------------------------------------------------------------------------
def variations_du_jour(prix: dict[str, pd.DataFrame]) -> dict[str, dict[str, Any]]:
    """Calcule la variation de la dernière séance pour chaque valeur.

    Args:
        prix: historiques par ticker, au format de :mod:`dataio.market`.

    Returns:
        Par ticker : ``variation_pct``, ``cloture``, ``cloture_precedente``,
        ``date``. Les valeurs sans historique exploitable sont absentes.
    """
    resultat: dict[str, dict[str, Any]] = {}
    for ticker, cadre in (prix or {}).items():
        if cadre is None or cadre.empty or "close" not in cadre.columns:
            _LOG.warning("Pas d'historique exploitable pour %s.", ticker)
            continue
        serie = cadre["close"].dropna()
        if len(serie) < 2:
            _LOG.warning("Moins de deux séances pour %s : variation non calculable.", ticker)
            continue

        precedente = float(serie.iloc[-2])
        if precedente == 0.0:
            continue
        courante = float(serie.iloc[-1])
        resultat[ticker] = {
            "variation_pct": (courante / precedente - 1.0) * 100.0,
            "cloture": courante,
            "cloture_precedente": precedente,
            "date": str(pd.Timestamp(serie.index[-1]).date()),
        }
    return resultat


def variation_derniere_seance(serie: pd.Series | None, en_points: bool = False) -> float | None:
    """Variation de la dernière observation d'une série.

    Args:
        serie: série observée.
        en_points: ``True`` pour une différence en niveau (cas d'un taux),
            ``False`` pour une variation relative en pourcentage.

    Returns:
        La variation, ou ``None`` si la série est trop courte.
    """
    if serie is None:
        return None
    propre = serie.dropna()
    if len(propre) < 2:
        return None
    courante, precedente = float(propre.iloc[-1]), float(propre.iloc[-2])
    if en_points:
        return courante - precedente
    return None if precedente == 0.0 else (courante / precedente - 1.0) * 100.0


# ---------------------------------------------------------------------------
# Résultat
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class Mouvement:
    """Un mouvement de prix marqué, et ce qui l'entoure.

    Attributes:
        ticker: valeur concernée.
        nom: raison sociale.
        variation_pct: variation de la séance, en pourcentage.
        seuil_pct: seuil qui a déclenché l'analyse.
        classification: ``sectoriel`` ou ``specifique``.
        variations_secteur: variation du jour des autres valeurs suivies.
        valeurs_concordantes: valeurs allant dans le même sens avec une
            amplitude comparable.
        contexte_macro: variations des taux réels et de l'indice de référence.
        actualites: articles trouvés sur la valeur, cités tels quels.
        depots_sec: dépôts réglementaires récents.
        activite_inities: dépôts Form 4 récents.
        signaux_contradictoires: éléments allant dans des sens opposés,
            listés sans être arbitrés.
        constat: description factuelle, sans jugement ni conseil.
    """

    ticker: str
    nom: str
    variation_pct: float
    seuil_pct: float
    classification: str
    variations_secteur: dict[str, float] = field(default_factory=dict)
    valeurs_concordantes: list[str] = field(default_factory=list)
    contexte_macro: dict[str, Any] = field(default_factory=dict)
    actualites: list[dict[str, Any]] = field(default_factory=list)
    depots_sec: list[dict[str, Any]] = field(default_factory=list)
    activite_inities: dict[str, Any] = field(default_factory=dict)
    signaux_contradictoires: list[dict[str, str]] = field(default_factory=list)
    constat: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Sérialise le mouvement pour le rapport JSON."""
        return {
            "ticker": self.ticker,
            "nom": self.nom,
            "variation_pct": round(self.variation_pct, 3),
            "seuil_pct": self.seuil_pct,
            "classification": self.classification,
            "variations_secteur": {k: round(v, 3) for k, v in self.variations_secteur.items()},
            "valeurs_concordantes": list(self.valeurs_concordantes),
            "contexte_macro": dict(self.contexte_macro),
            "actualites": list(self.actualites),
            "depots_sec": list(self.depots_sec),
            "activite_inities": dict(self.activite_inities),
            "signaux_contradictoires": list(self.signaux_contradictoires),
            "constat": self.constat,
        }


def _classer(
    ticker: str,
    variation: float,
    variations: dict[str, dict[str, Any]],
    min_concordantes: int,
    ratio_amplitude: float,
) -> tuple[str, list[str], dict[str, float]]:
    """Décide si un mouvement est sectoriel ou spécifique.

    Le critère : au moins ``min_concordantes`` autres valeurs allant dans le
    **même sens** avec au moins ``ratio_amplitude`` de l'amplitude du titre
    déclencheur. Le seuil d'amplitude est indispensable — sans lui, un secteur
    en baisse de 0,2 % « confirmerait » une chute de 16 %.

    Args:
        ticker: valeur déclenchante.
        variation: sa variation du jour.
        variations: variations de toutes les valeurs suivies.
        min_concordantes: nombre minimal de valeurs concordantes.
        ratio_amplitude: fraction d'amplitude exigée.

    Returns:
        Triplet ``(classification, concordantes, variations_des_autres)``.
    """
    autres = {t: float(v["variation_pct"]) for t, v in variations.items() if t != ticker}
    seuil_amplitude = abs(variation) * ratio_amplitude

    concordantes = [
        t for t, v in autres.items()
        # Même signe, et amplitude suffisante pour que la concordance ait un sens.
        if v * variation > 0.0 and abs(v) >= seuil_amplitude
    ]
    classification = SECTORIEL if len(concordantes) >= min_concordantes else SPECIFIQUE
    return classification, sorted(concordantes), autres


def _constat(
    ticker: str,
    nom: str,
    variation: float,
    classification: str,
    concordantes: list[str],
    macro: dict[str, Any],
) -> str:
    """Rédige la description factuelle du mouvement.

    Le texte décrit ce qui est observé et s'arrête là. Il ne conclut pas, ne
    qualifie pas le titre et ne suggère aucune action : la fonction
    :func:`verifier_absence_recommandation` contrôle cette propriété sur la
    sortie complète.

    Args:
        ticker: valeur concernée.
        nom: raison sociale.
        variation: variation du jour.
        classification: ``sectoriel`` ou ``specifique``.
        concordantes: valeurs ayant bougé de concert.
        macro: contexte macro du jour.

    Returns:
        Le constat, en français.
    """
    sens = "recule" if variation < 0 else "progresse"
    phrases = [f"{nom} ({ticker}) {sens} de {abs(variation):.1f} % sur la séance."]

    if classification == SECTORIEL:
        phrases.append(
            f"{len(concordantes)} autre(s) valeur(s) du secteur ({', '.join(concordantes)}) "
            "évoluent dans le même sens avec une amplitude comparable : le mouvement "
            "n'est pas propre à cette société."
        )
        variation_taux = macro.get("variation_taux_reels_points")
        if variation_taux is not None and abs(float(variation_taux)) >= float(
            macro.get("seuil_variation_taux_points", 0.03)
        ):
            hausse_taux = float(variation_taux) > 0.0
            if hausse_taux and variation < 0.0:
                phrases.append(
                    f"Les taux réels 10 ans montent de {abs(float(variation_taux)) * 100:.0f} "
                    "points de base le même jour. Ce schéma — taux réels en hausse, valeurs "
                    "à flux de trésorerie négatifs en baisse — est celui qui a produit la "
                    "chute de 58 % de Pasqal début septembre 2026 : la valeur d'une société "
                    "sans bénéfices tient dans des flux lointains, et le taux qui les "
                    "actualise vient de monter. Ce constat ne dit rien de la société elle-même."
                )
            else:
                phrases.append(
                    f"Les taux réels 10 ans varient de {float(variation_taux) * 100:+.0f} "
                    "points de base le même jour."
                )
        variation_marche = macro.get("variation_marche_pct")
        if variation_marche is not None:
            phrases.append(
                f"L'indice de référence varie de {float(variation_marche):+.1f} % sur la séance."
            )
    else:
        phrases.append(
            "Aucune autre valeur du secteur ne bouge de façon comparable : le mouvement "
            "paraît propre à cette société. Les éléments réunis ci-dessous — actualité "
            "et dépôts réglementaires — sont rapportés sans être hiérarchisés."
        )
    return " ".join(phrases)


def _decrire_declarant(identite: dict[str, Any] | None) -> str:
    """Nomme le déclarant d'une opération, ou constate qu'il est inconnu.

    Args:
        identite: bloc ``identite`` d'une opération, éventuellement ``None``.

    Returns:
        Une désignation lisible, jamais vide.
    """
    if not identite or not identite.get("nom"):
        return "un déposant non identifié"

    roles = {
        "administrateur": "administrateur",
        "dirigeant": "dirigeant",
        "actionnaire_10pct": "actionnaire à plus de 10 %",
    }
    nom = str(identite["nom"])
    role = roles.get(str(identite.get("role") or ""), "")
    titre = str(identite.get("titre_fonction") or "").strip()

    if role and titre:
        return f"{nom}, {role} ({titre})"
    if role:
        return f"{nom}, {role}"
    return nom


def _decrire_operation(operation: dict[str, Any]) -> str:
    """Décrit une opération d'initié en une phrase factuelle.

    La description dit ce qui est déclaré et rien de plus. Elle ne juge pas
    l'opération et ne la présente jamais comme un signal à suivre.

    Args:
        operation: opération sérialisée par ``sec_filings``.

    Returns:
        La description, en français.
    """
    qui = _decrire_declarant(operation.get("identite"))
    titres = operation.get("nombre_titres")
    quantite = f"{int(titres):,}".replace(",", " ") if titres else "un nombre non précisé de"

    sens = str(operation.get("sens", "indetermine"))
    if sens == "achat":
        action = f"achat de {quantite} titres sur le marché"
    elif sens == "vente":
        action = f"vente de {quantite} titres sur le marché"
    else:
        libelle = str(operation.get("libelle_code") or "opération de nature non précisée")
        action = f"opération portant sur {quantite} titres ({libelle})"

    phrase = f"{action} par {qui}"
    valeur = operation.get("valeur_totale_usd")
    if valeur:
        # Le séparateur de milliers est remplacé sur le seul nombre : appliqué
        # à la phrase entière, il effacerait aussi la virgule de ponctuation.
        montant = f"{float(valeur):,.0f}".replace(",", " ")
        phrase += f", pour {montant} $"
    date_operation = operation.get("date_transaction")
    if date_operation:
        phrase += f", déclarée le {date_operation}"
    return phrase


def _signaux_contradictoires(
    variation: float,
    activite_inities: dict[str, Any],
    depots: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Relève les éléments qui pointent dans des sens opposés.

    Le module ne tranche pas : il pose les faits côte à côte. Un titre qui
    chute pendant qu'un administrateur déclare un achat est une situation
    ambiguë, et la présenter comme telle est plus honnête que de choisir
    laquelle des deux observations compte.

    Les opérations dont le sens reste indéterminé — exercices d'options,
    attributions, retenues fiscales — ne sont **pas** comptées comme des
    contradictions : elles ne traduisent aucune décision de marché, et les
    présenter comme un contrepoint à une baisse serait trompeur.

    Args:
        variation: variation du jour.
        activite_inities: sortie de ``detect_insider_activity``.
        depots: dépôts réglementaires récents.

    Returns:
        Liste de tensions constatées.
    """
    signaux: list[dict[str, str]] = []
    operations = list(activite_inities.get("transactions") or [])

    achats = [o for o in operations if o.get("sens") == "achat"]
    ventes = [o for o in operations if o.get("sens") == "vente"]

    if achats and variation < 0.0:
        details = " ; ".join(_decrire_operation(o) for o in achats[:3])
        signaux.append(
            {
                "nature": "baisse du titre et achat d'initié déclaré",
                "constat": (
                    f"Le titre recule de {abs(variation):.1f} % alors qu'un achat sur le "
                    f"marché a été déclaré : {details}. Le fait est rapporté tel quel — "
                    "les motifs d'un initié ne figurent dans aucune donnée publique."
                ),
            }
        )

    if ventes and variation > 0.0:
        details = " ; ".join(_decrire_operation(o) for o in ventes[:3])
        signaux.append(
            {
                "nature": "hausse du titre et vente d'initié déclarée",
                "constat": (
                    f"Le titre progresse de {variation:.1f} % alors qu'une vente sur le "
                    f"marché a été déclarée : {details}. Une vente peut relever d'un plan "
                    "programmé à l'avance : le fait est signalé, pas interprété."
                ),
            }
        )

    types_dilutifs = {"S-3", "424B5", "S-1"}
    dilutifs = [
        d for d in depots
        if any(str(d.get("type", "")).upper().startswith(t) for t in types_dilutifs)
    ]
    if dilutifs and variation > 0.0:
        signaux.append(
            {
                "nature": "hausse du titre et dépôt de levée de fonds",
                "constat": (
                    f"Le titre progresse de {variation:.1f} % alors qu'un dépôt de type "
                    f"{dilutifs[0].get('type')} a été enregistré, lequel ouvre la voie à "
                    "une émission d'actions nouvelles."
                ),
            }
        )
    return signaux


def detecter_mouvements(
    prix: dict[str, pd.DataFrame],
    watchlist: list[dict[str, Any]],
    configuration: dict[str, Any],
    variation_taux_reels: float | None = None,
    variation_marche_pct: float | None = None,
    actualites_par_ticker: dict[str, list[dict[str, Any]]] | None = None,
    depots_par_ticker: dict[str, list[dict[str, Any]]] | None = None,
    inities_par_ticker: dict[str, dict[str, Any]] | None = None,
) -> list[Mouvement]:
    """Détecte et documente les mouvements dépassant leur seuil.

    Args:
        prix: historiques par ticker.
        watchlist: entrées ``quantum_watchlist`` de la configuration.
        configuration: bloc ``quantum`` de la configuration.
        variation_taux_reels: variation du jour du taux réel 10 ans, en points
            de pourcentage.
        variation_marche_pct: variation du jour de l'indice de référence.
        actualites_par_ticker: articles déjà collectés, par ticker.
        depots_par_ticker: dépôts SEC déjà collectés, par ticker.
        inities_par_ticker: activité d'initiés déjà collectée, par ticker.

    Returns:
        Mouvements détectés, du plus ample au moins ample.
    """
    variations = variations_du_jour(prix)
    if not variations:
        _LOG.warning("Aucune variation calculable : détection impossible.")
        return []

    min_concordantes = int(configuration.get("min_valeurs_concordantes", 2))
    ratio_amplitude = float(configuration.get("ratio_amplitude_min", 0.40))
    seuil_taux = float(configuration.get("seuil_variation_taux_pct", 0.03))

    mouvements: list[Mouvement] = []
    for entree in watchlist:
        ticker = str(entree.get("ticker", "")).upper()
        mesure = variations.get(ticker)
        if mesure is None:
            continue

        variation = float(mesure["variation_pct"])
        seuil = float(entree.get("seuil_mouvement_pct", 8.0))
        if abs(variation) < seuil:
            continue

        classification, concordantes, autres = _classer(
            ticker, variation, variations, min_concordantes, ratio_amplitude
        )
        macro = {
            "variation_taux_reels_points": variation_taux_reels,
            "seuil_variation_taux_points": seuil_taux,
            "variation_marche_pct": variation_marche_pct,
            "serie_taux_reels": configuration.get("serie_taux_reels", "DFII10"),
            "reference_marche": configuration.get("reference_marche", "QQQ"),
        }

        # Actualité et dépôts ne sont réunis que pour un mouvement spécifique :
        # sur un mouvement sectoriel, la cause est ailleurs, et remonter des
        # dépêches sur la société entretiendrait une explication fausse.
        actualites = (
            list((actualites_par_ticker or {}).get(ticker, []))
            if classification == SPECIFIQUE
            else []
        )
        depots = (
            list((depots_par_ticker or {}).get(ticker, []))
            if classification == SPECIFIQUE
            else []
        )
        inities = dict((inities_par_ticker or {}).get(ticker, {}))

        mouvements.append(
            Mouvement(
                ticker=ticker,
                nom=str(entree.get("name", ticker)),
                variation_pct=variation,
                seuil_pct=seuil,
                classification=classification,
                variations_secteur=autres,
                valeurs_concordantes=concordantes,
                contexte_macro=macro,
                actualites=actualites,
                depots_sec=depots,
                activite_inities=inities,
                signaux_contradictoires=_signaux_contradictoires(variation, inities, depots),
                constat=_constat(
                    ticker, str(entree.get("name", ticker)), variation,
                    classification, concordantes, macro,
                ),
            )
        )

    mouvements.sort(key=lambda m: abs(m.variation_pct), reverse=True)
    _LOG.info(
        "Mouvements détectés : %d (%d sectoriel(s), %d spécifique(s)).",
        len(mouvements),
        sum(1 for m in mouvements if m.classification == SECTORIEL),
        sum(1 for m in mouvements if m.classification == SPECIFIQUE),
    )
    return mouvements
