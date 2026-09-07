# Structure du JSON produit par le moteur or

Fichiers écrits par `python -m modules.gold.run` :

- `reports/gold/AAAA-MM-JJ.json` — le rapport du jour, conservé ;
- `reports/gold/latest.json` — copie du plus récent, adresse stable pour la
  page web et l'indicateur TradingView ;
- `reports/gold/historique_biais.jsonl` — une ligne par jour, pour noter le
  biais rétrospectivement.

## Ce qu'il faut lire en premier

Trois champs conditionnent la lecture de tous les autres. Les ignorer revient
à lire des chiffres sans savoir s'ils veulent dire quelque chose.

| Champ | Pourquoi il passe avant le reste |
|---|---|
| `juste_valeur.fiable` | À `false`, le R² du modèle est passé sous son seuil : le `z_score` existe encore mais n'est plus interprétable, et la composante correspondante du biais est désactivée. |
| `biais.donnees_partielles` | À `true`, une ou plusieurs sources étaient muettes. `biais.composantes_indisponibles` dit lesquelles, `biais.couverture_donnees` de combien. |
| `<bloc>._meta.age_jours` | L'âge de chaque donnée. Le COT a systématiquement trois jours ou plus ; l'afficher à côté d'un prix de la minute serait trompeur. |

Chaque bloc porte par ailleurs `disponible` et, quand il vaut `false`, un
`motif` en français qui dit ce qui a manqué. Aucun bloc n'est jamais rempli
par une valeur de remplacement silencieuse.

## Conventions

- **Unités.** `ecart_usd` est en dollars par once tant que
  `prix.unite_prix` vaut `USD par once`. En cas de repli sur l'ETF GLD, ce
  champ le signale et les écarts ne sont plus des dollars par once.
- **Taux.** Les variations de taux sont en **points de base**
  (`unite_variation`), jamais en pourcentage : passer de 0,10 % à 0,20 %
  n'est pas « +100 % ».
- **Heures.** `heure_conventionnelle` vaut toujours `true` dans le
  calendrier : FRED donne la date de publication, jamais l'heure.
- **Approximations.** `est_approximation` à `true` signale une valeur qui
  n'est pas la grandeur demandée mais un substitut ; `limite` dit laquelle.

## Arbre complet

