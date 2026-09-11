"""Découverte et classement des sujets géopolitiques par pertinence marché.

Le problème que ce module résout
--------------------------------
Les dossiers de conflits ont été écrits à la main, de mémoire. Deux défauts
symétriques en découlent : des sujets qui bougent les marchés cette semaine
et que personne n'a inscrits, et des sujets inscrits qui ne bougent plus
rien parce que le marché les a entièrement intégrés. Ce module fait faire
le tri au système lui-même, à partir de ce qu'il mesure déjà.

Trois étapes
------------
1. **Candidats** — :func:`candidats_evenements` lit les exports GDELT
   Events des dernières heures (agrégés par
   :func:`dataio.gdelt_events.recuperer_exports_recents` — un seul export
   de quinze minutes ne contient qu'une vingtaine d'événements de conflit,
   trop peu pour distinguer un sujet du bruit) et en tire les paires de
   pays les plus actives en événements de conflit (classes CAMEO « conflit verbal » et « conflit matériel »),
   pondérées par le nombre de mentions. Aucune liste préétablie : une paire
   qui n'existe dans aucun dossier remonte quand même. Les thèmes GDELT
   génériques (tensions énergétiques, sanctions…) fournissent la seconde
   source de candidats, par leur intensité de couverture.
2. **Pertinence marché** — :func:`pertinence_marche` confronte la couverture
   journalière d'un sujet sur trois mois (une seule requête GDELT DOC, dont
   les trente derniers jours servent à l'intensité) aux mouvements des actifs suivis
   (pétrole, taux réels, VIX, or). La mesure : les jours où la couverture
   dépasse sa moyenne d'au moins un écart-type (« jours de pic »), de
   combien les actifs bougent-ils *plus* que les autres jours ? Un ratio de
   1 dit que le sujet ne fait rien bouger ; 2 dit que ses pics coïncident
   avec des séances deux fois plus amples. C'est une coïncidence mesurée,
   pas une causalité prouvée — et le texte le dit.
3. **Classement** — :func:`classer` range les sujets : ``actif`` (la
   couverture coïncide avec des mouvements), ``veille`` (mesuré, mais le
   marché ne réagit pas — gardé, un sujet inerte peut se réactiver du jour
   au lendemain), ``candidat`` (pas assez d'observations pour conclure),
   ``epingle`` (imposé par la configuration, quoi que mesure le système).

La mise en garde statistique
----------------------------
Une corrélation sur quelques jours ne prouve rien — c'est le piège des 37
trades qui ne permettaient aucune conclusion. Un sujet n'est classé que sur
au moins :data:`MIN_OBSERVATIONS` séances communes et
:data:`MIN_PICS` jours de pic ; en deçà, il reste ``candidat`` et
l'insuffisance est écrite noir sur blanc dans le rapport et sur le site.

Le filet « Autres »
-------------------
:func:`est_significatif` décide ce qui mérite d'y apparaître : une
couverture à au moins :data:`SEUIL_INTENSITE_SIGNIFICATIVE` fois sa
normale, ou une paire d'acteurs qui concentre une part notable des
événements de conflit du dernier export. Le mouvement de prix d'un seul
jour n'est volontairement pas un critère d'entrée : trop bruité pour
trancher (voir la mise en garde ci-dessus) ; il sert au classement, pas au
filtre.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Final

import numpy as np
import pandas as pd

_LOG: Final = logging.getLogger(__name__)

#: Séances communes (couverture × marché) minimales pour classer un sujet.
MIN_OBSERVATIONS: Final[int] = 20

#: Jours de pic de couverture minimaux : un seul pic n'est qu'une anecdote.
MIN_PICS: Final[int] = 3

#: Écart-type au-dessus de la moyenne à partir duquel un jour est un pic.
SEUIL_PIC_Z: Final[float] = 1.0

#: En deçà de ces effectifs, un classement est possible mais fragile : il
#: est publié avec la mention « fiabilité faible » plutôt que tu.
OBSERVATIONS_FIABLES: Final[int] = 40
PICS_FIABLES: Final[int] = 5

#: Ratio de mouvement (jours de pic / autres jours) à partir duquel le
#: marché est dit « réagir », et en deçà duquel le sujet est dit inerte.
SEUIL_REAGIT: Final[float] = 1.5
SEUIL_INERTE: Final[float] = 0.8

#: Couverture rapportée à sa normale à partir de laquelle un sujet hors
#: dossier est significatif.
SEUIL_INTENSITE_SIGNIFICATIVE: Final[float] = 1.5

#: Pour une paire d'acteurs : événements de conflit minimaux dans l'export
#: et part minimale de tous les événements de conflit de l'export.
MIN_EVENEMENTS_PAIRE: Final[int] = 10
PART_MIN_PAIRE: Final[float] = 0.03

#: Colonnes lues dans un export GDELT Events (voir dataio/gdelt_events.py).
_COL_ACTOR1_PAYS: Final[int] = 7
_COL_ACTOR2_PAYS: Final[int] = 17
_COL_QUADCLASS: Final[int] = 29
_COL_GOLDSTEIN: Final[int] = 30
_COL_MENTIONS: Final[int] = 31
_NB_COLONNES_MIN: Final[int] = 61

#: Classes CAMEO retenues : 3 = conflit verbal, 4 = conflit matériel.
CLASSES_CONFLIT: Final[frozenset[str]] = frozenset({"3", "4"})

#: Codes pays CAMEO → nom anglais, pour composer la requête GDELT DOC d'une
#: paire découverte (l'API DOC n'accepte pas les codes d'acteurs). Liste
#: volontairement limitée aux acteurs que la géopolitique de marché croise
#: le plus ; un code absent donne un candidat sans requête, listé mais non
#: mesuré — jamais inventé.
NOMS_PAYS: Final[dict[str, str]] = {
    "USA": "United States", "CHN": "China", "RUS": "Russia", "UKR": "Ukraine", "ISR": "Israel",
    "PSE": "Palestinian", "IRN": "Iran", "IRQ": "Iraq", "SAU": "Saudi Arabia", "YEM": "Yemen",
    "SYR": "Syria", "LBN": "Lebanon", "JOR": "Jordan", "EGY": "Egypt", "ARE": "United Arab Emirates",
    "QAT": "Qatar", "KWT": "Kuwait", "BHR": "Bahrain", "OMN": "Oman", "TUR": "Turkey",
    "TWN": "Taiwan", "PRK": "North Korea", "KOR": "South Korea", "JPN": "Japan", "IND": "India",
    "PAK": "Pakistan", "AFG": "Afghanistan", "GBR": "United Kingdom", "FRA": "France",
    "DEU": "Germany", "ITA": "Italy", "ESP": "Spain", "POL": "Poland", "BLR": "Belarus",
    "GEO": "Georgia", "ARM": "Armenia", "AZE": "Azerbaijan", "VEN": "Venezuela", "COL": "Colombia",
    "MEX": "Mexico", "BRA": "Brazil", "ARG": "Argentina", "CAN": "Canada", "AUS": "Australia",
    "PHL": "Philippines", "VNM": "Vietnam", "MYS": "Malaysia", "IDN": "Indonesia", "THA": "Thailand",
    "MMR": "Myanmar", "NGA": "Nigeria", "ETH": "Ethiopia", "SDN": "Sudan", "SSD": "South Sudan",
    "SOM": "Somalia", "LBY": "Libya", "DZA": "Algeria", "MAR": "Morocco", "ZAF": "South Africa",
    "COD": "Congo", "MLI": "Mali", "NER": "Niger", "BFA": "Burkina Faso", "CUB": "Cuba",
    "HTI": "Haiti", "SRB": "Serbia", "XKX": "Kosovo", "BIH": "Bosnia", "MDA": "Moldova",
    "HUN": "Hungary", "NLD": "Netherlands", "SWE": "Sweden", "FIN": "Finland", "NOR": "Norway",
}

__all__ = [
    "MIN_OBSERVATIONS", "MIN_PICS", "SEUIL_INTENSITE_SIGNIFICATIVE",
    "candidats_evenements", "pertinence_marche", "classer", "est_significatif",
    "requete_paire", "libelle_paire",
]


# ---------------------------------------------------------------------------
# Candidats
# ---------------------------------------------------------------------------
def libelle_paire(paire: tuple[str, str] | list[str]) -> str:
    """« Israel – Iran », ou les codes bruts si un nom manque."""
    a, b = paire[0], paire[1]
    return f"{NOMS_PAYS.get(a, a)} – {NOMS_PAYS.get(b, b)}"


def requete_paire(paire: tuple[str, str] | list[str]) -> str:
    """Compose la requête GDELT DOC d'une paire : les deux noms exigés.

    Returns:
        La requête, ou une chaîne vide si l'un des codes n'a pas de nom
        connu — un candidat sans requête est listé mais pas mesuré.
    """
    noms = [NOMS_PAYS.get(code) for code in paire[:2]]
    if not all(noms):
        return ""
    return " ".join(f'"{n}"' if " " in n else n for n in noms if n)


def _rattachement(
    paire: tuple[str, str], dossiers_cfg: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Trouve le dossier configuré auquel une paire appartient.

    Un dossier bilatéral (``acteurs_gdelt``) l'emporte sur un dossier
    régional (``pays``) : « Israël - Gaza » est plus précis que
    « Moyen-Orient ».
    """
    codes = set(paire)
    for cfg in dossiers_cfg:
        acteurs = {str(a) for a in (cfg.get("acteurs_gdelt") or [])}
        if len(acteurs) == 2 and acteurs == codes:
            return {"type": "dossier", "id": str(cfg.get("id", "")), "nom_affiche": str(cfg.get("nom_affiche", ""))}
    for cfg in dossiers_cfg:
        pays = {str(p) for p in (cfg.get("pays") or [])}
        if pays and codes <= pays:
            return {"type": "region", "id": str(cfg.get("id", "")), "nom_affiche": str(cfg.get("nom_affiche", ""))}
    return None


