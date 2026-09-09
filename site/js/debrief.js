/**
 * Débrief du soir : le biais annoncé confronté au mouvement réel.
 *
 * Un score de fiabilité sur trois jours n'est pas un score, c'est une
 * anecdote. La page le dit explicitement au lieu d'afficher un pourcentage
 * qui paraîtrait mesuré — c'est la même règle que celle appliquée aux
 * précédents historiques du moteur or.
 */

import { CONFIG } from "./config.js";
import { chargerJson, chargerJsonl, lire } from "./donnees.js";
import { ABSENT, dateHeure, echapper, nombre, pourcent } from "./format.js";
import { rendreBadge, rendreIndisponible, tonDuBiais } from "./rendu.js";
import { installerTheme } from "./theme.js";

/**
 * Évalue la tenue des biais passés.
 *
 * Chaque ligne d'historique porte le prix de l'or au moment du biais. En
 * comparant au prix de la ligne suivante, on sait si le biais annoncé allait
 * dans le bon sens. Un biais neutre n'est pas noté : il n'annonçait rien.
 *
 * @param {Array} lignes Historique des biais, du plus ancien au plus récent.
 * @param {number} minJours Nombre de jours en deçà duquel on refuse de conclure.
 * @returns {object} Score et détail par jour.
 */
export function evaluerFiabilite(lignes, minJours = CONFIG.minJoursFiabilite) {
  const triees = (Array.isArray(lignes) ? lignes.slice() : [])
    .filter((l) => l && l.date)
    .sort((a, b) => String(a.date).localeCompare(String(b.date)));

  const evaluables = [];
  for (let i = 0; i < triees.length - 1; i += 1) {
    const jour = triees[i];
    const suivant = triees[i + 1];
    const p0 = Number(jour.prix_or_au_moment_du_biais);
    const p1 = Number(suivant.prix_or_au_moment_du_biais);
    if (!Number.isFinite(p0) || !Number.isFinite(p1) || p0 === 0) continue;
    if (jour.biais !== "haussier" && jour.biais !== "vendeur") continue;

    const variation = (p1 / p0 - 1) * 100;
    const attendu = jour.biais === "haussier" ? 1 : -1;
    evaluables.push({
      date: jour.date,
      biais: jour.biais,
      conviction: jour.conviction,
      variation,
      correct: Math.sign(variation) === attendu,
    });
  }

  const suffisant = triees.length >= minJours;
  const justes = evaluables.filter((e) => e.correct).length;

  return {
    n_jours_historique: triees.length,
    n_evaluables: evaluables.length,
    n_justes: justes,
    taux: evaluables.length ? justes / evaluables.length : null,
    suffisant,
    motif: suffisant
      ? ""
      : `L'historique compte ${triees.length} jour(s), il en faut ${minJours} pour ` +
        "qu'un taux de réussite veuille dire quelque chose. Un score sur quelques " +
        "jours mesure le hasard, pas le système.",
    detail: evaluables.slice(-30).reverse(),
  };
}

/**
 * Rend le tableau des biais évalués.
 *
 * @param {object} fiabilite Sortie de :func:`evaluerFiabilite`.
 * @returns {string} HTML du tableau.
 */
export function rendreFiabilite(fiabilite) {
  const entete = `
    <div class="trame-section">
      <h4>Score de fiabilité sur 30 jours</h4>
      ${
        fiabilite.suffisant && fiabilite.taux !== null
          ? `<p class="cle-valeur">${nombre(fiabilite.taux * 100, 0)} %
             <small>sur ${fiabilite.n_evaluables} biais directionnels</small></p>`
          : rendreIndisponible(
              fiabilite.motif ||
                "aucun biais directionnel à évaluer : les biais neutres n'annoncent rien",
            )
      }
    </div>`;

  if (!fiabilite.detail.length) {
    return `${entete}<p class="fil-vide">Aucun biais directionnel dans l'historique.
      Un biais neutre n'annonce rien, il n'est donc pas noté.</p>`;
  }

  const lignes = fiabilite.detail
    .map((e) => `<tr>
      <td>${echapper(e.date)}</td>
      <td>${rendreBadge(e.biais, tonDuBiais(e.biais))}</td>
      <td>${echapper(e.conviction || ABSENT)}</td>
      <td>${pourcent(e.variation)}</td>
      <td>${e.correct ? "✓" : "✗"}</td>
    </tr>`)
    .join("");

  return `${entete}
    <div class="tableau-enveloppe">
      <table class="tableau">
        <thead><tr><th>Date</th><th>Biais annoncé</th><th>Conviction</th>
          <th>Variation suivante</th><th>Tenu</th></tr></thead>
        <tbody>${lignes}</tbody>
      </table>
    </div>`;
}

/** Point d'entrée de la page. */
async function demarrer() {
  installerTheme();
  const zone = document.getElementById("debrief");
  if (!zone) return;

  const [or, fil, historique] = await Promise.all([
    chargerJson(CONFIG.sources.or),
    chargerJson(CONFIG.sources.filQuantique),
    chargerJsonl(CONFIG.sources.historiqueBiais),
  ]);

  const horodatage = document.getElementById("horodatage");
  if (horodatage) {
    horodatage.textContent = or.disponible
      ? `données du ${dateHeure(lire(or.donnees, "meta.horodatage_utc", null))}`
      : "horodatage indisponible";
  }

  const biais = lire(or.donnees, "biais", {});
  const prix = lire(or.donnees, "prix", {});

  const journee = or.disponible
    ? `<div class="trame-section"><h4>La journée</h4>
        <p>Biais annoncé : ${rendreBadge(biais.biais || ABSENT, tonDuBiais(biais.biais))}
        (conviction ${echapper(biais.conviction || ABSENT)}).</p>
        <p>Mouvement du prix : ${
          prix.disponible
            ? `${pourcent(prix.variation_5j_pct)} sur cinq séances,
               ${pourcent(prix.variation_20j_pct)} sur vingt.`
            : echapper(prix.motif || "prix indisponible")
        }</p></div>`
    : rendreIndisponible(or.motif);

  const items = Array.isArray(fil.donnees) ? fil.donnees : [];
  const coincidences = items.length
    ? `<ul class="fil-liste">${items.slice(0, 8).map((i) => `
        <li class="fil-item"><a href="news.html?id=${encodeURIComponent(i.id || "")}">${
          echapper(i.titre_affiche)
        }</a>
        <div class="fil-meta"><span>${echapper(i.source_nom || "")}</span>
        <span>${echapper(dateHeure(i.horodatage_utc))}</span></div></li>`).join("")}</ul>`
    : `<p class="fil-vide">Aucune actualité collectée pour cette journée.</p>`;

  const fiabilite = historique.disponible
    ? rendreFiabilite(evaluerFiabilite(historique.lignes))
    : rendreIndisponible(historique.motif);

  zone.innerHTML = `${journee}
    <div class="trame-section"><h4>Actualités de la journée</h4>${coincidences}</div>
    ${fiabilite}`;
}

// Le module s'exécute au chargement dans le navigateur, mais reste importable
// hors navigateur : les tests en vérifient les fonctions pures sans DOM.
if (typeof document !== "undefined") {
  demarrer();
}
