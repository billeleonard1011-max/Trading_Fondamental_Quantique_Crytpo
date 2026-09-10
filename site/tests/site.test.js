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
  rubriqueCrypto, rubriqueGeopolitique, rubriqueOr, rubriqueQuantique,
} from "../js/rubriques.js";
import { filtrer, rendreFil, rendreVide } from "../js/fil.js";
import { construireContexte, demander, suggestions } from "../js/assistant.js";
import { evaluerFiabilite, rendreFiabilite } from "../js/debrief.js";
import { rendreDetail } from "../js/news.js";
import { EXPLICATIONS } from "../js/libelles.js";
import {
  agregerParMois, agregerSignaux, avertissementEchantillon, cleMois, estResolue,
  extraireMetriquesBacktest, libelleStatut, rendreComparaisonBacktest, rendreFilAlertes,
  rendrePaliers, rendreTableauBordMensuel, texteSurAudite, tonStatut, VARIANTES,
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

test("VARIANTES couvre exactement les cinq variantes du backtest", () => {
  assert.deepEqual(VARIANTES, ["a", "b15", "b2", "b3", "c"]);
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
