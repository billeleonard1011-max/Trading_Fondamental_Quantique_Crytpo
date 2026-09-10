/**
 * Récupération des bougies PAXG/USD depuis l'API publique Kraken.
 *
 * Binance a été écarté après déploiement réel : Binance renvoie 403 à toute
 * requête provenant du réseau sortant de Cloudflare Workers (confirmé via
 * `wrangler tail` sur le Worker déployé, y compris avec le miroir
 * data-api.binance.vision et un en-tête User-Agent de navigateur — donc un
 * blocage au niveau IP/ASN, pas un simple filtre applicatif). Kraken est
 * vérifié joignable depuis ce même Worker déployé (voir README.md).
 *
 * Endpoint public, aucune clé, granularité 1 minute disponible. PAXG est un
 * jeton adossé à de l'or physique : un proxy de XAUUSD, pas XAUUSD
 * lui-même — voir README.md pour l'écart que cela peut introduire avec le
 * prix réel chez un courtier. Coté ici en USD directement (PAXG/USD), et
 * non plus via l'intermédiaire USDT comme avec Binance.
 */

const URL_OHLC = "https://api.kraken.com/0/public/OHLC";
const PAIRE = "PAXGUSD";

/**
 * Récupère les dernières bougies 1 minute de PAXG/USD.
 *
 * @param {number} limite Nombre de bougies souhaitées en sortie (Kraken ne
 *   permet pas de borner sa réponse ; on tronque nous-mêmes après coup).
 * @param {Function} recuperer Implémentation de fetch, injectable pour les tests.
 * @returns {Promise<{disponible: boolean, bougies: Array, motif: string}>}
 *   Bougies au format `{t, ouverture, haut, bas, cloture, volume}`, triées
 *   croissant. `disponible: false` en cas d'échec réseau ou de réponse
 *   inattendue — jamais d'exception remontée à l'appelant, jamais de
 *   bougie inventée.
 */
export async function recupererBougiesRecentes(limite = 5, recuperer = globalThis.fetch) {
  const url = `${URL_OHLC}?pair=${PAIRE}&interval=1`;
  let reponse;
  try {
    reponse = await recuperer(url);
  } catch (erreur) {
    return { disponible: false, bougies: [], motif: `Kraken injoignable : ${erreur && erreur.name ? erreur.name : "erreur réseau"}` };
  }

  if (!reponse.ok) {
    return { disponible: false, bougies: [], motif: `Kraken a répondu ${reponse.status}` };
  }

  let corps;
  try {
    corps = await reponse.json();
  } catch {
    return { disponible: false, bougies: [], motif: "réponse Kraken illisible (JSON invalide)" };
  }

  if (corps && Array.isArray(corps.error) && corps.error.length > 0) {
    return { disponible: false, bougies: [], motif: `Kraken a signalé une erreur : ${corps.error.join(", ")}` };
  }

  const brut = corps && corps.result ? corps.result[PAIRE] : undefined;
  if (!Array.isArray(brut)) {
    return { disponible: false, bougies: [], motif: "réponse Kraken de forme inattendue" };
  }

  // Format Kraken : [tempsSecondes, open, high, low, close, vwap, volume, nbTransactions].
  // Comme Binance, la dernière entrée correspond à la période en cours de
  // formation (non close) : on l'écarte via le même filtre sur la clôture.
  const maintenant = Date.now();
  const bougies = brut
    .map((k) => ({
      t: Number(k[0]) * 1000,
      ouverture: Number(k[1]),
      haut: Number(k[2]),
      bas: Number(k[3]),
      cloture: Number(k[4]),
      volume: Number(k[6]),
      finMs: (Number(k[0]) + 60) * 1000,
    }))
    .filter((b) => Number.isFinite(b.t) && b.finMs < maintenant)
    .map(({ finMs, ...b }) => b)
    .sort((a, b) => a.t - b.t)
    .slice(-Math.min(Math.max(limite, 1), 1000));

  return { disponible: true, bougies, motif: "" };
}
