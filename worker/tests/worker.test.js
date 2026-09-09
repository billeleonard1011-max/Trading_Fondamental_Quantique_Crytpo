/**
 * Tests du Worker, sans compte Cloudflare ni appel réseau réel.
 *
 * `env.LIMITEUR` est simulé par une classe en mémoire qui reproduit la
 * seule partie de l'API KV utilisée (`get`, `put` avec `expirationTtl`).
 * `fetch` global est remplacé pour intercepter l'appel à OpenAI : aucune
 * clé, aucun réseau, mais la logique de construction de la requête, de la
 * limitation de débit et des erreurs est bien celle qui tournera en
 * production.
 *
 * Exécution :
 *     node --test worker/tests/
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import worker from "../src/index.js";

/** KV en mémoire, avec expiration réellement appliquée par horodatage. */
class KvFactice {
  constructor() {
    this.magasin = new Map();
  }
  async get(cle) {
    const entree = this.magasin.get(cle);
    if (!entree) return null;
    if (entree.expire && entree.expire < Date.now()) {
      this.magasin.delete(cle);
      return null;
    }
    return entree.valeur;
  }
  async put(cle, valeur, options = {}) {
    const expire = options.expirationTtl ? Date.now() + options.expirationTtl * 1000 : null;
    this.magasin.set(cle, { valeur, expire });
  }
}

/** Fabrique une requête POST minimale. */
function requetePost(corps, adresse = "203.0.113.1") {
  return new Request("https://worker.test/", {
    method: "POST",
    headers: { "Content-Type": "application/json", "CF-Connecting-IP": adresse },
    body: JSON.stringify(corps),
  });
}

/** Environnement de base pour les tests. */
function env(surcharge = {}) {
  return { OPENAI_API_KEY: "sk-test-factice", LIMITEUR: new KvFactice(), ...surcharge };
}

/** Intercepte le prochain appel à fetch (celui vers OpenAI). */
function simulerOpenAI(reponseTexte, statut = 200) {
  const original = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    assert.match(String(url), /api\.openai\.com/);
    const corps = JSON.parse(options.body);
    assert.equal(corps.messages[0].role, "system");
    assert.match(corps.messages[0].content, /Ne recommande JAMAIS/);
    return {
      ok: statut < 400,
      status: statut,
      json: async () => ({ choices: [{ message: { content: reponseTexte } }] }),
    };
  };
  return () => {
    globalThis.fetch = original;
  };
}

test("sans clé configurée, le Worker répond une erreur claire, pas un plantage", async () => {
  const reponse = await worker.fetch(
    requetePost({ question: "Q", contexte: {} }),
    env({ OPENAI_API_KEY: undefined }),
  );
  assert.equal(reponse.status, 500);
  const charge = await reponse.json();
  assert.match(charge.erreur, /worker\/README\.md/);
});

test("une question sans contexte reçoit une réponse", async () => {
  const retablir = simulerOpenAI("Le MVRV rapporte la capitalisation de marché à la capitalisation réalisée.");
  try {
    const reponse = await worker.fetch(
      requetePost({ question: "Que mesure le MVRV ?", contexte: { mvrv: 1.49 } }),
      env(),
    );
    assert.equal(reponse.status, 200);
    const charge = await reponse.json();
    assert.match(charge.reponse, /MVRV/);
    assert.equal(charge.limitation_active, true);
  } finally {
    retablir();
  }
});

test("la limitation de débit bloque après le seuil, par adresse", async () => {
  const retablir = simulerOpenAI("Réponse.");
  try {
    const environnement = env();
    const adresse = "198.51.100.7";
    let derniere;
    for (let i = 0; i < 6; i += 1) {
      derniere = await worker.fetch(requetePost({ question: "Q" }, adresse), environnement);
      assert.equal(derniere.status, 200, `Requête ${i + 1}/6 devait passer`);
    }
    const septieme = await worker.fetch(requetePost({ question: "Q" }, adresse), environnement);
    assert.equal(septieme.status, 429);
    const charge = await septieme.json();
    assert.match(charge.erreur, /Trop de questions/);
    assert.ok(septieme.headers.get("Retry-After"));
  } finally {
    retablir();
  }
});

