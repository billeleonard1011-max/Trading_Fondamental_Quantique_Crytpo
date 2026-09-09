/**
 * Coûts d'exécution, dimensionnement en euros, stop.
 *
 * Port fidèle de backtest/execution.py. Mêmes valeurs par défaut, mêmes
 * règles — voir worker-scanner/README.md pour la démarche de portage.
 */

import { HAUSSIER } from "./ict.js";

/** Onces d'or par lot standard sur XAUUSD (et son proxy PAXG). */
export const ONCES_PAR_LOT = 100.0;

/** Pas de lot minimal chez la plupart des courtiers. */
export const PAS_DE_LOT = 0.01;

/** Écart de cotation par défaut, en dollars. Mesuré sur les ticks Dukascopy. */
export const SPREAD_DEFAUT = 0.62;

/** Glissement par défaut, en dollars, appliqué à l'entrée comme à la sortie. */
export const SLIPPAGE_DEFAUT = 0.3;

/** Marge du stop au-delà de l'order block, en dollars. */
export const MARGE_STOP_DEFAUT = 1.0;

/** Fourchette de perte visée au stop, en euros. */
export const PERTE_MIN_EUR = 50.0;
export const PERTE_MAX_EUR = 60.0;

/**
 * Configuration de coût et de dimensionnement par défaut.
 * @returns {object} Réglages par défaut, copiables et surchargeables.
 */
export function configExecutionDefaut() {
  return {
    spread: SPREAD_DEFAUT,
    slippage: SLIPPAGE_DEFAUT,
    margeStop: MARGE_STOP_DEFAUT,
    perteMinEur: PERTE_MIN_EUR,
    perteMaxEur: PERTE_MAX_EUR,
    pasDeLot: PAS_DE_LOT,
    lotMin: PAS_DE_LOT,
  };
}

/**
 * Place le stop au-delà de l'order block.
 *
 * Le stop se pose à `marge` dollars au-delà de l'extrémité de la zone. Si
 * la mèche de la bougie 2 du motif dépasse déjà cette marge, le stop se
 * place au-delà de cette mèche, avec la même marge nominale.
 *
 * @param {string} sens `haussier` pour un achat, `baissier` pour une vente.
 * @param {number} obHaut Borne haute de la zone.
 * @param {number} obBas Borne basse.
 * @param {number} mecheBougie2 Extrême de la bougie 2 du côté du stop.
 * @param {number} marge Distance nominale, en dollars.
 * @returns {number} Le niveau de stop.
 */
export function calculerStop(sens, obHaut, obBas, mecheBougie2, marge = MARGE_STOP_DEFAUT) {
  if (sens === HAUSSIER) {
    const nominal = obBas - marge;
    return Math.min(nominal, mecheBougie2 - marge);
  }
  const nominal = obHaut + marge;
  return Math.max(nominal, mecheBougie2 + marge);
}

/**
 * Choisit la taille pour que la perte au stop tombe dans la fourchette.
 *
 * Sur XAUUSD (et son proxy PAXG), un lot porte cent onces. Quand aucun
 * multiple du pas de lot ne place la perte entre les deux bornes, le trade
 * est déclaré non prenable plutôt qu'arrondi.
 *
 * @param {number} distanceStopUsd Distance entre entrée et stop, en dollars.
 * @param {number} tauxEurusd Dollars par euro à la date d'entrée.
 * @param {object} config Réglages d'exécution (voir configExecutionDefaut).
 * @returns {object} `{prenable, motif, lots, perteEur, ...}`.
 */
export function dimensionner(distanceStopUsd, tauxEurusd, config = null) {
  const reglages = config || configExecutionDefaut();
  if (distanceStopUsd <= 0.0) {
    return { prenable: false, motif: "distance au stop nulle ou négative", lots: 0.0, perteEur: null };
  }
  if (tauxEurusd <= 0.0) {
    return {
      prenable: false,
      motif: "taux EUR/USD indisponible : le risque en euros n'est pas calculable",
      lots: 0.0,
      perteEur: null,
    };
  }

  const pertePapLotEur = (distanceStopUsd * ONCES_PAR_LOT) / tauxEurusd;
  const cible = (reglages.perteMinEur + reglages.perteMaxEur) / 2.0;
  const lotsTheoriques = cible / pertePapLotEur;

  const pas = reglages.pasDeLot;
  const arrondi10 = (v) => Math.round(v * 1e10) / 1e10;
  const candidats = Array.from(
    new Set([
      arrondi10(Math.floor(lotsTheoriques / pas) * pas),
      arrondi10(Math.ceil(lotsTheoriques / pas) * pas),
    ]),
  ).sort((a, b) => a - b);

  for (const lots of candidats) {
    if (lots < reglages.lotMin) continue;
    const perte = lots * pertePapLotEur;
    if (perte >= reglages.perteMinEur && perte <= reglages.perteMaxEur) {
      return {
        prenable: true,
        motif: "",
        lots,
        perteEur: perte,
        pertePapLotEur,
        distanceStopUsd,
        tauxEurusd,
      };
    }
  }

  const perteLotMin = reglages.lotMin * pertePapLotEur;
  const motif =
    `aucune taille multiple de ${pas} lot ne place la perte entre ` +
    `${reglages.perteMinEur.toFixed(0)} € et ${reglages.perteMaxEur.toFixed(0)} € ` +
    `(distance ${distanceStopUsd.toFixed(2)} $, perte au lot minimum ${perteLotMin.toFixed(2)} €)`;
  return {
    prenable: false,
    motif,
    lots: 0.0,
    perteEur: null,
    pertePapLotEur,
    distanceStopUsd,
    tauxEurusd,
  };
}

/**
 * Dégrade le prix d'entrée de l'écart et du glissement.
 *
 * @param {number} prix Prix théorique d'exécution.
 * @param {string} sens `haussier` pour un achat, `baissier` pour une vente.
 * @param {object} config Réglages d'exécution.
 * @returns {number} Le prix effectivement obtenu.
 */
export function appliquerCoutsEntree(prix, sens, config = null) {
  const reglages = config || configExecutionDefaut();
  const cout = reglages.spread + reglages.slippage;
  return sens === HAUSSIER ? prix + cout : prix - cout;
}

/**
 * Dégrade le prix de sortie du glissement (l'écart a déjà été payé à l'entrée).
 *
 * @param {number} prix Prix théorique de sortie.
 * @param {string} sens Sens de la position.
 * @param {object} config Réglages d'exécution.
 * @returns {number} Le prix effectivement obtenu.
 */
export function appliquerCoutsSortie(prix, sens, config = null) {
  const reglages = config || configExecutionDefaut();
  return sens === HAUSSIER ? prix - reglages.slippage : prix + reglages.slippage;
}
