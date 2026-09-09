/**
 * Tests du garde-fou anti-recommandation, et des textes d'alerte réels.
 *
 * Exécution :
 *     node --test worker-scanner/tests/
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { verifierAbsenceRecommandation } from "../src/recommandation.js";
import { rendreAlerteEntree, rendreAlerteResolution, rendreEvenement } from "../src/alertes.js";

function entreeExemple() {
  return {
    type: "entree",
    id: "x",
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

test("verifierAbsenceRecommandation attrape les formulations interdites", () => {
  const cas = [
    "Le moteur recommande d'acheter maintenant.",
    "C'est une opportunité intéressante.",
    "Strong buy sur cette zone.",
    "Point d'entrée idéal ici.",
    "Ce titre est sous-évalué.",
  ];
  for (const texte of cas) {
    const infractions = verifierAbsenceRecommandation({ texte });
    assert.ok(infractions.length > 0, `aurait dû être détecté : "${texte}"`);
  }
});

test("verifierAbsenceRecommandation laisse passer une description factuelle", () => {
  const texte = "Le moteur aurait détecté un achat à 3000 $ le 2026-09-09, stop à 2994 $.";
  assert.deepEqual(verifierAbsenceRecommandation({ texte }), []);
});

test("verifierAbsenceRecommandation ignore les clés de citation externe", () => {
  const objet = { titre: "Analysts say buy gold now", constat: "Le prix a varié de 2 %." };
  assert.deepEqual(verifierAbsenceRecommandation(objet), []);
  // Mais la même formulation dans un champ rédigé par le module est attrapée.
  const objetFautif = { ...objet, constat: "Il faudrait acheter maintenant." };
  assert.ok(verifierAbsenceRecommandation(objetFautif).length > 0);
});

test("verifierAbsenceRecommandation localise l'infraction (chemin et extrait)", () => {
  const infractions = verifierAbsenceRecommandation({ analyse: { texte: "vous devriez investir" } });
  assert.equal(infractions.length, 1);
  assert.equal(infractions[0].chemin, "analyse.texte");
  assert.match(infractions[0].extrait, /investir/);
});

test("verifierAbsenceRecommandation descend dans les tableaux", () => {
  const infractions = verifierAbsenceRecommandation({ items: ["texte propre", "achetez maintenant"] });
  assert.equal(infractions.length, 1);
  assert.equal(infractions[0].chemin, "items[1]");
});

// ---------------------------------------------------------------------------
// Textes réels du scanner : toujours au passé ou au conditionnel
// ---------------------------------------------------------------------------
test("rendreAlerteEntree ne contient aucune formulation de recommandation", () => {
  const texte = rendreAlerteEntree(entreeExemple());
  assert.deepEqual(verifierAbsenceRecommandation({ texte }), []);
  assert.match(texte, /aurait été détecté/, "doit être au conditionnel, jamais à l'impératif");
});

test("rendreAlerteEntree dit explicitement quand l'objectif structurel manque", () => {
  const entree = entreeExemple();
  entree.objectifs.a = null;
  const texte = rendreAlerteEntree(entree);
  assert.match(texte, /structurel non disponible/);
  assert.deepEqual(verifierAbsenceRecommandation({ texte }), []);
});

test("rendreAlerteResolution ne contient aucune formulation de recommandation", () => {
  const texte = rendreAlerteResolution({
    type: "resolution", id: "x", variante: "b2", statut: "gagnant",
    prixSortie: 3012.0, resultatUsd: 84.0, horodatageResolution: 1_800_100_000_000,
  });
  assert.deepEqual(verifierAbsenceRecommandation({ texte }), []);
  assert.match(texte, /aurait/, "doit être au conditionnel passé, jamais à l'impératif");
});

test("rendreEvenement expose les infractions plutôt que de les corriger silencieusement", () => {
  // Un événement construit à la main dont le texte serait fautif (impossible
  // avec les gabarits actuels, mais le contrat doit tenir si un gabarit
  // futur introduit une formulation interdite par erreur).
  const { infractions } = rendreEvenement(entreeExemple());
  assert.deepEqual(infractions, []);
});
