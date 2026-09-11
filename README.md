# Trading Fondamental — Quantique — Crypto

Système personnel d'aide à la décision. L'instrument tradé est **l'or au
comptant (XAUUSD)**, sur un compte prop firm de 10 000 $, avec une approche
ICT / order flow. Le système assure en parallèle une veille sur trois valeurs
du secteur quantique et sur des positions crypto.

**État : socle technique uniquement.** Cette première étape construit la
couche d'accès aux données, les indicateurs, la configuration et
l'automatisation. Elle ne contient aucune règle de trading.

---

## Le principe directeur

Une stratégie n'est définie **qu'à un seul endroit** : [core/strategy.py](core/strategy.py).

Le backtester et le scan quotidien importeront la même classe et appelleront
les mêmes méthodes. C'est la seule façon de garantir que le signal reçu le
matin est exactement celui qui a été testé sur l'historique.

L'erreur classique consiste à écrire la stratégie deux fois : une version
vectorisée pour le backtest, une version « en direct » pour le scan. Les deux
divergent au premier ajustement, et l'écart ne se voit jamais. Le backtest
continue d'afficher de beaux chiffres pendant que le scan produit autre chose.

D'où deux règles tenues fermement :

1. **Une seule définition de la stratégie.** Toute logique de décision passe
   par l'interface `Strategy`. Aucun raccourci vectorisé ailleurs.
2. **Aucun indicateur ne regarde le futur.** La valeur à la barre `i` ne
   dépend que des barres `0..i`. Cette propriété est vérifiée
   mécaniquement, pas supposée.

Le second point est testé par [tests/test_indicators.py](tests/test_indicators.py),
de deux façons indépendantes :

- **par troncature** : enrichir l'historique complet puis un historique coupé
  doit donner exactement les mêmes valeurs sur la partie commune ;
- **par perturbation du futur** : multiplier par 3,5 toutes les barres
  postérieures à une date ne doit rien changer avant cette date.

La première attrape les décalages ; la seconde attrape les normalisations sur
échantillon complet, qu'une troncature laisserait passer.

---

## Arborescence

