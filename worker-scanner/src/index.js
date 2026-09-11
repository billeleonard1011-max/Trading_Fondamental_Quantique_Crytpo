/**
 * Scanner en direct : applique la mécanique du backtest sur PAXG/USD,
 * minute par minute, et journalise chaque signal détecté.
 *
 * Ce Worker ne recommande jamais de prendre un trade — voir README.md et
 * src/recommandation.js. Il mesure la mécanique, il ne conseille rien.
 *
 * Déclenchement : Cron Trigger toutes les minutes (voir wrangler.toml).
 * Une exécution manquée ou en échec ne doit jamais corrompre l'état
 * persisté : voir la gestion des pannes ci-dessous et
 * tests/resilience.test.js.
 */

import { recupererBougiesRecentes } from "./kraken.js";
import { jourUtc, recupererTauxEurusd } from "./taux.js";
import { etatInitial, traiterNouvellesBougies } from "./moteur.js";
import { configExecutionDefaut } from "./execution.js";
import { chargerEtat, sauvegarderEtat, tauxDuJour, enregistrerTauxDuJour, appliquerEvenements } from "./journal.js";
import { rendreEvenement } from "./alertes.js";
import { construireReponseJournal } from "./api.js";

/**
 * Colonnes lues par la route /journal. Choisies explicitement — plutôt que
 * `SELECT *` suivi d'un tri — pour que `lots` et `resultat_*_usd` (donnée
 * de position et de compte) ne soient jamais chargés en mémoire ici, pas
 * seulement omis à l'affichage. Voir src/api.js et le commentaire de
 * schema.sql sur `prix_sortie_*`.
 */
const COLONNES_JOURNAL_PUBLIQUES = [
  "id", "horodatage_detection", "timeframe_ob", "ob_haut", "ob_bas", "sens",
  "timeframe_fvg", "fvg_haut", "fvg_bas",
  "prix_entree", "sl", "tp_a", "tp_b15", "tp_b2", "tp_b3", "tp_c",
  "statut_a", "statut_b15", "statut_b2", "statut_b3", "statut_c",
  "prix_sortie_a", "prix_sortie_b15", "prix_sortie_b2", "prix_sortie_b3", "prix_sortie_c",
  "horodatage_resolution_a", "horodatage_resolution_b15", "horodatage_resolution_b2",
  "horodatage_resolution_b3", "horodatage_resolution_c",
  "horodatage_resolution",
  // Setup sweep : ce que l'alerte doit dire (niveau balayé, sa formation).
  "setup", "niveau_prix", "niveau_cote", "niveau_unite", "niveau_formation", "sweep_extreme",
  "reference_prix", "unite_fibo",
  "tp_s1", "tp_s2", "tp_s3", "statut_s1", "statut_s2", "statut_s3",
  "prix_sortie_s1", "prix_sortie_s2", "prix_sortie_s3",
  "horodatage_resolution_s1", "horodatage_resolution_s2", "horodatage_resolution_s3",
].join(", ");

/** Nombre maximal de signaux renvoyés par la route /journal. */
const LIMITE_JOURNAL = 2000;

/**
 * En-têtes de partage entre origines pour la route /journal.
 *
 * Même convention que worker/src/index.js (l'assistant) : l'origine
 * autorisée vient d'une variable d'environnement, jamais codée en dur, pour
 * que le même Worker reste utilisable en local (Pages de prévisualisation,
 * `wrangler dev`) sans modifier le code.
 *
 * @param {string} origineAutorisee Origine autorisée, ou ``*`` si absente.
 * @returns {object} En-têtes CORS.
 */
function entetesCors(origineAutorisee) {
  return {
    "Access-Control-Allow-Origin": origineAutorisee || "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
  };
}

/**
 * Gère la route /journal : lit le journal D1 (colonnes publiques
 * uniquement) et l'état du moteur, et renvoie la charge JSON pour l'onglet
 * Trading du site.
 *
 * @param {object} env Liaisons du Worker (D1 notamment).
 * @param {object} cors En-têtes CORS à joindre à la réponse.
 * @returns {Promise<Response>} Réponse JSON.
 */
async function repondreJournal(env, cors) {
  const [{ results: lignes }, etatLigne, { results: lignesPaliers }] = await Promise.all([
    env.DB.prepare(
      `SELECT ${COLONNES_JOURNAL_PUBLIQUES} FROM journal ORDER BY horodatage_detection DESC LIMIT ?`,
    ).bind(LIMITE_JOURNAL).all(),
    env.DB.prepare("SELECT mise_a_jour FROM etat_moteur WHERE id = 1").first(),
    // Détail des tranches. `resultat_usd` n'est pas sélectionné : le site
    // exprime la performance en multiple de risque, jamais en dollars.
    env.DB.prepare(
      `SELECT id_signal, rang, zone, origine, fraction, ratio_risque, statut,
              prix_sortie, motif_sortie, horodatage_resolution, variante
       FROM paliers ORDER BY id_signal, rang`,
    ).all(),
  ]);

  const charge = construireReponseJournal(
    lignes, etatLigne ? etatLigne.mise_a_jour : null, lignesPaliers,
  );
  return new Response(JSON.stringify(charge), {
    status: 200,
    headers: { "Content-Type": "application/json; charset=utf-8", ...cors },
  });
}