test("deux adresses différentes ont chacune leur propre quota", async () => {
  const retablir = simulerOpenAI("Réponse.");
  try {
    const environnement = env();
    for (let i = 0; i < 6; i += 1) {
      await worker.fetch(requetePost({ question: "Q" }, "10.0.0.1"), environnement);
    }
    const bloquee = await worker.fetch(requetePost({ question: "Q" }, "10.0.0.1"), environnement);
    assert.equal(bloquee.status, 429);

    // Une autre adresse n'est pas affectée par le quota de la première.
    const autre = await worker.fetch(requetePost({ question: "Q" }, "10.0.0.2"), environnement);
    assert.equal(autre.status, 200);
  } finally {
    retablir();
  }
});

test("sans espace KV, le Worker fonctionne mais le signale", async () => {
  const retablir = simulerOpenAI("Réponse.");
  try {
    const reponse = await worker.fetch(
      requetePost({ question: "Q" }),
      env({ LIMITEUR: undefined }),
    );
    assert.equal(reponse.status, 200);
    const charge = await reponse.json();
    assert.equal(charge.limitation_active, false);
  } finally {
    retablir();
  }
});

test("une question vide est refusée avant l'appel à OpenAI", async () => {
  let appele = false;
  const original = globalThis.fetch;
  globalThis.fetch = async () => {
    appele = true;
    throw new Error("ne devrait pas être appelé");
  };
  try {
    const reponse = await worker.fetch(requetePost({ question: "   " }), env());
    assert.equal(reponse.status, 400);
    assert.equal(appele, false);
  } finally {
    globalThis.fetch = original;
  }
});

test("un corps illisible ne fait pas planter le Worker", async () => {
  const requete = new Request("https://worker.test/", {
    method: "POST",
    headers: { "Content-Type": "application/json", "CF-Connecting-IP": "1.2.3.4" },
    body: "{ ceci n'est pas du JSON",
  });
  const reponse = await worker.fetch(requete, env());
  assert.equal(reponse.status, 400);
});

test("un contexte trop volumineux est refusé", async () => {
  const enorme = "x".repeat(300_000);
  const reponse = await worker.fetch(
    requetePost({ question: "Q", contexte: { blob: enorme } }, "9.9.9.9"),
    env(),
  );
  assert.equal(reponse.status, 413);
});

test("une méthode autre que POST est refusée", async () => {
  const reponse = await worker.fetch(new Request("https://worker.test/", { method: "GET" }), env());
  assert.equal(reponse.status, 405);
});

test("les requêtes de préflight CORS reçoivent une réponse vide et les bons en-têtes", async () => {
  const reponse = await worker.fetch(
    new Request("https://worker.test/", { method: "OPTIONS" }),
    env(),
  );
  assert.equal(reponse.status, 204);
  assert.equal(reponse.headers.get("Access-Control-Allow-Methods"), "POST, OPTIONS");
});

test("une erreur amont d'OpenAI ne relaie pas le détail au navigateur", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => ({
    ok: false,
    status: 401,
    json: async () => ({ error: { message: "Incorrect API key provided: sk-secret-detail" } }),
  });
  try {
    const reponse = await worker.fetch(requetePost({ question: "Q" }, "5.5.5.5"), env());
    const charge = await reponse.json();
    assert.equal(reponse.status, 502);
    assert.doesNotMatch(charge.erreur, /sk-secret-detail/);
  } finally {
    globalThis.fetch = original;
  }
});

test("aucune clé n'apparaît dans le code source du Worker", async () => {
  const { readFileSync } = await import("node:fs");
  const contenu = readFileSync(new URL("../src/index.js", import.meta.url), "utf8");
  assert.doesNotMatch(contenu, /sk-[A-Za-z0-9]{16,}/);
  assert.match(contenu, /env\.OPENAI_API_KEY/, "La clé doit venir de l'environnement, jamais du code.");
});