```
.github/workflows/daily.yml   Rapport quotidien automatisé (cron + manuel)
config/
  feeds.yaml                  Flux RSS et requêtes GDELT
  universe.yaml               Benchmarks, secteurs, séries FRED, risque
  gold.yaml                   Réglages du moteur or : pondérations, seuils,
                              thèmes GDELT
  fomc_calendar_cache.json    Cache des réunions du FOMC, régénéré
                              automatiquement — ne pas éditer à la main
core/
  indicators.py               Indicateurs causaux + contrôle anti-look-ahead
  strategy.py                 Contrat Signal / Position / RiskConfig / Strategy
dataio/
  market.py                   Prix actions et ETF (yfinance, secours Stooq)
  macro.py                    Séries FRED et lecture du régime macro
  news.py                     Flux RSS et GDELT, intensité, déduplication
  crypto.py                   CoinGecko : OHLC, contexte, instantané
  cot.py                      Positionnement CFTC sur l'or (COMEX 088691)
  sec_filings.py              EDGAR : dépôts, trésorerie XBRL, Form 4 détaillés
  etf_flows.py                Flux des ETF spot BTC et ETH (Farside, repli AUM)
  dukascopy.py                Ticks XAUUSD et EUR/USD, agrégés en M1, mis en cache
  gold_flows.py               Ratio or/argent, minières/or, encours GLD
  calendar.py                 Publications macro (FRED) et réunions du FOMC,
                              collectées sur le site de la Fed, avec cache
modules/
  gold/
    fair_value.py             Juste valeur par taux réels et dollar, z-score
    geopolitics.py            Dossiers géopolitiques (conflits, régions,
                              thématiques), chaîne de transmission par canal
    analogues.py              Précédents historiques et leurs suites
    bias.py                   Biais quotidien décomposé par composante
    explain.py                Explications vérifiées numériquement
    run.py                    Orchestration et publication du JSON
  quantum/
    moves.py                  Mouvements marqués : sectoriel ou spécifique
    industry.py               Trésorerie, financements, entrants, corrélation
    feed.py                   Fil d'actualité continu, format unifié
    run.py                    Orchestration et publication du JSON
  geopolitique/
    feed.py                   Fil d'actualité géopolitique (thèmes génériques)
    sujets.py                 Découverte des sujets (GDELT Events), pertinence
                              marché, classement actif / veille / candidat
  crypto/
    regime.py                 Régime de marché par le MVRV, avec invalidation
    positioning.py            Funding, open interest, positions, déblocages
    rotation.py               Dominance BTC, ratio ETH/BTC, largeur de marché
    run.py                    Orchestration et publication du JSON
backtest/
  data.py                     Agrégation des unités depuis une seule série M1
  ict.py                      Order blocks, jambes, FVG, Fibonacci OTE
  execution.py                Coûts, dimensionnement en euros, stop
  propfirm.py                 Règles FTMO et Monte Carlo
  moteur.py                   Moteur causal, barre par barre
  run.py                      Quatre variantes d'objectif, comparées
report/                       Rendu du rapport et page de suivi (à venir)
docs/
  schema_or.md                Structure du JSON produit par le moteur or
  schema_quantum_crypto.md    Structure des JSON quantique et crypto
scripts/
  check_feeds.py              Diagnostic des flux : sain, figé ou mort
tests/
  test_indicators.py          Causalité des indicateurs, contrat de risque
  test_fair_value.py          Anti-look-ahead, z-score, fiabilité du modèle
  test_analogues.py           Séparation des précédents, nombre minimal de cas
  test_gold_engine.py         Géopolitique, pondérations, garde-fou numérique
  test_fomc_calendar.py       Analyse de la page de la Fed, cache, alerte
  test_quantum_moves.py       Épisode Pasqal, anti-recommandation, corrélation
  test_crypto_regime.py       Régimes et invalidation, funding, positions
  test_crypto_rotation.py     Synthèse de rotation, réserves, largeur
  test_quantum_feed.py        Fil unifié, non-réanalyse, Form 4
  test_ict_patterns.py        Motifs ICT et leurs cas limites
  test_backtest_engine.py     Anti-look-ahead, dimensionnement, prop firm
site/
  index.html, news.html, debrief.html   Pages du site public
  js/                          Modules ES : rendu, données, assistant, thème
  css/style.css                Palette claire/sombre, responsive
  tests/site.test.js           36 tests, sans navigateur (node --test)
worker/
  src/index.js                 Proxy Cloudflare : appelle OpenAI, limite le débit
  wrangler.toml                Configuration du Worker (aucun secret)
  README.md                    Déploiement pas à pas
  tests/worker.test.js         12 tests, KV et fetch OpenAI simulés
  fixtures/                   Extrait figé de la page FOMC, pour tester
                              l'analyse sans réseau
reports/
  gold/                       Rapports quotidiens JSON + historique des biais
  quantum/                    Rapports quotidiens de la veille quantique
  crypto/                     Rapports quotidiens de la veille crypto
requirements.txt
```

---

## Sources de données

| Source | Usage | Coût | Clé requise | Limite connue |
|---|---|---|---|---|
| yfinance (Yahoo) | Prix actions et ETF, source primaire | Gratuit | Non | Débit limité, coupures ponctuelles |
| Stooq | Prix actions et ETF, source de secours | Gratuit | Non | Ajusté des splits, pas toujours des dividendes |
| FRED | Séries macroéconomiques | Gratuit | **Oui** — `FRED_API_KEY` | Fréquences hétérogènes, publication décalée |
| GDELT | Volume de couverture médiatique | Gratuit | Non | `artlist` plafonné à 250 articles, code 429 fréquent |
| Réserve fédérale | Dates des réunions du FOMC | Gratuit | Non | Page HTML : structure susceptible de changer, d'où le cache |
| SEC EDGAR | Dépôts, trésorerie XBRL, Form 4 | Gratuit | Non, mais **User-Agent identifiant obligatoire** | 403 sans adresse de contact |
| Coin Metrics Community | MVRV (`CapMVRVCur`) | Gratuit | Non | 6 000 requêtes / 20 s ; `CapRealUSD` réservé aux offres payantes |
| DefiLlama | Offre de stablecoins | Gratuit | Non | `/emissions` (déblocages) réservé à l'offre payante |
| Binance / Bybit | Funding et open interest des perpétuels | Gratuit | Non | Données publiques de marché uniquement |
| Farside Investors | Flux des ETF spot BTC et ETH | Gratuit | Non | Exige des **en-têtes de navigateur** : 403 avec un User-Agent générique |
| blockchaincenter | Indice de saison des altcoins | Gratuit | Non | Page HTML sans API : sert de recoupement, pas de source principale |
| Dukascopy | Ticks XAUUSD et EUR/USD | Gratuit | Non | Se dégrade à l'usage (503) ; mois indexé à zéro dans les URL |
| Flux RSS | Titres et chapeaux | Gratuit | Non | Flux figés sans erreur visible |
| CoinGecko | Prix et contexte crypto | Gratuit | Facultative — `COINGECKO_API_KEY` | Code 429 fréquent sans clé |
| CFTC (Socrata) | Positionnement futures or | Gratuit | Facultative — `CFTC_APP_TOKEN` | Publication vendredi, données de mardi |
| OpenAI | Explications en français | Payant à l'usage | Facultative — `OPENAI_API_KEY` | Sans elle, mode gabarit |

