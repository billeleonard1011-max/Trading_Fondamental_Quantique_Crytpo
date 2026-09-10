/**
 * Tests du journal D1 : état persisté et statuts de résolution.
 *
 * Utilise l'adaptateur D1 de test (d1_test_adapter.js), adossé à
 * node:sqlite — un vrai moteur SQL, pas une simulation.
 *
 * Exécution :
 *     node --test worker-scanner/tests/
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { creerD1Test } from "./d1_test_adapter.js";
import {
  appliquerResolution, chargerEtat, enregistrerTauxDuJour, insererEntree, sauvegarderEtat, tauxDuJour,
} from "../src/journal.js";
import { etatInitial } from "../src/moteur.js";

const SCHEMA = readFileSync(new URL("../schema.sql", import.meta.url), "utf8");

function entreeExemple(id = "entree-1") {
  return {
    id,
    horodatageDetection: 1_800_000_000_000,
    timeframeOb: "M15",
    obHaut: 2999.0,
    obBas: 2995.0,
    sens: "haussier",
    timeframeFvg: "M5",
    prixEntree: 3000.0,
    stop: 2994.0,
    objectifs: { a: 3010.0, b15: 3009.0, b2: 3012.0, b3: 3018.0 },
    lots: 0.07,
  };
}

test("chargerEtat renvoie un état initial quand aucune ligne n'existe encore", async () => {
  const db = creerD1Test(SCHEMA);
  const etat = await chargerEtat(db, etatInitial);
  assert.deepEqual(etat.orderBlocksActifs, []);
  assert.deepEqual(etat.setupsEnAttente, []);
  assert.equal(etat.positionOuverte, null);
  assert.deepEqual(etat.bougiesM1, []);
});

test("sauvegarderEtat puis chargerEtat restitue exactement l'état écrit", async () => {
  const db = creerD1Test(SCHEMA);
  const etat = {
    ...etatInitial(),
    orderBlocksActifs: [{ unite: "H1", sens: "haussier", haut: 3000, bas: 2995, mitige: false }],
    bougiesM1: [{ t: 1000, ouverture: 1, haut: 2, bas: 0.5, cloture: 1.5, volume: 10 }],
  };
  await sauvegarderEtat(db, etat);
  const relu = await chargerEtat(db, etatInitial);
  assert.deepEqual(relu, etat);
});

test("sauvegarderEtat écrase l'état précédent (une seule ligne persistée)", async () => {
  const db = creerD1Test(SCHEMA);
  await sauvegarderEtat(db, { ...etatInitial(), bougiesM1: [], derniereBougieTraiteeT: 1 });
  await sauvegarderEtat(db, { ...etatInitial(), bougiesM1: [], derniereBougieTraiteeT: 2 });
  const relu = await chargerEtat(db, etatInitial);
  assert.equal(relu.derniereBougieTraiteeT, 2);
});

test("le taux EUR/USD du jour est mis en cache et relu", async () => {
  const db = creerD1Test(SCHEMA);
  assert.equal(await tauxDuJour(db, "2026-09-09"), null);
  await enregistrerTauxDuJour(db, "2026-09-09", 1.0842);
  assert.equal(await tauxDuJour(db, "2026-09-09"), 1.0842);
});

test("insererEntree écrit les quatre TP et initialise les statuts", async () => {
  const db = creerD1Test(SCHEMA);
  await insererEntree(db, entreeExemple());
  const ligne = await db.prepare("SELECT * FROM journal WHERE id = ?").bind("entree-1").first();
  assert.equal(ligne.statut_a, "ouvert");
  assert.equal(ligne.statut_b15, "ouvert");
  assert.equal(ligne.tp_b2, 3012.0);
  assert.equal(ligne.prix_entree, 3000.0);
});

test("une variante sans objectif structurel est journalisée 'sans_objectif', pas 'ouvert'", async () => {
  const db = creerD1Test(SCHEMA);
  const entree = entreeExemple("entree-sans-objectif");
  entree.objectifs.a = null;
  await insererEntree(db, entree);
  const ligne = await db.prepare("SELECT * FROM journal WHERE id = ?").bind("entree-sans-objectif").first();
  assert.equal(ligne.statut_a, "sans_objectif");
  assert.equal(ligne.tp_a, null);
  assert.equal(ligne.statut_b15, "ouvert");
});

test("appliquerResolution met à jour la variante concernée et pose l'horodatage global au dernier statut", async () => {
  const db = creerD1Test(SCHEMA);
  await insererEntree(db, entreeExemple("entree-2"));

  await appliquerResolution(db, {
    id: "entree-2", variante: "a", statut: "gagnant", prixSortie: 3010.0, resultatUsd: 70.0, horodatageResolution: 1000,
  });
  let ligne = await db.prepare("SELECT * FROM journal WHERE id = ?").bind("entree-2").first();
  assert.equal(ligne.statut_a, "gagnant");
  assert.equal(ligne.resultat_a_usd, 70.0);
  assert.equal(ligne.prix_sortie_a, 3010.0, "le prix de sortie est persisté séparément du résultat en dollars");
  assert.equal(ligne.horodatage_resolution, null, "il reste des variantes ouvertes : pas de clôture globale");

  for (const variante of ["b15", "b2"]) {
    await appliquerResolution(db, {
      id: "entree-2", variante, statut: "perdant", prixSortie: 2994.0, resultatUsd: -42.0, horodatageResolution: 2000,
    });
  }
  await appliquerResolution(db, {
    id: "entree-2", variante: "b3", statut: "gagnant", prixSortie: 3018.0, resultatUsd: 126.0, horodatageResolution: 3000,
  });
  ligne = await db.prepare("SELECT * FROM journal WHERE id = ?").bind("entree-2").first();
  assert.equal(ligne.statut_b3, "gagnant");
  assert.equal(ligne.horodatage_resolution, 3000, "posé une fois la dernière variante résolue");
});

test("une entrée résolue reste résolue : appliquer la même résolution deux fois ne change rien", async () => {
  const db = creerD1Test(SCHEMA);
  await insererEntree(db, entreeExemple("entree-3"));

  const resolution = {
    id: "entree-3", variante: "a", statut: "gagnant", prixSortie: 3010.0, resultatUsd: 70.0, horodatageResolution: 1000,
  };
  await appliquerResolution(db, resolution);

  // Rejouer la même résolution avec un résultat différent (simulerait un
  // bug amont) : le statut déjà posé doit rester intact, jamais réécrit.
  await appliquerResolution(db, { ...resolution, resultatUsd: 999.0, horodatageResolution: 5000 });

  const ligne = await db.prepare("SELECT * FROM journal WHERE id = ?").bind("entree-3").first();
  assert.equal(ligne.resultat_a_usd, 70.0, "la deuxième application n'a pas dû modifier le résultat déjà posé");
  assert.equal(ligne.horodatage_resolution_a, 1000);
});

test("insererEntree est idempotent : la même entrée envoyée deux fois ne duplique pas la ligne", async () => {
  const db = creerD1Test(SCHEMA);
  await insererEntree(db, entreeExemple("entree-4"));
  await insererEntree(db, entreeExemple("entree-4"));
  const { results } = await db.prepare("SELECT * FROM journal WHERE id = ?").bind("entree-4").all();
  assert.equal(results.length, 1);
});
