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
  s1: "sortie complète au 0,72 (sweep, variante 1)",
  s2: "0,72 puis niveau structurel (sweep, variante 2)",
  s3: "niveau structurel seul (sweep, variante 3)",
};

/** Variantes à paliers, par setup : la seule dont la résolution se détaille tranche par tranche. */
const VARIANTE_A_PALIERS = { order_block: "c", sweep: "s2" };

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
  fibonacci_0_72: "0,72 du mouvement de référence",
  niveau_haut: "ancien plus haut non balayé",
  niveau_bas: "ancien plus bas non balayé",
};

/**
 * Décrit le déclencheur d'une entrée : la zone d'order block, ou le niveau
 * balayé — avec son unité, son prix, quand il s'est formé et jusqu'où la
 * mèche du sweep est allée. C'est ce qui dit de quel setup vient le signal.
 *
 * @param {object} entree Événement d'entrée, ou ligne mise en forme par api.js.
 * @returns {string} Fragment de phrase, sans point final.
 */
function rendreDeclencheur(entree) {
  if (entree.setup === "sweep") {
    const cote = entree.niveauCote === "haut" ? "plus haut" : "plus bas";
    const prix = typeof entree.niveauPrix === "number" ? `${entree.niveauPrix.toFixed(2)} $` : "niveau non journalisé";
    const forme = entree.niveauFormation ? `, formé le ${horodatage(entree.niveauFormation)}` : "";
    const meche = typeof entree.sweepExtreme === "number" ? `, mèche du sweep à ${entree.sweepExtreme.toFixed(2)} $` : "";
    return `sur le balayage d'un ancien ${cote} ${entree.niveauUnite || entree.timeframeOb} à ${prix}${forme}${meche}`;
  }
  return `sur un order block ${entree.timeframeOb} [${entree.obBas.toFixed(2)}, ${entree.obHaut.toFixed(2)}] $`;
}

/**
 * Énumère les objectifs suivis d'une entrée, selon son setup.
 *
 * @param {object} entree Événement d'entrée.
 * @returns {string} Énumération lisible.
 */
function rendreObjectifs(entree) {
  const o = entree.objectifs || {};
  const prix = (v) => (typeof o[v] === "number" ? `${o[v].toFixed(2)} $` : "—");
  if (entree.setup === "sweep") {
    return [
      o.s1 !== null && o.s1 !== undefined ? `0,72 du mouvement de référence à ${prix("s1")}` : "0,72 non disponible pour ce signal",
      o.s2 !== null && o.s2 !== undefined ? `0,72 puis niveau structurel (première cible à ${prix("s2")})` : "0,72 puis structurel non disponible",
      o.s3 !== null && o.s3 !== undefined ? `niveau structurel seul à ${prix("s3")}` : "structurel seul non disponible pour ce signal",
    ].join(", ");
  }
  return [
    o.a !== null && o.a !== undefined ? `structurel à ${prix("a")}` : "structurel non disponible pour ce signal",
    `1:1,5 à ${prix("b15")}`,
    `1:2 à ${prix("b2")}`,
    `1:3 à ${prix("b3")}`,
  ].join(", ");
}

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
    // Cas réel : les signaux journalisés avant l'ajout des colonnes fvg_haut
    // et fvg_bas (10 septembre 2026) n'ont jamais porté ce niveau. Le dire
    // plutôt que de le recalculer après coup — une zone reconstruite avec le
    // code d'aujourd'hui ne serait pas celle qui a confirmé ce signal-là.
    return `un écart de valeur (FVG) en ${timeframeFvg}, dont le niveau n'a pas été journalisé`;
  }
  return `un écart de valeur (FVG) en ${timeframeFvg} [${fvgBas.toFixed(2)}, ${fvgHaut.toFixed(2)}] $`;
}

/** Libellé court de la variante qui porte chaque objectif : sans lui, quatre
 * nombres alignés ne veulent plus rien dire. */
const LIBELLE_COURT_VARIANTE = {
  a: "structurel",
  b15: "1:1,5",
  b2: "1:2",
  b3: "1:3",
  s1: "0,72 du mouvement de référence",
  s3: "niveau structurel",
};

/** Variantes dont l'objectif tient en un prix, par setup, dans l'ordre des TP.
 *
 * La variante à paliers de chaque setup (`c`, `s2`) en est absente : elle ne
 * vise pas un prix mais une suite de zones, détaillée dans son propre
 * tableau. La faire figurer ici afficherait deux fois le même premier
 * niveau, puisqu'elle partage sa première cible avec la variante
 * structurelle.
 */
const VARIANTES_A_PRIX = {
  order_block: ["a", "b15", "b2", "b3"],
  sweep: ["s1", "s3"],
};

