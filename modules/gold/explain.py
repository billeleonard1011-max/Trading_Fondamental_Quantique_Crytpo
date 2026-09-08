"""Couche pédagogique : mettre les chiffres en français lisible.

Rôle et limite
--------------
Ce module explique, il ne conseille pas. Il ne dit jamais d'acheter, de
vendre, ni où placer un stop. Il prend les chiffres déjà calculés par les
autres modules et les rend compréhensibles à quelqu'un qui n'a pas la thèse
du système en tête.

Chaque métrique est expliquée en quatre parties, toujours dans le même ordre :

1. **Ce que mesure l'indicateur**, en français simple ;
2. **Où se situe la valeur du jour** sur son échelle historique ;
3. **Pourquoi ça compte** pour quelqu'un qui trade l'or ;
4. **Ce que les précédents indiquent**, ou l'absence de précédents.

Le garde-fou numérique
----------------------
Un modèle de langage à qui l'on donne « z-score : 1,76 » écrira volontiers
« environ 1,8 », ce qui est acceptable, mais aussi « soit le 94e percentile »
alors que ce chiffre n'existe nulle part dans les données. Le second cas est
indétectable à la lecture et parfaitement crédible : c'est exactement ce qui
rend l'erreur dangereuse.

La parade est mécanique, pas déclarative. Après génération, **tous** les
nombres du texte produit sont extraits par expression régulière et comparés
à l'ensemble des valeurs présentes dans le JSON d'entrée, à une tolérance
d'arrondi près. Un seul nombre sans correspondance suffit à rejeter le
texte : une deuxième tentative est faite avec un rappel explicite de la
contrainte, et si elle échoue à son tour, le module retombe sur les gabarits
plutôt que de publier un texte non vérifié.

Un texte pauvre mais exact vaut mieux qu'un texte élégant et faux.

Mode dégradé
------------
Sans clé d'API, sans quota, sans réseau ou après échec de la vérification,
le module produit le même découpage en quatre parties à partir de gabarits.
Le système reste donc entièrement fonctionnel sans OpenAI : la couche
pédagogique est un confort, jamais une dépendance.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Final

_LOG: Final = logging.getLogger(__name__)

#: Modèle par défaut : suffisant pour de la reformulation sous contrainte.
MODELE_DEFAUT: Final = "gpt-4o-mini"

#: Tolérance relative du vérificateur numérique.
TOLERANCE_RELATIVE: Final[float] = 0.02

#: Tolérance absolue, pour les valeurs proches de zéro où le relatif explose.
TOLERANCE_ABSOLUE: Final[float] = 0.005

#: Délai maximal, en secondes, accordé à l'appel OpenAI.
TIMEOUT: Final[float] = float(os.environ.get("OPENAI_TIMEOUT", "45"))

#: Nombres extraits du texte. Gère « 1 234,56 », « 1,234.56 », « -0.9 », « +2 ».
#:
#: Le motif distingue les séparateurs de milliers du séparateur décimal :
#: un groupe de milliers compte exactement trois chiffres, une décimale un
#: nombre quelconque. Sans cette distinction, « 4,429.80 » se lirait comme
#: deux nombres — 4,429 puis 80 — et la vérification rejetterait un texte
#: pourtant exact. Les espaces acceptés comme séparateurs de milliers
#: incluent l'espace insécable et l'espace fine, que les modèles emploient
#: volontiers dans les grands nombres.
_MOTIF_NOMBRE: Final = re.compile(
    r"[-+]?\d+(?:[ \u00a0\u202f,.]\d{3})*(?:[.,]\d+)?"
)

#: Puces et numérotations de liste, retirées avant extraction : « 3. » en
#: début de ligne est un marqueur de liste, pas une affirmation chiffrée.
_MOTIF_PUCE: Final = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+", flags=re.MULTILINE)

CONSIGNE_SYSTEME: Final = """Tu es un pédagogue financier. Tu expliques des indicateurs sur l'or à un trader qui les découvre.

RÈGLES ABSOLUES, sans exception :
1. N'utilise QUE les nombres présents dans le JSON fourni. Tu peux les arrondir, jamais en inventer un autre.
2. N'invente aucun pourcentage, aucun percentile, aucune statistique, aucune date qui ne soit dans le JSON.
3. Ne recommande JAMAIS d'acheter, de vendre, d'entrer, de sortir, ni où placer un stop. Tu expliques, tu ne conseilles pas.
4. Quand une donnée est absente ou marquée indisponible, dis-le explicitement au lieu de la contourner.
5. Écris en français simple, sans jargon non expliqué. Pas de liste numérotée, pas de puces : des paragraphes.
6. Dans le doute sur un chiffre, n'en cite aucun et décris la situation avec des mots.

