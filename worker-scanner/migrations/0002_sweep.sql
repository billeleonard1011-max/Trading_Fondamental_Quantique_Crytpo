-- Migration : second setup (prise de liquidité, sweep).
--
-- À appliquer sur une base créée avant ce setup :
--   wrangler d1 execute scanner-or --file=worker-scanner/migrations/0002_sweep.sql --remote
-- Une base neuve n'en a pas besoin : schema.sql porte déjà ces colonnes.
-- SQLite ne sait pas ajouter une colonne « si elle n'existe pas » : rejouer
-- cette migration sur une base déjà migrée échoue proprement à la première
-- ligne (colonne dupliquée) sans rien modifier.

ALTER TABLE journal ADD COLUMN setup TEXT NOT NULL DEFAULT 'order_block';
ALTER TABLE journal ADD COLUMN niveau_prix REAL;
ALTER TABLE journal ADD COLUMN niveau_cote TEXT;
ALTER TABLE journal ADD COLUMN niveau_unite TEXT;
ALTER TABLE journal ADD COLUMN niveau_formation INTEGER;
ALTER TABLE journal ADD COLUMN sweep_extreme REAL;
ALTER TABLE journal ADD COLUMN reference_prix REAL;
ALTER TABLE journal ADD COLUMN unite_fibo TEXT;
ALTER TABLE journal ADD COLUMN tp_s1 REAL;
ALTER TABLE journal ADD COLUMN tp_s2 REAL;
ALTER TABLE journal ADD COLUMN tp_s3 REAL;
ALTER TABLE journal ADD COLUMN statut_s1 TEXT NOT NULL DEFAULT 'sans_objectif';
ALTER TABLE journal ADD COLUMN statut_s2 TEXT NOT NULL DEFAULT 'sans_objectif';
ALTER TABLE journal ADD COLUMN statut_s3 TEXT NOT NULL DEFAULT 'sans_objectif';
ALTER TABLE journal ADD COLUMN resultat_s1_usd REAL;
ALTER TABLE journal ADD COLUMN resultat_s2_usd REAL;
ALTER TABLE journal ADD COLUMN resultat_s3_usd REAL;
ALTER TABLE journal ADD COLUMN prix_sortie_s1 REAL;
ALTER TABLE journal ADD COLUMN prix_sortie_s2 REAL;
ALTER TABLE journal ADD COLUMN prix_sortie_s3 REAL;
ALTER TABLE journal ADD COLUMN horodatage_resolution_s1 INTEGER;
ALTER TABLE journal ADD COLUMN horodatage_resolution_s2 INTEGER;
ALTER TABLE journal ADD COLUMN horodatage_resolution_s3 INTEGER;
ALTER TABLE paliers ADD COLUMN variante TEXT NOT NULL DEFAULT 'c';
