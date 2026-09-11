/**
 * Persistance D1 : état du moteur (une ligne) et journal des trades.
 *
 * D1 a été retenu pour les deux plutôt que KV (voir l'étude de faisabilité,
 * partie 1) : le scanner tourne chaque minute et écrit son état à chaque
 * exécution, soit 1440 écritures par jour — au-delà des 1000 écritures
 * quotidiennes du plan gratuit de KV, largement dans les 100 000 de D1.
 *
 * L'état du moteur (zones actives, setups en attente, position ouverte, et
 * la fenêtre glissante de bougies M1) tient dans une seule ligne, en JSON :
 * ce n'est pas une donnée qu'on interroge en SQL, juste un état qu'on
 * relit et réécrit intégralement à chaque tick. Le journal, lui, est une
 * vraie table : c'est lui que l'onglet Trading du site interrogera
 * (agrégations mensuelles, par variante, par heure).
 */

const ID_ETAT = 1;

/** Statuts possibles d'une variante. Le quatrième, `sans_objectif`, est une
 * extension documentée du prompt (qui n'en prévoyait que trois) : la
 * variante structurelle (A) peut n'avoir aucun niveau de liquidité devant
 * le prix au moment de l'entrée. Plutôt que d'inventer un objectif ou de
 * taire l'entrée, ce statut le dit explicitement, et n'apparaît que si
 * l'entrée elle-même a bien eu lieu. */
export const STATUTS = Object.freeze(["ouvert", "gagnant", "perdant", "sans_objectif"]);

/**
 * Charge l'état persisté du moteur, ou un état initial si première exécution.
 *
 * @param {D1Database} db Base D1 liée au Worker.
 * @param {object} etatInitial État à renvoyer si aucune ligne n'existe encore.
 * @returns {Promise<object>} L'état du moteur (voir moteur.js::etatInitial()).
 */
export async function chargerEtat(db, etatInitial) {
  const ligne = await db.prepare("SELECT donnees FROM etat_moteur WHERE id = ?").bind(ID_ETAT).first();
  if (!ligne) return { ...etatInitial(), bougiesM1: [] };
  try {
    return JSON.parse(ligne.donnees);
  } catch {
    // État corrompu : on redémarre à neuf plutôt que de planter. Les
    // prochaines exécutions redétecteront les zones et setups en cours à
    // partir des bougies fraîchement récupérées — dégradation, pas panne.
    return { ...etatInitial(), bougiesM1: [] };
  }
}

/**
 * Sauvegarde l'état du moteur, y compris la fenêtre glissante de bougies.
 *
 * @param {D1Database} db Base D1.
 * @param {object} etat État complet, avec `bougiesM1` inclus.
 * @returns {Promise<void>}
 */
export async function sauvegarderEtat(db, etat) {
  const donnees = JSON.stringify(etat);
  await db
    .prepare(
      "INSERT INTO etat_moteur (id, donnees, mise_a_jour) VALUES (?, ?, ?) " +
        "ON CONFLICT(id) DO UPDATE SET donnees = excluded.donnees, mise_a_jour = excluded.mise_a_jour",
    )
    .bind(ID_ETAT, donnees, Date.now())
    .run();
}

/**
 * Récupère le taux EUR/USD déjà connu pour un jour donné.
 *
 * @param {D1Database} db Base D1.
 * @param {string} jour Date AAAA-MM-JJ (UTC).
 * @returns {Promise<number|null>} Le taux, ou `null` si non encore récupéré.
 */
export async function tauxDuJour(db, jour) {
  const ligne = await db.prepare("SELECT taux FROM taux_eurusd WHERE jour = ?").bind(jour).first();
  return ligne ? Number(ligne.taux) : null;
}

/**
 * Enregistre le taux EUR/USD d'un jour.
 *
 * @param {D1Database} db Base D1.
 * @param {string} jour Date AAAA-MM-JJ (UTC).
 * @param {number} taux Taux EUR/USD.
 * @returns {Promise<void>}
 */
export async function enregistrerTauxDuJour(db, jour, taux) {
  await db
    .prepare(
      "INSERT INTO taux_eurusd (jour, taux, recupere_le) VALUES (?, ?, ?) " +
        "ON CONFLICT(jour) DO UPDATE SET taux = excluded.taux, recupere_le = excluded.recupere_le",
    )
    .bind(jour, taux, Date.now())
    .run();
}

/**
 * Insère une nouvelle entrée de journal à partir d'un événement d'entrée.
 *
 * @param {D1Database} db Base D1.
 * @param {object} entree Événement `{type: "entree", ...}` de moteur.js.
 * @returns {Promise<void>}
 */
