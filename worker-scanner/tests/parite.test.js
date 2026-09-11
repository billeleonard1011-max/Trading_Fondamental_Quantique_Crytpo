/**
 * Test de parité : le portage JavaScript produit-il les mêmes résultats que
 * le moteur Python du backtest sur les mêmes données ?
 *
 * C'est le test le plus important de tout worker-scanner/ : Python et
 * JavaScript sont deux exécutions différentes, aucun moyen de partager le
 * code source directement (voir README.md). Les fixtures de
 * tests/fixtures/*.json sont générées par tests/fixtures/generer.py à
 * partir des vraies fonctions Python déjà testées du backtest ; ce fichier
 * relit ces mêmes entrées, appelle les fonctions JavaScript équivalentes,
 * et vérifie l'identité — pas une simple ressemblance — des sorties.
 *
 * Régénérer les fixtures après toute modification du backtest Python :
 *     .venv/bin/python worker-scanner/tests/fixtures/generer.py
 *
 * Exécution :
 *     node --test worker-scanner/tests/
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { agreger, fenetreClose } from "../src/agregation.js";
import {
  BAISSIER, HAUSSIER, detecterFvg, detecterOrderBlocks, detecterSwings, origineDeJambe,
} from "../src/ict.js";
import {
  appliquerCoutsEntree, appliquerCoutsSortie, calculerStop, configExecutionDefaut, dimensionner,
} from "../src/execution.js";
import { etatInitial, repartirPaliers, traiterNouvellesBougies } from "../src/moteur.js";

const DOSSIER_FIXTURES = new URL("./fixtures/", import.meta.url);

function fixture(nom) {
  return JSON.parse(readFileSync(new URL(`${nom}.json`, DOSSIER_FIXTURES), "utf8"));
}

/** Tolérance flottante : les deux langages font la même arithmétique IEEE
 * 754, l'écart ne devrait être que du bruit d'arrondi d'affichage côté
 * Python (round() à 6 décimales dans generer.py). */
const EPSILON = 1e-6;
function assertProche(a, b, message) {
  assert.ok(Math.abs(a - b) < EPSILON, `${message} : ${a} !== ${b}`);
}

// ---------------------------------------------------------------------------
// 1. Agrégation
// ---------------------------------------------------------------------------
test("parité — agreger() produit les mêmes bougies que backtest.data.agreger", () => {
  const { m1, cas } = fixture("agregation");
  for (const c of cas) {
    const resultat = agreger(m1, c.unite);
    assert.equal(resultat.length, c.attendu.length, `unité ${c.unite} : nombre de bougies`);
    for (let i = 0; i < resultat.length; i += 1) {
      assert.equal(resultat[i].t, c.attendu[i].t, `${c.unite}[${i}].t`);
      assertProche(resultat[i].ouverture, c.attendu[i].ouverture, `${c.unite}[${i}].ouverture`);
      assertProche(resultat[i].haut, c.attendu[i].haut, `${c.unite}[${i}].haut`);
      assertProche(resultat[i].bas, c.attendu[i].bas, `${c.unite}[${i}].bas`);
      assertProche(resultat[i].cloture, c.attendu[i].cloture, `${c.unite}[${i}].cloture`);
    }
  }
});

test("parité — fenetreClose() ne rend que les bougies closes, comme fenetre_close", () => {
  const { h1, cas } = fixture("fenetre_close");
  for (const c of cas) {
    const resultat = fenetreClose(h1, "H1", c.instantMs);
    assert.equal(resultat.length, c.attendu.length, `instant ${c.instantMs}`);
    for (let i = 0; i < resultat.length; i += 1) {
      assert.equal(resultat[i].t, c.attendu[i].t);
    }
  }
});

