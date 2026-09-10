/**
 * Rendu textuel des alertes du fil de trading.
 *
 * Règle absolue du projet, rappelée ici parce qu'elle est le seul point de
 * sortie textuelle du scanner : ce système ne recommande jamais de prendre
 * un trade. Chaque phrase décrit ce que la mécanique a détecté, au passé ou
 * au conditionnel (« le moteur aurait ouvert »), jamais à l'impératif.
 * Chaque texte produit ici est vérifié par verifierAbsenceRecommandation()
 * avant publication (voir index.js) — la vérification ne fait pas confiance
 * à la seule discipline de rédaction.
 */

import { verifierAbsenceRecommandation } from "./recommandation.js";

const LIBELLE_VARIANTE = {
  a: "structurelle (A)",
  b15: "ratio 1:1,5 (B)",
  b2: "ratio 1:2 (B)",
  b3: "ratio 1:3 (B)",
};

/**
 * Formate un instant en horodatage lisible, UTC explicite.
 * @param {number} ms Instant en millisecondes UTC.
 * @returns {string} Par exemple "2026-09-09 14:32 UTC".
 */
function horodatage(ms) {
  return `${new Date(ms).toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

/**
 * Rend le texte d'une détection (entrée confirmée).
 *
 * @param {object} entree Événement `{type: "entree", ...}` de moteur.js.
 * @returns {string} Texte au conditionnel passé, jamais à l'impératif.
 */
export function rendreAlerteEntree(entree) {
  const sensTexte = entree.sens === "haussier" ? "un achat" : "une vente";
  const objectifs = [
    entree.objectifs.a !== null ? `structurel à ${entree.objectifs.a.toFixed(2)} $` : "structurel non disponible pour ce signal",
    `1:1,5 à ${entree.objectifs.b15 !== null ? entree.objectifs.b15.toFixed(2) : "—"} $`,
    `1:2 à ${entree.objectifs.b2 !== null ? entree.objectifs.b2.toFixed(2) : "—"} $`,
    `1:3 à ${entree.objectifs.b3 !== null ? entree.objectifs.b3.toFixed(2) : "—"} $`,
  ].join(", ");

  // Pas de phrase de dénégation finale ("ceci n'est pas une recommandation") :
  // écrire ce mot pour le nier le fait détecter par le garde-fou lui-même
  // (vérifié en écrivant le test — voir modules/quantum/moves.py, où le
  // même piège justifie CLES_AVERTISSEMENT côté Python). Le message reste
  // au conditionnel du début à la fin, sans qu'aucun mot du champ lexical
  // interdit n'y figure ; l'avertissement permanent de l'onglet Trading
  // (partie 4) porte la mise en garde une fois pour toutes, pas ce texte.
  return (
    `D'après la mécanique suivie, ${sensTexte} aurait été détecté à ` +
    `${entree.prixEntree.toFixed(2)} $ le ${horodatage(entree.horodatageDetection)}, ` +
    `sur un order block ${entree.timeframeOb} [${entree.obBas.toFixed(2)}, ${entree.obHaut.toFixed(2)}] $, ` +
    `confirmé par un écart de valeur (FVG) en ${entree.timeframeFvg}. ` +
    `Stop de la mécanique : ${entree.stop.toFixed(2)} $. Objectifs suivis : ${objectifs}. ` +
    `Taille correspondant au risque suivi : ${entree.lots} lot(s).`
  );
}

/**
 * Rend le texte d'une résolution (une variante atteint son stop ou son objectif).
 *
 * @param {object} resolution Événement `{type: "resolution", ...}` de moteur.js.
 * @returns {string} Texte au passé, jamais à l'impératif.
 */
export function rendreAlerteResolution(resolution) {
  const issue = resolution.statut === "gagnant" ? "atteint son objectif" : "touché son stop";
  // Voir la note dans rendreAlerteEntree : pas de dénégation finale, pour
  // les mêmes raisons.
  return (
    `La variante ${LIBELLE_VARIANTE[resolution.variante] || resolution.variante} du signal aurait ` +
    `${issue} le ${horodatage(resolution.horodatageResolution)}, ` +
    `sortie à ${resolution.prixSortie.toFixed(2)} $, ` +
    `résultat suivi de ${resolution.resultatUsd >= 0 ? "+" : ""}${resolution.resultatUsd.toFixed(2)} $.`
  );
}

