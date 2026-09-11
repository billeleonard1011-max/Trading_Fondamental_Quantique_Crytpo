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
    fvg_haut: 2998.4,
    fvg_bas: 2997.5,
    tp_c: 3010.0,
    statut_a: "ouvert",
    statut_b15: "ouvert",
    statut_b2: "ouvert",
    statut_b3: "ouvert",
    statut_c: "ouvert",
    prix_sortie_a: null,
    prix_sortie_b15: null,
    prix_sortie_b2: null,
    prix_sortie_b3: null,
    prix_sortie_c: null,
    horodatage_resolution_a: null,
    horodatage_resolution_b15: null,
    horodatage_resolution_b2: null,
    horodatage_resolution_b3: null,
    horodatage_resolution_c: null,
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

// ---------------------------------------------------------------------------
// Sortie par paliers (partie A)
// ---------------------------------------------------------------------------
function paliersExemple(idSignal = "sig-1") {
  return [
    {
      id_signal: idSignal, rang: 1, zone: 3012.0, origine: "veille_haut",
      fraction: 0.5, ratio_risque: 2.0, statut: "gagnant", prix_sortie: 3012.0,
      motif_sortie: "objectif", horodatage_resolution: 1_800_100_000_000,
    },
    {
      id_signal: idSignal, rang: 2, zone: 3030.0, origine: "order_block",
      fraction: 0.5, ratio_risque: 5.0, statut: "perdant", prix_sortie: 3000.0,
      motif_sortie: "break_even", horodatage_resolution: 1_800_200_000_000,
    },
  ];
}

test("la route expose le détail de chaque tranche, jamais un total opaque", () => {
  const ligne = ligneExemple({
    statut_c: "gagnant", prix_sortie_c: 3000.0, horodatage_resolution_c: 1_800_200_000_000,
  });
  const reponse = construireReponseJournal([ligne], null, paliersExemple());
  const [signal] = reponse.signaux;

  assert.equal(signal.paliers.length, 2);
  assert.equal(signal.paliers[0].rang, 1);
  assert.equal(signal.paliers[0].origine, "veille_haut");
  assert.equal(signal.paliers[0].motif_sortie, "objectif");
  assert.equal(signal.paliers[1].motif_sortie, "break_even");
});

test("le R d'une tranche est pondéré par sa part de position", () => {
  const reponse = construireReponseJournal([ligneExemple()], null, paliersExemple());
  const [signal] = reponse.signaux;
  // Tranche 1 : sortie à 3012 depuis 3000, stop 2994 → R brut 2, part 50 % → 1.
  assert.equal(signal.paliers[0].r, 1);
  // Tranche 2 : sortie au prix d'entrée → R brut 0, donc 0 quelle que soit la part.
  assert.equal(signal.paliers[1].r, 0);
});

test("le R de la variante à paliers est la somme de ses tranches, pas un recalcul sur le dernier prix", () => {
  const ligne = ligneExemple({
    statut_c: "gagnant", prix_sortie_c: 3000.0, horodatage_resolution_c: 1_800_200_000_000,
  });
  const reponse = construireReponseJournal([ligne], null, paliersExemple());
  const { c } = reponse.signaux[0].variantes;
  // Un recalcul sur le seul dernier prix de sortie (3000, le prix d'entrée)
  // donnerait 0 et effacerait le gain du premier palier.
  assert.equal(c.r, 1);
});

test("le détail des tranches ne fait apparaître aucun montant en dollars", () => {
  const ligne = ligneExemple({ statut_c: "gagnant", prix_sortie_c: 3000.0 });
  const texte = JSON.stringify(construireReponseJournal([ligne], null, paliersExemple()));
  assert.doesNotMatch(texte, /resultat_usd/);
  assert.doesNotMatch(texte, /"lots"/);
});