// ---------------------------------------------------------------------------
// 2. Order blocks
// ---------------------------------------------------------------------------
test("parité — detecterOrderBlocks() trouve exactement les mêmes zones que detecter_order_blocks", () => {
  const { cas } = fixture("order_blocks");
  for (const c of cas) {
    const resultat = detecterOrderBlocks(c.cadre, "H1");
    assert.equal(resultat.length, c.attendu.length, `${c.nom} : nombre de zones`);
    for (let i = 0; i < resultat.length; i += 1) {
      assert.equal(resultat[i].sens, c.attendu[i].sens, `${c.nom}[${i}].sens`);
      assertProche(resultat[i].haut, c.attendu[i].haut, `${c.nom}[${i}].haut`);
      assertProche(resultat[i].bas, c.attendu[i].bas, `${c.nom}[${i}].bas`);
      assert.equal(resultat[i].finMotif, c.attendu[i].finMotif, `${c.nom}[${i}].finMotif`);
      assertProche(resultat[i].mecheBougie2, c.attendu[i].mecheBougie2, `${c.nom}[${i}].mecheBougie2`);
    }
  }
});

// ---------------------------------------------------------------------------
// 3. Swings et origine de la jambe
// ---------------------------------------------------------------------------
test("parité — detecterSwings() et origineDeJambe() reproduisent detecter_swings / origine_de_jambe", () => {
  const { cas } = fixture("swings");
  for (const c of cas) {
    if ("sensibilite" in c && c.nom.startsWith("palier")) {
      const { sommets, creux } = detecterSwings(c.cadre, c.sensibilite);
      assert.deepEqual(sommets, c.attendu.sommets, `${c.nom} sommets`);
      assert.deepEqual(creux, c.attendu.creux, `${c.nom} creux`);
    } else {
      const origine = origineDeJambe(c.cadre, c.sensObOuSens, c.sensibilite);
      assert.equal(origine, c.attendu.origine, c.nom);
    }
  }
});

// ---------------------------------------------------------------------------
// 4. FVG
// ---------------------------------------------------------------------------
test("parité — detecterFvg() reproduit exactement detecter_fvg", () => {
  const { cas } = fixture("fvg");
  for (const c of cas) {
    const resultat = detecterFvg(c.cadre, "M5", c.sens);
    assert.equal(resultat.length, c.attendu.length, `${c.nom} : nombre d'écarts`);
    for (let i = 0; i < resultat.length; i += 1) {
      assert.equal(resultat[i].sens, c.attendu[i].sens, `${c.nom}[${i}].sens`);
      assertProche(resultat[i].haut, c.attendu[i].haut, `${c.nom}[${i}].haut`);
      assertProche(resultat[i].bas, c.attendu[i].bas, `${c.nom}[${i}].bas`);
    }
  }
});

// ---------------------------------------------------------------------------
// 5. Exécution : stop, dimensionnement, coûts
// ---------------------------------------------------------------------------
test("parité — calculerStop() reproduit calculer_stop, y compris le cas de la mèche débordante", () => {
  const { stop } = fixture("execution");
  for (const c of stop) {
    const resultat = calculerStop(c.sens, c.obHaut, c.obBas, c.mecheBougie2, c.marge);
    assertProche(resultat, c.attendu, `stop ${c.sens} ob=[${c.obBas},${c.obHaut}] meche=${c.mecheBougie2}`);
  }
});

test("parité — dimensionner() choisit la même taille (ou le même refus) que dimensionner (Python)", () => {
  const { dimensionnement } = fixture("execution");
  const config = configExecutionDefaut();
  for (const c of dimensionnement) {
    const resultat = dimensionner(c.distanceStopUsd, c.tauxEurusd, config);
    assert.equal(resultat.prenable, c.attendu.prenable, `prenable, distance=${c.distanceStopUsd} taux=${c.tauxEurusd}`);
    if (c.attendu.prenable) {
      assertProche(resultat.lots, c.attendu.lots, "lots");
      assertProche(resultat.perteEur, c.attendu.perteEur, "perteEur");
    }
  }
});

test("parité — appliquerCoutsEntree/Sortie reproduisent leurs équivalents Python", () => {
  const { couts } = fixture("execution");
  const config = configExecutionDefaut();
  for (const c of couts) {
    assertProche(appliquerCoutsEntree(c.prix, c.sens, config), c.entree, `entrée ${c.sens}`);
    assertProche(appliquerCoutsSortie(c.prix, c.sens, config), c.sortie, `sortie ${c.sens}`);
  }
});