STRUCTURE, quatre paragraphes courts, dans cet ordre, sans titre :
- ce que mesure l'indicateur ;
- où se situe la valeur du jour sur son échelle historique ;
- pourquoi cela compte pour quelqu'un qui trade l'or ;
- ce que les précédents historiques indiquent, ou qu'il n'y en a pas.

Maximum 200 mots au total."""

__all__ = [
    "ResultatExplication",
    "extraire_nombres",
    "valeurs_autorisees",
    "verifier_nombres",
    "expliquer",
    "expliquer_rapport",
]


@dataclass(slots=True, frozen=True)
class ResultatExplication:
    """Texte produit et conditions de sa production.

    Attributes:
        texte: explication finale, toujours renseignée.
        mode: ``openai`` ou ``gabarit``.
        modele: modèle utilisé, vide en mode gabarit.
        tentatives: nombre d'appels effectués.
        nombres_rejetes: nombres hallucinés détectés, s'il y en a eu.
        motif_repli: raison du repli sur les gabarits, vide sinon.
    """

    texte: str
    mode: str
    modele: str = ""
    tentatives: int = 0
    nombres_rejetes: list[float] = field(default_factory=list)
    motif_repli: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Sérialise le résultat pour le rapport JSON."""
        return {
            "texte": self.texte,
            "mode": self.mode,
            "modele": self.modele,
            "tentatives": self.tentatives,
            "nombres_rejetes": list(self.nombres_rejetes),
            "motif_repli": self.motif_repli,
            "verifie_numeriquement": self.mode == "openai",
        }


# ---------------------------------------------------------------------------
# Garde-fou numérique
# ---------------------------------------------------------------------------
def _en_flottant(brut: str) -> float | None:
    """Interprète un nombre écrit à la française ou à l'anglaise.

    « 1 234,56 » et « 1,234.56 » désignent la même valeur. La règle retenue :
    le dernier séparateur suivi de un à trois chiffres et précédé d'autres
    chiffres est décimal si c'est une virgule isolée, sinon les virgules sont
    des séparateurs de milliers.

    Args:
        brut: nombre tel qu'il apparaît dans le texte.

    Returns:
        La valeur, ou ``None`` si elle n'est pas interprétable.
    """
    texte = brut.strip().replace(" ", "").replace(" ", "").replace(" ", "")
    if not texte or texte in {"+", "-"}:
        return None

    if "," in texte and "." in texte:
        # Le séparateur décimal est le dernier des deux.
        if texte.rfind(",") > texte.rfind("."):
            texte = texte.replace(".", "").replace(",", ".")
        else:
            texte = texte.replace(",", "")
    elif "," in texte:
        partie = texte.rsplit(",", 1)[-1]
        # « 1,234 » est ambigu ; trois chiffres après la virgule sont plus
        # souvent des milliers qu'une précision au millième dans ce contexte.
        texte = texte.replace(",", "") if len(partie) == 3 else texte.replace(",", ".")

    try:
        return float(texte)
    except ValueError:
        return None


def extraire_nombres(texte: str) -> list[float]:
    """Extrait tous les nombres d'un texte.

    Les marqueurs de liste en début de ligne sont retirés d'abord : « 2. »
    ouvrant un paragraphe est une numérotation, pas une donnée.

    Args:
        texte: texte à analyser.

    Returns:
        Liste des valeurs trouvées, dans l'ordre d'apparition.
    """
    nettoye = _MOTIF_PUCE.sub("", texte or "")
    valeurs: list[float] = []
    for correspondance in _MOTIF_NOMBRE.finditer(nettoye):
        valeur = _en_flottant(correspondance.group())
        if valeur is not None:
            valeurs.append(valeur)
    return valeurs


