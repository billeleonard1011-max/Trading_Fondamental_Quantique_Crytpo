/**
 * Motifs ICT : order blocks, jambes, FVG et niveaux de liquidité (sweep).
 *
 * Port fidèle de backtest/ict.py (version déjà nettoyée de la classification
 * normale/violente et du Fibonacci/OTE, retirés du moteur Python). Voir
 * worker-scanner/README.md pour la démarche de portage et
 * tests/parite.test.js pour la preuve d'identité des résultats.
 *
 * Aucune règle n'est inventée ici : les mêmes choix d'interprétation que
 * ceux du backtest s'appliquent, cités dans les commentaires « CHOIX
 * D'INTERPRÉTATION » comme côté Python.
 */

import { DUREES_MS } from "./agregation.js";

export const HAUSSIER = "haussier";
export const BAISSIER = "baissier";

/**
 * Nombre de bougies exigées de chaque côté pour valider un point de
 * retournement. Décide du découpage de la jambe, donc de la fenêtre où
 * chercher le FVG.
 */
export const SENSIBILITE_SWING = 4;

/**
 * Nombre de bougies exigées de chaque côté d'un pivot pour en faire un
 * niveau de liquidité (setup sweep). Distinct de la sensibilité des swings
 * de jambe — même valeur par défaut que backtest/ict.py::SENSIBILITE_PIVOT.
 */
export const SENSIBILITE_PIVOT = 4;

/** Côtés d'un niveau : ancien plus haut (balayé par le haut, vendu) ou
 * ancien plus bas (balayé par le bas, acheté). */
export const COTE_HAUT = "haut";
export const COTE_BAS = "bas";

/**
 * Cherche le motif d'order block en trois bougies consécutives.
 *
 * Le motif, sans condition supplémentaire :
 * - bougie 1 — la zone, fourchette complète mèches comprises ;
 * - bougie 2 — de sens opposé à la première, clôturant au-delà de son
 *   extrême (sous son plus bas si la bougie 1 est haussière, au-dessus de
 *   son plus haut si elle est baissière) ;
 * - bougie 3 — sens indifférent, mais son extrême ne doit pas revenir
 *   toucher celui de la bougie 1 (inégalité stricte).
 *
 * Une bougie 1 haussière donne un order block baissier (zone de vente) ;
 * une bougie 1 baissière donne un order block haussier.
 *
 * @param {Array} cadre Bougies OHLC de l'unité, closes, triées par ouverture.
 * @param {string} unite Nom de l'unité, repris dans les zones produites.
 * @returns {Array<object>} Zones trouvées, dans l'ordre chronologique.
 */
export function detecterOrderBlocks(cadre, unite) {
  if (!cadre || cadre.length < 3) return [];
  const duree = DUREES_MS[unite] || 0;
  const zones = [];

  for (let i = 0; i < cadre.length - 2; i += 1) {
    const b1 = cadre[i];
    const b1Haussiere = b1.cloture > b1.ouverture;
    const b1Baissiere = b1.cloture < b1.ouverture;
    if (!b1Haussiere && !b1Baissiere) continue;

    const j = i + 1;
    const k = i + 2;
    const b2 = cadre[j];
    const b3 = cadre[k];
    const b2Haussiere = b2.cloture > b2.ouverture;
    const b2Baissiere = b2.cloture < b2.ouverture;

    let sens;
    let meche;
    if (b1Haussiere) {
      if (!(b2Baissiere && b2.cloture < b1.bas)) continue;
      if (b3.haut >= b1.bas) continue;
      sens = BAISSIER;
      meche = b2.haut;
    } else {
      if (!(b2Haussiere && b2.cloture > b1.haut)) continue;
      if (b3.bas <= b1.haut) continue;
      sens = HAUSSIER;
      meche = b2.bas;
    }

    zones.push({
      unite,
      sens,
      haut: b1.haut,
      bas: b1.bas,
      ouvertureBougie1: b1.t,
      // La zone n'est connue qu'une fois la troisième bougie close.
      finMotif: b3.t + duree,
      mecheBougie2: meche,
      mitige: false,
      horodatageMitigation: null,
    });
  }

  return zones;
}

/**
 * Repère les points de retournement d'une série de bougies.
 *
 * Un sommet est une bougie dont le plus haut dépasse strictement celui des
 * `sensibilite` bougies de chaque côté. Un retournement à la position `i`
 * n'est confirmé qu'à la position `i + sensibilite` : l'appelant doit n'en
 * utiliser que des confirmés (voir origineDeJambe).
 *
 * @param {Array} cadre Bougies OHLC, dans l'ordre chronologique.
 * @param {number} sensibilite Nombre de bougies exigées de chaque côté.
 * @returns {{sommets: number[], creux: number[]}} Positions des sommets et des creux.
 */