def candidats_evenements(
    lignes: list[list[str]],
    dossiers_cfg: list[dict[str, Any]] | None = None,
    max_candidats: int = 5,
) -> list[dict[str, Any]]:
    """Tire des exports Events les paires d'acteurs les plus actives.

    Args:
        lignes: lignes brutes d'un ou plusieurs exports (voir
            :func:`dataio.gdelt_events.recuperer_exports_recents`).
        dossiers_cfg: dossiers configurés, pour dire si une paire est déjà
            couverte (bilatéral ou régional).
        max_candidats: nombre de paires retenues, les plus mentionnées.

    Returns:
        Liste de candidats, du plus mentionné au moins : ``paire``,
        ``libelle``, ``n_evenements``, ``mentions``, ``part`` (des
        événements de conflit de l'export), ``goldstein_moyen``,
        ``rattachement`` (``None`` si aucun dossier ne la couvre),
        ``requete``. Vide si l'export l'est.
    """
    comptes: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"n": 0, "mentions": 0.0, "goldstein": 0.0})
    total_conflit = 0
    for ligne in lignes:
        if len(ligne) < _NB_COLONNES_MIN or ligne[_COL_QUADCLASS] not in CLASSES_CONFLIT:
            continue
        a, b = ligne[_COL_ACTOR1_PAYS], ligne[_COL_ACTOR2_PAYS]
        if not a or not b or a == b:
            continue
        total_conflit += 1
        cle = tuple(sorted((a, b)))
        try:
            mentions = float(ligne[_COL_MENTIONS] or 0)
        except ValueError:
            mentions = 0.0
        try:
            goldstein = float(ligne[_COL_GOLDSTEIN])
        except ValueError:
            goldstein = 0.0
        comptes[cle]["n"] += 1
        comptes[cle]["mentions"] += mentions
        comptes[cle]["goldstein"] += goldstein

    if not comptes:
        return []
    ordonnes = sorted(comptes.items(), key=lambda kv: (kv[1]["mentions"], kv[1]["n"]), reverse=True)
    candidats: list[dict[str, Any]] = []
    for paire, c in ordonnes[:max_candidats]:
        n = int(c["n"])
        candidats.append({
            "type": "paire",
            "paire": list(paire),
            "libelle": libelle_paire(paire),
            "n_evenements": n,
            "mentions": int(c["mentions"]),
            "part": round(n / total_conflit, 4) if total_conflit else 0.0,
            "goldstein_moyen": round(c["goldstein"] / n, 2) if n else None,
            "rattachement": _rattachement(paire, dossiers_cfg or []),
            "requete": requete_paire(paire),
        })
    return candidats