def valeurs_autorisees(donnees: Any) -> set[float]:
    """Rassemble toutes les valeurs numériques citables.

    Sont retenus les nombres présents dans le JSON, **et** ceux contenus dans
    ses chaînes de caractères : les commentaires produits par les autres
    modules contiennent déjà des chiffres vérifiés, et les dates fournissent
    les années. Sans cela, une reformulation fidèle d'un commentaire exact
    serait rejetée.

    Chaque valeur est déclinée en plusieurs arrondis, parce qu'un modèle qui
    écrit « environ 1,8 » pour 1,7649 ne se trompe pas.

    Args:
        donnees: structure JSON d'entrée.

    Returns:
        Ensemble des valeurs acceptables.
    """
    autorisees: set[float] = set()

    def _parcourir(noeud: Any) -> None:
        """Descend récursivement dans la structure."""
        if isinstance(noeud, bool) or noeud is None:
            return
        if isinstance(noeud, (int, float)):
            autorisees.add(float(noeud))
        elif isinstance(noeud, str):
            autorisees.update(extraire_nombres(noeud))
        elif isinstance(noeud, dict):
            for cle, valeur in noeud.items():
                autorisees.update(extraire_nombres(str(cle)))
                _parcourir(valeur)
        elif isinstance(noeud, (list, tuple)):
            for element in noeud:
                _parcourir(element)

    _parcourir(donnees)

    # Déclinaisons d'arrondi et de signe : « 1,76 » peut s'écrire « 1,8 »,
    # « 2 », et une baisse de -0,9 % se dit souvent « 0,9 % de baisse ».
    declinaisons: set[float] = set()
    for valeur in autorisees:
        declinaisons.add(abs(valeur))
        for decimales in (0, 1, 2):
            declinaisons.add(round(valeur, decimales))
            declinaisons.add(round(abs(valeur), decimales))
        # Un ratio de 0,73 se raconte volontiers « 73 % ».
        if -1.0 <= valeur <= 1.0:
            declinaisons.update({round(valeur * 100.0, d) for d in (0, 1)})
    autorisees |= declinaisons

    # 0 et 1 sont toujours tolérés : « aucun », « un seul », « le premier ».
    autorisees.update({0.0, 1.0})
    return autorisees


def verifier_nombres(
    texte: str,
    donnees: Any,
    tolerance_relative: float = TOLERANCE_RELATIVE,
) -> tuple[bool, list[float]]:
    """Vérifie que chaque nombre du texte existe dans les données.

    Args:
        texte: texte produit par le modèle.
        donnees: JSON fourni au modèle.
        tolerance_relative: écart relatif toléré, pour absorber les arrondis.

    Returns:
        Couple ``(conforme, nombres_rejetes)``.
    """
    autorisees = valeurs_autorisees(donnees)
    rejetes: list[float] = []

    for nombre in extraire_nombres(texte):
        tolerance = max(abs(nombre) * tolerance_relative, TOLERANCE_ABSOLUE)
        if not any(abs(nombre - permise) <= tolerance for permise in autorisees):
            rejetes.append(nombre)

    if rejetes:
        _LOG.warning(
            "Nombres sans correspondance dans les données : %s",
            ", ".join(f"{n:g}" for n in rejetes[:8]),
        )
    return (not rejetes), rejetes


