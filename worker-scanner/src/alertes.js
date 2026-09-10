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
  c: "sortie par paliers (C)",
};

/** Libellés des origines de zone de liquidité.
 *
 * Les identifiants techniques (`veille_haut`, `asie_bas`...) ne doivent
 * jamais atteindre un texte lisible : c'est exactement le défaut corrigé
 * ailleurs dans le projet, où « rupture sur 2_petrole » s'affichait tel quel.
 */
const LIBELLE_ORIGINE_ZONE = {
  veille_haut: "haut de la veille",
  veille_bas: "bas de la veille",
  asie_haut: "haut de la session asiatique",
  asie_bas: "bas de la session asiatique",
  order_block: "order block encore actif",
};

/**
 * Rend la liste ordonnée des zones de liquidité visées.
 *
 * @param {Array<object>} paliers Tranches de l'entrée, déjà ordonnées.
 * @returns {string} Énumération lisible, vide si aucune zone.
 */
function rendreZones(paliers) {
  if (!Array.isArray(paliers) || paliers.length === 0) return "";
  const items = paliers.map((p) => {
    const origine = LIBELLE_ORIGINE_ZONE[p.origine] || p.origine;
    const part = `${Math.round(p.fraction * 100)} % de la position`;
    // Virgule décimale, comme partout ailleurs dans les textes du projet.
    const ratio = p.ratioRisque.toFixed(1).replace(".", ",");
    return `${p.rang}) ${origine} à ${p.zone.toFixed(2)} $, ` +
      `rapport risque/récompense 1:${ratio}, ${part}`;
  });
  return ` Zones de liquidité devant le prix, de la plus proche à la plus lointaine : ${items.join(" ; ")}.`;
}

/**
 * Rend l'intervalle de prix d'un FVG.
 *
 * @param {object} evenement Entrée portant `fvgBas` et `fvgHaut`.
 * @returns {string} L'intervalle, ou une mention explicite de son absence.
 */
function rendreFvg(evenement) {
  const { fvgBas, fvgHaut, timeframeFvg } = evenement;
  if (typeof fvgBas !== "number" || typeof fvgHaut !== "number") {
    return `un écart de valeur (FVG) en ${timeframeFvg}, dont le niveau n'a pas été journalisé`;
  }
  return `un écart de valeur (FVG) en ${timeframeFvg} entre ${fvgBas.toFixed(2)} et ${fvgHaut.toFixed(2)} $`;
}

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
  // Accord en genre : « un achat … détecté », « une vente … détectée ».
  const achat = entree.sens === "haussier";
  const sensTexte = achat ? "un achat" : "une vente";
  const accord = achat ? "détecté" : "détectée";
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
    `D'après la mécanique suivie, ${sensTexte} aurait été ${accord} à ` +
    `${entree.prixEntree.toFixed(2)} $ le ${horodatage(entree.horodatageDetection)}, ` +
    `sur un order block ${entree.timeframeOb} [${entree.obBas.toFixed(2)}, ${entree.obHaut.toFixed(2)}] $, ` +
    `confirmé par ${rendreFvg(entree)}. ` +
    `Stop de la mécanique : ${entree.stop.toFixed(2)} $. Objectifs suivis : ${objectifs}.` +
    `${rendreZones(entree.paliers)} ` +
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
  // Accord en genre : « un achat … détecté », « une vente … détectée ».
  const achat = entree.sens === "haussier";
  const sensTexte = achat ? "un achat" : "une vente";
  const accord = achat ? "détecté" : "détectée";
  const objectifs = [
    entree.objectifs.a !== null ? `structurel à ${entree.objectifs.a.toFixed(2)} $` : "structurel non disponible pour ce signal",
    `1:1,5 à ${entree.objectifs.b15 !== null ? entree.objectifs.b15.toFixed(2) : "—"} $`,
    `1:2 à ${entree.objectifs.b2 !== null ? entree.objectifs.b2.toFixed(2) : "—"} $`,
    `1:3 à ${entree.objectifs.b3 !== null ? entree.objectifs.b3.toFixed(2) : "—"} $`,
  ].join(", ");

  return (
    `D'après la mécanique suivie, ${sensTexte} aurait été ${accord} à ` +
    `${entree.prixEntree.toFixed(2)} $ le ${horodatage(entree.horodatageDetection)}, ` +
    `sur un order block ${entree.timeframeOb} [${entree.obBas.toFixed(2)}, ${entree.obHaut.toFixed(2)}] $, ` +
    `confirmé par ${rendreFvg(entree)}. ` +
    `Stop de la mécanique : ${entree.stop.toFixed(2)} $. Objectifs suivis : ${objectifs}.` +
    `${rendreZones(entree.paliers)}`
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
  // Variante à paliers : une seule phrase ne peut pas résumer plusieurs
  // sorties à des prix différents. Le texte détaille alors chaque tranche.
  if (resolution.variante === "c" && Array.isArray(resolution.paliers) && resolution.paliers.length) {
    return (
      `La sortie par paliers (C) du signal se serait dénouée le ` +
      `${horodatage(resolution.horodatageResolution)} : ${rendreTranches(resolution.paliers)}.`
    );
  }
  const issue = resolution.statut === "gagnant" ? "atteint son objectif" : "touché son stop";
  return (
    `La variante ${LIBELLE_VARIANTE[resolution.variante] || resolution.variante} du signal aurait ` +
    `${issue} le ${horodatage(resolution.horodatageResolution)}, ` +
    `sortie à ${resolution.prixSortie.toFixed(2)} $.`
  );
}

