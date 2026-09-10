/**
 * Construction de la réponse JSON publique de la route /journal (partie 4
 * du prompt : l'onglet Trading du site).
 *
 * Séparé de index.js pour rester une fonction pure, testable sans D1 ni
 * fetch. La garantie de confidentialité tient à deux niveaux, pas un seul :
 * la requête SQL de index.js ne sélectionne jamais `lots` ni
 * `resultat_*_usd` (la donnée n'existe donc même pas ici, en mémoire), et
 * ce module ne les lirait de toute façon pas s'ils étaient présents — voir
 * site/js/rendu.js::MOTIFS_INTERDITS côté site pour la même règle appliquée
 * à l'affichage.
 */

import { rendreEvenementPublic } from "./alertes.js";

const VARIANTES = ["a", "b15", "b2", "b3"];

function entreeDepuisLigne(ligne) {
  return {
    type: "entree",
    id: ligne.id,
    horodatageDetection: ligne.horodatage_detection,
    timeframeOb: ligne.timeframe_ob,
    obHaut: ligne.ob_haut,
    obBas: ligne.ob_bas,
    sens: ligne.sens,
    timeframeFvg: ligne.timeframe_fvg,
    prixEntree: ligne.prix_entree,
    stop: ligne.sl,
    objectifs: { a: ligne.tp_a, b15: ligne.tp_b15, b2: ligne.tp_b2, b3: ligne.tp_b3 },
  };
}

/**
 * Calcule le multiple de risque (R) d'une variante résolue.
 *
 * R vaut -1 pile au stop et +ratio pile à l'objectif construit avec ce
 * ratio (à un petit écart près dû aux coûts, déjà appliqués aux prix
 * stockés) — la même définition que la colonne `resultat_r` du backtest
 * (voir backtest/moteur.py), pour que les deux soient comparables sans
 * jamais passer par un montant en dollars ni une taille de position.
 *
 * @param {string} sens ``haussier`` ou ``baissier``.
 * @param {number} prixEntree Prix d'entrée de la mécanique.
 * @param {number} stop Stop de la mécanique.
 * @param {number} prixSortie Prix de sortie de la variante résolue.
 * @returns {number|null} Le multiple R, ou `null` si non calculable.
 */
export function calculerR(sens, prixEntree, stop, prixSortie) {
  const distanceRisque = sens === "haussier" ? prixEntree - stop : stop - prixEntree;
  if (!Number.isFinite(distanceRisque) || distanceRisque === 0) return null;
  if (!Number.isFinite(prixSortie)) return null;
  const mouvement = sens === "haussier" ? prixSortie - prixEntree : prixEntree - prixSortie;
  return mouvement / distanceRisque;
}

/**
 * Construit la charge JSON de la route /journal à partir des lignes sûres
 * du journal D1 (voir index.js pour la liste exacte des colonnes lues).
 *
 * @param {Array<object>} lignes Lignes de la table `journal`.
 * @param {number|null} derniereExecutionMs Horodatage de la dernière exécution du moteur.
 * @returns {object} Charge JSON, triée du signal le plus récent au plus ancien.
 */
export function construireReponseJournal(lignes, derniereExecutionMs) {
  const signaux = lignes.map((ligne) => {
    const entree = entreeDepuisLigne(ligne);
    const { texte: texteDetection, infractions: infractionsDetection } = rendreEvenementPublic(entree);

    const variantes = {};
    for (const variante of VARIANTES) {
      const statut = ligne[`statut_${variante}`];
      const prixSortie = ligne[`prix_sortie_${variante}`];
      const horodatageResolution = ligne[`horodatage_resolution_${variante}`];
      const resolue = statut === "gagnant" || statut === "perdant";

      let texteResolution = null;
      if (resolue) {
        const rendu = rendreEvenementPublic({
          type: "resolution", variante, statut, prixSortie, horodatageResolution,
        });
        texteResolution = rendu.infractions.length === 0 ? rendu.texte : null;
      }

      variantes[variante] = {
        objectif: entree.objectifs[variante],
        statut,
        prix_sortie: resolue ? prixSortie : null,
        r: resolue ? calculerR(entree.sens, entree.prixEntree, entree.stop, prixSortie) : null,
        horodatage_resolution_utc: horodatageResolution ? new Date(horodatageResolution).toISOString() : null,
        texte: texteResolution,
      };
    }

    return {
      id: entree.id,
      horodatage_detection_utc: new Date(entree.horodatageDetection).toISOString(),
      timeframe_ob: entree.timeframeOb,
      ob_haut: entree.obHaut,
      ob_bas: entree.obBas,
      sens: entree.sens,
      timeframe_fvg: entree.timeframeFvg,
      prix_entree: entree.prixEntree,
      stop: entree.stop,
      horodatage_resolution_utc: ligne.horodatage_resolution
        ? new Date(ligne.horodatage_resolution).toISOString()
        : null,
      texte_detection: infractionsDetection.length === 0 ? texteDetection : null,
      variantes,
    };
  });

  return {
    meta: {
      horodatage_generation_utc: new Date().toISOString(),
      derniere_execution_utc: derniereExecutionMs ? new Date(derniereExecutionMs).toISOString() : null,
      n_signaux: signaux.length,
    },
    signaux,
  };
}
