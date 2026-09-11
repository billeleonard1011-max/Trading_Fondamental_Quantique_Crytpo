/**
 * Tests du site, exécutés sans navigateur.
 *
 * Le rendu vit dans des fonctions pures, ce qui permet de vérifier
 * mécaniquement les règles qui comptent :
 *
 * * un bloc indisponible affiche **son motif**, jamais une valeur vide ni un
 *   zéro — un zéro se lit comme une mesure et ferait prendre une panne pour
 *   une information ;
 * * un rapport absent ou tronqué n'emporte pas le reste de la page ;
 * * rien de ce qui touche au compte de l'utilisateur ne s'affiche.
 *
 * Exécution :
 *     node --test site/tests/
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { ABSENT, compteARebours, echapper, estDatee, libelleAge, nombre, pourcent }
  from "../js/format.js";
import { chargerJson, chargerJsonl, lire } from "../js/donnees.js";
import {
  champsInterdits, rendreEchelle, rendreIndisponible, rendreLibelleAvecInfobulle,
  rendrePrecedents, rendrePrix,
} from "../js/rendu.js";
import {
  estLieAUnDossier, itemRelieAUnDossier, normaliserTexteGeo, rendreContexteMacro, rendreSynthese,
  rubriqueCrypto, rubriqueGeopolitique, rubriqueOr, rubriqueQuantique,
} from "../js/rubriques.js";
import { filtrer, rendreFil, rendreVide } from "../js/fil.js";
import { construireContexte, demander, suggestions } from "../js/assistant.js";
import { evaluerFiabilite, rendreFiabilite } from "../js/debrief.js";
import { rendreDetail } from "../js/news.js";
import { EXPLICATIONS, libelle } from "../js/libelles.js";
import {
  agregerParMois, agregerSignaux, avertissementEchantillon, cleMois, estResolue,
  extraireMetriquesBacktest, libelleStatut, rendreComparaisonBacktest, rendreFilAlertes,
  rendreNiveaux, rendrePaliers, rendreTableauBordMensuel, texteSurAudite, tonStatut, VARIANTES, VARIANTES_PAR_SETUP,
} from "../js/trading.js";

/** Charge un rapport réel du dépôt. */
function rapport(chemin) {
  return JSON.parse(readFileSync(new URL(`../../${chemin}`, import.meta.url), "utf8"));
}

/** Fabrique un fetch factice rendant une charge donnée. */
function fauxFetch(charge, { ok = true, statut = 200, texte = null } = {}) {
  return async () => ({
    ok,
    status: statut,
    json: async () => {
      if (charge instanceof Error) throw charge;
      return charge;
    },
    text: async () => (texte === null ? JSON.stringify(charge) : texte),
  });
}

// ---------------------------------------------------------------------------
// 1. Absence de donnée : motif affiché, jamais de valeur vide
// ---------------------------------------------------------------------------
test("un bloc indisponible affiche son motif", () => {
  const html = rendreIndisponible("API CFTC injoignable");
  assert.match(html, /API CFTC injoignable/);
  assert.match(html, /indisponible/);
  assert.doesNotMatch(html, />0</);
});

test("un motif vide donne quand même une explication", () => {
  const html = rendreIndisponible("");
  assert.match(html, /sans motif précisé/);
});

test("une valeur absente rend un tiret, jamais un zéro", () => {
  for (const vide of [null, undefined, NaN]) {
    assert.equal(nombre(vide), ABSENT);
    assert.equal(pourcent(vide), ABSENT);
    assert.equal(compteARebours(vide), ABSENT);
  }
  assert.notEqual(nombre(null), "0");
  assert.equal(nombre(0), "0,00", "Un vrai zéro doit rester affiché.");
});

test("les quatre rubriques affichent le motif quand la source manque", () => {
  const absent = { disponible: false, donnees: null, motif: "fichier introuvable (HTTP 404)" };
  for (const rubrique of [rubriqueOr, rubriqueGeopolitique, rubriqueQuantique, rubriqueCrypto]) {
    const rendu = rubrique(absent);
    assert.match(rendu.corps, /fichier introuvable/, `${rubrique.name} masque le motif`);
    assert.match(rendu.resume, /indisponible/);
  }
});

test("une échelle sans percentile le dit au lieu d'afficher zéro", () => {
  const html = rendreEchelle(null, "bon marché", "cher");
  assert.match(html, /non calculée/);
  assert.doesNotMatch(html, /left:0%/);
});

test("des précédents insuffisants sont annoncés comme tels", () => {
  const html = rendrePrecedents({ "20j": { disponible: false, motif: "8 cas requis" } });
  assert.match(html, /8 cas requis/);
});

// ---------------------------------------------------------------------------
// 2. Robustesse : fichier absent ou malformé
// ---------------------------------------------------------------------------
test("un fichier absent est signalé sans lever d'exception", async () => {
  const etat = await chargerJson("/inexistant.json", fauxFetch(null, { ok: false, statut: 404 }));
  assert.equal(etat.disponible, false);
  assert.match(etat.motif, /404/);
  assert.equal(etat.donnees, null);
});

test("un JSON tronqué est signalé sans lever d'exception", async () => {
  const etat = await chargerJson("/casse.json", fauxFetch(new SyntaxError("Unexpected end")));
  assert.equal(etat.disponible, false);
  assert.match(etat.motif, /illisible/);
});

test("une ligne corrompue ne fait pas perdre l'historique", async () => {
  const brut = '{"date":"2026-09-01"}\nligne cassée\n{"date":"2026-09-02"}\n';
  const etat = await chargerJsonl("/h.jsonl", fauxFetch(null, { texte: brut }));
  assert.equal(etat.disponible, true);
  assert.equal(etat.lignes.length, 2);
  assert.equal(etat.lignesIgnorees, 1);
});

test("lire ne lève jamais sur un chemin absent", () => {
  assert.equal(lire(null, "a.b.c", "defaut"), "defaut");
  assert.equal(lire({ a: 1 }, "a.b.c", "defaut"), "defaut");
  assert.equal(lire({ a: { b: { c: 7 } } }, "a.b.c"), 7);
});

test("une rubrique absente n'empêche pas les autres de rendre", () => {
  const etat = {
    or: { disponible: true, donnees: rapport("reports/gold/latest.json") },
    quantique: { disponible: false, motif: "rapport quantique absent" },
    crypto: { disponible: true, donnees: rapport("reports/crypto/latest.json") },
  };
  assert.match(rubriqueOr(etat.or).corps, /trame-section/);
  assert.match(rubriqueQuantique(etat.quantique).corps, /rapport quantique absent/);
  assert.match(rubriqueCrypto(etat.crypto).corps, /trame-section/);
});

// ---------------------------------------------------------------------------
// 3. Rien de privé sur un site public
// ---------------------------------------------------------------------------
test("aucun champ de compte n'est détecté dans les rapports servis", () => {
  for (const chemin of [
    "reports/gold/latest.json",
    "reports/quantum/latest.json",
    "reports/crypto/latest.json",
  ]) {
    const fautifs = champsInterdits(rapport(chemin));
    assert.deepEqual(fautifs, [], `${chemin} porte des champs privés : ${fautifs}`);
  }
});