/**
 * Décrit le dénouement de chaque tranche, dans l'ordre des zones.
 *
 * @param {Array<object>} paliers Tranches dénouées, telles que publiées par api.js.
 * @returns {string} Énumération lisible.
 */
function rendreTranches(paliers) {
  return paliers
    .map((p) => {
      const part = `${Math.round(p.fraction * 100)} %`;
      const origine = LIBELLE_ORIGINE_ZONE[p.origine] || p.origine;
      if (p.motif_sortie === "objectif") {
        return `${part} sur ${origine} à ${Number(p.zone).toFixed(2)} $`;
      }
      if (p.motif_sortie === "break_even") {
        return `${part} au prix d'entrée, le stop ayant été ramené là après la première zone`;
      }
      if (p.motif_sortie === "stop") {
        return `${part} au stop`;
      }
      return `${part} encore en cours`;
    })
    .join(", ");
}

/**
 * Rend le texte d'une tranche dénouée, pour les journaux du Worker.
 *
 * @param {object} evenement Événement `{type: "resolution_palier", ...}`.
 * @returns {string} Texte au conditionnel passé.
 */
export function rendreAlerteTranche(evenement) {
  const origine = LIBELLE_ORIGINE_ZONE[evenement.origine] || evenement.origine;
  const part = `${Math.round(evenement.fraction * 100)} %`;
  const issue = {
    objectif: `aurait atteint ${origine} à ${evenement.zone.toFixed(2)} $`,
    break_even: "serait sortie au prix d'entrée (stop ramené à break-even)",
    stop: "aurait touché le stop",
  }[evenement.motifSortie] || "se serait dénouée";
  return (
    `Palier ${evenement.rang} (${part} de la position) : la tranche ${issue}, ` +
    `le ${horodatage(evenement.horodatageResolution)}, à ${evenement.prixSortie.toFixed(2)} $.`
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
  let texte;
  if (evenement.type === "entree") texte = rendreAlerteEntree(evenement);
  else if (evenement.type === "resolution_palier") texte = rendreAlerteTranche(evenement);
  else texte = rendreAlerteResolution(evenement);
  const infractions = verifierAbsenceRecommandation({ texte });
  return { texte, infractions };
}