# ---------------------------------------------------------------------------
# Pertinence marché
# ---------------------------------------------------------------------------
#: Libellés des séries de marché, dans l'ordre d'affichage.
_LIBELLES_MARCHE: Final[tuple[tuple[str, str], ...]] = (
    ("petrole", "le pétrole"), ("taux_reels", "les taux réels"), ("vix", "le VIX"), ("or", "l'or"),
)


def marches_journaliers(
    series_macro: pd.DataFrame | None, prix_or: pd.Series | None
) -> pd.DataFrame:
    """Prépare les mouvements journaliers absolus des actifs suivis.

    Args:
        series_macro: séries FRED (``DCOILWTICO``, ``DFII10``, ``VIXCLS``).
        prix_or: prix de l'or.

    Returns:
        DataFrame indexé par date, colonnes parmi ``petrole``, ``taux_reels``,
        ``vix``, ``or`` — variations en pourcentage, sauf les taux réels en
        points. Vide si rien n'est mesurable.
    """
    colonnes: dict[str, pd.Series] = {}
    cadre = series_macro if series_macro is not None else pd.DataFrame()
    for nom, serie_id, en_pct in (("petrole", "DCOILWTICO", True), ("taux_reels", "DFII10", False), ("vix", "VIXCLS", True)):
        if serie_id in cadre.columns:
            s = pd.to_numeric(cadre[serie_id], errors="coerce").dropna()
            colonnes[nom] = (s.pct_change() * 100.0) if en_pct else s.diff()
    if prix_or is not None and len(prix_or) > 1:
        s = pd.to_numeric(prix_or, errors="coerce").dropna()
        colonnes["or"] = s.pct_change() * 100.0
    if not colonnes:
        return pd.DataFrame()
    marches = pd.DataFrame(colonnes)
    marches.index = pd.to_datetime(marches.index).normalize()
    return marches.dropna(how="all")


