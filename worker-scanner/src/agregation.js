/**
 * Agrégation des unités de temps à partir d'une unique série M1.
 *
 * Port fidèle de backtest/data.py. Voir worker-scanner/README.md pour la
 * justification du portage (Python côté backtest, JavaScript côté Worker
 * Cloudflare — deux exécutions différentes, aucun moyen de partager le code
 * source directement) et pour la façon dont l'identité des résultats est
 * vérifiée (tests/parite.test.js).
 *
 * Pourquoi une seule source
 * -------------------------
 * Toutes les unités de temps du scanner — M3, M5, M15, M30, H1 — sont
 * construites ici à partir de la même série d'une minute. Mélanger deux
 * fournisseurs pour deux unités différentes suffirait à fabriquer des
 * signaux qui n'ont jamais existé.
 *
 * Bougies closes uniquement
 * -------------------------
 * Une bougie H1 n'existe pour le moteur qu'une fois sa dernière minute
 * écoulée. fenetreClose() applique cette règle : à l'instant `t`, elle ne
 * rend que les bougies dont la fin est antérieure ou égale à `t`. C'est la
 * première ligne de défense contre le look-ahead.
 *
 * Représentation d'une bougie
 * ----------------------------
 * Une bougie est un objet `{ ouverture, haut, bas, cloture, volume, t }`,
 * où `t` est l'horodatage d'ouverture en millisecondes UTC (epoch). C'est
 * la même convention que backtest/data.py : l'horodatage d'une bougie est
 * celui de son ouverture, sa clôture se déduit en ajoutant la durée de
 * l'unité.
 */

/** Durée de chaque unité, en millisecondes. Équivalent de DUREES (data.py). */
export const DUREES_MS = Object.freeze({
  M1: 60_000,
  M3: 3 * 60_000,
  M5: 5 * 60_000,
  M15: 15 * 60_000,
  M30: 30 * 60_000,
  H1: 60 * 60_000,
});

/** Unités sur lesquelles les order blocks sont cherchés. */
export const UNITES_ORDER_BLOCK = Object.freeze(["H1", "M30", "M15"]);

/** Unités où l'on cherche un FVG de confirmation, dans cet ordre. */
export const UNITES_FVG = Object.freeze(["M5", "M3", "M1"]);

/**
 * Construit une unité de temps supérieure à partir du M1.
 *
 * @param {Array<{t:number, ouverture:number, haut:number, bas:number, cloture:number, volume:number}>} m1
 *   Bougies d'une minute, triées par horodatage croissant.
 * @param {string} unite Nom de l'unité voulue, clé de DUREES_MS.
 * @returns {Array} Bougies OHLCV de l'unité, dans l'ordre chronologique.
 *   Vide si l'entrée l'est ou si l'unité est inconnue.
 */
export function agreger(m1, unite) {
  const duree = DUREES_MS[unite];
  if (!m1 || m1.length === 0 || !duree) return [];
  if (unite === "M1") return m1.map((b) => ({ ...b }));

  // Regroupement par fenêtre alignée sur l'époque Unix, label="left" comme
  // pandas.resample(..., label="left", closed="left") : chaque bougie porte
  // l'horodatage du début de sa fenêtre.
  const groupes = new Map();
  for (const bougie of m1) {
    const debutFenetre = Math.floor(bougie.t / duree) * duree;
    let groupe = groupes.get(debutFenetre);
    if (!groupe) {
      groupe = { t: debutFenetre, ouverture: bougie.ouverture, haut: bougie.haut, bas: bougie.bas, cloture: bougie.cloture, volume: 0 };
      groupes.set(debutFenetre, groupe);
    }
    // Les bougies M1 arrivent triées : la première vue dans une fenêtre
    // porte l'ouverture, la dernière vue porte la clôture.
    groupe.haut = Math.max(groupe.haut, bougie.haut);
    groupe.bas = Math.min(groupe.bas, bougie.bas);
    groupe.cloture = bougie.cloture;
    groupe.volume += bougie.volume;
  }

  return Array.from(groupes.values()).sort((a, b) => a.t - b.t);
}

/**
 * Ne rend que les bougies effectivement closes à un instant donné.
 *
 * C'est la barrière anti-look-ahead du moteur. Une bougie H1 ouverte à
 * 10:00 n'est close qu'à 11:00 : la consulter à 10:30 reviendrait à lire une
 * clôture qui n'a pas encore eu lieu.
 *
 * @param {Array} cadre Bougies de l'unité, triées par horodatage croissant.
 * @param {string} unite Nom de l'unité, pour connaître sa durée.
 * @param {number} instant Instant courant du moteur, en millisecondes UTC.
 * @returns {Array} Sous-ensemble des bougies dont la clôture est antérieure
 *   ou égale à `instant`.
 */
export function fenetreClose(cadre, unite, instant) {
  const duree = DUREES_MS[unite];
  if (!cadre || cadre.length === 0 || !duree) return [];
  return cadre.filter((b) => b.t + duree <= instant);
}
