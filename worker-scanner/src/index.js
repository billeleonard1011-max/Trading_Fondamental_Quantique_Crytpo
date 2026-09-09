/**
 * Scanner en direct : applique la mécanique du backtest sur PAXGUSDT,
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

import { recupererBougiesRecentes } from "./binance.js";
import { jourUtc, recupererTauxEurusd } from "./taux.js";
import { etatInitial, traiterNouvellesBougies } from "./moteur.js";
import { configExecutionDefaut } from "./execution.js";
import { chargerEtat, sauvegarderEtat, tauxDuJour, enregistrerTauxDuJour, appliquerEvenements } from "./journal.js";
import { rendreEvenement } from "./alertes.js";

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
  // lot récupéré, jusqu'à la limite de l'historique demandé à Binance).
  const { disponible: binanceOk, bougies: nouvellesBougies, motif: motifBinance } =
    await recupererBougiesRecentes(10);
  if (!binanceOk) {
    console.error(`Binance indisponible : ${motifBinance}. Exécution passée, état inchangé.`);
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

  // Un gestionnaire fetch minimal : Cloudflare exige que le Worker en
  // déclare un, même s'il n'est pas destiné à être appelé directement (le
  // déclenchement se fait par Cron Trigger). Utile aussi pour vérifier
  // manuellement, via workflow_dispatch équivalent ou un simple curl, que
  // le Worker est bien déployé.
  async fetch(request, env, ctx) {
    if (new URL(request.url).pathname === "/declencher-manuellement") {
      await traiterExecution(env);
      return new Response("Exécution manuelle terminée. Voir les journaux Cloudflare.", { status: 200 });
    }
    return new Response("Scanner en direct : voir /declencher-manuellement pour un test manuel.", { status: 200 });
  },
};

export { traiterExecution };
