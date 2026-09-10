/**
 * Résilience : une panne d'API ne doit jamais corrompre l'état persisté.
 *
 * Une exécution manquée ou en échec doit juste être rattrapée à la
 * suivante, pas casser le journal ni la fenêtre de bougies déjà connue.
 * `global.fetch` est remplacé le temps du test, comme dans
 * worker/tests/worker.test.js — même convention que le Worker existant du
 * projet.
 *
 * Exécution :
 *     node --test worker-scanner/tests/
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { creerD1Test } from "./d1_test_adapter.js";
import { traiterExecution } from "../src/index.js";
import { chargerEtat, insererEntree, sauvegarderEtat } from "../src/journal.js";
import { etatInitial } from "../src/moteur.js";

const SCHEMA = readFileSync(new URL("../schema.sql", import.meta.url), "utf8");

/** Sauvegarde et restaure global.fetch, pour ne pas polluer les autres tests. */
function avecFetchFactice(implementation, corps) {
  const original = globalThis.fetch;
  globalThis.fetch = implementation;
  return corps().finally(() => {
    globalThis.fetch = original;
  });
}

test("une panne réseau totale de Kraken laisse l'état persisté intact", async () => {
  const db = creerD1Test(SCHEMA);
  const etatDepart = {
    ...etatInitial(),
    bougiesM1: [{ t: 1_800_000_000_000, ouverture: 1, haut: 1.1, bas: 0.9, cloture: 1.0, volume: 1 }],
    derniereBougieTraiteeT: 1_800_000_000_000,
  };
  await sauvegarderEtat(db, etatDepart);

  await avecFetchFactice(
    async () => { throw new Error("réseau indisponible"); },
    () => traiterExecution({ DB: db }),
  );

  const etatApres = await chargerEtat(db, etatInitial);
  assert.deepEqual(etatApres, etatDepart, "l'état ne doit pas bouger quand Binance est injoignable");

  const { results } = await db.prepare("SELECT * FROM journal").all();
  assert.equal(results.length, 0, "aucune ligne de journal ne doit apparaître sans nouvelle donnée");
});

test("une réponse Kraken en erreur HTTP (503) dégrade sans lever d'exception", async () => {
  const db = creerD1Test(SCHEMA);
  await sauvegarderEtat(db, { ...etatInitial(), bougiesM1: [] });

  await assert.doesNotReject(
    avecFetchFactice(
      async () => ({ ok: false, status: 503 }),
      () => traiterExecution({ DB: db }),
    ),
  );
});

test("une réponse Kraken illisible (JSON invalide) dégrade sans lever d'exception", async () => {
  const db = creerD1Test(SCHEMA);
  await sauvegarderEtat(db, { ...etatInitial(), bougiesM1: [] });

  await assert.doesNotReject(
    avecFetchFactice(
      async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError("Unexpected token"); } }),
      () => traiterExecution({ DB: db }),
    ),
  );
});

test("Kraken disponible mais Frankfurter en panne : les positions déjà ouvertes restent surveillées", async () => {
  const db = creerD1Test(SCHEMA);
  // Horodatages relatifs à maintenant : kraken.js écarte toute bougie dont
  // la clôture n'est pas encore passée, un horodatage figé dans le passé
  // finirait par tomber dans le futur du point de vue du test.
  const maintenant = Date.now();
  // 61 s (pas 60) : kraken.js calcule la clôture comme (t + 60 s), qui doit
  // rester strictement dans le passé pour que la bougie ne soit pas écartée
  // comme "encore en formation" — une marge d'une seconde absorbe l'arrondi
  // du Math.floor() ci-dessous.
  const debutBougie = maintenant - 61_000;

  // Position ouverte, achetée à 3000 $, stop 2994 $, objectif ratio 2 à 3012 $ :
  // aucune des deux n'a besoin du taux EUR/USD pour être détectée.
  const position = {
    id: "pos-1",
    horodatageDetection: debutBougie - 60_000,
    timeframeOb: "M15", obHaut: 2999, obBas: 2995, sens: "haussier", timeframeFvg: "M5",
    prixEntree: 3000.0, stop: 2994.0,
    objectifs: { a: null, b15: null, b2: 3012.0, b3: null },
    lots: 0.07,
    variantesResolues: { a: true, b15: true, b2: false, b3: true },
  };
  await sauvegarderEtat(db, {
    ...etatInitial(),
    positionOuverte: position,
    derniereBougieTraiteeT: debutBougie - 60_000,
    bougiesM1: [],
  });
  // La ligne de journal existe déjà, comme elle l'aurait été au moment de
  // la détection réelle de "pos-1" (avant la fenêtre de ce test).
  await insererEntree(db, position);

  // Format Kraken : [tempsSecondes, open, high, low, close, vwap, volume, nbTransactions].
  const bougieGagnante = [
    Math.floor(debutBougie / 1000), "3013.00", "3013.50", "3012.90", "3013.20", "3013.10", "1.0", 1,
  ];

  await avecFetchFactice(
    async (url) => {
      if (String(url).includes("kraken.com")) {
        return { ok: true, status: 200, json: async () => ({ error: [], result: { PAXGUSD: [bougieGagnante] } }) };
      }
      // Frankfurter en panne : ne doit pas empêcher la résolution de la position.
      throw new Error("Frankfurter indisponible");
    },
    () => traiterExecution({ DB: db }),
  );

  const etatApres = await chargerEtat(db, etatInitial);
  assert.ok(etatApres, "l'état doit rester lisible malgré la panne Frankfurter");

  const ligne = await db.prepare("SELECT * FROM journal WHERE id = ?").bind("pos-1").first();
  assert.equal(
    ligne.statut_b2, "gagnant",
    "la résolution d'une position déjà ouverte ne dépend pas du taux EUR/USD et doit s'appliquer malgré la panne Frankfurter",
  );
});