```
meta:
  date: texte
  horodatage_utc: texte
  instrument: texte
  version_moteur: texte
  sources_en_echec: []
  donnees_partielles: booleen
  avertissement: texte
prix:
  disponible: booleen
  motif: texte
  prix: nombre
  unite_prix: texte
  variation_5j_pct: nombre
  variation_20j_pct: nombre
  _meta:
    source: texte
    horodatage_collecte_utc: texte
    date_donnee: texte
    age_jours: entier
juste_valeur:
  date: texte
  disponible: booleen
  motif: texte
  prix_observe: nombre
  prix_theorique: nombre
  ecart_usd: nombre
  ecart_pct: nombre
  z_score: nombre
  percentile_historique: nombre
  r2: nombre
  fiable: booleen
  seuil_r2: nombre
  n_observations: entier
  coefficients:
    constante: nombre
    taux_reel: nombre
    dollar: nombre
  contributions:
    taux_reel:
      coefficient: nombre
      valeur_courante: nombre
      contribution_log: nombre
      effet_pct: nombre
      effet_usd: nombre
    dollar:
      coefficient: nombre
      valeur_courante: nombre
      contribution_log: nombre
      effet_pct: nombre
      effet_usd: nombre
  facteur_dominant: texte
  lecture: texte
  unite_prix: texte
  _meta:
    source: texte
    horodatage_collecte_utc: texte
    date_donnee: texte
    age_jours: entier
positionnement_cot:
  disponible: booleen
  motif: texte
  source: texte
  date_observation: texte
  date_publication_estimee: texte
  age_jours: entier
  net_managed_money: entier
  net_producers: entier
  open_interest: entier
  percentile_managed_money: nombre
  n_semaines_percentile: entier
  variation_hebdo_managed_money: entier
  lecture: texte
  _meta:
    source: texte
    horodatage_collecte_utc: texte
    date_donnee: texte
    age_jours: entier
flux:
  _meta:
    source: texte
    horodatage_collecte_utc: texte
    date_donnee: null
    age_jours: null
  encours_gld:
    nom: texte
    disponible: booleen
    motif: texte
    valeur: nombre
    unite: texte
    date_valeur: texte
    percentile: nombre
    variation_pct: nombre
    tendance: texte
    source: texte
    est_approximation: booleen
    limite: texte
    details:
      n_seances: entier
  ratio_or_argent:
    nom: texte
    disponible: booleen
    motif: texte
    valeur: nombre
    unite: texte
    date_valeur: texte
    percentile: nombre
    variation_pct: nombre
    tendance: texte
    source: texte
    est_approximation: booleen
    limite: texte
    details:
      n_seances: entier
  ratio_gdx_or:
    nom: texte
    disponible: booleen
    motif: texte
    valeur: nombre
    unite: texte
    date_valeur: texte
    percentile: nombre
    variation_pct: nombre
    tendance: texte
    source: texte
    est_approximation: booleen
    limite: texte
    details:
      n_seances: entier
calendrier:
  disponible: booleen
  horodatage_calcul_utc: texte
  fenetre_silence_minutes: entier
  en_fenetre_de_silence: booleen
  commentaire: texte
  avertissement_heures: texte
  echeances:
    [6 elements, chacun :]
      nom: texte
      horodatage_utc: texte
      date: texte
      heure_locale_new_york: texte
      minutes_restantes: entier
      jours_restants: nombre
      heure_conventionnelle: booleen
      impact_or: texte
      source: texte
  _meta:
    source: texte
    horodatage_collecte_utc: texte
    date_donnee: texte
    age_jours: entier
geopolitique:
  disponible: booleen
  motif: texte
  horodatage_utc: texte
  source: texte
  n_themes_mesures: entier
  n_themes_configures: entier
  intensite_max: nombre
  theme_dominant: texte
  themes:
    [4 elements, chacun :]
      nom: texte
      disponible: booleen
      motif: texte
      intensite_ratio: nombre
      volume_24h: nombre
      alerte: booleen
      trajectoire: texte
      ratio_trajectoire: nombre
      anciennete_jours: entier
  chaine_de_transmission:
    horizon_seances: entier
    maillons:
      1_evenement:
        libelle: texte
        disponible: booleen
        motif: texte
        valeur: nombre
        unite_variation: texte
        variation: null
      2_petrole:
        libelle: texte
        disponible: booleen
        motif: texte
        valeur: nombre
        valeur_precedente: nombre
        variation: nombre
        unite_variation: texte
        date: texte
      3_inflation_anticipee:
        libelle: texte
        disponible: booleen
        motif: texte
        valeur: nombre
        valeur_precedente: nombre
        variation: nombre
        unite_variation: texte
        date: texte
      4_taux_reels:
        libelle: texte
        disponible: booleen
        motif: texte
        valeur: nombre
        valeur_precedente: nombre
        variation: nombre
        unite_variation: texte
        date: texte
      5_or:
        libelle: texte
        disponible: booleen
        motif: texte
        valeur: nombre
        valeur_precedente: nombre
        variation: nombre
        unite_variation: texte
        date: texte
    n_maillons_mesures: entier
    n_maillons_conformes: entier
    chaine_rompue: booleen
    commentaire: texte
  deja_dans_les_prix:
    valeur: booleen
    disponible: booleen
    motif: texte
    z_score_prime: nombre
    seuil_prime: nombre
    anciennete_max_jours: entier
    themes_installes: []
    commentaire: texte
  _meta:
    source: texte
    horodatage_collecte_utc: texte
    date_donnee: texte
    age_jours: entier
analogues:
  disponible: booleen
  motif: texte
  n_cas: entier
  variables_utilisees:
    [3 elements, chacun :]
      str
  separation_min_jours: entier
  min_cas: entier
  periode_base:
    debut: texte
    fin: texte
    n_configurations: entier
  precedents:
    [15 elements, chacun :]
      date: texte
      distance: nombre
      etat:
        z_fair_value: nombre
        percentile_cot: nombre
        regime_taux_reels: nombre
      rendements_pct:
        1: nombre
        5: nombre
        20: nombre
      drawdown_max_pct: nombre
  agregation:
    1j:
      disponible: booleen
      n_cas: entier
      rendement_median_pct: nombre
      rendement_moyen_pct: nombre
      proportion_haussiers: nombre
      pire_pct: nombre
      meilleur_pct: nombre
      etendue_pct: nombre
    5j:
      disponible: booleen
      n_cas: entier
      rendement_median_pct: nombre
      rendement_moyen_pct: nombre
      proportion_haussiers: nombre
      pire_pct: nombre
      meilleur_pct: nombre
      etendue_pct: nombre
    20j:
      disponible: booleen
      n_cas: entier
      rendement_median_pct: nombre
      rendement_moyen_pct: nombre
      proportion_haussiers: nombre
      pire_pct: nombre
      meilleur_pct: nombre
      etendue_pct: nombre
    drawdown:
      disponible: booleen
      median_pct: nombre
      pire_pct: nombre
  etat_du_jour:
    z_fair_value: nombre
    percentile_cot: nombre
    regime_taux_reels: nombre
  variables_sans_historique:
    [1 elements, chacun :]
      str
  _meta:
    source: texte
    horodatage_collecte_utc: texte
    date_donnee: texte
    age_jours: entier
biais:
  horodatage_utc: texte
  biais: texte
  score_composite: nombre
  conviction: texte
  conviction_brute: nombre
  concordance: nombre
  couverture_donnees: nombre
  couverture_min_requise: nombre
  donnees_partielles: booleen
  avertissement: texte
  seuils:
    haussier: nombre
    vendeur: nombre
  n_composantes_disponibles: entier
  n_composantes_totales: entier
  composantes_indisponibles: []
  composantes:
    [6 elements, chacun :]
      nom: texte
      disponible: booleen
      motif: texte
      score: nombre
      poids_configure: nombre
      poids_effectif: nombre
      contribution: nombre
      valeur_source: nombre
      sens: texte
      commentaire: texte
  invalidations:
    [4 elements, chacun :]
      variable: texte
      seuil: nombre
      operateur: texte
      lecture: texte
  precedents_historiques:
    1j:
      disponible: booleen
      n_cas: entier
      rendement_median_pct: nombre
      rendement_moyen_pct: nombre
      proportion_haussiers: nombre
      pire_pct: nombre
      meilleur_pct: nombre
      etendue_pct: nombre
    5j:
      disponible: booleen
      n_cas: entier
      rendement_median_pct: nombre
      rendement_moyen_pct: nombre
      proportion_haussiers: nombre
      pire_pct: nombre
      meilleur_pct: nombre
      etendue_pct: nombre
    20j:
      disponible: booleen
      n_cas: entier
      rendement_median_pct: nombre
      rendement_moyen_pct: nombre
      proportion_haussiers: nombre
      pire_pct: nombre
      meilleur_pct: nombre
      etendue_pct: nombre
    drawdown:
      disponible: booleen
      median_pct: nombre
      pire_pct: nombre
  _meta:
    source: texte
    horodatage_collecte_utc: texte
    date_donnee: texte
    age_jours: entier
explications:
  explications:
    juste_valeur:
      texte: texte
      mode: texte
      modele: texte
      tentatives: entier
      nombres_rejetes: []
      motif_repli: texte
      verifie_numeriquement: booleen
    positionnement_cot:
      texte: texte
      mode: texte
      modele: texte
      tentatives: entier
      nombres_rejetes: []
      motif_repli: texte
      verifie_numeriquement: booleen
    geopolitique:
      texte: texte
      mode: texte
      modele: texte
      tentatives: entier
      nombres_rejetes: []
      motif_repli: texte
      verifie_numeriquement: booleen
    biais:
      texte: texte
      mode: texte
      modele: texte
      tentatives: entier
      nombres_rejetes: []
      motif_repli: texte
      verifie_numeriquement: booleen
  n_openai: entier
  n_gabarit: entier
  toutes_verifiees: booleen
```

