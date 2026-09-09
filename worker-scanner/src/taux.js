/**
 * Taux EUR/USD quotidien, pour le dimensionnement des positions.
 *
 * Ni le prompt ni le backtest n'ont de source de taux de change en direct :
 * le backtest lit une série déjà téléchargée par Dukascopy. Ce module
 * comble ce manque explicitement plutôt que de fabriquer un taux —
 * documenté dans README.md.
 *
 * Source retenue : l'API Frankfurter (taux de référence quotidien de la
 * BCE), gratuite, sans clé — vérifiée par appel réel lors de l'étude de
 * faisabilité. Un seul appel par jour suffit, exactement comme le backtest
 * n'utilise qu'un taux par jour (bt_data ne rafraîchit jamais un taux
 * intra-journalier) : le dimensionnement n'a pas besoin de plus de
 * précision qu'un taux quotidien pour viser une fourchette de risque large
 * de dix euros.
 *
 * Alternative écartée : la paire EURUSDT sur Binance existe et répond
 * (vérifiée aussi), mais c'est un second proxy crypto au-dessus du premier
 * (PAXG pour l'or) — Frankfurter donne un taux de change réel, sans cumuler
 * les écarts de deux marchés synthétiques différents.
 */

const URL_FRANKFURTER = "https://api.frankfurter.dev/v1/latest?base=EUR&symbols=USD";

/**
 * Date du jour, au format AAAA-MM-JJ, en UTC.
 * @param {number} instantMs Instant en millisecondes UTC.
 * @returns {string} Date UTC.
 */
export function jourUtc(instantMs) {
  return new Date(instantMs).toISOString().slice(0, 10);
}

/**
 * Récupère le taux EUR/USD du jour depuis Frankfurter.
 *
 * @param {Function} recuperer Implémentation de fetch, injectable pour les tests.
 * @returns {Promise<{disponible: boolean, taux: number|null, motif: string}>}
 *   Jamais d'exception : un échec réseau ou une réponse inattendue rend
 *   `disponible: false`, jamais un taux inventé.
 */
export async function recupererTauxEurusd(recuperer = globalThis.fetch) {
  let reponse;
  try {
    reponse = await recuperer(URL_FRANKFURTER);
  } catch (erreur) {
    return { disponible: false, taux: null, motif: `Frankfurter injoignable : ${erreur && erreur.name ? erreur.name : "erreur réseau"}` };
  }

  if (!reponse.ok) {
    return { disponible: false, taux: null, motif: `Frankfurter a répondu ${reponse.status}` };
  }

  let brut;
  try {
    brut = await reponse.json();
  } catch {
    return { disponible: false, taux: null, motif: "réponse Frankfurter illisible (JSON invalide)" };
  }

  const taux = brut && brut.rates ? Number(brut.rates.USD) : NaN;
  if (!Number.isFinite(taux) || taux <= 0) {
    return { disponible: false, taux: null, motif: "réponse Frankfurter sans taux USD exploitable" };
  }
  return { disponible: true, taux, motif: "" };
}