/**
 * Énumère les niveaux d'exécution d'un signal, dans l'ordre d'affichage.
 *
 * Donnée structurée, pas une phrase : c'est le site (et le journal du
 * Worker) qui la met en liste. Les libellés sont des constantes de ce
 * module, comme les en-têtes du tableau des paliers — ils ne passent donc
 * pas par le garde-fou anti-recommandation, qui contrôle la prose générée.
 *
 * Le prix d'entrée est libellé « Prix d'entrée » et non « point d'entrée » :
 * cette dernière formulation figure dans les motifs interdits
 * (`recommandation.js`), et affaiblir ce garde-fou pour un libellé serait un
 * mauvais échange. Le sens est le même, le mot est factuel.
 *
 * @param {object} entree Événement d'entrée, ou signal mis en forme par api.js.
 * @returns {Array<{cle: string, libelle: string, prix: number|null}>} Niveaux,
 *   `prix` valant `null` quand la variante n'a pas d'objectif pour ce signal.
 */
export function niveauxExecution(entree) {
  const setup = entree.setup === "sweep" ? "sweep" : "order_block";
  const objectifs = entree.objectifs || {};
  const niveaux = [
    { cle: "entree", libelle: "Prix d'entrée", prix: entree.prixEntree },
    { cle: "stop", libelle: "Stop loss", prix: entree.stop },
  ];
  VARIANTES_A_PRIX[setup].forEach((variante, rang) => {
    const prix = objectifs[variante];
    niveaux.push({
      cle: variante,
      libelle: `TP${rang + 1} (${LIBELLE_COURT_VARIANTE[variante]})`,
      prix: typeof prix === "number" ? prix : null,
    });
  });
  return niveaux;
}

/**
 * Rend le contexte du setup, en une phrase : ce qui a été détecté, quand, et
 * sur quelles zones. Les niveaux d'exécution n'y figurent pas — ils sont
 * listés à part (voir {@link niveauxExecution}), parce que six prix noyés
 * dans un paragraphe ne se lisent pas d'un coup d'œil.
 *
 * @param {object} entree Événement d'entrée.
 * @returns {string} La phrase, au conditionnel passé.
 */
export function contexteEntree(entree) {
  // Accord en genre : « un achat … détecté », « une vente … détectée ».
  const achat = entree.sens === "haussier";
  const sensTexte = achat ? "un achat" : "une vente";
  const accord = achat ? "détecté" : "détectée";
  return (
    `D'après la mécanique suivie, ${sensTexte} aurait été ${accord} le ` +
    `${horodatage(entree.horodatageDetection)}, ${rendreDeclencheur(entree)}, ` +
    `confirmé par ${rendreFvg(entree)}.`
  );
}

/**
 * Met les niveaux d'exécution en lignes, pour les journaux du Worker (texte
 * brut). Le site, lui, les rend en liste à partir de la même donnée.
 *
 * @param {object} entree Événement d'entrée.
 * @returns {string} Une ligne par niveau, précédée d'un saut de ligne.
 */
function rendreNiveauxTexte(entree) {
  const niveaux = niveauxExecution(entree);
  const largeur = Math.max(...niveaux.map((n) => n.libelle.length));
  return niveaux
    .map((n) => `\n  ${n.libelle.padEnd(largeur)} : ${n.prix === null ? "non disponible pour ce signal" : `${n.prix.toFixed(2)} $`}`)
    .join("");
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
  // Pas de phrase de dénégation finale ("ceci n'est pas une recommandation") :
  // écrire ce mot pour le nier le fait détecter par le garde-fou lui-même
  // (vérifié en écrivant le test — voir modules/quantum/moves.py, où le
  // même piège justifie CLES_AVERTISSEMENT côté Python). Le message reste
  // au conditionnel du début à la fin, sans qu'aucun mot du champ lexical
  // interdit n'y figure ; l'avertissement permanent de l'onglet Trading
  // (partie 4) porte la mise en garde une fois pour toutes, pas ce texte.
  // Les zones commencent par une espace (elles se suivent en prose dans le
  // texte public) : en liste, elles prennent leur propre ligne.
  const zones = rendreZones(entree.paliers).replace(/^ /, "\n  ");
  return (
    `${contexteEntree(entree)}${rendreNiveauxTexte(entree)}${zones}` +
    `\n  Taille correspondant au risque suivi : ${entree.lots} lot(s).`
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
  // Les niveaux d'exécution ne sont plus dans cette phrase : la route les
  // publie en données structurées (`niveaux`, voir api.js) et le site les
  // met en liste. Les zones de liquidité restent citées ici, en plus de leur
  // tableau, parce qu'elles portent une lecture (quelle liquidité est visée)
  // que la seule colonne de prix ne donne pas.
  return `${contexteEntree(entree)}${rendreZones(entree.paliers)}`;
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
  const varianteAPaliers = VARIANTE_A_PALIERS[resolution.setup || "order_block"];
  if (resolution.variante === varianteAPaliers && Array.isArray(resolution.paliers) && resolution.paliers.length) {
    // La raison du break-even est dite une fois, en fin de phrase, plutôt
    // que répétée à chaque tranche concernée.
    const auBreakEven = resolution.paliers.some((p) => p.motif_sortie === "break_even");
    const rappel = auBreakEven
      ? ", le stop du solde ayant été ramené au prix d'entrée après la première zone"
      : "";
    return (
      `La ${LIBELLE_VARIANTE[resolution.variante]} du signal se serait dénouée le ` +
      `${horodatage(resolution.horodatageResolution)} : ${rendreTranches(resolution.paliers)}${rappel}.`
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
        return `${part} au prix d'entrée`;
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