## Le bloc `biais` en détail

`score_composite` est la somme exacte des `contribution` des composantes
disponibles — la propriété est vérifiée par un test. Chaque contribution vaut
`score × poids_effectif`.

Le `poids_effectif` est **renormalisé sur les seules composantes
disponibles** : une source muette est retirée du dénominateur au lieu d'être
comptée comme un avis neutre, ce qui ferait passer une absence d'information
pour un signal d'équilibre. La perte d'information se lit dans
`couverture_donnees`, pas dans le score.

`invalidations` donne des seuils vérifiables sur un graphique : le prix
théorique vers lequel la prime disparaît, le R² sous lequel le modèle cesse
d'être valable, le percentile COT qui neutraliserait l'argument de
positionnement.

## Le bloc `explications`

`mode` vaut `openai` ou `gabarit`. En mode `openai`, le texte a passé la
vérification numérique : chaque nombre qu'il contient correspond à une valeur
du JSON, à la tolérance d'arrondi près. En mode `gabarit`, `motif_repli` dit
pourquoi — clé absente, quota, ou vérification échouée deux fois.

`verifie_numeriquement` résume : un texte en mode `gabarit` est exact par
construction, un texte en mode `openai` l'est parce qu'il a été contrôlé.
Aucun texte non vérifié n'est publié.