// ---------------------------------------------------------------------------
// 6. Moteur complet — le test qui compte vraiment
// ---------------------------------------------------------------------------
test("parité — le moteur JS rejoue la même série et produit exactement les mêmes trades que Backtest (Python)", () => {
  const donnees = fixture("moteur_complet");
  const config = configExecutionDefaut();
  config.spread = 0.62;
  config.slippage = 0.3;
  config.margeStop = 1.0;

  // Une seule variante active (« b2 », ratio 1:2) : cela neutralise la
  // différence d'architecture assumée dans moteur.js (une entrée / quatre
  // sorties côté scanner, contre quatre simulations indépendantes côté
  // backtest) et reproduit exactement le blocage à une position du
  // backtest Python pour cette variante précise.
  const variantesActives = ["b2"];

  // Traiter la série jour par jour : le taux EUR/USD change chaque jour
  // dans la fixture, exactement comme Backtest._taux() le lit un jour à la
  // fois depuis la série quotidienne.
  const parJour = new Map();
  for (const bougie of donnees.m1) {
    const jour = new Date(bougie.t).toISOString().slice(0, 10);
    if (!parJour.has(jour)) parJour.set(jour, []);
    parJour.get(jour).push(bougie);
  }

  let etat = etatInitial();
  const fenetreCumulative = [];
  const evenements = [];
  for (const [jour, bougiesDuJour] of parJour) {
    fenetreCumulative.push(...bougiesDuJour);
    const taux = donnees.tauxEurusdParJour[jour] ?? null;
    const resultat = traiterNouvellesBougies(etat, fenetreCumulative, config, taux, 4, variantesActives);
    etat = resultat.etat;
    evenements.push(...resultat.evenements);
  }

  const entrees = evenements.filter((e) => e.type === "entree");
  const resolutions = evenements.filter((e) => e.type === "resolution" && e.variante === "b2");

  assert.equal(
    entrees.length, donnees.attenduNTrades,
    `nombre de trades : JS=${entrees.length} Python=${donnees.attenduNTrades}`,
  );

  for (let i = 0; i < donnees.attenduTrades.length; i += 1) {
    const attendu = donnees.attenduTrades[i];
    const obtenu = entrees[i];
    const resolution = resolutions.find((r) => r.id === obtenu.id);

    assert.equal(obtenu.horodatageDetection, attendu.horodatageEntree, `trade ${i} : horodatage d'entrée`);
    assert.equal(obtenu.sens, attendu.sens, `trade ${i} : sens`);
    assert.equal(obtenu.timeframeOb, attendu.uniteOb, `trade ${i} : unité d'order block`);
    assert.equal(obtenu.timeframeFvg, attendu.uniteFvg, `trade ${i} : unité de FVG`);
    assertProche(obtenu.obHaut, attendu.obHaut, `trade ${i} : ob_haut`);
    assertProche(obtenu.obBas, attendu.obBas, `trade ${i} : ob_bas`);
    assertProche(obtenu.prixEntree, attendu.prixEntree, `trade ${i} : prix d'entrée`);
    assertProche(obtenu.stop, attendu.stop, `trade ${i} : stop`);
    assertProche(obtenu.objectifs.b2, attendu.objectif, `trade ${i} : objectif (ratio 1:2)`);
    assertProche(obtenu.lots, attendu.lots, `trade ${i} : lots`);

    assert.ok(resolution, `trade ${i} : aucune résolution trouvée côté JS`);
    if (resolution) {
      assert.equal(resolution.horodatageResolution, attendu.horodatageSortie, `trade ${i} : horodatage de sortie`);
      assertProche(resolution.prixSortie, attendu.prixSortie, `trade ${i} : prix de sortie`);
      assert.equal(
        resolution.statut, attendu.motifSortie === "objectif" ? "gagnant" : "perdant",
        `trade ${i} : statut de résolution`,
      );
    }
  }
});

