/**
 * Formatage : dates, âges, nombres, libellés.
 *
 * Toutes les fonctions sont pures et tolèrent l'absence de donnée. Une
 * valeur manquante ne rend jamais « 0 » ni une chaîne vide : elle rend un
 * tiret cadratin, qui se lit comme une absence et non comme une mesure.
 */

/** Marque d'absence, volontairement distincte d'un zéro. */
export const ABSENT = "—";

/**
 * Formate un nombre avec un nombre de décimales fixe.
 *
 * @param {number|null|undefined} valeur Valeur à formater.
 * @param {number} decimales Nombre de décimales.
 * @param {boolean} signe Forcer le signe, même positif.
 * @returns {string} Le nombre formaté, ou une marque d'absence.
 */
export function nombre(valeur, decimales = 2, signe = false) {
  if (valeur === null || valeur === undefined || Number.isNaN(valeur)) return ABSENT;
  const n = Number(valeur);
  if (!Number.isFinite(n)) return ABSENT;
  const texte = n.toLocaleString("fr-FR", {
    minimumFractionDigits: decimales,
    maximumFractionDigits: decimales,
  });
  return signe && n > 0 ? `+${texte}` : texte;
}

/**
 * Formate un pourcentage.
 *
 * @param {number|null|undefined} valeur Valeur en pourcentage.
 * @param {number} decimales Nombre de décimales.
 * @param {boolean} signe Forcer le signe.
 * @returns {string} Le pourcentage formaté.
 */
export function pourcent(valeur, decimales = 1, signe = true) {
  const base = nombre(valeur, decimales, signe);
  return base === ABSENT ? ABSENT : `${base} %`;
}

/**
 * Formate un horodatage en heure locale courte.
 *
 * @param {string|null} iso Horodatage ISO.
 * @returns {string} Heure au format court.
 */
export function heure(iso) {
  if (!iso) return ABSENT;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return ABSENT;
  return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

/**
 * Formate un horodatage en date et heure lisibles.
 *
 * @param {string|null} iso Horodatage ISO.
 * @returns {string} Date et heure.
 */
export function dateHeure(iso) {
  if (!iso) return ABSENT;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return ABSENT;
  return d.toLocaleString("fr-FR", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

/**
 * Exprime un écart de temps en langage courant.
 *
 * @param {string|null} iso Horodatage ISO passé.
 * @param {Date} maintenant Instant de référence, pour les tests.
 * @returns {string} Écart relatif, par exemple « il y a 2 h ».
 */
export function ecoule(iso, maintenant = new Date()) {
  if (!iso) return ABSENT;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return ABSENT;
  const minutes = Math.round((maintenant.getTime() - d.getTime()) / 60000);
  if (minutes < 1) return "à l'instant";
  if (minutes < 60) return `il y a ${minutes} min`;
  const heures = Math.round(minutes / 60);
  if (heures < 24) return `il y a ${heures} h`;
  const jours = Math.round(heures / 24);
  return `il y a ${jours} j`;
}

/**
 * Exprime un compte à rebours en heures et minutes.
 *
 * @param {number|null} minutes Minutes restantes.
 * @returns {string} Compte à rebours lisible.
 */
export function compteARebours(minutes) {
  if (minutes === null || minutes === undefined || !Number.isFinite(Number(minutes))) {
    return ABSENT;
  }
  const m = Math.round(Number(minutes));
  if (m < 0) return "passée";
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ${String(m % 60).padStart(2, "0")}`;
  const j = Math.floor(h / 24);
  return `${j} j ${h % 24} h`;
}

/**
 * Dit si une donnée doit être signalée comme datée.
 *
 * Le seuil dépend de la nature du bloc : un rapport COT de trois jours est
 * normal, un prix de trois jours ne l'est pas.
 *
 * @param {number|null} ageJours Âge de la donnée.
 * @param {number} seuil Seuil applicable au bloc.
 * @returns {boolean} Vrai si l'âge dépasse le seuil.
 */
export function estDatee(ageJours, seuil) {
  if (ageJours === null || ageJours === undefined) return false;
  return Number(ageJours) > Number(seuil);
}

/**
 * Rend l'âge d'une donnée en toutes lettres.
 *
 * @param {number|null} ageJours Âge en jours.
 * @returns {string} Libellé de l'âge.
 */
export function libelleAge(ageJours) {
  if (ageJours === null || ageJours === undefined) return ABSENT;
  const n = Number(ageJours);
  if (n <= 0) return "aujourd'hui";
  if (n === 1) return "hier";
  return `il y a ${n} jours`;
}

/**
 * Échappe le texte destiné à être inséré dans du HTML.
 *
 * Les titres de presse et les motifs viennent de sources externes : les
 * insérer tels quels ouvrirait une injection. Cette fonction est appelée
 * sur toute chaîne d'origine externe.
 *
 * @param {*} valeur Valeur à échapper.
 * @returns {string} Texte sûr.
 */
export function echapper(valeur) {
  if (valeur === null || valeur === undefined) return "";
  return String(valeur)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
