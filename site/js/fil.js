/**
 * Fil de news : onglets par catégorie, et état vide explicite.
 *
 * Les fils crypto et géopolitique n'existent pas encore. Leurs onglets sont
 * néanmoins présents : une catégorie qui n'apparaîtrait qu'une fois remplie
 * laisserait croire que rien n'a été prévu, alors que le format unifié les
 * attend déjà.
 */

import { echapper, ecoule } from "./format.js";
import { rendreBadge } from "./rendu.js";

/** Libellés affichés pour chaque onglet. */
export const LIBELLES = {
  tout: "Tout",
  quantique: "Quantique",
  crypto: "Crypto",
  geopolitique: "Géopolitique",
};

/**
 * Message d'état vide, adapté à la catégorie.
 *
 * Un fil vide n'a pas la même signification selon la catégorie : le fil
 * quantique existe et n'a rien trouvé, les deux autres ne sont pas encore
 * construits. Afficher le même message pour les trois serait trompeur.
 *
 * @param {string} categorie Catégorie concernée.
 * @returns {string} HTML de l'état vide.
 */
export function rendreVide(categorie) {
  if (categorie === "crypto" || categorie === "geopolitique") {
    return `<p class="fil-vide">Le fil ${echapper(LIBELLES[categorie])} n'est pas encore
      construit. Son onglet est déjà là parce que le format de sortie l'attend :
      les items arriveront sans changement d'interface.</p>`;
  }
  if (categorie === "quantique") {
    return `<p class="fil-vide">Aucune actualité quantique retenue sur la période.
      Le fil ne garde que les articles citant une valeur suivie ou un acteur connu
      du secteur.</p>`;
  }
  return `<p class="fil-vide">Aucune actualité disponible.</p>`;
}

/**
 * Rend une entrée du fil.
 *
 * L'accueil n'affiche que le titre, la source et l'heure : le détail vit sur
 * sa propre page, ce qui garde le fil scannable.
 *
 * @param {object} item Item au format unifié.
 * @param {Date} maintenant Instant de référence, pour les tests.
 * @returns {string} HTML de l'entrée.
 */
export function rendreItem(item, maintenant = new Date()) {
  const impact = (item.tickers_ou_themes_lies || []).length > 1 ? "alerte" : "neutre";
  const themes = (item.tickers_ou_themes_lies || []).join(", ");
  return `
    <li class="fil-item ${item.nouveaute ? "fil-item--nouveau" : ""}">
      <a href="news.html?id=${encodeURIComponent(item.id || "")}">${
        echapper(item.titre_affiche)
      }</a>
      <div class="fil-meta">
        <span>${echapper(item.source_nom || "source inconnue")}</span>
        <span>${echapper(ecoule(item.horodatage_utc, maintenant))}</span>
        ${themes ? rendreBadge(themes, impact) : ""}
        ${item.nouveaute ? rendreBadge("nouveau", "alerte") : ""}
        ${item.a_une_analyse_interne ? rendreBadge("analysé", "neutre") : ""}
      </div>
    </li>`;
}

/**
 * Filtre les items d'une catégorie.
 *
 * @param {Array} items Tous les items.
 * @param {string} categorie Catégorie voulue, ``tout`` pour ne pas filtrer.
 * @returns {Array} Items retenus, du plus récent au plus ancien.
 */
export function filtrer(items, categorie) {
  const liste = Array.isArray(items) ? items : [];
  const retenus = categorie === "tout"
    ? liste.slice()
    : liste.filter((i) => i && i.categorie === categorie);
  return retenus.sort((a, b) =>
    String(b.horodatage_utc || "").localeCompare(String(a.horodatage_utc || "")));
}

/**
 * Rend la liste complète d'une catégorie.
 *
 * @param {Array} items Items disponibles.
 * @param {string} categorie Catégorie affichée.
 * @param {Date} maintenant Instant de référence.
 * @returns {string} HTML de la liste.
 */
export function rendreFil(items, categorie, maintenant = new Date()) {
  const retenus = filtrer(items, categorie);
  if (!retenus.length) return rendreVide(categorie);
  return `<ul class="fil-liste">${
    retenus.map((i) => rendreItem(i, maintenant)).join("")
  }</ul>`;
}