Aucun site payant n'est scrapé. Seuls des flux publics et des API ouvertes
sont utilisés.

Deux précautions valent d'être signalées, parce que leur oubli produit des
résultats faux sans lever la moindre erreur :

- **Compression Brotli.** Plusieurs flux servis derrière Cloudflare renvoient
  du `Content-Encoding: br` même quand la requête n'annonce que `gzip,
  deflate`. Sans le paquet `brotli`, `requests` rend alors les octets
  compressés tels quels et le flux paraît mal formé alors qu'il fonctionne :
  c'est ce qui a fait passer « Quantum Computing Report » pour mort pendant
  un temps. `brotli` est donc une dépendance de plein droit.
- **Cours ajusté.** [dataio/market.py](dataio/market.py) prend le cours
  ajusté comme `close`. Sur un cours brut, un split 4:1 se lit comme une
  chute de 75 % et déclenche des signaux qui n'ont jamais existé.
- **Unités FRED.** `WALCL` est publié en millions de dollars, `RRPONTSYD` en
  milliards. Soustraire les deux séries telles quelles donne un résultat faux
  d'un facteur mille. La conversion est faite dans
  [dataio/macro.py](dataio/macro.py).
- **Fraîcheur du COT.** Le rapport CFTC paraît le vendredi et décrit le mardi
  précédent. Une donnée « du jour » a donc au minimum trois jours.
  [dataio/cot.py](dataio/cot.py) expose toujours `date_observation` et
  `age_jours` : les afficher n'est pas une politesse, c'est ce qui évite de
  lire un positionnement périmé comme une photographie du marché.
- **Contrats or homonymes.** Le jeu de données CFTC contient trois contrats
  dont le nom comprend « GOLD » : `088691` (COMEX, 100 onces), `088695`
  (Micro Gold, 10 onces) et `088LM1` (Coinbase). Filtrer sur le libellé les
  agrégerait. Le filtre porte donc sur le **code de contrat**.
- **Heures de publication.** FRED donne la date d'une publication, jamais son
  heure. Les heures affichées par [dataio/calendar.py](dataio/calendar.py)
  sont les heures d'usage (08:30 et 14:00 à New York) et portent le drapeau
  `heure_conventionnelle`.
- **FRED ne couvre pas le FOMC.** La release « FOMC Press Release »
  (`rid=101`) existe, mais ne publie que les bornes du corridor des Fed
  funds, séries quotidiennes 7 jours sur 7 : ses dates de publication sont
  journalières, pas les huit réunions annuelles. S'en servir donnerait un
  compte à rebours annonçant une réunion pour demain, chaque jour de
  l'année. Les dates viennent donc de la page officielle de la Fed, avec
  cache versionné et alerte de renouvellement.

---

## Installation

Python 3.12 ou plus récent.

```bash
git clone https://github.com/billeleonard1011-max/Trading_Fondamental_Quantique_Crytpo.git
cd Trading_Fondamental_Quantique_Crytpo

python3 -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -r requirements.txt
```

Les clés d'API se passent par variables d'environnement. Aucune n'est écrite
dans le code, et le fichier `.env` est ignoré par git.

```bash
export FRED_API_KEY="votre_cle"           # https://fredaccount.stlouisfed.org/apikeys
export COINGECKO_API_KEY="votre_cle"      # facultatif, relève la limite de débit
export ANTHROPIC_API_KEY="votre_cle"      # pour la synthèse à venir
```

### Vérifier que tout fonctionne

```bash
# Contrôle anti-look-ahead des indicateurs
python -m core.indicators