/**
 * Fenêtre glissante de bougies M1 conservées, en minutes.
 *
 * Dimensionnée sur le pire cas du moteur : PROFONDEUR_JAMBE (50 bougies sur
 * l'unité de l'order block, jusqu'à 50 heures pour un OB en H1) et les
 * extrêmes de session (jusqu'à 48 heures de recul). Quatre jours laissent
 * une marge confortable pour les deux, y compris après une exécution
 * manquée qui aurait besoin de rattraper plusieurs bougies d'un coup.
 */
const FENETRE_MINUTES = 4 * 24 * 60;

/**
 * Point d'entrée du Cron Trigger.
 *
 * @param {ScheduledController} controleur Fourni par le runtime Workers.
 * @param {object} env Liaisons du Worker (D1 notamment).
 * @param {ExecutionContext} ctx Contexte d'exécution.
 * @returns {Promise<void>}
 */
async function traiterExecution(env) {
  const db = env.DB;

  // 1. Bougies récentes. Sans elles, rien à faire cette minute : l'état
  // persisté n'est pas touché, la prochaine exécution rattrapera le
  // terrain perdu (les bougies manquées seront comprises dans le prochain
  // lot récupéré, jusqu'à la limite de l'historique renvoyé par Kraken).
  const { disponible: krakenOk, bougies: nouvellesBougies, motif: motifKraken } =
    await recupererBougiesRecentes(10);
  if (!krakenOk) {
    console.error(`Kraken indisponible : ${motifKraken}. Exécution passée, état inchangé.`);
    return;
  }
  if (nouvellesBougies.length === 0) {
    console.log("Aucune bougie close nouvelle depuis le dernier appel — rien à traiter.");
    return;
  }

  // 2. État persisté (fenêtre de bougies incluse), fusion avec les nouvelles.
  const etatComplet = await chargerEtat(db, etatInitial);
  const bougiesConnues = new Map((etatComplet.bougiesM1 || []).map((b) => [b.t, b]));
  for (const bougie of nouvellesBougies) bougiesConnues.set(bougie.t, bougie);
  const fenetre = Array.from(bougiesConnues.values())
    .sort((a, b) => a.t - b.t)
    .slice(-FENETRE_MINUTES);

  // 3. Taux EUR/USD du jour, en cache D1, un seul appel Frankfurter par jour.
  const jour = jourUtc(Date.now());
  let taux = await tauxDuJour(db, jour);
  if (taux === null) {
    const resultatTaux = await recupererTauxEurusd();
    if (resultatTaux.disponible) {
      taux = resultatTaux.taux;
      await enregistrerTauxDuJour(db, jour, taux);
    } else {
      console.error(`Taux EUR/USD indisponible : ${resultatTaux.motif}. Les nouvelles entrées seront écartées ce tour-ci (taille non prenable) ; les positions déjà ouvertes continuent d'être surveillées.`);
    }
  }

  // 4. Le moteur, sur l'état persisté (hors fenêtre de bougies, qui n'est
  // pas un champ de moteur.js mais un ajout de la couche de persistance).
  const { orderBlocksActifs, setupsEnAttente, positionOuverte, derniereBougieTraiteeT } = etatComplet;
  const { etat: nouvelEtat, evenements } = traiterNouvellesBougies(
    { orderBlocksActifs, setupsEnAttente, positionOuverte, derniereBougieTraiteeT },
    fenetre,
    configExecutionDefaut(),
    taux,
  );

  // 5. Journalisation. Les infractions au garde-fou anti-recommandation ne
  // doivent jamais bloquer l'écriture des chiffres eux-mêmes (le journal
  // reste correct), seulement la publication d'un texte défaillant.
  for (const evenement of evenements) {
    const { texte, infractions } = rendreEvenement(evenement);
    if (infractions.length > 0) {
      console.error(
        `Texte d'alerte refusé (formulation de recommandation détectée) pour l'événement ${evenement.type} ${evenement.id} :`,
        infractions,
      );
    } else {
      console.log(texte);
    }
  }
  await appliquerEvenements(db, evenements);

  // 6. État persisté, fenêtre de bougies comprise.
  await sauvegarderEtat(db, { ...nouvelEtat, bougiesM1: fenetre });

  console.log(
    `Exécution terminée : ${nouvellesBougies.length} nouvelle(s) bougie(s), ` +
      `${evenements.filter((e) => e.type === "entree").length} entrée(s), ` +
      `${evenements.filter((e) => e.type === "resolution").length} résolution(s).`,
  );
}

export default {
  async scheduled(controleur, env, ctx) {
    ctx.waitUntil(traiterExecution(env));
  },

  // Le déclenchement normal se fait par Cron Trigger ; ce gestionnaire fetch
  // sert à la vérification manuelle (/declencher-manuellement) et à la
  // route consommée par l'onglet Trading du site (/journal, partie 4).
  async fetch(request, env, ctx) {
    const cors = entetesCors(env.ORIGINE_AUTORISEE);
    const chemin = new URL(request.url).pathname;

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }
    if (chemin === "/journal") {
      return repondreJournal(env, cors);
    }
    if (chemin === "/declencher-manuellement") {
      await traiterExecution(env);
      return new Response("Exécution manuelle terminée. Voir les journaux Cloudflare.", { status: 200 });
    }
    return new Response("Scanner en direct : voir /declencher-manuellement ou /journal.", { status: 200 });
  },
};

export { traiterExecution };