export async function insererEntree(db, entree) {
  const objectifs = entree.objectifs || {};
  const objectif = (v) => (objectifs[v] === undefined ? null : objectifs[v]);
  const statutInitial = (v) => (objectif(v) === null ? "sans_objectif" : "ouvert");
  const setup = entree.setup || "order_block";
  await db
    .prepare(
      `INSERT INTO journal (
        id, horodatage_detection, timeframe_ob, ob_haut, ob_bas, sens, timeframe_fvg,
        fvg_haut, fvg_bas,
        prix_entree, sl, tp_a, tp_b15, tp_b2, tp_b3, tp_c,
        statut_a, statut_b15, statut_b2, statut_b3, statut_c, lots,
        setup, niveau_prix, niveau_cote, niveau_unite, niveau_formation, sweep_extreme,
        reference_prix, unite_fibo,
        tp_s1, tp_s2, tp_s3, statut_s1, statut_s2, statut_s3
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(id) DO NOTHING`,
    )
    .bind(
      entree.id, entree.horodatageDetection, entree.timeframeOb, entree.obHaut, entree.obBas,
      entree.sens, entree.timeframeFvg,
      entree.fvgHaut ?? null, entree.fvgBas ?? null,
      entree.prixEntree, entree.stop,
      objectif("a"), objectif("b15"), objectif("b2"), objectif("b3"), objectif("c"),
      statutInitial("a"), statutInitial("b15"), statutInitial("b2"), statutInitial("b3"),
      statutInitial("c"),
      entree.lots,
      setup, entree.niveauPrix ?? null, entree.niveauCote ?? null, entree.niveauUnite ?? null,
      entree.niveauFormation ?? null, entree.sweepExtreme ?? null,
      entree.referencePrix ?? null, entree.uniteFibo ?? null,
      objectif("s1"), objectif("s2"), objectif("s3"),
      statutInitial("s1"), statutInitial("s2"), statutInitial("s3"),
    )
    .run();

  // Détail des tranches (variante C d'un order block, variante s2 d'un
  // sweep). Idempotent comme l'entrée elle-même : rejouer une exécution ne
  // duplique pas les paliers.
  const variantePaliers = setup === "sweep" ? "s2" : "c";
  for (const palier of entree.paliers || []) {
    await db
      .prepare(
        `INSERT INTO paliers (
          id_signal, rang, zone, origine, fraction, ratio_risque, statut, variante
        ) VALUES (?,?,?,?,?,?,'ouvert',?)
        ON CONFLICT(id_signal, rang) DO NOTHING`,
      )
      .bind(entree.id, palier.rang, palier.zone, palier.origine, palier.fraction, palier.ratioRisque, variantePaliers)
      .run();
  }
}

/**
 * Applique la résolution d'une tranche de la variante à paliers.
 *
 * Idempotent par la même clause que {@link appliquerResolution} : une
 * tranche déjà dénouée n'est jamais réécrite.
 *
 * @param {D1Database} db Base D1.
 * @param {object} evenement Événement `{type: "resolution_palier", ...}`.
 * @returns {Promise<void>}
 */
export async function appliquerResolutionPalier(db, evenement) {
  await db
    .prepare(
      `UPDATE paliers SET statut = ?, prix_sortie = ?, resultat_usd = ?,
         motif_sortie = ?, horodatage_resolution = ?
       WHERE id_signal = ? AND rang = ? AND statut = 'ouvert'`,
    )
    .bind(
      evenement.statut, evenement.prixSortie, evenement.resultatUsd,
      evenement.motifSortie, evenement.horodatageResolution,
      evenement.id, evenement.rang,
    )
    .run();
}

/**
 * Applique la résolution d'une variante à une entrée déjà journalisée.
 *
 * Idempotent par construction : la clause `AND statut_<variante> = 'ouvert'`
 * fait qu'appliquer deux fois la même résolution (par exemple si une
 * exécution est rejouée après une panne partielle) ne change rien la
 * seconde fois — un trade résolu reste résolu, jamais réécrit.
 *
 * @param {D1Database} db Base D1.
 * @param {object} resolution Événement `{type: "resolution", ...}` de moteur.js.
 * @returns {Promise<void>}
 */
export async function appliquerResolution(db, resolution) {
  const colStatut = `statut_${resolution.variante}`;
  const colResultat = `resultat_${resolution.variante}_usd`;
  const colPrixSortie = `prix_sortie_${resolution.variante}`;
  const colHorodatage = `horodatage_resolution_${resolution.variante}`;

  await db
    .prepare(
      `UPDATE journal SET ${colStatut} = ?, ${colResultat} = ?, ${colPrixSortie} = ?, ${colHorodatage} = ? ` +
        `WHERE id = ? AND ${colStatut} = 'ouvert'`,
    )
    .bind(
      resolution.statut, resolution.resultatUsd, resolution.prixSortie,
      resolution.horodatageResolution, resolution.id,
    )
    .run();

  // Horodatage global de résolution : posé une fois que plus aucune
  // variante n'est "ouvert" (les variantes "sans_objectif" ne bloquent
  // jamais cette clôture, puisqu'elles n'ont jamais été à surveiller).
  await db
    .prepare(
      `UPDATE journal SET horodatage_resolution = ? ` +
        `WHERE id = ? AND horodatage_resolution IS NULL ` +
        `AND statut_a != 'ouvert' AND statut_b15 != 'ouvert' AND statut_b2 != 'ouvert' ` +
        `AND statut_b3 != 'ouvert' AND statut_c != 'ouvert' ` +
        `AND statut_s1 != 'ouvert' AND statut_s2 != 'ouvert' AND statut_s3 != 'ouvert'`,
    )
    .bind(resolution.horodatageResolution, resolution.id)
    .run();
}

/**
 * Applique une liste d'événements (entrées et résolutions) au journal, en
 * un seul aller-retour D1 quand c'est possible.
 *
 * @param {D1Database} db Base D1.
 * @param {Array<object>} evenements Événements produits par moteur.js.
 * @returns {Promise<void>}
 */
export async function appliquerEvenements(db, evenements) {
  for (const evenement of evenements) {
    if (evenement.type === "entree") {
      await insererEntree(db, evenement);
    } else if (evenement.type === "resolution_palier") {
      await appliquerResolutionPalier(db, evenement);
    } else if (evenement.type === "resolution") {
      await appliquerResolution(db, evenement);
    }
  }
}
