/**
 * Adaptateur D1 minimal, adossé à node:sqlite, pour tester journal.js sans
 * compte Cloudflare.
 *
 * D1 est lui-même construit sur SQLite : les requêtes de schema.sql et de
 * journal.js sont du SQLite valide, que node:sqlite (disponible nativement
 * depuis Node 22.5, stable ici) exécute réellement — un vrai moteur SQL,
 * pas une resimulation approximative des quelques requêtes utilisées.
 * Seule la forme de l'API (`prepare().bind().first()/.run()`) est adaptée à
 * ce que journal.js attend d'un D1Database.
 */

import { DatabaseSync } from "node:sqlite";

/**
 * Crée une base D1 factice en mémoire, avec le schéma déjà appliqué.
 *
 * @param {string} schemaSql Contenu de schema.sql.
 * @returns {object} Objet compatible avec la forme de D1Database utilisée
 *   par journal.js : `prepare(sql).bind(...args).run()/.first()`.
 */
export function creerD1Test(schemaSql) {
  const db = new DatabaseSync(":memory:");
  db.exec(schemaSql);

  return {
    prepare(sql) {
      const stmt = db.prepare(sql);
      return {
        bind(...args) {
          return {
            async run() {
              return stmt.run(...args);
            },
            async first() {
              const ligne = stmt.get(...args);
              return ligne === undefined ? null : ligne;
            },
            async all() {
              return { results: stmt.all(...args) };
            },
          };
        },
        async run() {
          return stmt.run();
        },
        async first() {
          const ligne = stmt.get();
          return ligne === undefined ? null : ligne;
        },
        async all() {
          return { results: stmt.all() };
        },
      };
    },
  };
}