# Suite de tests complète
python -m tests.test_indicators
# ou, si pytest est installé :
pytest tests/ -v
```

### Lancer le moteur or

```bash
# Rapport complet, écrit dans reports/gold/
python -m modules.gold.run

# Sans aucun appel OpenAI : les explications passent en mode gabarit
python -m modules.gold.run --sans-explication

# Rejouer une date passée
python -m modules.gold.run --date 2026-09-04
```

Le moteur aboutit même quand une source est muette : le bloc concerné est
marqué indisponible avec son motif, et le biais signale qu'il repose sur des
données partielles.

La structure du JSON produit est décrite dans
[docs/schema_or.md](docs/schema_or.md).

### Lancer les veilles quantique et crypto

```bash
python -m modules.quantum.run      # écrit reports/quantum/
python -m modules.crypto.run       # écrit reports/crypto/
```

Ces deux modules **expliquent, ils ne recommandent jamais**. Aucune sortie ne
suggère d'acheter, de vendre ou de se positionner. La contrainte est vérifiée
mécaniquement : `modules/quantum/run.py` passe le rapport au crible de
`verifier_absence_recommandation` **avant publication** et refuse d'écrire si
une formulation interdite apparaît.

### Fil d'actualité quantique

```bash
python -m modules.quantum.feed                  # collecte, explique, publie
python -m modules.quantum.feed --sans-analyse   # sans appel OpenAI
```

Conçu pour tourner toutes les quinze à trente minutes, pas une fois par jour :
le workflow quotidien ne l'appelle donc pas. Le calendrier sera réglé avec la
page web. Le format de sortie est un contrat partagé avec les fils crypto et
géopolitique à venir — voir
[docs/schema_quantum_crypto.md](docs/schema_quantum_crypto.md).

### Backtester la stratégie ICT

```bash
python -m backtest.run --debut 2025-03-01 --fin 2025-03-31
python -m backtest.run --hors-ligne          # sans aucun téléchargement
python -m backtest.run --spread 0.80 --slippage 0.50
```

Les quatre variantes d'objectif sont jouées sur les mêmes setups. Le moteur
n'a accès, à chaque barre, qu'aux bougies déjà closes : la propriété est
vérifiée par troncature **et** par perturbation des barres futures.

Les zones d'ombre de l'énoncé de la stratégie ne sont pas tranchées en
silence : elles sortent dans `meta.choix_interpretation` du JSON de synthèse.

#### Second setup : prise de liquidité (sweep)

Indépendant du setup order block, joué séparément ou avec lui (une seule
position à la fois) :

* **niveau de liquidité** : un pivot strict (`sensibilite_pivot` bougies de
  chaque côté, testé à 3, 4 et 5), détecté en M15, M30 et H1, connu seulement
  à la clôture de la bougie `i + k` ;
* **sweep** : une mèche M1 au-delà du niveau ouvre le sweep ; **une bougie de
  l'unité du niveau qui clôture de l'autre côté** le confirme. Un niveau
  traversé sans clôture de l'autre côté reste actif ; un niveau balayé sort
  définitivement de la liste ;
* **confirmation et entrée** : FVG dans le sens du trade (M5, puis M3, puis
  M1), touche, clôture au-delà, entrée au marché — la même chaîne que
  l'order block ; **stop** au-delà de la mèche du sweep (`marge_stop_sweep`,
  par défaut la marge du setup OB) ;
* **objectifs**, trois familles : `S1_fibo` (tout à 0,72 du dernier mouvement
  directionnel précédant le sweep, ancré sur M15, M30 ou H1),
  `S2_fibo_structurel` (une part au 0,72, le solde sur le premier niveau non
  balayé au-delà — répartitions 50/50, 33/67, 67/33, avec ou sans
  break-even), `S3_structurel` (tout sur le premier niveau non balayé au-delà
  de l'entrée).

```bash
python -m backtest.run --sweep --hors-ligne          # matrice complète : variantes × ancrage Fibonacci × pivot, puis avec l'order block
python -m backtest.run --sweep --sensibilite-pivot 3
```

Sorties : `reports/backtest/sweep_synthese.json` (blocs `par_fibo`,
`par_sensibilite_pivot`, `ensemble` ventilé `par_setup`), les journaux
`trades_sweep_*.csv` / `paliers_sweep_*.csv` (ancrage M15) et
`trades_ensemble_*.csv`. Les variantes par défaut sont aussi reprises dans
`synthese.json` sous `sweep_*`, pour la comparaison du site avec le scanner.
Les zones d'ombre de l'énoncé tranchées ici (unité de la bougie de
confirmation, fenêtre du FVG, plafond de niveaux actifs, mouvement de
référence, niveau structurel de la variante 2) sont listées dans
`backtest/ict.py::CHOIX_INTERPRETATION` et republiées dans chaque synthèse.
Un script Pine autonome, `pine/liquidite_sweep.pine`, dessine les niveaux
actifs du timeframe affiché et marque les sweeps confirmés.

**Deux mesures à connaître avant d'utiliser ce setup** (août-septembre 2026,
27 jours — beaucoup trop court pour conclure, voir l'avertissement ci-dessous) :

* **la sensibilité du pivot pilote le signe du résultat**, de façon monotone
  sur toute la plage testée : k = 2 donne +226 € (S1), k = 3 +58 €, k = 4
  −148 €, k = 5 −450 €. Le R moyen par trade suit la même pente (+0,04 à
  −0,19). Mais **85 à 88 % des niveaux finissent balayés quelle que soit la
  sensibilité**, avec une durée de vie médiane de sept à neuf heures : la
  détection ne sélectionne donc pas des réservoirs de liquidité rares, elle
  décrit surtout le retour du prix sur ses extrêmes récents. Un pivot à deux
  bougies en M15 est l'extrême d'une fenêtre de 75 minutes : la prémisse
  « des positions s'y sont créées, donc des stops s'y accumulent » y est
  faible. Un paramètre dont l'optimum est au bord de la plage testée, sur un
  mois, est un signal d'alerte, pas un réglage ;
* **les deux setups se gênent, et c'est l'order block qui paie.** Sur la même
  période, l'order block seul fait 44 trades et +152 € ; joué avec le sweep,
  il tombe à 36 trades et −152 €, parce que 22 touches de zone surviennent
  pendant qu'une position sweep occupe la place (et 64 sweeps sont bloqués
  par une position order block, l'inverse). La règle de priorité à l'essai
  (`priorite_ob`, `proximite_ob_usd`) refuse un sweep quand un order block
  actif de même sens est proche : à 10 $, elle rend à l'order block ses
  trades et son résultat (+121 à +227 € selon la variante), mais dégrade
  d'autant le sweep — elle déplace le problème, elle ne l'annule pas. La
  seule façon de le supprimer serait d'autoriser une position par setup,
  ce qui est une décision de stratégie, pas un correctif.

### Site web de suivi (GitHub Pages)

Interface publique de lecture, en HTML/CSS/JS purs, sans framework ni étape
de compilation : `site/index.html` en est le point d'entrée.

**Emplacement retenu : `/site` à la racine du dépôt, avec GitHub Pages
configuré pour servir la racine du dépôt** (`Settings → Pages → Source :
Deploy from a branch → main → / (root)`). Ce choix, plutôt que `docs/site/`,
tient à une contrainte simple : les JSON sont lus **par chemins relatifs
directement depuis le dépôt**, sans être copiés. Comme `reports/` vit à la
racine, servir la racine entière garde `site/` et `reports/` dans la même
arborescence sans réécrire un seul chemin. Un `index.html` à la racine
redirige vers `site/index.html`, et `.nojekyll` désactive le traitement
Jekyll, inutile ici et parfois intrusif sur des fichiers Markdown déjà
présents dans `docs/`.

Le site consomme :
- `reports/gold/latest.json`, `reports/quantum/latest.json`,
  `reports/quantum/feed_latest.json`, `reports/crypto/latest.json` ;
- `reports/gold/historique_biais.jsonl`, pour le débrief du soir.

**Règle de confidentialité, vérifiée par test** : ce site est public. Aucune
taille de position, montant investi, solde ou donnée de règle de société de
financement ne doit y apparaître — `site/tests/site.test.js` scanne les
rapports réellement servis et le contexte envoyé à l'assistant pour
détecter tout champ de cette nature avant publication.

```bash
node --test site/tests/site.test.js     # 36 tests, sans navigateur ni réseau
```

Pages : `index.html` (accueil), `news.html` (détail d'un article),
`debrief.html` (bilan du soir). Deux thèmes (clair par défaut, sombre),
bascule mémorisée dans `localStorage`.

### Assistant et proxy Cloudflare Worker

L'assistant du site ne parle jamais directement à OpenAI : le code du
navigateur est public, une clé qui s'y trouverait serait récupérée en
quelques secondes. `worker/` contient le proxy Cloudflare qui détient la
clé, applique une limitation de débit par adresse IP (Cloudflare KV), et
impose côté serveur les mêmes contraintes que `modules/gold/explain.py` —
n'utiliser que les chiffres fournis, ne jamais recommander d'acheter ou de
vendre.

Étapes de déploiement détaillées, pour quelqu'un qui n'a jamais utilisé
Cloudflare : [worker/README.md](worker/README.md). Une fois déployé,
reporter l'adresse obtenue dans `site/js/config.js` (`urlAssistant`).

```bash
node --test worker/tests/worker.test.js  # 12 tests, KV et OpenAI simulés
```

### Diagnostic des flux d'actualité

Ce script effectue des appels réseau ; il est volontairement séparé des tests.

```bash
python -m scripts.check_feeds                    # contrôle standard
python -m scripts.check_feeds --heures 72        # tolérance de fraîcheur élargie
python -m scripts.check_feeds --intensite        # ajoute le ratio de couverture GDELT
python -m scripts.check_feeds --rss-seulement    # sans appel à GDELT
python -m scripts.check_feeds --json             # sortie exploitable par un programme
```

Codes de sortie : `0` tout est sain, `1` au moins un flux figé, `2` au moins
un flux mort.

Un flux **figé** répond correctement mais n'est plus alimenté. C'est le cas le
plus pernicieux : il ne provoque aucune erreur et vide la veille en silence.

---

## Secrets à créer sur GitHub

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`