/**
 * Rend le texte public d'une détection, pour l'onglet Trading du site.
 *
 * Différence avec {@link rendreAlerteEntree} : pas de taille de position.
 * Le site est public et ne publie jamais de donnée de compte, de position
 * ou de montant (règle du site, voir site/js/rendu.js::MOTIFS_INTERDITS) —
 * la variante destinée aux journaux Cloudflare (privés, réservés au
 * propriétaire via `wrangler tail`) reste la seule à mentionner les lots.
 *
 * @param {object} entree Ligne du journal, mise en forme par src/api.js.
 * @returns {string} Texte au conditionnel passé, jamais à l'impératif.
 */
export function rendrePublicEntree(entree) {
  const sensTexte = entree.sens === "haussier" ? "un achat" : "une vente";
  const objectifs = [
    entree.objectifs.a !== null ? `structurel à ${entree.objectifs.a.toFixed(2)} $` : "structurel non disponible pour ce signal",
    `1:1,5 à ${entree.objectifs.b15 !== null ? entree.objectifs.b15.toFixed(2) : "—"} $`,
    `1:2 à ${entree.objectifs.b2 !== null ? entree.objectifs.b2.toFixed(2) : "—"} $`,
    `1:3 à ${entree.objectifs.b3 !== null ? entree.objectifs.b3.toFixed(2) : "—"} $`,
  ].join(", ");

  return (
    `D'après la mécanique suivie, ${sensTexte} aurait été détecté à ` +
    `${entree.prixEntree.toFixed(2)} $ le ${horodatage(entree.horodatageDetection)}, ` +
    `sur un order block ${entree.timeframeOb} [${entree.obBas.toFixed(2)}, ${entree.obHaut.toFixed(2)}] $, ` +
    `confirmé par un écart de valeur (FVG) en ${entree.timeframeFvg}. ` +
    `Stop de la mécanique : ${entree.stop.toFixed(2)} $. Objectifs suivis : ${objectifs}.`
  );
}

/**
 * Rend le texte public d'une résolution, pour l'onglet Trading du site.
 *
 * Différence avec {@link rendreAlerteResolution} : pas de résultat en
 * dollars (dépend de la taille de position, donc de données de compte). Le
 * site exprime la performance en multiple de risque (R), calculé côté site
 * à partir du prix d'entrée, du stop et de ce prix de sortie — jamais à
 * partir d'un montant.
 *
 * @param {object} resolution Résolution mise en forme par src/api.js.
 * @returns {string} Texte au passé, jamais à l'impératif.
 */
export function rendrePublicResolution(resolution) {
  const issue = resolution.statut === "gagnant" ? "atteint son objectif" : "touché son stop";
  return (
    `La variante ${LIBELLE_VARIANTE[resolution.variante] || resolution.variante} du signal aurait ` +
    `${issue} le ${horodatage(resolution.horodatageResolution)}, ` +
    `sortie à ${resolution.prixSortie.toFixed(2)} $.`
  );
}

/**
 * Rend un événement public (entrée ou résolution) et vérifie l'absence de
 * toute formulation de recommandation avant de le renvoyer. Pendant public
 * de {@link rendreEvenement}, pour la route /journal.
 *
 * @param {object} evenement Objet `{type: "entree"|"resolution", ...}`.
 * @returns {{texte: string, infractions: Array}} Le texte, et les infractions.
 */
export function rendreEvenementPublic(evenement) {
  const texte = evenement.type === "entree" ? rendrePublicEntree(evenement) : rendrePublicResolution(evenement);
  const infractions = verifierAbsenceRecommandation({ texte });
  return { texte, infractions };
}

/**
 * Rend un événement (entrée ou résolution) et vérifie l'absence de toute
 * formulation de recommandation avant de le renvoyer.
 *
 * @param {object} evenement Événement de moteur.js.
 * @returns {{texte: string, infractions: Array}} Le texte, et la liste des
 *   infractions détectées (vide si le texte est propre). L'appelant
 *   (index.js) doit refuser de publier un texte dont `infractions` n'est
 *   pas vide plutôt que de le corriger à la volée — un correctif silencieux
 *   masquerait un vrai bug de rédaction.
 */
export function rendreEvenement(evenement) {
  const texte = evenement.type === "entree" ? rendreAlerteEntree(evenement) : rendreAlerteResolution(evenement);
  const infractions = verifierAbsenceRecommandation({ texte });
  return { texte, infractions };
}