export function detecterSwings(cadre, sensibilite = SENSIBILITE_SWING) {
  const k = Math.max(Math.trunc(sensibilite), 1);
  if (!cadre || cadre.length < 2 * k + 1) return { sommets: [], creux: [] };

  const sommets = [];
  const creux = [];
  for (let i = k; i < cadre.length - k; i += 1) {
    const hautCourant = cadre[i].haut;
    const basCourant = cadre[i].bas;
    let sommet = true;
    let creuxIci = true;
    for (let d = 1; d <= k; d += 1) {
      if (cadre[i - d].haut >= hautCourant || cadre[i + d].haut >= hautCourant) sommet = false;
      if (cadre[i - d].bas <= basCourant || cadre[i + d].bas <= basCourant) creuxIci = false;
    }
    if (sommet) sommets.push(i);
    if (creuxIci) creux.push(i);
  }
  return { sommets, creux };
}

/**
 * Situe le départ de la jambe qui va chercher l'order block.
 *
 * La jambe est le dernier segment directionnel menant à la zone : elle part
 * du dernier point de retournement confirmé, pas de l'extrême le plus
 * lointain. Un order block baissier (zone de vente) est atteint par le
 * bas : la jambe est haussière et part du dernier creux ; l'inverse pour un
 * order block haussier.
 *
 * @param {Array} cadre Bougies de l'unité de l'order block, closes, la
 *   dernière étant celle du contact avec la zone.
 * @param {string} sensOb Sens de l'order block.
 * @param {number} sensibilite Nombre de bougies exigées de chaque côté.
 * @returns {number|null} Position du départ de la jambe, ou `null` si aucun
 *   retournement confirmé ne précède le contact.
 */
export function origineDeJambe(cadre, sensOb, sensibilite = SENSIBILITE_SWING) {
  const k = Math.max(Math.trunc(sensibilite), 1);
  const { sommets, creux } = detecterSwings(cadre, k);
  const candidats = sensOb === BAISSIER ? creux : sommets;
  if (candidats.length === 0) return null;

  const derniere = cadre.length - 1;
  const confirmes = candidats.filter((i) => i + k <= derniere);
  if (confirmes.length === 0) return null;
  return confirmes[confirmes.length - 1];
}

/**
 * Cherche les écarts de valeur (FVG) en trois bougies.
 *
 * Un écart haussier existe quand le plus haut de la première bougie reste
 * sous le plus bas de la troisième ; l'écart baissier est le symétrique.
 *
 * @param {Array} cadre Bougies OHLC de l'unité, closes.
 * @param {string} unite Nom de l'unité.
 * @param {string|null} sens Ne garder que les écarts de ce sens. Tous si `null`.
 * @returns {Array<object>} Écarts trouvés, dans l'ordre chronologique.
 */
export function detecterFvg(cadre, unite, sens = null) {
  if (!cadre || cadre.length < 3) return [];
  const duree = DUREES_MS[unite] || 0;
  const ecarts = [];

  for (let i = 0; i < cadre.length - 2; i += 1) {
    const k = i + 2;
    const b1 = cadre[i];
    const b3 = cadre[k];
    let trouve = null;
    if (b1.haut < b3.bas) {
      trouve = { unite, sens: HAUSSIER, haut: b3.bas, bas: b1.haut, finMotif: b3.t + duree };
    } else if (b1.bas > b3.haut) {
      trouve = { unite, sens: BAISSIER, haut: b1.bas, bas: b3.haut, finMotif: b3.t + duree };
    } else {
      continue;
    }
    if (sens === null || trouve.sens === sens) ecarts.push(trouve);
  }
  return ecarts;
}

// ---------------------------------------------------------------------------
// Niveaux de liquidité (setup sweep) — port de backtest/ict.py
// ---------------------------------------------------------------------------