| Nom | Obligatoire | Utilité |
|---|---|---|
| `FRED_API_KEY` | Oui | Séries macroéconomiques. Sans elle, la juste valeur et les publications CPI / emploi / PCE ne sont pas calculables. Les réunions du FOMC, elles, ne dépendent pas de FRED. |
| `COINGECKO_API_KEY` | Non | Relève la limite de débit CoinGecko. |
| `OPENAI_API_KEY` | Non | Explications en français du moteur or. Sans elle, le mode gabarit prend le relais et le rapport reste complet. |
| `CFTC_APP_TOKEN` | Non | Relève la limite de débit de l'API Socrata de la CFTC. L'accès reste public sans jeton. |
| `SEC_CONTACT_EMAIL` | Oui pour la veille quantique | Adresse de contact exigée par la SEC dans le User-Agent. Sans elle, EDGAR répond 403 et la trésorerie ne peut pas être estimée. |

Le workflow tourne en cron à **11:30 UTC**, du lundi au vendredi, et peut être
lancé à la main depuis l'onglet `Actions`.

GitHub Actions exécute les crons **en UTC, sans ajustement d'heure d'été** :
11:30 UTC correspond à 13:30 à Paris l'été et 12:30 l'hiver. Le décalage d'une
heure entre mars et octobre est normal. GitHub met aussi les crons en file
d'attente aux heures chargées, un retard de quelques minutes est courant.

