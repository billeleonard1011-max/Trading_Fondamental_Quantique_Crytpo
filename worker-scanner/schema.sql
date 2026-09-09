-- Schéma D1 du scanner en direct.
--
-- Trois tables :
--   etat_moteur   une seule ligne, l'état complet du moteur entre deux
--                 exécutions (zones actives, setups en attente, position
--                 ouverte, fenêtre glissante de bougies M1), en JSON. Ce
--                 n'est pas une donnée qu'on interroge en SQL : voir
--                 src/journal.js.
--   journal       une ligne par signal détecté, avec les quatre variantes
--                 de TP en colonnes parallèles (voir Partie 3 du prompt).
--                 C'est la table qu'interroge l'onglet Trading du site.
--   taux_eurusd   cache du taux de change quotidien (une ligne par jour),
--                 pour éviter un appel Frankfurter à chaque exécution.
--
-- Appliquer ce schéma :
--   wrangler d1 execute scanner-or --file=worker-scanner/schema.sql
--   wrangler d1 execute scanner-or --file=worker-scanner/schema.sql --remote

CREATE TABLE IF NOT EXISTS etat_moteur (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  donnees TEXT NOT NULL,
  mise_a_jour INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS journal (
  id TEXT PRIMARY KEY,
  horodatage_detection INTEGER NOT NULL,
  timeframe_ob TEXT NOT NULL,
  ob_haut REAL NOT NULL,
  ob_bas REAL NOT NULL,
  sens TEXT NOT NULL,
  timeframe_fvg TEXT NOT NULL,
  prix_entree REAL NOT NULL,
  sl REAL NOT NULL,
  tp_a REAL,
  tp_b15 REAL,
  tp_b2 REAL,
  tp_b3 REAL,
  -- 'ouvert' | 'gagnant' | 'perdant' | 'sans_objectif' (voir src/journal.js)
  statut_a TEXT NOT NULL DEFAULT 'ouvert',
  statut_b15 TEXT NOT NULL DEFAULT 'ouvert',
  statut_b2 TEXT NOT NULL DEFAULT 'ouvert',
  statut_b3 TEXT NOT NULL DEFAULT 'ouvert',
  resultat_a_usd REAL,
  resultat_b15_usd REAL,
  resultat_b2_usd REAL,
  resultat_b3_usd REAL,
  lots REAL NOT NULL,
  horodatage_resolution_a INTEGER,
  horodatage_resolution_b15 INTEGER,
  horodatage_resolution_b2 INTEGER,
  horodatage_resolution_b3 INTEGER,
  -- Posé une fois que les quatre variantes ne sont plus 'ouvert'.
  horodatage_resolution INTEGER
);

CREATE INDEX IF NOT EXISTS idx_journal_horodatage ON journal (horodatage_detection);
CREATE INDEX IF NOT EXISTS idx_journal_timeframe_ob ON journal (timeframe_ob);

CREATE TABLE IF NOT EXISTS taux_eurusd (
  jour TEXT PRIMARY KEY,
  taux REAL NOT NULL,
  recupere_le INTEGER NOT NULL
);
