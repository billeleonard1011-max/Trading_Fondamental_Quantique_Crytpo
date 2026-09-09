/**
 * Récupération des bougies PAXGUSDT depuis l'API publique Binance.
 *
 * Vérifié par appel réel (voir la vérification de faisabilité, partie 1) :
 * endpoint public, aucune clé, granularité 1 minute disponible, limite de
 * débit (6000 de poids par minute) sans commune mesure avec un appel par
 * minute. PAXG est un jeton adossé à de l'or physique, coté en dollars : un
 * proxy de XAUUSD, pas XAUUSD lui-même — voir README.md pour l'écart que
 * cela peut introduire avec le prix réel chez un courtier.
 */

const URL_KLINES = "https://api.binance.com/api/v3/klines";

/**
 * Récupère les dernières bougies 1 minute de PAXGUSDT.
 *
 * @param {number} limite Nombre de bougies demandées (au plus 1000, limite Binance).
 * @param {Function} recuperer Implémentation de fetch, injectable pour les tests.
 * @returns {Promise<{disponible: boolean, bougies: Array, motif: string}>}
 *   Bougies au format `{t, ouverture, haut, bas, cloture, volume}`, triées
 *   croissant. `disponible: false` en cas d'échec réseau ou de réponse
 *   inattendue — jamais d'exception remontée à l'appelant, jamais de
 *   bougie inventée.
 */
export async function recupererBougiesRecentes(limite = 5, recuperer = globalThis.fetch) {
  const url = `${URL_KLINES}?symbol=PAXGUSDT&interval=1m&limit=${Math.min(Math.max(limite, 1), 1000)}`;
  let reponse;
  try {
    reponse = await recuperer(url);
  } catch (erreur) {
    return { disponible: false, bougies: [], motif: `Binance injoignable : ${erreur && erreur.name ? erreur.name : "erreur réseau"}` };
  }

  if (!reponse.ok) {
    return { disponible: false, bougies: [], motif: `Binance a répondu ${reponse.status}` };
  }

  let brut;
  try {
    brut = await reponse.json();
  } catch {
    return { disponible: false, bougies: [], motif: "réponse Binance illisible (JSON invalide)" };
  }

  if (!Array.isArray(brut)) {
    return { disponible: false, bougies: [], motif: "réponse Binance de forme inattendue" };
  }

  // Format Binance : [ouvertureMs, open, high, low, close, volume, fermetureMs, ...].
  // La dernière bougie renvoyée par Binance est parfois encore en cours de
  // formation (sa clôture n'est pas encore passée) : on l'écarte, le
  // moteur ne doit jamais recevoir une bougie qui n'est pas close.
  const maintenant = Date.now();
  const bougies = brut
    .map((k) => ({
      t: Number(k[0]),
      ouverture: Number(k[1]),
      haut: Number(k[2]),
      bas: Number(k[3]),
      cloture: Number(k[4]),
      volume: Number(k[5]),
      finMs: Number(k[6]),
    }))
    .filter((b) => Number.isFinite(b.t) && b.finMs < maintenant)
    .map(({ finMs, ...b }) => b)
    .sort((a, b) => a.t - b.t);

  return { disponible: true, bougies, motif: "" };
}