/**
 * Repère les pivots d'une unité et en fait des niveaux de liquidité.
 *
 * Même règle que detecterSwings (comparaison stricte), et même point de
 * causalité : un pivot en position `i` n'est **connu** qu'à la clôture de la
 * bougie `i + sensibilite`. Le niveau porte cet instant (`connuA`), jamais
 * celui du pivot lui-même.
 *
 * Tri stable par instant de connaissance, sommets énumérés avant les creux :
 * exactement l'ordre de backtest/ict.py::detecter_niveaux_liquidite.
 *
 * @param {Array} cadre Bougies OHLC de l'unité, closes, triées par ouverture.
 * @param {string} unite Nom de l'unité.
 * @param {number} sensibilite Nombre de bougies exigées de chaque côté.
 * @returns {Array<object>} Niveaux, triés par `connuA`.
 */
export function detecterNiveauxLiquidite(cadre, unite, sensibilite = SENSIBILITE_PIVOT) {
  const k = Math.max(Math.trunc(sensibilite), 1);
  if (!cadre || cadre.length < 2 * k + 1) return [];
  const duree = DUREES_MS[unite] || 0;
  const { sommets, creux } = detecterSwings(cadre, k);
  const fabriquer = (i, cote, prix) => ({
    unite, cote, prix, formation: cadre[i].t, connuA: cadre[i + k].t + duree,
    enSweep: false, extremeSweep: null, debutSweep: null, balaye: false, horodatageBalayage: null,
  });
  const niveaux = [
    ...sommets.map((i) => fabriquer(i, COTE_HAUT, cadre[i].haut)),
    ...creux.map((i) => fabriquer(i, COTE_BAS, cadre[i].bas)),
  ];
  // Le tri de JavaScript est stable depuis ES2019 : à `connuA` égal, les
  // sommets restent devant les creux, comme côté Python.
  niveaux.sort((a, b) => a.connuA - b.connuA);
  return niveaux;
}

/**
 * Sens du trade qu'un balayage de ce niveau déclenche.
 * @param {object} niveau Niveau de liquidité.
 * @returns {string} HAUSSIER pour un plus bas balayé, BAISSIER pour un plus haut.
 */
export function sensTradeNiveau(niveau) {
  return niveau.cote === COTE_BAS ? HAUSSIER : BAISSIER;
}

/**
 * Met à jour l'état de sweep d'un niveau avec une bougie M1.
 *
 * Une mèche au-delà suffit à ouvrir le sweep (inégalité stricte : toucher
 * le niveau exactement n'est pas le dépasser). Tant que le sweep dure,
 * l'extrême suit le point le plus loin atteint. Port de avancer_niveau.
 *
 * @param {object} niveau Niveau actif, muté sur place.
 * @param {number} haut Plus haut de la bougie M1.
 * @param {number} bas Plus bas de la bougie M1.
 * @param {number} ouverture Ouverture de la bougie M1, en ms UTC.
 * @returns {boolean} `true` si la bougie a dépassé le niveau.
 */
export function avancerNiveau(niveau, haut, bas, ouverture) {
  if (niveau.balaye) return false;
  let depasse;
  let extreme;
  let plusLoin;
  if (niveau.cote === COTE_BAS) {
    depasse = bas < niveau.prix;
    extreme = bas;
    plusLoin = niveau.extremeSweep === null || extreme < niveau.extremeSweep;
  } else {
    depasse = haut > niveau.prix;
    extreme = haut;
    plusLoin = niveau.extremeSweep === null || extreme > niveau.extremeSweep;
  }
  if (!depasse) return false;
  if (!niveau.enSweep) {
    niveau.enSweep = true;
    niveau.debutSweep = ouverture;
    niveau.extremeSweep = extreme;
  } else if (plusLoin) {
    niveau.extremeSweep = extreme;
  }
  return true;
}

/**
 * Teste si une clôture de l'unité du niveau confirme le sweep.
 *
 * Le niveau doit être en sweep et la bougie clôturer strictement de l'autre
 * côté. Une clôture restée du côté du sweep laisse le niveau actif —
 * traversé sans clôture de l'autre côté. Port de confirmer_balayage.
 *
 * @param {object} niveau Niveau actif, muté sur place s'il est confirmé.
 * @param {number} cloture Clôture de la bougie de l'unité du niveau.
 * @param {number} instant Instant de cette clôture, en ms UTC.
 * @returns {boolean} `true` si le sweep est confirmé (le niveau est alors `balaye`).
 */
export function confirmerBalayage(niveau, cloture, instant) {
  if (niveau.balaye || !niveau.enSweep) return false;
  const reprise = niveau.cote === COTE_BAS ? cloture > niveau.prix : cloture < niveau.prix;
  if (!reprise) return false;
  niveau.balaye = true;
  niveau.horodatageBalayage = instant;
  return true;
}