test("le texte de résolution d'une sortie par paliers décrit chaque tranche", () => {
  const ligne = ligneExemple({
    statut_c: "gagnant", prix_sortie_c: 3000.0, horodatage_resolution_c: 1_800_200_000_000,
  });
  const reponse = construireReponseJournal([ligne], null, paliersExemple());
  const texte = reponse.signaux[0].variantes.c.texte;
  assert.match(texte, /50 % sur haut de la veille à 3012\.00 \$/);
  assert.match(texte, /50 % au prix d'entrée/);
  assert.deepEqual(verifierAbsenceRecommandation({ texte }), []);
});

test("l'alerte publie le niveau du FVG et les zones visées", () => {
  const reponse = construireReponseJournal([ligneExemple()], null, paliersExemple());
  const signal = reponse.signaux[0];
  assert.equal(signal.fvg_haut, 2998.4);
  assert.match(signal.texte_detection, /entre 2997\.50 et 2998\.40 \$/);
  assert.match(signal.texte_detection, /haut de la veille à 3012\.00 \$/);
});

// ---------------------------------------------------------------------------
// Setup sweep : le signal dit d'où il vient
// ---------------------------------------------------------------------------
import { rendreAlerteEntree, rendreEvenementPublic } from "../src/alertes.js";

function entreeSweep() {
  return {
    type: "entree", id: "sweep-1", setup: "sweep",
    horodatageDetection: Date.UTC(2026, 8, 10, 14, 32),
    timeframeOb: "M15", obHaut: 4412.1, obBas: 4412.1,
    niveauPrix: 4412.1, niveauCote: "bas", niveauUnite: "M15",
    niveauFormation: Date.UTC(2026, 8, 10, 9, 15), sweepExtreme: 4410.3, sweepDebut: Date.UTC(2026, 8, 10, 14, 20),
    referencePrix: 4440.0, uniteFibo: "M15",
    sens: "haussier", timeframeFvg: "M5", fvgHaut: 4414.2, fvgBas: 4413.1,
    prixEntree: 4415.0, stop: 4409.3,
    objectifs: { a: null, b15: null, b2: null, b3: null, c: null, s1: 4431.7, s2: 4431.7, s3: 4438.0 },
    lots: 0.09,
    paliers: [
      { rang: 1, zone: 4431.7, origine: "fibonacci_0_72", fraction: 0.5, ratioRisque: 2.93 },
      { rang: 2, zone: 4438.0, origine: "niveau_haut", fraction: 0.5, ratioRisque: 4.04 },
    ],
  };
}

test("l'alerte d'un sweep dit quel niveau a été balayé, quand il s'est formé et jusqu'où la mèche est allée", () => {
  const texte = rendreAlerteEntree(entreeSweep());
  assert.match(texte, /balayage d'un ancien plus bas M15 à 4412\.10 \$/);
  assert.match(texte, /formé le 2026-09-10 09:15 UTC/);
  assert.match(texte, /mèche du sweep à 4410\.30 \$/);
  assert.match(texte, /0,72 du mouvement de référence à 4431\.70 \$/);
  assert.match(texte, /niveau structurel seul à 4438\.00 \$/);
  assert.match(texte, /ancien plus haut non balayé/);
  assert.doesNotMatch(texte, /order block M15 \[/, "un sweep ne se présente pas comme un order block");
  assert.doesNotMatch(texte, /fibonacci_0_72|niveau_haut/, "aucun identifiant technique brut");
});

test("les textes d'un sweep restent conformes au garde-fou anti-recommandation", () => {
  const entree = rendreEvenementPublic(entreeSweep());
  assert.equal(entree.infractions.length, 0, JSON.stringify(entree.infractions));
  const resolution = rendreEvenementPublic({
    type: "resolution", setup: "sweep", variante: "s2", statut: "gagnant", prixSortie: 4438.0,
    horodatageResolution: Date.UTC(2026, 8, 10, 16, 5),
    paliers: [
      { rang: 1, zone: 4431.7, origine: "fibonacci_0_72", fraction: 0.5, motif_sortie: "objectif" },
      { rang: 2, zone: 4438.0, origine: "niveau_haut", fraction: 0.5, motif_sortie: "objectif" },
    ],
  });
  assert.equal(resolution.infractions.length, 0);
  assert.match(resolution.texte, /0,72 puis niveau structurel \(sweep, variante 2\)/);
  assert.match(resolution.texte, /50 % sur 0,72 du mouvement de référence à 4431\.70 \$/);
});

test("construireReponseJournal expose le setup et le niveau balayé, et n'affiche que les variantes du setup", () => {
  const ligne = {
    id: "sweep-1", setup: "sweep", horodatage_detection: 1_800_000_000_000,
    timeframe_ob: "M15", ob_haut: 4412.1, ob_bas: 4412.1, sens: "haussier", timeframe_fvg: "M5",
    fvg_haut: 4414.2, fvg_bas: 4413.1, prix_entree: 4415.0, sl: 4409.3,
    niveau_prix: 4412.1, niveau_cote: "bas", niveau_unite: "M15", niveau_formation: 1_799_990_000_000,
    sweep_extreme: 4410.3, reference_prix: 4440.0, unite_fibo: "M15",
    tp_a: null, tp_b15: null, tp_b2: null, tp_b3: null, tp_c: null, tp_s1: 4431.7, tp_s2: 4431.7, tp_s3: 4438.0,
    statut_a: "sans_objectif", statut_b15: "sans_objectif", statut_b2: "sans_objectif", statut_b3: "sans_objectif", statut_c: "sans_objectif",
    statut_s1: "gagnant", statut_s2: "ouvert", statut_s3: "ouvert",
    prix_sortie_s1: 4431.4, horodatage_resolution_s1: 1_800_000_600_000,
  };
  const charge = construireReponseJournal([ligne], null, []);
  const signal = charge.signaux[0];
  assert.equal(signal.setup, "sweep");
  assert.equal(signal.niveau_prix, 4412.1);
  assert.equal(signal.niveau_cote, "bas");
  assert.match(signal.niveau_formation_utc, /^2027-/);
  assert.equal(signal.variantes.s1.statut, "gagnant");
  assert.ok(signal.variantes.s1.r > 2.5, `R attendu proche de 2,9, obtenu ${signal.variantes.s1.r}`);
  assert.equal(signal.variantes.a.statut, "sans_objectif");
  assert.match(signal.texte_detection, /balayage d'un ancien plus bas M15/);
  assert.doesNotMatch(JSON.stringify(charge), /"lots"|resultat_s1_usd/);
});