// ---------------------------------------------------------------------------
// 7. Sortie par paliers — le test qui compte pour la partie A
// ---------------------------------------------------------------------------
test("parité — repartirPaliers() reproduit repartir_paliers (Python)", () => {
  const { repartitions } = fixture("paliers");
  for (const [nZones, attendu] of Object.entries(repartitions)) {
    const obtenu = repartirPaliers(Number(nZones));
    assert.equal(obtenu.length, attendu.length, `${nZones} zone(s) : nombre de parts`);
    obtenu.forEach((part, i) => assertProche(part, attendu[i], `${nZones} zones, part ${i}`));
    assertProche(obtenu.reduce((s, p) => s + p, 0), 1.0, `${nZones} zones : somme des parts`);
  }
});

test("parité — le moteur JS reproduit exactement les trades à paliers du backtest (Python)", () => {
  const donnees = fixture("paliers");
  const config = configExecutionDefaut();
  config.spread = 0.62;
  config.slippage = 0.3;
  config.margeStop = 1.0;

  // Seule la variante à paliers est active : cela reproduit exactement le
  // blocage à une position du backtest Python pour cette variante, comme le
  // fait déjà le test du moteur complet avec « b2 ».
  const variantesActives = ["c"];

  const parJour = new Map();
  for (const bougie of donnees.m1) {
    const jour = new Date(bougie.t).toISOString().slice(0, 10);
    if (!parJour.has(jour)) parJour.set(jour, []);
    parJour.get(jour).push(bougie);
  }

  let etat = etatInitial();
  const fenetreCumulative = [];
  const evenements = [];
  for (const [jour, bougiesDuJour] of parJour) {
    fenetreCumulative.push(...bougiesDuJour);
    const taux = donnees.tauxEurusdParJour[jour] ?? null;
    const resultat = traiterNouvellesBougies(etat, fenetreCumulative, config, taux, 4, variantesActives);
    etat = resultat.etat;
    evenements.push(...resultat.evenements);
  }

  const entrees = evenements.filter((e) => e.type === "entree");
  const tranches = evenements.filter((e) => e.type === "resolution_palier");

  assert.equal(
    entrees.length, donnees.attenduNTrades,
    `nombre de trades : JS=${entrees.length} Python=${donnees.attenduNTrades}`,
  );

  for (let i = 0; i < donnees.attenduTrades.length; i += 1) {
    const attendu = donnees.attenduTrades[i];
    const obtenu = entrees[i];

    assert.equal(obtenu.horodatageDetection, attendu.horodatageEntree, `trade ${i} : horodatage d'entrée`);
    assert.equal(obtenu.sens, attendu.sens, `trade ${i} : sens`);
    assert.equal(obtenu.timeframeOb, attendu.uniteOb, `trade ${i} : unité d'order block`);
    assert.equal(obtenu.timeframeFvg, attendu.uniteFvg, `trade ${i} : unité de FVG`);
    assertProche(obtenu.prixEntree, attendu.prixEntree, `trade ${i} : prix d'entrée`);
    assertProche(obtenu.stop, attendu.stop, `trade ${i} : stop`);
    assertProche(obtenu.objectifs.c, attendu.objectif, `trade ${i} : première zone`);
    assertProche(obtenu.lots, attendu.lots, `trade ${i} : lots`);
    if (attendu.fvgHaut !== null) {
      assertProche(obtenu.fvgHaut, attendu.fvgHaut, `trade ${i} : haut du FVG`);
      assertProche(obtenu.fvgBas, attendu.fvgBas, `trade ${i} : bas du FVG`);
    }

    // Le détail des tranches, zone par zone.
    assert.equal(
      obtenu.paliers.length, attendu.paliers.length,
      `trade ${i} : nombre de zones (JS=${obtenu.paliers.length} Python=${attendu.paliers.length})`,
    );
    for (let j = 0; j < attendu.paliers.length; j += 1) {
      const p = attendu.paliers[j];
      const q = obtenu.paliers[j];
      assert.equal(q.rang, p.rang, `trade ${i} palier ${j} : rang`);
      assertProche(q.zone, p.zone, `trade ${i} palier ${j} : zone visée`);
      assert.equal(q.origine, p.origine, `trade ${i} palier ${j} : origine du niveau`);
      assertProche(q.fraction, p.fraction, `trade ${i} palier ${j} : part de la position`);
      assertProche(q.ratioRisque, p.ratioRisque, `trade ${i} palier ${j} : ratio risque/récompense`);

      // Le dénouement de la tranche, tel que le journal le retiendra.
      const tranche = tranches.find((t) => t.id === obtenu.id && t.rang === p.rang);
      if (p.prixSortie === null) {
        assert.equal(tranche, undefined, `trade ${i} palier ${j} : tranche non dénouée côté Python`);
        continue;
      }
      assert.ok(tranche, `trade ${i} palier ${j} : aucune tranche dénouée côté JS`);
      assertProche(tranche.prixSortie, p.prixSortie, `trade ${i} palier ${j} : prix de sortie`);
      assert.equal(tranche.motifSortie, p.motifSortie, `trade ${i} palier ${j} : motif de sortie`);
      assert.equal(
        tranche.horodatageResolution, p.horodatageResolution,
        `trade ${i} palier ${j} : horodatage de résolution`,
      );
    }
  }
});

