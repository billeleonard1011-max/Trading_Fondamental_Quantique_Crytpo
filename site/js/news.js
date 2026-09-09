/**
 * Page de détail d'une actualité.
 *
 * Le fil ne montre que les titres ; le détail vit ici. L'article source
 * s'ouvre dans un nouvel onglet avec ``rel="noopener"``, faute de quoi la
 * page ouverte pourrait manipuler celle-ci.
 */

import { CONFIG } from "./config.js";
import { chargerJson } from "./donnees.js";
import { dateHeure, echapper } from "./format.js";
import { rendreBadge, rendreIndisponible } from "./rendu.js";
import { installerTheme } from "./theme.js";

/**
 * Rend le détail d'un item.
 *
 * @param {object|null} item Item trouvé, ou ``null``.
 * @param {string} identifiant Identifiant demandé.
 * @returns {string} HTML du détail.
 */
export function rendreDetail(item, identifiant) {
  if (!item) {
    return `<article class="fil-item">
      <p class="fil-vide">Aucune actualité ne porte l'identifiant
      « ${echapper(identifiant)} ». Le fil ne conserve que les items récents :
      celui-ci a pu en sortir.</p>
      <p><a href="index.html">Retour au fil</a></p></article>`;
  }

  const themes = (item.tickers_ou_themes_lies || [])
    .map((t) => rendreBadge(t, "neutre"))
    .join(" ");

  const analyse = item.a_une_analyse_interne && item.analyse_interne
    ? `<div class="trame-section"><h4>Analyse</h4>
        <p>${echapper(item.analyse_interne)}</p></div>`
    : `<div class="trame-section"><h4>Analyse</h4>
        ${rendreIndisponible(
          "cet item n'a pas encore été analysé : il était déjà connu lors de la " +
          "dernière collecte, ou le quota d'analyses de l'exécution était atteint",
        )}</div>`;

  return `
    <article class="fil-item">
      <h2>${echapper(item.titre_affiche)}</h2>
      <div class="fil-meta">
        <span>${echapper(item.source_nom || "source inconnue")}</span>
        <span>${echapper(dateHeure(item.horodatage_utc))}</span>
        ${item.nouveaute ? rendreBadge("nouveau", "alerte") : ""}
      </div>
      ${themes ? `<div class="trame-section"><h4>Valeurs et thèmes liés</h4>
        <p>${themes}</p></div>` : ""}
      ${analyse}
      ${item.url_source
        ? `<p><a href="${echapper(item.url_source)}" target="_blank" rel="noopener noreferrer">
             Lire l'article source ↗</a></p>`
        : "<p>Aucun lien source publié pour cet item.</p>"}
      <p><a href="index.html">Retour au fil</a></p>
    </article>`;
}

/** Point d'entrée de la page. */
async function demarrer() {
  installerTheme();
  const zone = document.getElementById("detail");
  if (!zone) return;

  const identifiant = new URLSearchParams(window.location.search).get("id") || "";
  const etat = await chargerJson(CONFIG.sources.filQuantique);
  if (!etat.disponible) {
    zone.innerHTML = rendreIndisponible(etat.motif);
    return;
  }
  const items = Array.isArray(etat.donnees) ? etat.donnees : [];
  zone.innerHTML = rendreDetail(items.find((i) => i && i.id === identifiant), identifiant);
}

// Le module s'exécute au chargement dans le navigateur, mais reste importable
// hors navigateur : les tests en vérifient les fonctions pures sans DOM.
if (typeof document !== "undefined") {
  demarrer();
}
