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
                              thèmes GDELT, calendrier FOMC
core/
  indicators.py               Indicateurs causaux + contrôle anti-look-ahead
  strategy.py                 Contrat Signal / Position / RiskConfig / Strategy
dataio/
  market.py                   Prix actions et ETF (yfinance, secours Stooq)
  macro.py                    Séries FRED et lecture du régime macro
  news.py                     Flux RSS et GDELT, intensité, déduplication
  crypto.py                   CoinGecko : OHLC, contexte, instantané
  cot.py                      Positionnement CFTC sur l'or (COMEX 088691)
  gold_flows.py               Ratio or/argent, minières/or, encours GLD
  calendar.py                 Prochaines publications macro et FOMC
modules/
  gold/
    fair_value.py             Juste valeur par taux réels et dollar, z-score
    geopolitics.py            Intensité GDELT et chaîne de transmission
    analogues.py              Précédents historiques et leurs suites
    bias.py                   Biais quotidien décomposé par composante
    explain.py                Explications vérifiées numériquement
    run.py                    Orchestration et publication du JSON
report/                       Rendu du rapport et page de suivi (à venir)
docs/
  schema_or.md                Structure du JSON produit par le moteur or
scripts/
  check_feeds.py              Diagnostic des flux : sain, figé ou mort
tests/
  test_indicators.py          Causalité des indicateurs, contrat de risque
  test_fair_value.py          Anti-look-ahead, z-score, fiabilité du modèle
  test_analogues.py           Séparation des précédents, nombre minimal de cas
  test_gold_engine.py         Géopolitique, pondérations, garde-fou numérique
reports/
  gold/                       Rapports quotidiens JSON + historique des biais
requirements.txt
```

---

## Sources de données

| Source | Usage | Coût | Clé requise | Limite connue |
|---|---|---|---|---|
| yfinance (Yahoo) | Prix actions et ETF, source primaire | Gratuit | Non | Débit limité, coupures ponctuelles |
| Stooq | Prix actions et ETF, source de secours | Gratuit | Non | Ajusté des splits, pas toujours des dividendes |
| FRED | Séries macroéconomiques | Gratuit | **Oui** — `FRED_API_KEY` | Fréquences hétérogènes, publication décalée |
| GDELT | Volume de couverture médiatique | Gratuit | Non | `artlist` plafonné à 250 articles |
| Flux RSS | Titres et chapeaux | Gratuit | Non | Flux figés sans erreur visible |
| CoinGecko | Prix et contexte crypto | Gratuit | Facultative — `COINGECKO_API_KEY` | Code 429 fréquent sans clé |
| CFTC (Socrata) | Positionnement futures or | Gratuit | Facultative — `CFTC_APP_TOKEN` | Publication vendredi, données de mardi |
| OpenAI | Explications en français | Payant à l'usage | Facultative — `OPENAI_API_KEY` | Sans elle, mode gabarit |

Aucun site payant n'est scrapé. Seuls des flux publics et des API ouvertes
sont utilisés.

Deux précautions valent d'être signalées, parce que leur oubli produit des
résultats faux sans lever la moindre erreur :

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
| `FRED_API_KEY` | Oui | Séries macroéconomiques. Sans elle, la juste valeur et le calendrier des publications ne sont pas calculables. |
| `COINGECKO_API_KEY` | Non | Relève la limite de débit CoinGecko. |
| `OPENAI_API_KEY` | Non | Explications en français du moteur or. Sans elle, le mode gabarit prend le relais et le rapport reste complet. |
| `CFTC_APP_TOKEN` | Non | Relève la limite de débit de l'API Socrata de la CFTC. L'accès reste public sans jeton. |

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

- [ ] Stratégie réelle sur XAUUSD (ICT / order flow) dans `core/strategy.py`
- [ ] Backtester consommant l'interface `Strategy`
- [ ] Rapport quotidien rendu par Jinja2 dans `report/`
- [ ] Page web de suivi
- [ ] Indicateur TradingView (Pine Script)
- [ ] Suivi des trois valeurs quantiques
- [ ] Suivi des positions crypto

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