---

## Avancement

Socle technique

- [x] Indicateurs techniques causaux, vérifiés par deux tests indépendants
- [x] Contrat de stratégie partagé backtest / scan
- [x] Dimensionnement par le risque, stop obligatoire
- [x] Prix actions et ETF, avec source de secours
- [x] Client FRED et lecture du régime macro sur trois axes
- [x] Veille RSS et GDELT, mesure d'intensité, déduplication
- [x] Client CoinGecko avec gestion du débit
- [x] Configuration des flux et de l'univers
- [x] Diagnostic des flux
- [x] Automatisation GitHub Actions

Moteur d'analyse fondamentale de l'or

- [x] Juste valeur par les taux réels et le dollar, z-score du résidu
- [x] Percentile historique de l'écart depuis 2010, R² et drapeau de fiabilité
- [x] Contribution de chaque facteur au prix théorique
- [x] Positionnement CFTC en percentile cinq ans, avec âge de la donnée
- [x] Ratio or/argent et ratio minières/or
- [x] Calendrier CPI, emploi, PCE et FOMC avec compte à rebours
- [x] Collecte automatique des réunions du FOMC, cache versionné et alerte
      de renouvellement remontée jusqu'à `meta.avertissement`
- [x] Intensité géopolitique GDELT, trajectoire et chaîne de transmission
- [x] Indicateur `deja_dans_les_prix`
- [x] Précédents historiques, avec séparation minimale et nombre de cas minimal
- [x] Biais quotidien décomposé, pondérations en configuration
- [x] Historique des biais pour l'auto-évaluation ultérieure
- [x] Explications vérifiées nombre par nombre, mode gabarit en repli
- [ ] Encours du GLD : aucune source de tonnage exploitable par programme
      (voir [dataio/gold_flows.py](dataio/gold_flows.py))
