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
-- Appliquer ce schéma (base neuve) :
--   wrangler d1 execute scanner-or --file=worker-scanner/schema.sql
--   wrangler d1 execute scanner-or --file=worker-scanner/schema.sql --remote
-- Base existante (créée avant le setup sweep) : appliquer la migration
--   wrangler d1 execute scanner-or --file=worker-scanner/migrations/0002_sweep.sql --remote

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
  -- Prix du FVG qui a confirmé l'entrée : l'alerte donne son niveau, pas
  -- seulement son unité de temps (partie A1).
  fvg_haut REAL,
  fvg_bas REAL,
  tp_a REAL,
  tp_b15 REAL,
  tp_b2 REAL,
  tp_b3 REAL,
  -- Variante C : sortie par paliers. `tp_c` est la première zone visée ;
  -- le détail tranche par tranche vit dans la table `paliers`.
  tp_c REAL,
  -- 'ouvert' | 'gagnant' | 'perdant' | 'sans_objectif' (voir src/journal.js)
  statut_a TEXT NOT NULL DEFAULT 'ouvert',
  statut_b15 TEXT NOT NULL DEFAULT 'ouvert',
  statut_b2 TEXT NOT NULL DEFAULT 'ouvert',
  statut_b3 TEXT NOT NULL DEFAULT 'ouvert',
  statut_c TEXT NOT NULL DEFAULT 'ouvert',
  resultat_a_usd REAL,
  resultat_b15_usd REAL,
  resultat_b2_usd REAL,
  resultat_b3_usd REAL,
  resultat_c_usd REAL,
  lots REAL NOT NULL,
  -- Prix de sortie : un fait de marché (contrairement à resultat_*_usd et
  -- lots, qui dépendent de la taille de position). C'est le seul des deux
  -- que l'onglet Trading du site expose (voir route /journal dans
  -- index.js) : le site ne publie jamais de donnée de compte, de position
  -- ou de montant (règle du site, voir site/js/rendu.js::MOTIFS_INTERDITS).
  -- Le site calcule un multiple de risque (R) à partir de ce prix, du prix
  -- d'entrée et du stop — comparable à la colonne resultat_r du backtest,
  -- sans jamais faire transiter de somme en dollars ni de taille de lot.
  prix_sortie_a REAL,
  prix_sortie_b15 REAL,
  prix_sortie_b2 REAL,
  prix_sortie_b3 REAL,
  prix_sortie_c REAL,
  horodatage_resolution_a INTEGER,
  horodatage_resolution_b15 INTEGER,
  horodatage_resolution_b2 INTEGER,
  horodatage_resolution_b3 INTEGER,
  horodatage_resolution_c INTEGER,
  -- Posé une fois que toutes les variantes ne sont plus 'ouvert'.
  horodatage_resolution INTEGER,
  -- Setup d'origine du signal : 'order_block' ou 'sweep'. Pour un sweep,
  -- timeframe_ob porte l'unité du niveau balayé et ob_haut = ob_bas = le
  -- niveau lui-même (les colonnes sont NOT NULL et antérieures au second
  -- setup) ; les colonnes niveau_* et sweep_* portent le détail.
  setup TEXT NOT NULL DEFAULT 'order_block',
  niveau_prix REAL,
  -- 'haut' (ancien plus haut balayé, vente) ou 'bas' (ancien plus bas, achat).
  niveau_cote TEXT,
  niveau_unite TEXT,
  -- Ouverture de la bougie pivot : « quand le niveau avait été formé ».
  niveau_formation INTEGER,
  sweep_extreme REAL,
  reference_prix REAL,
  unite_fibo TEXT,
  -- Variantes du setup sweep (voir src/moteur.js::VARIANTES_SWEEP).
  tp_s1 REAL,
  tp_s2 REAL,
  tp_s3 REAL,
  statut_s1 TEXT NOT NULL DEFAULT 'sans_objectif',
  statut_s2 TEXT NOT NULL DEFAULT 'sans_objectif',
  statut_s3 TEXT NOT NULL DEFAULT 'sans_objectif',
  resultat_s1_usd REAL,
  resultat_s2_usd REAL,
  resultat_s3_usd REAL,
  prix_sortie_s1 REAL,
  prix_sortie_s2 REAL,
  prix_sortie_s3 REAL,
  horodatage_resolution_s1 INTEGER,
  horodatage_resolution_s2 INTEGER,
  horodatage_resolution_s3 INTEGER
);

-- Détail des tranches de la variante C, une ligne par tranche.
--
-- Une table à part plutôt que des colonnes numérotées dans `journal` : le
-- nombre de zones varie d'un signal à l'autre (une à trois), et
-- l'utilisateur doit pouvoir lire le détail palier par palier autant que le
-- total. `zone` et `origine` disent quelle liquidité était visée,
-- `ratio_risque` ce que valait la cible au moment de l'entrée.
CREATE TABLE IF NOT EXISTS paliers (
  id_signal TEXT NOT NULL,
  rang INTEGER NOT NULL,
  zone REAL NOT NULL,
  -- 'veille_haut' | 'veille_bas' | 'asie_haut' | 'asie_bas' | 'order_block'.
  -- Identifiant technique : traduit avant tout affichage, jamais rendu brut.
  origine TEXT NOT NULL,
  fraction REAL NOT NULL,
  ratio_risque REAL NOT NULL,
  statut TEXT NOT NULL DEFAULT 'ouvert',
  prix_sortie REAL,
  resultat_usd REAL,
  -- 'objectif' | 'stop' | 'break_even'
  motif_sortie TEXT,
  horodatage_resolution INTEGER,
  -- 'c' (order block, sortie par paliers) ou 's2' (sweep, 0,72 puis structurel).
  variante TEXT NOT NULL DEFAULT 'c',
  PRIMARY KEY (id_signal, rang)
);

CREATE INDEX IF NOT EXISTS idx_paliers_signal ON paliers (id_signal);

CREATE INDEX IF NOT EXISTS idx_journal_horodatage ON journal (horodatage_detection);
CREATE INDEX IF NOT EXISTS idx_journal_timeframe_ob ON journal (timeframe_ob);

CREATE TABLE IF NOT EXISTS taux_eurusd (
  jour TEXT PRIMARY KEY,
  taux REAL NOT NULL,
  recupere_le INTEGER NOT NULL
);