test("parité — le passage à break-even se voit dans les motifs de sortie des deux moteurs", () => {
  const donnees = fixture("paliers");
  const motifs = {};
  for (const trade of donnees.attenduTrades) {
    for (const p of trade.paliers) {
      motifs[p.motifSortie || "non_denoue"] = (motifs[p.motifSortie || "non_denoue"] || 0) + 1;
    }
  }
  assert.ok(
    motifs.break_even > 0,
    `la fixture doit contenir des sorties à break-even pour que la parité soit probante (motifs : ${JSON.stringify(motifs)})`,
  );
});

// ---------------------------------------------------------------------------
// 8. Setup sweep — niveaux de liquidité, cycle de vie, moteur complet
// ---------------------------------------------------------------------------
import { avancerNiveau, confirmerBalayage, detecterNiveauxLiquidite } from "../src/ict.js";

test("parité — detecterNiveauxLiquidite() reproduit detecter_niveaux_liquidite pour les sensibilités 3, 4 et 5", () => {
  const { cas } = fixture("niveaux");
  for (const c of cas) {
    const resultat = detecterNiveauxLiquidite(c.cadre, c.unite, c.sensibilite);
    assert.equal(resultat.length, c.attendu.length, `${c.nom} : nombre de niveaux`);
    for (let i = 0; i < resultat.length; i += 1) {
      assert.equal(resultat[i].cote, c.attendu[i].cote, `${c.nom}[${i}].cote`);
      assertProche(resultat[i].prix, c.attendu[i].prix, `${c.nom}[${i}].prix`);
      assert.equal(resultat[i].formation, c.attendu[i].formation, `${c.nom}[${i}].formation`);
      assert.equal(resultat[i].connuA, c.attendu[i].connuA, `${c.nom}[${i}].connuA`);
    }
  }
});

test("parité — avancerNiveau() et confirmerBalayage() suivent exactement le cycle de vie Python", () => {
  const { sequences } = fixture("niveaux");
  for (const seq of sequences) {
    const niveau = {
      unite: "M15", cote: seq.cote, prix: seq.prix, formation: 0, connuA: 0,
      enSweep: false, extremeSweep: null, debutSweep: null, balaye: false, horodatageBalayage: null,
    };
    seq.trace.forEach((etape, i) => {
      const retour = etape.etape[0] === "m1"
        ? avancerNiveau(niveau, etape.etape[1], etape.etape[2], etape.instantMs)
        : confirmerBalayage(niveau, etape.etape[1], etape.instantMs);
      assert.equal(retour, etape.retour, `${seq.cote} étape ${i} : retour`);
      assert.equal(niveau.enSweep, etape.etat.enSweep, `${seq.cote} étape ${i} : enSweep`);
      assert.equal(niveau.balaye, etape.etat.balaye, `${seq.cote} étape ${i} : balaye`);
      assert.equal(niveau.debutSweep, etape.etat.debutSweep, `${seq.cote} étape ${i} : debutSweep`);
      if (etape.etat.extremeSweep === null) assert.equal(niveau.extremeSweep, null, `${seq.cote} étape ${i} : extremeSweep`);
      else assertProche(niveau.extremeSweep, etape.etat.extremeSweep, `${seq.cote} étape ${i} : extremeSweep`);
    });
  }
});

