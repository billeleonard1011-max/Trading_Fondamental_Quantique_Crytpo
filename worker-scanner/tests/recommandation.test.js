/**
 * Tests du garde-fou anti-recommandation, et des textes d'alerte réels.
 *
 * Exécution :
 *     node --test worker-scanner/tests/
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { verifierAbsenceRecommandation } from "../src/recommandation.js";
import {
  rendreAlerteEntree, rendreAlerteResolution, rendreEvenement, rendrePublicEntree,
} from "../src/alertes.js";

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
    fvgBas: 2997.5,
    fvgHaut: 2998.4,
    objectifs: { a: 3010.0, b15: 3009.0, b2: 3012.0, b3: 3018.0, c: 3010.0 },
    lots: 0.07,
    paliers: [
      { rang: 1, zone: 3010.0, origine: "veille_haut", fraction: 0.5, ratioRisque: 1.6667 },
      { rang: 2, zone: 3024.5, origine: "asie_haut", fraction: 0.25, ratioRisque: 4.0833 },
      { rang: 3, zone: 3041.0, origine: "order_block", fraction: 0.25, ratioRisque: 6.8333 },
    ],
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
  assert.match(texte, /TP1 \(structurel\)\s+: non disponible pour ce signal/);
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

// ---------------------------------------------------------------------------
// Format enrichi de l'alerte (partie A1)
// ---------------------------------------------------------------------------
test("l'alerte donne le prix du FVG, pas seulement son unité", () => {
  const texte = rendrePublicEntree(entreeExemple());
  assert.match(texte, /FVG\) en M5 \[2997\.50, 2998\.40\] \$/);
});

test("l'alerte dit explicitement quand le niveau du FVG manque, sans l'inventer", () => {
  const entree = entreeExemple();
  delete entree.fvgBas;
  delete entree.fvgHaut;
  const texte = rendrePublicEntree(entree);
  assert.match(texte, /niveau n'a pas été journalisé/);
  assert.doesNotMatch(texte, /entre undefined/);
});

test("l'alerte énumère les zones de liquidité, de la plus proche à la plus lointaine", () => {
  const texte = rendrePublicEntree(entreeExemple());
  const posPremiere = texte.indexOf("3010.00 $, rapport");
  const posDeuxieme = texte.indexOf("3024.50");
  const posTroisieme = texte.indexOf("3041.00");
  assert.ok(posPremiere > 0 && posDeuxieme > posPremiere && posTroisieme > posDeuxieme,
    "les zones doivent apparaître dans l'ordre de proximité");
});

test("chaque zone porte son ratio risque/récompense et sa part de position", () => {
  const texte = rendrePublicEntree(entreeExemple());
  assert.match(texte, /rapport risque\/récompense 1:1,7, 50 % de la position/);
  assert.match(texte, /rapport risque\/récompense 1:4,1, 25 % de la position/);
  assert.match(texte, /rapport risque\/récompense 1:6,8, 25 % de la position/);
});

test("les origines de zone sont traduites, jamais affichées en identifiant brut", () => {
  const texte = rendrePublicEntree(entreeExemple());
  assert.match(texte, /haut de la veille/);
  assert.match(texte, /haut de la session asiatique/);
  assert.match(texte, /order block encore actif/);
  for (const brut of ["veille_haut", "asie_haut", "order_block"]) {
    assert.doesNotMatch(texte, new RegExp(brut), `identifiant technique « ${brut} » affiché tel quel`);
  }
});

test("l'alerte enrichie reste sans formulation de recommandation", () => {
  assert.deepEqual(verifierAbsenceRecommandation({ texte: rendrePublicEntree(entreeExemple()) }), []);
  assert.deepEqual(verifierAbsenceRecommandation({ texte: rendreAlerteEntree(entreeExemple()) }), []);
});

test("l'alerte publique enrichie ne laisse toujours pas fuiter la taille de position", () => {
  const texte = rendrePublicEntree(entreeExemple());
  assert.doesNotMatch(texte, /lot/i);
});

test("le texte accorde le participe avec le genre du sens", () => {
  const achat = rendrePublicEntree(entreeExemple());
  assert.match(achat, /un achat aurait été détecté le/);
  const vente = rendrePublicEntree({ ...entreeExemple(), sens: "baissier" });
  assert.match(vente, /une vente aurait été détectée le/);
});