test("le détecteur de champs privés attrape bien ce qu'il vise", () => {
  const fautifs = champsInterdits({
    lots: 0.5,
    prix: 3000,
    imbrique: { solde: 10000, propfirm: {} },
  });
  assert.ok(fautifs.includes("lots"));
  assert.ok(fautifs.includes("imbrique.solde"));
  assert.ok(fautifs.includes("imbrique.propfirm"));
  assert.ok(!fautifs.includes("prix"), "Un prix public ne doit pas être filtré.");
});

test("le contexte envoyé à l'assistant ne contient rien de privé", () => {
  const etat = {
    or: { disponible: true, donnees: rapport("reports/gold/latest.json") },
    crypto: { disponible: true, donnees: rapport("reports/crypto/latest.json") },
    quantique: { disponible: true, donnees: rapport("reports/quantum/latest.json") },
  };
  const contexte = construireContexte(etat);
  assert.deepEqual(champsInterdits(contexte), []);
  assert.ok(contexte.or.juste_valeur, "Le contexte doit rester utile.");
});

test("aucune clé d'API dans les fichiers servis", () => {
  const motifs = [/sk-[A-Za-z0-9]{16,}/, /api[_-]?key\s*[:=]\s*["'][A-Za-z0-9]{12,}/i];
  const fichiers = [
    "site/index.html", "site/news.html", "site/debrief.html", "site/css/style.css",
    "site/js/config.js", "site/js/accueil.js", "site/js/assistant.js",
    "site/js/donnees.js", "site/js/format.js", "site/js/rendu.js",
    "site/js/rubriques.js", "site/js/fil.js", "site/js/news.js",
    "site/js/debrief.js", "site/js/theme.js", "site/js/libelles.js",
  ];
  for (const chemin of fichiers) {
    const contenu = readFileSync(new URL(`../../${chemin}`, import.meta.url), "utf8");
    for (const motif of motifs) {
      assert.ok(!motif.test(contenu), `${chemin} contient peut-être une clé`);
    }
  }
});

// ---------------------------------------------------------------------------
// 4. Horodatage et fraîcheur
// ---------------------------------------------------------------------------
test("l'âge est signalé selon un seuil propre au bloc", () => {
  // Un COT de six jours est normal ; un prix de six jours ne l'est pas.
  assert.equal(estDatee(6, 8), false);
  assert.equal(estDatee(6, 1), true);
  assert.equal(estDatee(null, 1), false, "Un âge inconnu n'est pas une donnée datée.");
});

test("l'âge se lit en toutes lettres", () => {
  assert.equal(libelleAge(0), "aujourd'hui");
  assert.equal(libelleAge(1), "hier");
  assert.equal(libelleAge(6), "il y a 6 jours");
  assert.equal(libelleAge(null), ABSENT);
});

test("chaque vignette de prix porte son horodatage", () => {
  const html = rendrePrix({
    symbole: "XAUUSD", prix: 4429.8, variation: 1.2, horodatage: "2026-09-08",
  });
  assert.match(html, /2026-09-08/);
  assert.match(html, /XAUUSD/);
});

test("une vignette sans prix affiche un tiret, pas un zéro", () => {
  const html = rendrePrix({ symbole: "BTC", prix: null, variation: null, horodatage: null });
  assert.match(html, new RegExp(ABSENT));
  assert.doesNotMatch(html, />0,00</);
});

// ---------------------------------------------------------------------------
// 5. Fil de news
// ---------------------------------------------------------------------------
test("chaque catégorie a son propre message d'état vide, non générique", () => {
  assert.match(rendreVide("crypto"), /Aucune actualité crypto/);
  assert.match(rendreVide("geopolitique"), /Aucune actualité géopolitique/);
  assert.match(rendreVide("quantique"), /Aucune actualité quantique/);
  // Trois messages distincts : un texte générique ferait croire à un fil
  // non construit plutôt qu'à une période sans article pertinent.
  assert.notEqual(rendreVide("crypto"), rendreVide("geopolitique"));
  assert.notEqual(rendreVide("crypto"), rendreVide("quantique"));
});

test("le filtre par catégorie ne rend que la bonne catégorie", () => {
  const items = [
    { id: "a", categorie: "quantique", horodatage_utc: "2026-09-08T10:00:00Z" },
    { id: "b", categorie: "crypto", horodatage_utc: "2026-09-08T11:00:00Z" },
  ];
  assert.equal(filtrer(items, "quantique").length, 1);
  assert.equal(filtrer(items, "geopolitique").length, 0);
  assert.equal(filtrer(items, "tout").length, 2);
  // Le plus récent d'abord.
  assert.equal(filtrer(items, "tout")[0].id, "b");
});

test("le fil réel ne montre que les titres, pas l'analyse", () => {
  const items = rapport("reports/quantum/feed_latest.json");
  const html = rendreFil(items, "quantique");
  assert.match(html, /fil-item/);
  for (const item of items) {
    if (item.analyse_interne) {
      assert.ok(
        !html.includes(item.analyse_interne),
        "L'analyse appartient à la page de détail, pas au fil.",
      );
    }
  }
});

test("la page de détail ouvre la source dans un nouvel onglet en sécurité", () => {
  const html = rendreDetail(
    {
      id: "x", titre_affiche: "Titre", source_nom: "S", horodatage_utc: "2026-09-08T10:00:00Z",
      a_une_analyse_interne: true, analyse_interne: "Une analyse.",
      tickers_ou_themes_lies: ["RGTI"], url_source: "https://exemple.test/a",
    },
    "x",
  );
  assert.match(html, /target="_blank"/);
  assert.match(html, /rel="noopener noreferrer"/);
  assert.match(html, /Une analyse\./);
});

test("un item introuvable est expliqué, pas affiché vide", () => {
  const html = rendreDetail(undefined, "inconnu");
  assert.match(html, /Aucune actualité ne porte l'identifiant/);
});

// ---------------------------------------------------------------------------
// 6. Assistant
// ---------------------------------------------------------------------------
test("sans proxy configuré, l'assistant explique au lieu d'échouer", async () => {
  const resultat = await demander("Une question", {}, "");
  assert.equal(resultat.ok, false);
  assert.match(resultat.texte, /worker\/README\.md/);
});

test("une limitation de débit est expliquée au visiteur", async () => {
  const resultat = await demander("Q", {}, "https://x.test", fauxFetch(null, { ok: false, statut: 429 }));
  assert.equal(resultat.ok, false);
  assert.match(resultat.texte, /Trop de questions/);
});

test("les suggestions s'adaptent aux données du jour", () => {
  const etat = { or: { disponible: true, donnees: rapport("reports/gold/latest.json") } };
  const propositions = suggestions(etat);
  assert.equal(propositions.length, 3);
  for (const p of propositions) assert.ok(p.length > 10);
});

// ---------------------------------------------------------------------------
// 7. Débrief
// ---------------------------------------------------------------------------
test("un historique trop court refuse de donner un score", () => {
  const lignes = [
    { date: "2026-09-01", biais: "haussier", prix_or_au_moment_du_biais: 4000 },
    { date: "2026-09-02", biais: "vendeur", prix_or_au_moment_du_biais: 4100 },
    { date: "2026-09-03", biais: "haussier", prix_or_au_moment_du_biais: 4050 },
  ];
  const resultat = evaluerFiabilite(lignes, 30);
  assert.equal(resultat.suffisant, false);
  assert.match(resultat.motif, /mesure le hasard/);
  // Le rendu doit afficher le motif, pas un pourcentage sur trois jours.
  const html = rendreFiabilite(resultat);
  assert.match(html, /indisponible/);
  assert.match(html, /mesure le hasard/);
});

test("un biais neutre n'est pas noté", () => {
  const lignes = [
    { date: "2026-09-01", biais: "neutre", prix_or_au_moment_du_biais: 4000 },
    { date: "2026-09-02", biais: "neutre", prix_or_au_moment_du_biais: 4100 },
  ];
  const resultat = evaluerFiabilite(lignes, 1);
  assert.equal(resultat.n_evaluables, 0, "Un biais neutre n'annonce rien.");
  assert.equal(resultat.taux, null);
});

test("un biais tenu et un biais démenti sont correctement notés", () => {
  const lignes = [
    { date: "2026-09-01", biais: "haussier", prix_or_au_moment_du_biais: 4000 },
    { date: "2026-09-02", biais: "vendeur", prix_or_au_moment_du_biais: 4100 },
    { date: "2026-09-03", biais: "neutre", prix_or_au_moment_du_biais: 4200 },
  ];
  const resultat = evaluerFiabilite(lignes, 1);
  // Haussier puis prix en hausse : tenu. Vendeur puis prix en hausse : démenti.
  assert.equal(resultat.n_evaluables, 2);
  assert.equal(resultat.n_justes, 1);
  assert.equal(resultat.taux, 0.5);
});

// ---------------------------------------------------------------------------
// 8. Sécurité du rendu
// ---------------------------------------------------------------------------
test("le texte externe est échappé avant insertion", () => {
  assert.equal(echapper('<img src=x onerror="1">'), "&lt;img src=x onerror=&quot;1&quot;&gt;");
  const html = rendreIndisponible('<script>alert(1)</script>');
  assert.doesNotMatch(html, /<script>/);
});

test("un titre d'article hostile ne s'injecte pas dans le fil", () => {
  const html = rendreFil(
    [{
      id: "x", categorie: "quantique", titre_affiche: '<img src=x onerror=alert(1)>',
      horodatage_utc: "2026-09-08T10:00:00Z", source_nom: "S", tickers_ou_themes_lies: [],
    }],
    "quantique",
  );
  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /&lt;img/);
});

// ---------------------------------------------------------------------------
// 9. Tenue sur écran étroit
// ---------------------------------------------------------------------------
test("aucune largeur fixe ne déborde d'un écran de 375 px", () => {
  const css = readFileSync(new URL("../css/style.css", import.meta.url), "utf8");

  // Une largeur fixe supérieure à la largeur utile d'un mobile déborde
  // forcément : 375 px moins les marges de l'enveloppe.
  const largeurUtile = 375 - 2 * 10;
  const fixes = [...css.matchAll(/(?:^|[\s;{])(?:min-)?width\s*:\s*(\d+)px/g)]
    .map((m) => Number(m[1]))
    .filter((v) => v > largeurUtile);
  assert.deepEqual(fixes, [], `Largeurs fixes trop grandes : ${fixes}`);

  // Les grilles doivent pouvoir retomber sur une colonne.
  const minmax = [...css.matchAll(/minmax\(\s*(\d+)px/g)].map((m) => Number(m[1]));
  for (const valeur of minmax) {
    assert.ok(valeur <= largeurUtile, `minmax(${valeur}px) déborde sur mobile`);
  }
});

test("l'infobulle la plus longue n'est ni tronquée ni positionnée en absolu", () => {
  // .rubrique porte overflow:hidden pour ses coins arrondis (voir plus bas
  // dans la feuille de style) : une bulle en position:absolute finirait
  // rognée par ce conteneur, ou déborderait de l'écran près des bords. Le
  // texte doit se déplier dans le flux normal à la place.
  const css = readFileSync(new URL("../css/style.css", import.meta.url), "utf8");
  const regleBulle = css.match(/\.infobulle-bulle\s*\{[^}]*\}/)[0];
  assert.doesNotMatch(regleBulle, /position\s*:\s*absolute/);

  const plusLongue = Object.entries(EXPLICATIONS)
    .sort((a, b) => b[1].length - a[1].length)[0];
  const [id, texte] = plusLongue;
  assert.ok(texte.length > 400, `attendu un texte de test long, eu ${texte.length} car. (${id})`);

  const html = rendreLibelleAvecInfobulle(id);
  const attendu = echapper(texte).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  assert.match(html, new RegExp(attendu));
});

test("les contenus larges peuvent défiler au lieu de déborder", () => {
  const css = readFileSync(new URL("../css/style.css", import.meta.url), "utf8");
  // Un tableau ne se comprime pas : il lui faut un conteneur qui défile.
  assert.match(css, /\.tableau-enveloppe\s*\{[^}]*overflow-x:\s*auto/);
  // Les longues chaînes des barres de composantes ne doivent pas figer la
  // largeur : la piste a un minimum bas et peut rétrécir.
  assert.match(css, /\.composante-barre\s*\{[^}]*min-width:\s*\d+px/);
});

test("chaque page déclare le viewport mobile", () => {
  for (const page of ["site/index.html", "site/news.html", "site/debrief.html", "site/trading.html"]) {
    const html = readFileSync(new URL(`../../${page}`, import.meta.url), "utf8");
    assert.match(html, /name="viewport"[^>]*width=device-width/, `${page} sans viewport`);
  }
});

test("les deux thèmes définissent la palette complète", () => {
  const css = readFileSync(new URL("../css/style.css", import.meta.url), "utf8");
  const clair = css.match(/:root\s*\{([^}]*)\}/)[1];
  const sombre = css.match(/\[data-theme="sombre"\]\s*\{([^}]*)\}/)[1];

  const variables = (bloc) =>
    new Set([...bloc.matchAll(/(--[a-z-]+)\s*:/g)].map((m) => m[1]));
  const enClair = variables(clair);
  const enSombre = variables(sombre);

  // Toute couleur du thème clair doit avoir son équivalent sombre, sinon un
  // coin de page garderait l'ancienne teinte après la bascule.
  const couleurs = [...enClair].filter((v) => !["--rayon"].includes(v));
  for (const variable of couleurs) {
    assert.ok(enSombre.has(variable), `${variable} n'est pas redéfini en thème sombre`);
  }
});

// ---------------------------------------------------------------------------
// 4. Onglet Trading (scanner en direct)
// ---------------------------------------------------------------------------

/** Fabrique un signal /journal, avec une variante b2 résolue par défaut. */
function signalExemple(overrides = {}) {
  return {
    id: "sig-1",
    horodatage_detection_utc: "2026-08-15T10:00:00.000Z",
    timeframe_ob: "M15",
    ob_haut: 2999, ob_bas: 2995,
    sens: "haussier",
    timeframe_fvg: "M5",
    prix_entree: 3000, stop: 2994,
    horodatage_resolution_utc: "2026-08-15T12:00:00.000Z",
    texte_detection: "D'après la mécanique suivie, un achat aurait été détecté à 3000.00 $ " +
      "le 2026-08-15 10:00 UTC, sur un order block M15 [2995.00, 2999.00] $, confirmé par " +
      "un écart de valeur (FVG) en M5. Stop de la mécanique : 2994.00 $. Objectifs suivis : " +
      "structurel à 3020.00 $, 1:1,5 à 3009.00 $, 1:2 à 3012.00 $, 1:3 à 3018.00 $.",
    variantes: {
      a: { objectif: 3020, statut: "ouvert", prix_sortie: null, r: null, horodatage_resolution_utc: null, texte: null },
      b15: { objectif: 3009, statut: "ouvert", prix_sortie: null, r: null, horodatage_resolution_utc: null, texte: null },
      b2: {
        objectif: 3012, statut: "gagnant", prix_sortie: 3012, r: 2,
        horodatage_resolution_utc: "2026-08-15T12:00:00.000Z",
        texte: "La variante ratio 1:2 (B) du signal aurait atteint son objectif le " +
          "2026-08-15 12:00 UTC, sortie à 3012.00 $.",
      },
      b3: { objectif: 3018, statut: "sans_objectif", prix_sortie: null, r: null, horodatage_resolution_utc: null, texte: null },
      c: { objectif: 3010, statut: "ouvert", prix_sortie: null, r: null, horodatage_resolution_utc: null, texte: null },
    },
    paliers: [
      { rang: 1, zone: 3010, origine: "veille_haut", fraction: 0.5, ratio_risque: 1.67,
        statut: "ouvert", motif_sortie: null, prix_sortie: null, r: null, horodatage_resolution_utc: null },
      { rang: 2, zone: 3024.5, origine: "order_block", fraction: 0.5, ratio_risque: 4.08,
        statut: "ouvert", motif_sortie: null, prix_sortie: null, r: null, horodatage_resolution_utc: null },
    ],
    ...overrides,
  };
}

test("estResolue ne considère gagnant et perdant comme des issues, pas ouvert ni sans_objectif", () => {
  assert.equal(estResolue("gagnant"), true);
  assert.equal(estResolue("perdant"), true);
  assert.equal(estResolue("ouvert"), false);
  assert.equal(estResolue("sans_objectif"), false);
});

test("tonStatut et libelleStatut couvrent les quatre statuts possibles", () => {
  for (const statut of ["gagnant", "perdant", "ouvert", "sans_objectif"]) {
    assert.notEqual(tonStatut(statut), undefined);
    assert.notEqual(libelleStatut(statut), statut === "gagnant" ? undefined : null);
  }
});

test("agregerSignaux calcule taux de réussite, espérance et profit factor en R, pas en dollars", () => {
  const signaux = [
    signalExemple({ id: "s1" }), // b2 : gagnant, R=2
    signalExemple({
      id: "s2",
      variantes: {
        ...signalExemple().variantes,
        b2: { objectif: 3012, statut: "perdant", prix_sortie: 2994, r: -1, horodatage_resolution_utc: "2026-08-16T00:00:00.000Z", texte: "x" },
      },
    }),
  ];
  const m = agregerSignaux(signaux, "b2");
  assert.equal(m.n_trades, 2);
  assert.equal(m.n_gagnants, 1);
  assert.equal(m.n_perdants, 1);
  assert.equal(m.taux_reussite, 0.5);
  assert.equal(m.esperance_r, 0.5); // (2 + -1) / 2
  assert.equal(m.profit_factor, 2); // gains=2, pertes=|-1|=1
});

test("agregerSignaux ne confond jamais 'aucun trade' avec un taux de zéro", () => {
  const m = agregerSignaux([signalExemple()], "a"); // "a" reste ouvert dans le fixture
  assert.equal(m.n_trades, 0);
  assert.equal(m.taux_reussite, null);
  assert.equal(m.esperance_r, null);
  assert.equal(m.profit_factor, null);
});

test("agregerSignaux ne compte pas 'sans_objectif' comme un trade résolu", () => {
  const m = agregerSignaux([signalExemple()], "b3");
  assert.equal(m.n_trades, 0);
});

test("cleMois extrait l'année-mois d'un horodatage ISO", () => {
  assert.equal(cleMois("2026-08-15T12:00:00.000Z"), "2026-08");
  assert.equal(cleMois(null), null);
});

test("agregerParMois groupe et trie du mois le plus récent au plus ancien", () => {
  const signaux = [
    signalExemple({ id: "aout" }),
    signalExemple({
      id: "sept",
      variantes: {
        ...signalExemple().variantes,
        b2: { objectif: 3012, statut: "gagnant", prix_sortie: 3012, r: 2, horodatage_resolution_utc: "2026-09-01T00:00:00.000Z", texte: "x" },
      },
    }),
  ];
  const lignes = agregerParMois(signaux, "b2");
  assert.deepEqual(lignes.map((l) => l.mois), ["2026-09", "2026-08"]);
  assert.equal(lignes[0].n_trades, 1);
});

test("extraireMetriquesBacktest lit le rapport réel et reste dans les mêmes unités (R) que le scanner", () => {
  const synthese = rapport("reports/backtest/synthese.json");
  const m = extraireMetriquesBacktest(synthese, "b2");
  assert.equal(m.disponible, true);
  assert.ok(m.n_trades > 0);
  assert.ok(m.taux_reussite >= 0 && m.taux_reussite <= 1);
  assert.equal(typeof m.esperance_r, "number");
});

test("extraireMetriquesBacktest dit explicitement l'absence plutôt que de renvoyer des zéros", () => {
  const m = extraireMetriquesBacktest({ variantes: {} }, "b2");
  assert.equal(m.disponible, false);
  assert.ok(m.motif);
});

test("avertissementEchantillon avertit sous le seuil et se tait au-dessus", () => {
  assert.match(avertissementEchantillon(5, 30), /Échantillon réduit/);
  assert.equal(avertissementEchantillon(30, 30), "");
});

test("texteSurAudite laisse passer un texte propre et bloque une formulation de recommandation", () => {
  assert.equal(texteSurAudite("Le moteur aurait détecté un achat à 3000 $."), "Le moteur aurait détecté un achat à 3000 $.");
  assert.equal(texteSurAudite("Il faudrait acheter maintenant."), null);
  assert.equal(texteSurAudite(null), null);
});

test("rendreFilAlertes affiche un message explicite quand aucun signal n'existe", () => {
  assert.match(rendreFilAlertes([]), /fil-vide/);
});

test("rendreFilAlertes n'affiche jamais de lot ni de montant en dollars", () => {
  const html = rendreFilAlertes([signalExemple()]);
  assert.doesNotMatch(html, /\blots?\b/i);
  assert.doesNotMatch(html, /[$]\s*\d/, "aucun montant en dollars, seuls des prix de marché");
  assert.match(html, /aurait été détecté/);
  assert.match(html, /Objectif atteint/);
});

test("rendreFilAlertes ne montre pas le texte d'une variante toujours ouverte", () => {
  const html = rendreFilAlertes([signalExemple()]);
  assert.match(html, /En cours/); // badge de la variante "a", ouverte
});

test("rendreTableauBordMensuel distingue les variantes sans trade résolu, sans afficher un taux de zéro", () => {
  const html = rendreTableauBordMensuel([signalExemple()]);
  assert.match(html, /Aucun trade résolu pour cette variante/); // variantes a, b15, b3
  assert.match(html, /2026-08/); // le mois de résolution de b2
});

test("rendreComparaisonBacktest confronte scanner et backtest dans la même unité (R)", () => {
  const backtest = { disponible: true, donnees: rapport("reports/backtest/synthese.json") };
  const html = rendreComparaisonBacktest([signalExemple()], backtest);
  assert.match(html, /scanner en direct/);
  assert.match(html, /backtest 2025-2026/);
  assert.doesNotMatch(html, /[$]\s*\d/);
});

test("rendreComparaisonBacktest affiche le motif quand le rapport de backtest est indisponible", () => {
  const html = rendreComparaisonBacktest([signalExemple()], { disponible: false, motif: "hors ligne" });
  assert.match(html, /hors ligne/);
});

test("la page Trading porte l'avertissement permanent et ne recommande jamais un trade", () => {
  const html = readFileSync(new URL("../trading.html", import.meta.url), "utf8");
  assert.match(html, /ne recommande jamais/);
  assert.match(html, /PAXG/);
  assert.match(html, /multiple de risque/);
});

test("VARIANTES couvre les cinq variantes du setup order block et les trois du setup sweep", () => {
  assert.deepEqual(VARIANTES, ["a", "b15", "b2", "b3", "c", "s1", "s2", "s3"]);
  // Un signal n'affiche que les variantes de son setup, jamais celles de l'autre.
  assert.deepEqual(VARIANTES_PAR_SETUP.order_block, ["a", "b15", "b2", "b3", "c"]);
  assert.deepEqual(VARIANTES_PAR_SETUP.sweep, ["s1", "s2", "s3"]);
  assert.deepEqual([...VARIANTES_PAR_SETUP.order_block, ...VARIANTES_PAR_SETUP.sweep], VARIANTES);
});

test("les niveaux d'exécution s'affichent en liste, un prix par ligne, libellés par variante", () => {
  const signal = {
    setup: "order_block", prix_entree: 4401.88, stop: 4412.61,
    niveaux: [
      { cle: "entree", libelle: "Prix d'entrée", prix: 4401.88 },
      { cle: "stop", libelle: "Stop loss", prix: 4412.61 },
      { cle: "a", libelle: "TP1 (structurel)", prix: 4396.86 },
      { cle: "b15", libelle: "TP2 (1:1,5)", prix: 4385.79 },
    ],
  };
  const html = rendreNiveaux(signal);
  assert.match(html, /<dl class="trading-niveaux">/);
  assert.match(html, /<dt>Prix d&#39;entrée<\/dt>\s*<dd>4\s401,88 \$<\/dd>/);
  assert.match(html, /<dt>TP1 \(structurel\)<\/dt>/);
  assert.match(html, /<dt>TP2 \(1:1,5\)<\/dt>/);
  // Sans le libellé de variante, quatre nombres alignés ne veulent plus rien dire.
  assert.doesNotMatch(html, /<dt>TP1<\/dt>/);
  assert.doesNotMatch(html, /undefined/);
});

test("les niveaux se déduisent du signal quand la route ne les publie pas encore", () => {
  const signal = {
    setup: "order_block", prix_entree: 3000.0, stop: 2994.0,
    variantes: { a: { objectif: 3010.0 }, b15: { objectif: 3009.0 }, b2: { objectif: 3012.0 }, b3: { objectif: 3018.0 } },
  };
  const html = rendreNiveaux(signal);
  assert.match(html, /Prix d&#39;entrée<\/dt>\s*<dd>3\s000,00 \$/);
  assert.match(html, /TP3 \(1:2\)<\/dt>\s*<dd>3\s012,00 \$/);
});

test("une variante sans objectif affiche l'absence, jamais un zéro trompeur", () => {
  const html = rendreNiveaux({
    setup: "order_block", prix_entree: 3000.0, stop: 2994.0,
    niveaux: [
      { cle: "entree", libelle: "Prix d'entrée", prix: 3000.0 },
      { cle: "stop", libelle: "Stop loss", prix: 2994.0 },
      { cle: "a", libelle: "TP1 (structurel)", prix: null },
    ],
  });
  assert.match(html, /TP1 \(structurel\)<\/dt>\s*<dd>—<\/dd>/);
  // Le zéro trompeur serait sur la ligne du TP, pas sur celles de l'entrée
  // et du stop, qui valent bien 3 000,00 et 2 994,00 $.
  assert.doesNotMatch(html, /TP1 \(structurel\)<\/dt>\s*<dd>0,00/);
});

test("la carte d'un signal met le sens en évidence et sépare contexte et niveaux", () => {
  const signal = {
    id: "x", setup: "order_block", sens: "baissier", timeframe_ob: "M15",
    horodatage_detection_utc: "2026-09-10T08:33:00Z",
    texte_detection: "D'après la mécanique suivie, une vente aurait été détectée le 2026-09-10 08:33 UTC.",
    prix_entree: 4401.88, stop: 4412.61, paliers: [],
    niveaux: [{ cle: "entree", libelle: "Prix d'entrée", prix: 4401.88 }, { cle: "stop", libelle: "Stop loss", prix: 4412.61 }],
    variantes: { a: { statut: "ouvert", texte: null } },
  };
  const html = rendreFilAlertes([signal]);
  assert.match(html, /class="trading-sens trading-sens--vente">Vente</);
  assert.match(html, /class="trading-contexte"/);
  assert.match(html, /class="trading-niveaux"/);
  // Le contexte reste en prose, les prix n'y sont plus noyés.
  assert.doesNotMatch(html, /trading-contexte">[^<]*4 401,88/);
});

test("un signal sweep se présente comme un balayage, avec ses seules variantes", () => {
  const signal = {
    id: "s", setup: "sweep", sens: "haussier", timeframe_ob: "M15", niveau_unite: "M15", niveau_cote: "bas",
    horodatage_detection_utc: "2026-09-10T14:32:00Z", texte_detection: null, paliers: [],
    variantes: {
      s1: { statut: "gagnant", texte: null }, s2: { statut: "ouvert", texte: null }, s3: { statut: "ouvert", texte: null },
      a: { statut: "sans_objectif", texte: null },
    },
  };
  const html = rendreFilAlertes([signal]);
  assert.match(html, /balayage d'un ancien plus bas M15/);
  assert.match(html, /prise de liquidité \(sweep\)/);
  assert.match(html, /Sweep — sortie complète au 0,72/);
  assert.doesNotMatch(html, /Structurelle \(A\)/, "les variantes de l'order block ne s'affichent pas sur un sweep");
  assert.doesNotMatch(html, /undefined/);
});

test("le détail des paliers montre chaque tranche, pas seulement un total", () => {
  const html = rendrePaliers(signalExemple().paliers);
  assert.match(html, /haut de la veille/);
  assert.match(html, /order block encore actif/);
  assert.match(html, /1:1,7/);
  assert.match(html, /50 %/);
});

test("les origines de zone ne s'affichent jamais en identifiant brut", () => {
  const html = rendrePaliers(signalExemple().paliers);
  for (const brut of ["veille_haut", "order_block", "asie_bas"]) {
    assert.doesNotMatch(html, new RegExp(brut), `identifiant technique « ${brut} » affiché tel quel`);
  }
});

test("une tranche non dénouée affiche 'en cours', jamais un R de zéro trompeur", () => {
  const html = rendrePaliers(signalExemple().paliers);
  assert.match(html, /en cours/);
  assert.match(html, new RegExp(ABSENT));
});

test("une tranche dénouée affiche son motif de sortie traduit et son apport en R", () => {
  const paliers = [
    { rang: 1, zone: 3010, origine: "veille_haut", fraction: 0.5, ratio_risque: 1.67,
      statut: "gagnant", motif_sortie: "objectif", prix_sortie: 3010, r: 0.83,
      horodatage_resolution_utc: "2026-08-15T12:00:00.000Z" },
    { rang: 2, zone: 3024.5, origine: "order_block", fraction: 0.5, ratio_risque: 4.08,
      statut: "perdant", motif_sortie: "break_even", prix_sortie: 3000, r: 0,
      horodatage_resolution_utc: "2026-08-15T13:00:00.000Z" },
  ];
  const html = rendrePaliers(paliers);
  assert.match(html, /zone atteinte/);
  // L'apostrophe est échappée à l'insertion : c'est le comportement voulu.
  assert.match(html, /sorti au prix d&#39;entrée/);
  assert.match(html, /\+0,83 R/);
  assert.doesNotMatch(html, /break_even/);
});

test("un signal sans paliers ne rend rien plutôt qu'un tableau vide", () => {
  assert.equal(rendrePaliers([]), "");
  assert.equal(rendrePaliers(undefined), "");
});

test("le fil reste rendu même si un signal ancien ne porte pas toutes les variantes", () => {
  const ancien = signalExemple();
  delete ancien.variantes.c;
  const html = rendreFilAlertes([ancien]);
  assert.match(html, /Sortie par paliers \(C\)/);
  assert.match(html, /Sans objectif/);
});

// ---------------------------------------------------------------------------
// 5. Onglet Géopolitique — dossiers de conflits nommés
// ---------------------------------------------------------------------------
function dossierExemple(overrides = {}) {
  return {
    id: "israel_gaza",
    nom_affiche: "Israël - Gaza",
    mots_cles: ["Israël", "Gaza", "Hamas"],
    disponible: true,
    motif: "",
    intensite_ratio: 2.5,
    volume_24h: 800,
    trajectoire: "en accélération",
    anciennete_jours: 3,
    n_evenements_bilateraux: 4,
    motif_events: "",
    exemple_evenement: { code_evenement: "172", goldstein: -5.0, tonalite: -3.3, source_url: "https://exemple.test/ev" },
    developpements_recents: [
      { id: "abc123", titre: "Ceasefire talks resume", url: "https://exemple.test/a", source: "Reuters", horodatage_utc: "2026-08-30T10:00:00Z" },
    ],
    n_nouveaux_developpements: 1,
    nouveaux_developpements: [
      { id: "abc123", titre: "Ceasefire talks resume", url: "https://exemple.test/a", source: "Reuters", horodatage_utc: "2026-08-30T10:00:00Z" },
    ],
    etat_actuel: "Couverture à 2.5x sa moyenne, en accélération. 1 développement nouveau.",
    chaine_de_transmission: {
      maillons: {
        "2_petrole": { libelle: "Pétrole brut WTI (DCOILWTICO)", disponible: true, variation: 1.2, unite_variation: "%" },
        "5_or": { libelle: "Or au comptant", disponible: false, motif: "série absente" },
      },
      commentaire: "Chaîne rompue : 1/2 maillon(s) conformes. Ne suivent pas la direction attendue : Or au comptant (série absente).",
      chaine_rompue: true,
    },
    deja_dans_les_prix: { commentaire: "Non. Ni dossier installé ni prime élevée." },
    invalidation: "Un retour sous 1,0x viderait cette lecture de sa justification.",
    ...overrides,
  };
}

function etatGeoExemple(dossiers) {
  return {
    disponible: true,
    donnees: {
      geopolitique: {
        disponible: true, motif: "", source: "GDELT (DOC + Events) + FRED",
        n_dossiers_mesures: dossiers.length, n_dossiers_configures: dossiers.length,
        intensite_max: Math.max(...dossiers.map((d) => d.intensite_ratio || 0)),
        dossier_dominant: dossiers[0].id,
        dossiers,
        _meta: { source: "GDELT", age_jours: 0 },
      },
    },
  };
}

test("normaliserTexteGeo retire les accents et la ponctuation", () => {
  assert.equal(normaliserTexteGeo("Détroit d'Ormuz !"), "detroit d ormuz");
  assert.equal(normaliserTexteGeo(""), "");
  assert.equal(normaliserTexteGeo(null), "");
});

test("estLieAUnDossier détecte un mot-clé partagé, insensible à la casse et aux accents", () => {
  const dossiers = [{ mots_cles: ["Israël", "Gaza"] }];
  assert.ok(estLieAUnDossier("ISRAEL frappe un objectif", dossiers));
  assert.ok(estLieAUnDossier("Nouvelles tensions à Gaza", dossiers));
  assert.ok(!estLieAUnDossier("Résultats trimestriels d'une banque", dossiers));
});

test("rubriqueGeopolitique affiche un onglet par dossier plus un onglet Autres", () => {
  const etat = etatGeoExemple([dossierExemple(), dossierExemple({ id: "russie_ukraine", nom_affiche: "Russie - Ukraine", mots_cles: ["Russie", "Ukraine"] })]);
  const { corps } = rubriqueGeopolitique(etat.or ?? etat, []);
  assert.match(corps, /Israël - Gaza/);
  assert.match(corps, /Russie - Ukraine/);
  assert.match(corps, /Autres/);
  assert.match(corps, /geo-onglet/);
});

test("rubriqueGeopolitique ne fait apparaître aucun identifiant technique brut", () => {
  const etat = { disponible: true, donnees: etatGeoExemple([dossierExemple()]).donnees };
  const { corps } = rubriqueGeopolitique(etat, []);
  for (const brut of ["2_petrole", "3_inflation_anticipee", "4_taux_reels", "5_or"]) {
    assert.doesNotMatch(corps, new RegExp(`>${brut}<`), `identifiant technique brut affiché : ${brut}`);
  }
  assert.match(corps, /Pétrole brut WTI/);
  assert.match(corps, /Or au comptant/);
});

test("rubriqueGeopolitique signale un dossier indisponible avec son motif", () => {
  const dossier = dossierExemple({ disponible: false, motif: "GDELT indisponible pour ce dossier" });
  const etat = { disponible: true, donnees: etatGeoExemple([dossier]).donnees };
  const { corps } = rubriqueGeopolitique(etat, []);
  assert.match(corps, /GDELT indisponible pour ce dossier/);
});

test("un item reconnu par son chapô va dans l'onglet du dossier, pas dans Autres", () => {
  // Le moteur reconnaît désormais le titre ET le chapô ; il publie le
  // rattachement dans tickers_ou_themes_lies. Le site doit le suivre plutôt
  // que de refaire le test sur le seul titre.
  const dossiers = [{ id: "moyen_orient", nom_affiche: "Moyen-Orient (région)", mots_cles: ["Houthi", "mer Rouge"] }];
  const item = {
    titre_affiche: "Spokesperson to make a statement at 9 AM ET",
    tickers_ou_themes_lies: ["Moyen-Orient (région)"],
  };
  assert.ok(itemRelieAUnDossier(item, dossiers), "le rattachement du moteur doit primer");
  // Repli sur le titre pour un item publié avant ce rattachement.
  assert.ok(itemRelieAUnDossier({ titre_affiche: "Houthi statement" }, dossiers));
  assert.ok(!itemRelieAUnDossier({ titre_affiche: "Sommet économique", tickers_ou_themes_lies: ["Sanctions"] }, dossiers));
});

test("rubriqueGeopolitique répartit vers 'Autres' ce qui ne relève d'aucun dossier", () => {
  const etat = { disponible: true, donnees: etatGeoExemple([dossierExemple()]).donnees };
  const filGeopolitique = [
    { titre_affiche: "Nouvelle escalade à Gaza", url_source: "https://exemple.test/1", source_nom: "AP", horodatage_utc: "2026-08-30T09:00:00Z" },
    { titre_affiche: "Sommet économique en Amérique latine", url_source: "https://exemple.test/2", source_nom: "AFP", horodatage_utc: "2026-08-30T08:00:00Z" },
  ];
  const { corps } = rubriqueGeopolitique(etat, filGeopolitique);
  assert.match(corps, /Sommet économique en Amérique latine/);
});

test("rubriqueGeopolitique affiche l'intensité maximale et le dossier dominant en résumé", () => {
  const etat = { disponible: true, donnees: etatGeoExemple([dossierExemple()]).donnees };
  const { resume } = rubriqueGeopolitique(etat, []);
  assert.match(resume, /2,5×/);
  assert.match(resume, /Israël - Gaza/);
});

test("rubriqueGeopolitique reste indisponible explicite quand le bloc geopolitique est absent", () => {
  const { corps, resume } = rubriqueGeopolitique({ disponible: true, donnees: { geopolitique: { disponible: false, motif: "GDELT hors service" } } }, []);
  assert.match(corps, /GDELT hors service/);
  assert.match(resume, /indisponible/);
});

test("rubriqueGeopolitique affiche le classement et le statut de chaque dossier", () => {
  const donnees = etatGeoExemple([
    dossierExemple({ classement: { statut: "veille", rang: 2, donnees_suffisantes: true },
      pertinence: { disponible: true, score: 0.7, n_observations: 28, lecture: "inerte",
        commentaire: "Sur 28 séances communes, les 4 jours de pic voient les actifs bouger 0,7 fois plus." } }),
  ]).donnees;
  donnees.geopolitique.classement = [
    { rang: 1, nom: "Sanctions", statut: "actif", donnees_suffisantes: true, intensite_ratio: 1.8,
      pertinence: { disponible: true, score: 1.9, n_observations: 27, lecture: "réagit" } },
    { rang: 2, nom: "Israël - Gaza", id: "israel_gaza", statut: "veille", donnees_suffisantes: true, intensite_ratio: 1.1,
      pertinence: { disponible: true, score: 0.7, n_observations: 28, lecture: "inerte" } },
    { rang: 3, nom: "Yemen – Saudi Arabia", statut: "candidat", donnees_suffisantes: false, intensite_ratio: null,
      pertinence: { disponible: false, motif: "8 séance(s) commune(s) entre couverture et marché, 20 requises" } },
  ];
  const { corps } = rubriqueGeopolitique({ disponible: true, donnees }, []);
  assert.match(corps, /Classement des sujets/);
  assert.match(corps, /1\. Sanctions/);
  assert.match(corps, /Israël - Gaza · veille/, "le statut veille doit apparaître dans l'onglet");
  assert.match(corps, /données insuffisantes — 8 séance/, "l'insuffisance de données doit être écrite");
  assert.match(corps, /Statut dans le classement : <strong>veille<\/strong>/);
});

test("le classement affiche promotions, rétrogradations et durée d'inertie", () => {
  const donnees = etatGeoExemple([dossierExemple()]).donnees;
  donnees.geopolitique.classement = [
    { rang: 1, nom: "Sanctions", statut: "actif", donnees_suffisantes: true, intensite_ratio: 1.8,
      pertinence: { disponible: true, score: 1.9, n_observations: 60, lecture: "réagit" },
      historique: { changement: "promu", jours_consecutifs_statut: 1, inerte_depuis_jours: 0, jours_observes: 12 } },
    { rang: 2, nom: "Conflits majeurs", statut: "veille", donnees_suffisantes: true, intensite_ratio: 0.5,
      pertinence: { disponible: true, score: 0.6, n_observations: 60, lecture: "inerte" },
      historique: { changement: "stable", jours_consecutifs_statut: 15, inerte_depuis_jours: 15, tendance_score: "en baisse" } },
  ];
  const { corps } = rubriqueGeopolitique({ disponible: true, donnees }, []);
  assert.match(corps, /↑ promu/);
  assert.match(corps, /inerte depuis 15 jours de classement/);
  assert.match(corps, /score en baisse/);
  assert.doesNotMatch(corps, /undefined/);
});

test("l'onglet Autres liste les sujets significatifs du rapport et explique ses seuils", () => {
  const donnees = etatGeoExemple([dossierExemple()]).donnees;
  donnees.geopolitique.autres = [{
    type: "paire", libelle: "Yemen – Saudi Arabia", critere: "12 événements de conflit, 4,1 % de l'export",
    pertinence: { disponible: false, motif: "8 séance(s) commune(s), 20 requises" },
    articles: [{ titre: "Attaque contre une installation pétrolière", url: "https://exemple.test/y", source: "Reuters", horodatage_utc: "2026-09-11T08:00:00Z" }],
  }];
  donnees.geopolitique.criteres_autres = { intensite_min: 1.5 };
  const { corps } = rubriqueGeopolitique({ disponible: true, donnees }, []);
  assert.match(corps, /Yemen – Saudi Arabia/);
  assert.match(corps, /12 événements de conflit/);
  assert.match(corps, /Attaque contre une installation pétrolière/);
  assert.match(corps, /1,5×/);
});

test("l'onglet Autres vide dit ce qu'il mesure au lieu de laisser croire au calme mondial", () => {
  const donnees = etatGeoExemple([dossierExemple()]).donnees;
  donnees.geopolitique.autres = [];
  donnees.geopolitique.criteres_autres = { intensite_min: 1.5 };
  const { corps } = rubriqueGeopolitique({ disponible: true, donnees }, []);
  assert.match(corps, /ne signifie pas qu'il ne se passe rien ailleurs/);
  assert.doesNotMatch(corps, /undefined/);
});

// ---------------------------------------------------------------------------
// 6. Le rapport réellement publié doit rester affichable
// ---------------------------------------------------------------------------
//
// Ce bloc existe à cause d'un bug réel : le site a affiché
// « undefined/undefined dossiers mesurés » et un seul onglet vide, parce
// qu'il lisait une structure (dossiers) que le rapport publié ce jour-là ne
// portait pas encore. Le rendu doit rester lisible quelle que soit la
// version du rapport servi — c'est l'invariant, pas la présence des dossiers.
test("la rubrique Géopolitique n'affiche jamais « undefined » sur le rapport réel", () => {
  const or = { disponible: true, donnees: rapport("reports/gold/latest.json") };
  const { resume, corps } = rubriqueGeopolitique(or, []);
  assert.doesNotMatch(resume + corps, /undefined/,
    "un champ absent du rapport publié est rendu tel quel dans la page");
});

test("un rapport sans dossiers explique pourquoi au lieu de rendre une page vide", () => {
  // Forme d'avant les dossiers de conflits : uniquement des thèmes.
  const ancien = {
    disponible: true,
    donnees: {
      geopolitique: {
        disponible: true, horodatage_utc: "2026-09-10T15:12:13Z",
        n_themes_mesures: 4, n_themes_configures: 4, intensite_max: 0.9,
        themes: [], chaine_de_transmission: {}, deja_dans_les_prix: {},
      },
    },
  };
  const { resume, corps } = rubriqueGeopolitique(ancien, []);
  assert.doesNotMatch(resume + corps, /undefined/);
  assert.match(corps, /antérieur au suivi par dossiers/);
  assert.match(corps, /10\/09/, "la date du rapport en cause doit être citée");
});

test("un rapport avec dossiers rend bien un onglet par dossier", () => {
  const avec = {
    disponible: true,
    donnees: etatGeoExemple([
      dossierExemple(),
      dossierExemple({ id: "russie_ukraine", nom_affiche: "Russie - Ukraine" }),
    ]).donnees,
  };
  const { corps } = rubriqueGeopolitique(avec, []);
  const onglets = corps.match(/data-dossier="[^"]+"/g) || [];
  // Deux dossiers + « Autres », chacun présent en bouton et en panneau.
  assert.equal(onglets.length, 6, `attendu 3 onglets et 3 panneaux, eu ${onglets.length} marqueurs`);
  assert.match(corps, /data-dossier="israel_gaza"/);
  assert.match(corps, /data-dossier="russie_ukraine"/);
  assert.match(corps, /data-dossier="autres"/);
});

// ---------------------------------------------------------------------------
// 7. Contexte macro en prose
// ---------------------------------------------------------------------------
function contexteMacroExemple(overrides = {}) {
  return {
    disponible: true,
    motif: "",
    texte: "Inflation à 2,4 % sur un an (indice CPI à 322,1), proche de la cible. "
      + "Chômage à 4,3 %, en hausse de +0,4 point sur un an. "
      + "Appétit pour le risque neutre, sur 2 signal(aux) : VIX à 16.5, 37e percentile sur deux ans.",
    invalidation: "Cette lecture serait invalidée si : une inflation repassant le seuil de 3 % "
      + "(actuellement 2,4 %) changerait la contrainte qui pèse sur la Fed.",
    axes_indisponibles: [{ axe: "petrole", motif: "Série(s) requise(s) absente(s) : DCOILWTICO." }],
    dates_series: { CPIAUCSL: "2026-08-31", VIXCLS: "2026-09-09" },
    date_lecture: "2026-09-09",
    ...overrides,
  };
}

test("le contexte macro affiche sa prose, son invalidation et ses dates", () => {
  const html = rendreContexteMacro(contexteMacroExemple());
  assert.match(html, /Inflation à 2,4 %/);
  assert.match(html, /Cette lecture serait invalidée si/);
  assert.match(html, /CPIAUCSL au 2026-08-31/);
});

test("le contexte macro nomme les axes indisponibles, traduits", () => {
  const html = rendreContexteMacro(contexteMacroExemple());
  assert.match(html, /Pétrole/);
  assert.match(html, /DCOILWTICO/);
  assert.doesNotMatch(html, />petrole</, "identifiant technique brut affiché");
});

test("un contexte macro indisponible affiche son motif, pas un bloc vide", () => {
  const html = rendreContexteMacro({ disponible: false, motif: "FRED injoignable" });
  assert.match(html, /FRED injoignable/);
  assert.match(html, /indisponible/);
});

test("un rapport sans bloc contexte_macro ne casse pas la rubrique", () => {
  assert.equal(rendreContexteMacro(null), "");
  assert.equal(rendreContexteMacro(undefined), "");
});

test("le contexte macro s'affiche en tête de la rubrique, avant les dossiers", () => {
  const donnees = etatGeoExemple([dossierExemple()]).donnees;
  donnees.contexte_macro = contexteMacroExemple();
  const { corps } = rubriqueGeopolitique({ disponible: true, donnees }, []);
  const posMacro = corps.indexOf("Contexte macro et appétit");
  const posOnglets = corps.indexOf("geo-onglets");
  assert.ok(posMacro >= 0, "le contexte macro doit être rendu");
  assert.ok(posMacro < posOnglets, "le contexte macro doit précéder les onglets de dossiers");
});

test("le contexte macro reste affiché même quand la géopolitique est muette", () => {
  const donnees = {
    geopolitique: { disponible: false, motif: "GDELT hors service" },
    contexte_macro: contexteMacroExemple(),
  };
  const { corps } = rubriqueGeopolitique({ disponible: true, donnees }, []);
  assert.match(corps, /Inflation à 2,4 %/);
  assert.match(corps, /GDELT hors service/);
});

// ---------------------------------------------------------------------------
// 8. Aucun chiffre affiché sans son explication (audit généralisé)
// ---------------------------------------------------------------------------
//
// Onze métriques s'affichaient en chiffre brut, sans dire ce qu'elles
// mesurent ni dans quel sens les lire — « Couverture à 0,8× sa moyenne »
// en était l'exemple le plus visible. Ce test fige la correction.
test("les métriques auditées ont toutes une explication au dictionnaire", () => {
  const attendues = [
    "positionnement_cot", "score_composite", "conviction", "couverture_donnees",
    "intensite_couverture", "trajectoire_couverture", "evenements_bilateraux",
    "chaine_de_transmission", "correlation_positions", "mvrv", "part_offre_debloquee",
  ];
  for (const cle of attendues) {
    assert.ok(EXPLICATIONS[cle], `métrique « ${cle} » affichée sans explication`);
    assert.ok(EXPLICATIONS[cle].length > 80,
      `l'explication de « ${cle} » est trop courte pour expliquer quoi que ce soit`);
  }
});

test("chaque explication dit ce que la métrique mesure, pas seulement son nom", () => {
  // Une explication qui se contente de répéter le libellé n'explique rien.
  for (const [cle, texte] of Object.entries(EXPLICATIONS)) {
    assert.ok(texte.trim().length > 40, `explication trop courte pour « ${cle} »`);
    assert.notEqual(texte.trim().toLowerCase(), cle.replace(/_/g, " "),
      `l'explication de « ${cle} » ne fait que répéter son nom`);
  }
});

test("l'intensité de couverture géopolitique est affichée avec son explication", () => {
  const donnees = etatGeoExemple([dossierExemple()]).donnees;
  const { corps } = rubriqueGeopolitique({ disponible: true, donnees }, []);
  assert.match(corps, /Intensité de couverture/);
  assert.match(corps, /infobulle-bulle/, "l'explication doit être rendue à côté du chiffre");
  assert.match(corps, /Trajectoire/);
  assert.match(corps, /Chaîne de transmission vers l'or|Chaîne de transmission/);
});

// ---------------------------------------------------------------------------
// 9. Paragraphe de synthèse en tête de rubrique
// ---------------------------------------------------------------------------
test("une synthèse publiable s'affiche telle que le moteur l'a produite", () => {
  const html = rendreSynthese({
    publiable: true, texte: "L'or a varié de +0,8 % sur vingt séances.", motif: "",
  });
  assert.match(html, /\+0,8 %/);
  assert.doesNotMatch(html, /synthese--absente/);
});

test("une synthèse refusée affiche son motif, jamais un texte rafistolé", () => {
  const html = rendreSynthese({
    publiable: false, texte: "", motif: "paragraphe écarté : 42,7 sans correspondance",
  });
  assert.match(html, /sans correspondance/);
  assert.match(html, /synthese--absente/);
});

test("une rubrique sans bloc de synthèse ne casse pas", () => {
  assert.equal(rendreSynthese(null), "");
  assert.equal(rendreSynthese(undefined), "");
});

test("la synthèse est rendue en tête de la rubrique Or", () => {
  const or = {
    disponible: true,
    donnees: {
      ...rapport("reports/gold/latest.json"),
      synthese: { publiable: true, texte: "Phrase de synthèse du jour, 1,5 écart-type.", motif: "" },
    },
  };
  const { corps } = rubriqueOr(or);
  const posSynthese = corps.indexOf("Phrase de synthèse du jour");
  const posFraicheur = corps.indexOf("fraicheur");
  assert.ok(posSynthese >= 0, "la synthèse doit être rendue");
  assert.ok(posSynthese < posFraicheur, "la synthèse doit précéder les métriques");
});