/**
 * Rejoue une exécution de la fixture sweep à travers le moteur JS, jour par
 * jour (le taux EUR/USD change chaque jour, comme Backtest._taux()).
 */
function rejouerSweep(donnees, execution) {
  const config = configExecutionDefaut();
  config.spread = 0.62;
  config.slippage = 0.3;
  config.margeStop = 1.0;
  const options = { sensibilitePivot: donnees.sensibilitePivot, uniteFibo: donnees.uniteFibo, fractionFibo: 0.5, breakEvenS2: true };

  const parJour = new Map();
  for (const bougie of donnees.m1) {
    const jour = new Date(bougie.t).toISOString().slice(0, 10);
    if (!parJour.has(jour)) parJour.set(jour, []);
    parJour.get(jour).push(bougie);
  }
  let etat = etatInitial();
  const fenetreCumulative = [];
  const evenements = [];
  for (const [jour, bougiesDuJour] of parJour) {
    fenetreCumulative.push(...bougiesDuJour);
    const taux = donnees.tauxEurusdParJour[jour] ?? null;
    const resultat = traiterNouvellesBougies(etat, fenetreCumulative, config, taux, 4, execution.variantesActives, options);
    etat = resultat.etat;
    evenements.push(...resultat.evenements);
  }
  return evenements;
}

for (const nom of ["s1", "s2", "s3"]) {
  test(`parité — le moteur JS reproduit exactement les trades sweep de la variante ${nom} (Python)`, () => {
    const donnees = fixture("sweep");
    const execution = donnees.executions[nom];
    const evenements = rejouerSweep(donnees, execution);
    const entrees = evenements.filter((e) => e.type === "entree");
    const resolutions = evenements.filter((e) => e.type === "resolution" && e.variante === nom);
    const tranches = evenements.filter((e) => e.type === "resolution_palier");

    assert.equal(entrees.length, execution.attenduNTrades, `${nom} : nombre de trades JS=${entrees.length} Python=${execution.attenduNTrades}`);
    for (let i = 0; i < execution.attenduTrades.length; i += 1) {
      const attendu = execution.attenduTrades[i];
      const obtenu = entrees[i];
      assert.equal(obtenu.setup, "sweep", `trade ${i} : setup`);
      assert.equal(obtenu.horodatageDetection, attendu.horodatageEntree, `trade ${i} : horodatage d'entrée`);
      assert.equal(obtenu.sens, attendu.sens, `trade ${i} : sens`);
      assert.equal(obtenu.niveauUnite, attendu.uniteDetection, `trade ${i} : unité du niveau`);
      assert.equal(obtenu.niveauCote, attendu.niveauCote, `trade ${i} : côté du niveau`);
      assertProche(obtenu.niveauPrix, attendu.niveauPrix, `trade ${i} : niveau balayé`);
      assert.equal(obtenu.niveauFormation, attendu.niveauFormation, `trade ${i} : formation du niveau`);
      assertProche(obtenu.sweepExtreme, attendu.sweepExtreme, `trade ${i} : extrême du sweep`);
      assert.equal(obtenu.sweepDebut, attendu.sweepDebut, `trade ${i} : début du sweep`);
      assert.equal(obtenu.timeframeFvg, attendu.uniteFvg, `trade ${i} : unité de FVG`);
      assertProche(obtenu.prixEntree, attendu.prixEntree, `trade ${i} : prix d'entrée`);
      assertProche(obtenu.stop, attendu.stop, `trade ${i} : stop`);
      assertProche(obtenu.objectifs[nom], attendu.objectif, `trade ${i} : objectif ${nom}`);
      assertProche(obtenu.lots, attendu.lots, `trade ${i} : lots`);
      if (attendu.referencePrix !== null) {
        assertProche(obtenu.referencePrix, attendu.referencePrix, `trade ${i} : origine du mouvement de référence`);
        assert.equal(obtenu.uniteFibo, attendu.uniteFibo, `trade ${i} : unité d'ancrage`);
      }

      if (nom === "s2") {
        assert.equal(obtenu.paliers.length, attendu.paliers.length, `trade ${i} : nombre de paliers`);
        attendu.paliers.forEach((p, j) => {
          const q = obtenu.paliers[j];
          assertProche(q.zone, p.zone, `trade ${i} palier ${j} : zone`);
          assert.equal(q.origine, p.origine, `trade ${i} palier ${j} : origine`);
          assertProche(q.fraction, p.fraction, `trade ${i} palier ${j} : part`);
          const tranche = tranches.find((t) => t.id === obtenu.id && t.rang === p.rang);
          if (p.prixSortie === null) { assert.equal(tranche, undefined, `trade ${i} palier ${j} : non dénoué côté Python`); return; }
          assert.ok(tranche, `trade ${i} palier ${j} : aucune tranche dénouée côté JS`);
          assertProche(tranche.prixSortie, p.prixSortie, `trade ${i} palier ${j} : prix de sortie`);
          assert.equal(tranche.motifSortie, p.motifSortie, `trade ${i} palier ${j} : motif`);
          assert.equal(tranche.horodatageResolution, p.horodatageResolution, `trade ${i} palier ${j} : horodatage`);
        });
      } else {
        const resolution = resolutions.find((r) => r.id === obtenu.id);
        if (attendu.horodatageSortie === null) { assert.equal(resolution, undefined, `trade ${i} : non dénoué côté Python`); continue; }
        assert.ok(resolution, `trade ${i} : aucune résolution côté JS`);
        assert.equal(resolution.horodatageResolution, attendu.horodatageSortie, `trade ${i} : horodatage de sortie`);
        assertProche(resolution.prixSortie, attendu.prixSortie, `trade ${i} : prix de sortie`);
        assert.equal(resolution.statut, attendu.motifSortie === "objectif" ? "gagnant" : "perdant", `trade ${i} : statut`);
      }
    }
  });
}