def pertinence_marche(
    volumes: dict[str, float] | None,
    marches: pd.DataFrame | None,
    min_observations: int = MIN_OBSERVATIONS,
    min_pics: int = MIN_PICS,
) -> dict[str, Any]:
    """Mesure si les pics de couverture d'un sujet coïncident avec des mouvements.

    Args:
        volumes: couverture journalière (``AAAA-MM-JJ`` → articles), telle
            que renvoyée par ``dataio.news.gdelt_volume_journalier``.
        marches: sortie de :func:`marches_journaliers`.
        min_observations: séances communes minimales.
        min_pics: jours de pic minimaux.

    Returns:
        ``disponible`` dit si un classement est possible ; ``suffisant`` dit
        si les données le permettent (les deux tombent ensemble) ;
        ``n_observations``, ``n_pics``, ``ratios`` par actif, ``score``
        (médiane des ratios), ``lecture`` (``réagit`` / ``neutre`` /
        ``inerte``), ``commentaire`` chiffré et ``motif`` en cas
        d'indisponibilité. Tous les nombres du commentaire figurent dans le
        dictionnaire.
    """
    base = {"disponible": False, "suffisant": False, "n_observations": 0, "n_pics": 0,
            "min_observations": min_observations, "min_pics": min_pics, "ratios": {},
            "score": None, "lecture": "", "commentaire": ""}
    if not volumes:
        return base | {"motif": "aucune série de couverture sur trente jours"}
    if marches is None or marches.empty:
        return base | {"motif": "séries de marché absentes : coïncidence non mesurable"}

    couverture = pd.Series({pd.Timestamp(d).normalize(): float(v) for d, v in volumes.items()}).sort_index()
    cadre = marches.join(couverture.rename("couverture"), how="inner").dropna(subset=["couverture"])
    n = int(len(cadre))
    if n < min_observations:
        return base | {
            "n_observations": n,
            "motif": f"{n} séance(s) commune(s) entre couverture et marché, {min_observations} requises",
        }

    ecart = float(cadre["couverture"].std(ddof=0))
    if ecart == 0.0:
        return base | {"n_observations": n, "motif": "couverture constante sur la période : aucun pic à confronter"}
    z = (cadre["couverture"] - cadre["couverture"].mean()) / ecart
    pics = z >= SEUIL_PIC_Z
    n_pics = int(pics.sum())
    if n_pics < min_pics:
        return base | {
            "n_observations": n, "n_pics": n_pics,
            "motif": f"{n_pics} jour(s) de pic de couverture sur {n}, {min_pics} requis pour conclure",
        }

    ratios: dict[str, float] = {}
    for col, _ in _LIBELLES_MARCHE:
        if col not in cadre.columns:
            continue
        s = cadre[col].abs()
        reference = float(s[~pics].mean()) if (~pics).any() else 0.0
        if reference > 0 and s[pics].notna().any():
            ratios[col] = round(float(s[pics].mean()) / reference, 2)
    if not ratios:
        return base | {"n_observations": n, "n_pics": n_pics, "motif": "aucun actif mesurable sur les jours de pic"}

    score = round(float(np.median(list(ratios.values()))), 2)
    if score >= SEUIL_REAGIT:
        lecture = "réagit"
        sens = "ce qui signifie que ses pics d'attention coïncident avec des séances nettement plus amples — coïncidence mesurée, pas causalité prouvée"
    elif score < SEUIL_INERTE:
        lecture = "inerte"
        sens = "ce qui signifie que le marché ne réagit plus à son actualité : le sujet est intégré dans les prix"
    else:
        lecture = "neutre"
        sens = "ce qui ne distingue pas ses jours de pic des autres : aucun effet de marché net"

    detail = ", ".join(
        f"{libelle} {ratios[col]:.2f}".replace(".", ",") for col, libelle in _LIBELLES_MARCHE if col in ratios
    )
    fiabilite = "correcte" if (n >= OBSERVATIONS_FIABLES and n_pics >= PICS_FIABLES) else "faible"
    reserve = (
        f" Fiabilité faible : {n} séances et {n_pics} pics seulement, ce qui rend ce classement révisable "
        "à la prochaine mesure."
        if fiabilite == "faible" else ""
    )
    commentaire = (
        f"Sur {n} séances communes, les {n_pics} jours de pic de couverture voient les actifs bouger "
        f"{str(score).replace('.', ',')} fois plus que les autres jours ({detail}), {sens}.{reserve}"
    )
    return {
        "disponible": True, "suffisant": True, "n_observations": n, "n_pics": n_pics,
        "min_observations": min_observations, "min_pics": min_pics,
        "ratios": ratios, "score": score, "lecture": lecture, "fiabilite": fiabilite,
        "commentaire": commentaire, "motif": "",
    }


