/**
 * Tests de la route publique /journal : ni `lots` ni `resultat_*_usd` ne
 * doivent jamais y transiter (donnée de position et de compte, voir
 * site/js/rendu.js::MOTIFS_INTERDITS côté site), et les textes produits
 * doivent rester sans formulation de recommandation.
 *
 * Exécution :
 *     node --test worker-scanner/tests/
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { calculerR, construireReponseJournal } from "../src/api.js";
import { verifierAbsenceRecommandation } from "../src/recommandation.js";

function ligneExemple(overrides = {}) {
  return {
    id: "sig-1",
    horodatage_detection: 1_800_000_000_000,
    timeframe_ob: "M15",
    ob_haut: 2999.0,
    ob_bas: 2995.0,
    sens: "haussier",
    timeframe_fvg: "M5",
    prix_entree: 3000.0,
    sl: 2994.0,
    tp_a: 3010.0,
    tp_b15: 3009.0,
    tp_b2: 3012.0,
    tp_b3: 3018.0,
    statut_a: "ouvert",
    statut_b15: "ouvert",
    statut_b2: "ouvert",
    statut_b3: "ouvert",
    prix_sortie_a: null,
    prix_sortie_b15: null,
    prix_sortie_b2: null,
    prix_sortie_b3: null,
    horodatage_resolution_a: null,
    horodatage_resolution_b15: null,
    horodatage_resolution_b2: null,
    horodatage_resolution_b3: null,
    horodatage_resolution: null,
    ...overrides,
  };
}

test("calculerR vaut -1 pile au stop et +ratio pile à l'objectif, pour un achat", () => {
  assert.equal(calculerR("haussier", 3000, 2994, 2994), -1);
  assert.equal(calculerR("haussier", 3000, 2994, 3012), 2);
});

test("calculerR vaut -1 pile au stop et +ratio pile à l'objectif, pour une vente", () => {
  assert.equal(calculerR("baissier", 3000, 3006, 3006), -1);
  assert.equal(calculerR("baissier", 3000, 3006, 2988), 2);
});

test("calculerR renvoie null quand le stop est confondu avec l'entrée", () => {
  assert.equal(calculerR("haussier", 3000, 3000, 2994), null);
});

test("construireReponseJournal ne fait jamais apparaître 'lots' ni de résultat en dollars", () => {
  const ligne = ligneExemple({
    statut_b2: "gagnant", prix_sortie_b2: 3012.0, horodatage_resolution_b2: 1_800_100_000_000,
  });
  const reponse = construireReponseJournal([ligne], 1_800_200_000_000);
  const texteComplet = JSON.stringify(reponse);

  assert.doesNotMatch(texteComplet, /"lots"/);
  assert.doesNotMatch(texteComplet, /resultat_.*_usd/);
  assert.doesNotMatch(texteComplet, /[$]\d/, "aucun montant en dollars, seuls des prix de marché");
});

test("construireReponseJournal calcule le R de la variante résolue et laisse les autres à null", () => {
  const ligne = ligneExemple({
    statut_b2: "gagnant", prix_sortie_b2: 3012.0, horodatage_resolution_b2: 1_800_100_000_000,
  });
  const reponse = construireReponseJournal([ligne], null);
  const [signal] = reponse.signaux;

  assert.equal(signal.variantes.b2.r, 2);
  assert.equal(signal.variantes.b2.statut, "gagnant");
  assert.equal(signal.variantes.a.r, null, "variante non résolue : pas de R");
  assert.equal(signal.variantes.a.prix_sortie, null);
});

test("construireReponseJournal expose des textes conformes au garde-fou anti-recommandation", () => {
  const ligne = ligneExemple({
    statut_b3: "perdant", prix_sortie_b3: 2994.0, horodatage_resolution_b3: 1_800_100_000_000,
  });
  const reponse = construireReponseJournal([ligne], null);
  const [signal] = reponse.signaux;

  assert.ok(signal.texte_detection, "un texte de détection doit être produit");
  assert.deepEqual(verifierAbsenceRecommandation({ texte: signal.texte_detection }), []);
  assert.match(signal.texte_detection, /aurait été détecté/);
  assert.doesNotMatch(signal.texte_detection, /lot/i, "le texte public ne mentionne pas la taille de position");

  assert.ok(signal.variantes.b3.texte);
  assert.deepEqual(verifierAbsenceRecommandation({ texte: signal.variantes.b3.texte }), []);
  assert.doesNotMatch(signal.variantes.b3.texte, /[$]\d/, "pas de montant en dollars dans le texte de résolution");
});

test("construireReponseJournal trie les métadonnées et compte les signaux", () => {
  const reponse = construireReponseJournal([ligneExemple(), ligneExemple({ id: "sig-2" })], 1_800_000_500_000);
  assert.equal(reponse.meta.n_signaux, 2);
  assert.equal(reponse.meta.derniere_execution_utc, new Date(1_800_000_500_000).toISOString());
});