test("parité — les deux setups ensemble (order block structurel + sweep S1) produisent les mêmes trades que Python", () => {
  const donnees = fixture("sweep");
  const execution = donnees.executions.ensemble_a_s1;
  const evenements = rejouerSweep(donnees, execution);
  const entrees = evenements.filter((e) => e.type === "entree");
  assert.equal(entrees.length, execution.attenduNTrades, `nombre de trades JS=${entrees.length} Python=${execution.attenduNTrades}`);
  for (let i = 0; i < execution.attenduTrades.length; i += 1) {
    const attendu = execution.attenduTrades[i];
    const obtenu = entrees[i];
    assert.equal(obtenu.setup, attendu.setup, `trade ${i} : setup`);
    assert.equal(obtenu.horodatageDetection, attendu.horodatageEntree, `trade ${i} : horodatage d'entrée`);
    assert.equal(obtenu.sens, attendu.sens, `trade ${i} : sens`);
    assert.equal(obtenu.timeframeOb, attendu.uniteDetection, `trade ${i} : unité de détection`);
    assertProche(obtenu.prixEntree, attendu.prixEntree, `trade ${i} : prix d'entrée`);
    assertProche(obtenu.stop, attendu.stop, `trade ${i} : stop`);
    const variante = attendu.setup === "sweep" ? "s1" : "a";
    assertProche(obtenu.objectifs[variante], attendu.objectif, `trade ${i} : objectif ${variante}`);
    const resolution = evenements.find((e) => e.type === "resolution" && e.id === obtenu.id && e.variante === variante);
    if (attendu.horodatageSortie === null) continue;
    assert.ok(resolution, `trade ${i} : aucune résolution côté JS`);
    assert.equal(resolution.horodatageResolution, attendu.horodatageSortie, `trade ${i} : horodatage de sortie`);
    assertProche(resolution.prixSortie, attendu.prixSortie, `trade ${i} : prix de sortie`);
  }
  const setups = new Set(entrees.map((e) => e.setup));
  assert.ok(setups.has("sweep") && setups.has("order_block"), "les deux setups doivent produire des trades pour que la parité soit probante");
});