# ---------------------------------------------------------------------------
# Classement et significativité
# ---------------------------------------------------------------------------
def classer(sujets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attribue un statut et un rang à chaque sujet.

    Args:
        sujets: dictionnaires portant au moins ``nom``, ``pertinence``
            (sortie de :func:`pertinence_marche`), ``intensite_ratio`` et
            ``epingle``.

    Returns:
        Les mêmes sujets, enrichis de ``statut``, ``rang`` et
        ``donnees_suffisantes``, triés par pertinence mesurée : les sujets
        classables d'abord, du score le plus haut au plus bas — un sujet
        épinglé mais inerte se retrouve donc derrière un sujet actif, son
        épingle ne garantissant que son suivi, pas son rang —, puis les
        sujets non classables, par intensité de couverture.
    """
    classes: list[dict[str, Any]] = []
    for s in sujets:
        p = s.get("pertinence") or {}
        if s.get("epingle"):
            statut = "epingle"
        elif p.get("disponible"):
            statut = "actif" if p.get("lecture") == "réagit" else "veille"
        else:
            statut = "candidat"
        classes.append(s | {"statut": statut, "donnees_suffisantes": bool(p.get("disponible"))})

    def _cle(s: dict[str, Any]) -> tuple[int, float, float]:
        score = (s.get("pertinence") or {}).get("score")
        intens = s.get("intensite_ratio")
        return (0 if s["donnees_suffisantes"] else 1, -(score if score is not None else -1.0), -(intens if intens is not None else -1.0))

    classes.sort(key=_cle)
    for rang, s in enumerate(classes, start=1):
        s["rang"] = rang
    return classes


def est_significatif(sujet: dict[str, Any]) -> tuple[bool, str]:
    """Décide si un sujet hors dossier mérite le filet « Autres ».

    Args:
        sujet: candidat (paire ou thème) avec ``intensite_ratio`` et, pour
            une paire, ``n_evenements`` et ``part``.

    Returns:
        ``(retenu, critere)`` — le critère dit lequel des deux tests a
        joué, ou pourquoi aucun.
    """
    intens = sujet.get("intensite_ratio")
    if intens is not None and intens >= SEUIL_INTENSITE_SIGNIFICATIVE:
        return True, f"couverture à {str(round(float(intens), 1)).replace('.', ',')}× sa normale"
    if sujet.get("type") == "paire":
        n, part = int(sujet.get("n_evenements") or 0), float(sujet.get("part") or 0.0)
        if n >= MIN_EVENEMENTS_PAIRE and part >= PART_MIN_PAIRE:
            return True, f"{n} événements de conflit, {round(part * 100, 1)} % de l'export".replace(".", ",")
    return False, "sous les seuils de significativité"
