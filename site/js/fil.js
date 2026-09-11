/**
 * Fil de news : onglets par catégorie, et état vide explicite.
 *
 * Les trois fils (quantique, crypto, géopolitique) partagent le même format
 * de sortie et le même module de collecte : modules/quantum/feed.py,
 * modules/crypto/feed.py et modules/geopolitique/feed.py.
 */

import { echapper, ecoule } from "./format.js";
import { rendreBadge } from "./rendu.js";

/**
 * Message d'état vide, adapté à la catégorie.
 *
 * Chaque fil ne garde que les articles pertinents pour son domaine : une
 * valeur suivie, un jeton suivi, ou — pour le fil géopolitique — un article
 * touchant l'univers surveillé (config/univers_admission.yaml). Un fil vide
 * dit le plus souvent qu'aucun article de la période ne correspondait, pas
 * qu'il n'a rien collecté.
 *
 * @param {string} categorie Catégorie concernée.
 * @returns {string} HTML de l'état vide.
 */
export function rendreVide(categorie) {
  if (categorie === "quantique") {
    return `<p class="fil-vide">Aucune actualité quantique retenue sur la période.
      Le fil ne garde que les articles citant une valeur suivie ou un acteur connu
      du secteur.</p>`;
  }
  if (categorie === "crypto") {
    return `<p class="fil-vide">Aucune actualité crypto retenue sur la période.
      Le fil ne garde que les articles citant un jeton suivi.</p>`;
  }
  if (categorie === "geopolitique") {
    return `<p class="fil-vide">Aucune actualité géopolitique ou de marché retenue sur la période. Le fil garde les
      articles qui touchent l'univers suivi : les actifs détenus (or, quantique, crypto),
      ce qui les influence (pétrole, dollar, taux, inflation, banques centrales, actions et
      technologie, semi-conducteurs, matières premières, banques et crédit) et la géopolitique
      au sens large (conflits, sanctions, accords, élections, tensions commerciales).</p>`;
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