# ---------------------------------------------------------------------------
# Mode gabarit
# ---------------------------------------------------------------------------
def _gabarit(nom_metrique: str, donnees: dict[str, Any], motif: str) -> ResultatExplication:
    """Produit l'explication en quatre parties sans appel réseau.

    Le texte est assemblé à partir des commentaires que les modules de calcul
    ont déjà rédigés. Ces phrases sont exactes par construction : elles ont
    été écrites à côté du calcul qui les produit.

    Args:
        nom_metrique: nom de la métrique expliquée.
        donnees: bloc JSON de la métrique.
        motif: raison du recours au gabarit.

    Returns:
        Le résultat, en mode ``gabarit``.
    """
    definitions = {
        "juste_valeur": (
            "L'or ne verse aucun intérêt : le détenir coûte le rendement réel auquel on "
            "renonce. Le modèle estime ce que vaudrait l'or si seuls les taux réels et le "
            "dollar comptaient, puis mesure l'écart avec le prix observé. Cet écart est la "
            "prime payée pour autre chose : risque géopolitique, achats de banques centrales.",
            "Cet écart dit si le mouvement récent est justifié par les fondamentaux ou s'il "
            "repose sur une prime de peur, laquelle peut s'évaporer sans qu'aucun "
            "fondamental ne change.",
        ),
        "positionnement_cot": (
            "Le rapport de la CFTC dit qui détient quoi sur les contrats à terme. Les "
            "« managed money » sont les fonds spéculatifs : leur position nette mesure le "
            "consensus du marché.",
            "Quand ce positionnement est extrême, il ne reste presque personne pour "
            "acheter davantage, et le moindre revers force des ventes en cascade.",
        ),
        "flux": (
            "Le ratio or/argent et le ratio minières/or disent si la hausse de l'or est "
            "large ou étroite. L'argent et les mines montent plus vite que l'or quand la "
            "hausse est portée par l'appétit ; ils décrochent quand seule la peur agit.",
            "Une hausse que ni l'argent ni les minières ne confirment est une hausse "
            "défensive, historiquement moins durable.",
        ),
        "geopolitique": (
            "L'intensité mesure combien la presse mondiale parle d'un sujet, rapportée à "
            "sa normale. La chaîne de transmission vérifie ensuite le trajet du choc : "
            "pétrole, anticipations d'inflation, taux réels, puis or.",
            "Un thème déjà ancien dont la prime est déjà élevée n'est plus un moteur de "
            "hausse : ce qui reste à jouer, c'est la détente.",
        ),
        "biais": (
            "Le biais agrège six composantes indépendantes, chacune notée entre -1 et +1, "
            "pondérées selon la configuration.",
            "Le détail des contributions montre quelle composante porte le résultat, et "
            "donc laquelle surveiller en premier.",
        ),
    }
    quoi, pourquoi = definitions.get(
        nom_metrique,
        (
            f"Indicateur « {nom_metrique} » du moteur d'analyse fondamentale de l'or.",
            "Il entre dans la lecture d'ensemble du contexte.",
        ),
    )

    # Partie 2 : la lecture déjà rédigée par le module de calcul.
    situation = (
        donnees.get("lecture")
        or donnees.get("commentaire")
        or donnees.get("avertissement")
        or ""
    )
    if not situation:
        situation = (
            f"Donnée indisponible : {donnees.get('motif', 'motif non précisé')}."
            if not donnees.get("disponible", True)
            else "Aucune lecture chiffrée n'a été produite pour cette métrique."
        )

    # Partie 4 : les précédents, s'il y en a.
    precedents = donnees.get("precedents_historiques") or {}
    bloc_20j = precedents.get("20j") if isinstance(precedents, dict) else None
    if isinstance(bloc_20j, dict) and bloc_20j.get("disponible"):
        constat = (
            f"Sur {bloc_20j['n_cas']} configurations comparables, l'or a varié de "
            f"{bloc_20j['rendement_median_pct']} % en médiane à 20 jours, avec une hausse "
            f"dans {bloc_20j['proportion_haussiers']} des cas, entre "
            f"{bloc_20j['pire_pct']} % et {bloc_20j['meilleur_pct']} %."
        )
    else:
        constat = (
            "Aucun précédent historique exploitable n'accompagne cette métrique : "
            "soit la configuration est trop rare, soit la base n'atteint pas le nombre "
            "de cas minimal exigé."
        )

    texte = "\n\n".join([quoi, situation, pourquoi, constat])
    return ResultatExplication(
        texte=texte,
        mode="gabarit",
        nombres_rejetes=[],
        motif_repli=motif,
    )