- [ ] Stress géopolitique historique depuis 2010 : GDELT ne remonte pas
      aussi loin par l'API publique, la variable est absente de la base des
      précédents et signalée comme telle
- [ ] Notation rétrospective des biais à 1, 5 et 20 jours

Reste à construire

- [x] Stratégie ICT formalisée et backtester causal sur XAUUSD (`backtest/`)
- [x] Site web de suivi, statique, publié sur GitHub Pages (`site/`)
- [x] Assistant public avec proxy Cloudflare Worker (`worker/`)
- [ ] Rapport quotidien rendu par Jinja2 dans `report/`
- [~] Indicateur TradingView (Pine Script) : `pine/liquidite_sweep.pine` couvre les
      niveaux de liquidité et les sweeps ; les order blocks restent dans l'indicateur
      existant de l'utilisateur, hors dépôt
- [ ] Déploiement effectif du Worker sur un compte Cloudflare — le code et
      les 12 tests sont prêts, mais l'authentification interactive requise
      par `wrangler login` ne peut pas être faite depuis l'automatisation ;
      voir [worker/README.md](worker/README.md)
Veille quantique et crypto

- [x] Client EDGAR : dépôts, trésorerie XBRL, activité d'initiés
- [x] Mouvements de prix classés sectoriel ou spécifique, calibrés sur
      l'épisode Pasqal de septembre 2026
- [x] Contrôle anti-recommandation, bloquant à la publication
- [x] Trésorerie et autonomie estimée, alerte de dilution
- [x] Détection de nouveaux entrants, corrélation entre positions
- [x] Régime crypto par le MVRV, avec condition d'invalidation
- [x] Funding et open interest des perpétuels, percentile 90 jours
- [x] Flux des ETF spot BTC et ETH : mesurés chez Farside, qui répond avec
      des en-têtes de navigateur — le 403 initial venait de l'outil d'appel
- [x] Form 4 détaillés : identité, rôle, sens, montants, avec dégradation
      par paliers quand le schéma d'un dépôt diffère
- [x] Régime crypto étendu à l'ether, avec avertissement de calibrage
- [x] Rotation BTC / alts : dominance, ratio ETH/BTC, largeur de marché
- [x] Déblocages de jetons : cinq statuts distincts, saisis à la main
- [x] Fil d'actualité quantique au format unifié
- [ ] Comportement des détenteurs de long terme : aucune métrique gratuite
      d'ancienneté des pièces
- [ ] Déblocages de KNTQ et PONS : aucune source de suivi identifiée
- [ ] Fils crypto et géopolitique, sur le patron du fil quantique

La stratégie réelle n'est pas encore écrite. `ExampleTrendStrategy` n'existe
que pour illustrer la forme attendue : ses règles n'ont fait l'objet d'aucun
backtest, son attribut `TRADABLE` vaut `False`, et son instanciation
journalise un avertissement.

---

## Avertissement

Outil personnel, construit pour un usage privé. Les signaux qu'il produit ne
sont **pas des conseils d'investissement**. Aucune garantie n'est donnée sur
l'exactitude des données ni sur la pertinence des analyses. Les performances
passées ne préjugent pas des performances futures. Le trading sur produits à
effet de levier expose à un risque de perte en capital pouvant dépasser le
dépôt initial. Les décisions et leurs conséquences n'engagent que leur auteur.