# ---------------------------------------------------------------------------
# Mode OpenAI
# ---------------------------------------------------------------------------
def _appeler_openai(
    client: Any,
    modele: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> tuple[str | None, str]:
    """Appelle l'API et renvoie le texte produit.

    Args:
        client: client OpenAI déjà construit.
        modele: identifiant du modèle.
        messages: conversation.
        temperature: température d'échantillonnage.
        max_tokens: plafond de longueur.

    Returns:
        Couple ``(texte, motif)``. Le texte est ``None`` en cas d'échec.
    """
    try:
        reponse = client.chat.completions.create(
            model=modele,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 - le SDK remonte des erreurs très variées
        return None, f"appel OpenAI en échec ({type(exc).__name__})"

    try:
        texte = (reponse.choices[0].message.content or "").strip()
    except (AttributeError, IndexError, TypeError):
        return None, "réponse OpenAI de forme inattendue"

    return (texte, "") if texte else (None, "réponse OpenAI vide")


def expliquer(
    nom_metrique: str,
    donnees: dict[str, Any],
    configuration: dict[str, Any] | None = None,
    client: Any | None = None,
    consigne: str | None = None,
) -> ResultatExplication:
    """Explique une métrique, avec vérification numérique puis repli.

    Args:
        nom_metrique: nom de la métrique.
        donnees: bloc JSON de la métrique. C'est la seule source de chiffres
            autorisée, et la référence du vérificateur.
        configuration: bloc ``explication`` de ``config/gold.yaml``.
        client: client OpenAI déjà construit. Utile aux tests : un client
            factice permet de vérifier tout le circuit sans réseau.
        consigne: consigne système de remplacement, pour un appelant dont le
            besoin diffère — le fil d'actualité quantique, par exemple.
            :data:`CONSIGNE_SYSTEME` s'applique par défaut. Les contraintes
            numériques et l'interdiction de recommander restent vérifiées
            mécaniquement quelle que soit la consigne : elles ne dépendent pas
            de ce que le texte demande au modèle.

    Returns:
        Le résultat, en mode ``openai`` ou ``gabarit``.
    """
    reglages = dict(configuration or {})
    if not reglages.get("activee", True):
        return _gabarit(nom_metrique, donnees, "couche pédagogique désactivée dans la configuration")

    modele = str(reglages.get("modele", MODELE_DEFAUT))
    temperature = float(reglages.get("temperature", 0.2))
    max_tokens = int(reglages.get("max_tokens", 700))
    tolerance = float(reglages.get("tolerance_relative", TOLERANCE_RELATIVE))

    if client is None:
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            return _gabarit(nom_metrique, donnees, "OPENAI_API_KEY absente de l'environnement")
        try:
            from openai import OpenAI
        except ImportError:
            return _gabarit(nom_metrique, donnees, "le paquet openai n'est pas installé")
        try:
            client = OpenAI()
        except Exception as exc:  # noqa: BLE001 - construction du client
            return _gabarit(nom_metrique, donnees, f"client OpenAI non construit ({type(exc).__name__})")

    charge = json.dumps(donnees, ensure_ascii=False, indent=2, default=str)
    messages = [
        {"role": "system", "content": consigne or CONSIGNE_SYSTEME},
        {
            "role": "user",
            "content": (
                f"Métrique à expliquer : {nom_metrique}\n\n"
                f"Données (seule source de chiffres autorisée) :\n{charge}"
            ),
        },
    ]

    rejetes_derniers: list[float] = []
    motif = ""

    # Deux tentatives au maximum : la seconde rappelle explicitement quels
    # nombres ont été inventés. Au-delà, insister coûte des jetons sans rien
    # améliorer — mieux vaut le gabarit, exact par construction.
    for tentative in (1, 2):
        texte, motif = _appeler_openai(client, modele, messages, temperature, max_tokens)
        if texte is None:
            return _gabarit(nom_metrique, donnees, motif)

        conforme, rejetes = verifier_nombres(texte, donnees, tolerance_relative=tolerance)
        if conforme:
            return ResultatExplication(
                texte=texte,
                mode="openai",
                modele=modele,
                tentatives=tentative,
                nombres_rejetes=[],
            )

        rejetes_derniers = rejetes
        _LOG.warning(
            "Explication « %s » rejetée (tentative %d) : %d nombre(s) absent(s) des données.",
            nom_metrique,
            tentative,
            len(rejetes),
        )
        if tentative == 1:
            messages.extend(
                [
                    {"role": "assistant", "content": texte},
                    {
                        "role": "user",
                        "content": (
                            "Texte refusé. Les nombres suivants ne figurent pas dans les "
                            f"données fournies : {', '.join(f'{n:g}' for n in rejetes)}. "
                            "Réécris l'explication en n'utilisant QUE les nombres présents "
                            "dans le JSON ci-dessus, ou en n'en citant aucun. N'invente "
                            "aucun percentile, aucun pourcentage, aucune date."
                        ),
                    },
                ]
            )

    return _gabarit(
        nom_metrique,
        donnees,
        (
            f"vérification numérique échouée deux fois ; nombres inventés au dernier essai : "
            f"{', '.join(f'{n:g}' for n in rejetes_derniers)}"
        ),
    )


def expliquer_rapport(
    rapport: dict[str, Any],
    configuration: dict[str, Any] | None = None,
    metriques: tuple[str, ...] = ("juste_valeur", "positionnement_cot", "geopolitique", "biais"),
    client: Any | None = None,
) -> dict[str, Any]:
    """Explique plusieurs blocs d'un rapport.

    Args:
        rapport: rapport complet.
        configuration: bloc ``explication`` de ``config/gold.yaml``.
        metriques: blocs à expliquer, dans l'ordre.
        client: client OpenAI éventuel.

    Returns:
        Dictionnaire des explications, indexé par métrique, plus un résumé
        des modes effectivement employés.
    """
    explications: dict[str, Any] = {}
    for nom in metriques:
        bloc = rapport.get(nom)
        if not isinstance(bloc, dict):
            _LOG.warning("Bloc « %s » absent du rapport : explication ignorée.", nom)
            continue
        explications[nom] = expliquer(nom, bloc, configuration=configuration, client=client).to_dict()

    modes = [e["mode"] for e in explications.values()]
    return {
        "explications": explications,
        "n_openai": modes.count("openai"),
        "n_gabarit": modes.count("gabarit"),
        "toutes_verifiees": all(m == "openai" for m in modes) if modes else False,
    }
