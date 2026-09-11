# Scanner en direct — forward-test de la stratégie sur PAXGUSDT

Un Worker Cloudflare qui applique, minute par minute, la même mécanique
que `backtest.run` (order blocks en 3 bougies sur H1/M30/M15, FVG de
confirmation en M5→M3→M1, entrée au marché, stop à 1 $ au-delà de l'OB, et
les quatre variantes de TP), sur un flux de prix en direct — et journalise
chaque signal détecté dans D1.

**Ce système ne recommande jamais de prendre un trade.** Chaque texte
produit décrit ce que la mécanique a détecté, au passé ou au conditionnel,
et passe par `src/recommandation.js` avant publication — voir plus bas.

## Pourquoi un portage, pas un import

Le backtest est en Python ; ce Worker tourne dans l'isolat V8 de Cloudflare,
en JavaScript. Aucun mécanisme ne permet à un Worker d'exécuter du Python ni
d'importer un module Python — la logique de `backtest/data.py`,
`backtest/ict.py` et `backtest/execution.py` a donc été **réécrite** en
JavaScript, fonction par fonction, plutôt qu'appelée à distance (ce qui
aurait exigé un service Python à héberger, exploité et surveillé séparément
— une dépendance que le prompt ne demande pas et qui contredirait l'esprit
« Worker autonome, plan gratuit »).

**La garantie d'identité ne repose pas sur la relecture du code**, mais sur
`tests/parite.test.js` : `tests/fixtures/generer.py` fait tourner les
**vraies** fonctions Python (déjà testées ailleurs dans le projet) sur des
séries de bougies — synthétiques pour les cas unitaires, une série
aléatoire de 20 000 minutes pour le moteur complet — et enregistre en JSON
les entrées et les sorties exactes. Le test JS relit ces mêmes fixtures,
appelle le code JavaScript avec les mêmes entrées, et vérifie l'égalité
stricte des sorties (avec une tolérance de 1e-6 sur les flottants, pour le
seul bruit d'arrondi d'affichage introduit côté Python par `round()`).

**Ce test a trouvé un vrai bug avant que quiconque ne le voie tourner en
production.** En écrivant `moteur.js`, j'avais supposé qu'un setup pas
encore atteint par la boucle Python au moment où un autre confirme restait
« gelé » et reprenait plus tard. En réalité, `Backtest.executer()` fait
`setups = encore` après un `break` : tout setup non encore visité ce
tour-ci — y compris un setup flambant neuf créé à la même bougie — **n'est
jamais ajouté à `encore` et disparaît purement et simplement**, comme un
abandon silencieux. Le test de parité, en rejouant une série de 20 000
minutes en mode dégradé à une seule variante (voir plus bas), a exigé un
trade que le backtest Python n'a jamais pris — révélant l'écart. Voir le
commentaire « CHOIX DE FIDÉLITÉ AU BACKTEST » dans `src/moteur.js` pour le
détail. Sans ce test, cette différence de comportement serait passée
inaperçue jusqu'à ce que le scanner en direct diverge silencieusement du
backtest en production.

Régénérer les fixtures après toute modification du backtest Python :

```bash
.venv/bin/python worker-scanner/tests/fixtures/generer.py
node --test worker-scanner/tests/
```

## Différences assumées avec le backtest

Trois écarts, documentés ici et dans le code (recherchez « CHOIX
D'INTERPRÉTATION » et « CHOIX DE PORTAGE » dans `src/`) :

1. **Une entrée, quatre sorties — pas quatre simulations indépendantes.**
   Le backtest joue quatre `Backtest` séparés (un par variante de TP), qui
   peuvent donc prendre des trades différents : une variante qui sort plus
   tôt libère la voie à un nouveau setup plus tôt qu'une variante à
   l'objectif plus large. Le journal du scanner (partie 3 du prompt) demande
   une seule ligne par signal avec quatre statuts et quatre résultats en
   parallèle, ce qui suppose une seule entrée. C'est le choix retenu :
   l'entrée (prix, stop, taille) est calculée une fois, les quatre objectifs
   en même temps, et une nouvelle entrée n'est cherchée que lorsque les
   **quatre** variantes de la précédente sont résolues. `moteur.js` accepte
   un paramètre `variantesActives` qui restreint ce blocage à un
   sous-ensemble de variantes — c'est ce que le test de parité utilise
   (`["b2"]` seul) pour retrouver exactement le blocage à une position du
   backtest Python et vérifier le reste de la mécanique indépendamment de
   cette différence assumée.

2. **Objectif structurel introuvable → statut `sans_objectif`.** Le backtest
   abandonne tout le setup si la variante A ne trouve aucun niveau de
   liquidité devant le prix (`ABANDON_EXPIRATION`). Ici, l'entrée a déjà eu
   lieu pour les autres variantes : plutôt que d'inventer un objectif ou de
   taire la ligne, un quatrième statut explicite s'ajoute aux trois prévus
   par le prompt (`ouvert`, `gagnant`, `perdant`).

3. **Résultat en dollars, pas en euros.** Le journal (partie 3) demande
   `resultat_a_usd`, etc. Le taux EUR/USD ne sert donc qu'au dimensionnement
   de la taille (combien de lots pour viser 50-60 € de risque) ; le résultat
   affiché est en dollars, la devise de cotation de PAXGUSDT.

Un quatrième point n'est pas un écart de logique mais un manque comblé :
**ni le prompt ni le backtest ne prévoient de source de taux EUR/USD en
direct** (le backtest lit une série déjà téléchargée par Dukascopy). Voir
`src/taux.js` : l'API Frankfurter (taux de référence quotidien de la BCE),
gratuite et sans clé, vérifiée par appel réel — un seul appel par jour,
comme le backtest n'utilise qu'un taux par jour.

## Architecture : état persisté, pas rejeu complet

Le backtest tient tout son état (zones actives, setups, position) dans des
variables locales d'une seule exécution qui parcourt tout l'historique. Un
Worker ne garde rien en mémoire d'une exécution à l'autre : `src/moteur.js`
expose donc `traiterNouvellesBougies(etat, fenetre, ...)`, une fonction pure
qui avance l'état d'une ou plusieurs bougies et renvoie le nouvel état — le
même corps de boucle que `Backtest.executer()`, rejoué de façon incrémentale
plutôt qu'en une seule passe. `src/index.js` lit l'état depuis D1 au début
de chaque exécution, l'avance, puis le réécrit.

**Deux mécanismes de persistance, pas un seul rejeu :**

- **Zones actives et setups en attente** — portées dans l'état persisté
  (JSON, une ligne dans `etat_moteur`), avancées d'une bougie à la fois. Une
  fenêtre glissante de bougies M1 (4 jours, voir `FENETRE_MINUTES` dans
  `src/index.js`) est conservée dans le même état : assez pour couvrir le
  pire cas du moteur — `PROFONDEUR_JAMBE` (50 bougies sur l'unité de l'OB,
  jusqu'à 50 heures pour un H1) et les extrêmes de session (jusqu'à 48
  heures de recul) — avec une marge pour rattraper plusieurs exécutions
  manquées d'affilée.
- **Position ouverte** — n'a, elle, aucune durée de vie bornée par la
  stratégie (elle attend son stop ou son objectif, sans limite de temps) :
  la porter dans une fenêtre glissante bornée l'aurait perdue si elle restait
  ouverte plus longtemps que la fenêtre. Elle est donc portée dans l'état
  persisté indépendamment de la fenêtre de bougies, et sa résolution ne
  dépend que du prix courant comparé à des niveaux déjà calculés à
  l'entrée — jamais d'un nouveau rejeu de l'historique.

## Vérifications de faisabilité (partie 1 du prompt)

Résumées ici, détaillées avec sources dans la conversation d'origine :

- **Cloudflare Workers gratuit + Cron chaque minute** : faisable. 1440
  déclenchements/jour = 1,4 % du quota de 100 000 requêtes/jour. Le temps
  CPU (10 ms/invocation) ne compte que le calcul JS actif, jamais l'attente
  réseau — d'où l'exigence d'un moteur incrémental plutôt qu'un rejeu complet
  à chaque tick.
- **API Binance PAXGUSDT** : vérifiée par appel réel *depuis le bac à sable*
  — endpoint public, aucune clé, granularité 1 minute disponible. **Mais
  invalidée après déploiement réel** : Binance renvoie 403 à toute requête
  provenant du réseau sortant de Cloudflare Workers, y compris via le miroir
  `data-api.binance.vision` et avec un en-tête `User-Agent` de navigateur —
  un blocage au niveau IP/ASN, confirmé par `wrangler tail` sur le Worker
  déployé. Ce que la vérification de faisabilité en bac à sable ne pouvait
  pas détecter : le blocage ne s'applique qu'au réseau sortant de Cloudflare,
  pas à celui d'où partait la vérification initiale. Remplacé par
  **Kraken** (`api.kraken.com/0/public/OHLC`, paire `PAXGUSD`) : même
  garanties (public, aucune clé, granularité 1 minute), et vérifié joignable
  depuis ce Worker une fois déployé.
- **D1 plutôt que KV** : la piste initiale (KV pour l'état transitoire)
  ne tient pas sur le plan gratuit — KV plafonne à 1000 écritures/jour,
  et un Worker qui écrit son état à chaque tick en fait 1440. D1 autorise
  100 000 écritures/jour : d'où D1 pour tout, journal et état, KV écarté.

## PAXG, proxy de l'or — pas XAUUSD

PAXG est un jeton adossé à de l'or physique, coté en dollars sur Kraken
(paire PAXG/USD, directement en dollars — plus d'intermédiaire USDT comme
avec l'ancienne source Binance) : un proxy, pas le prix XAUUSD d'un
courtier. Ce scanner mesure la mécanique de la stratégie sur ce proxy ; il
ne simule pas une exécution réelle chez un courtier, et peut diverger
légèrement du prix réel — rappelé sur l'onglet Trading du site (partie 4).

## Structure

```
src/
  agregation.js     port de backtest/data.py (agréger M1→unités supérieures)
  ict.js            port de backtest/ict.py (order blocks, swings, FVG)
  execution.js      port de backtest/execution.py (stop, taille, coûts)
  moteur.js         port de backtest/moteur.py, incrémental (voir plus haut)
  kraken.js         récupération des bougies PAXG/USD
  taux.js           taux EUR/USD quotidien (Frankfurter)
  journal.js        lecture/écriture D1 (état + journal)
  recommandation.js garde-fou anti-recommandation (port de modules/quantum/moves.py)
  alertes.js        rendu textuel des événements, toujours au conditionnel
  api.js            construction de la réponse JSON de la route /journal (partie 4)
  index.js           point d'entrée du Worker (scheduled() et fetch())
schema.sql          schéma D1
wrangler.toml       configuration du Worker (Cron, liaison D1, origine CORS)
tests/
  fixtures/generer.py + *.json   fixtures partagées Python ↔ JS
  parite.test.js                 le test le plus important : identité avec le backtest
  agregation/ict/execution regroupés dans parite.test.js (mêmes fonctions)
  journal.test.js                D1 (via node:sqlite), statuts, idempotence
  recommandation.test.js         garde-fou + textes réels du scanner
  resilience.test.js             pannes Kraken/Frankfurter, état jamais corrompu
  api.test.js                    route /journal : ni lots ni résultat en dollars, jamais
  d1_test_adapter.js             adaptateur D1 minimal pour les tests
```

## Route /journal (partie 4 : l'onglet Trading du site)

`GET /journal` renvoie le journal en JSON, pour l'onglet Trading du site
(`site/trading.html` / `site/js/trading.js`). Deux garanties tiennent cette
route :

- **Confidentialité** : `lots` et `resultat_*_usd` (taille de position et
  résultat en dollars, donc des données de compte) ne sont ni sélectionnés
  par la requête SQL, ni lus par `src/api.js` — la donnée n'existe donc pas
  en mémoire à cet endroit, pas seulement filtrée à l'affichage. La
  performance publiée est un multiple de risque (R), calculé à partir du
  prix d'entrée, du stop et du prix de sortie (`prix_sortie_*`, ajouté au
  schéma pour cet usage) : -1 pile au stop, +ratio pile à l'objectif.
- **Anti-recommandation** : chaque texte (`texte_detection`, `variantes.*.texte`)
  repasse par `verifierAbsenceRecommandation()` avant d'être inclus ; un
  texte fautif devient `null` plutôt que d'être corrigé à la volée (voir
  `rendreEvenementPublic` dans `alertes.js`).

CORS restreint à l'origine du site (`ORIGINE_AUTORISEE` dans
`wrangler.toml`), même convention que `../worker/wrangler.toml`.

## Second setup : prise de liquidité (sweep)

Le moteur joue deux setups sur la même position (voir
`backtest/moteur.py`) : l'order block (variantes `a`, `b15`, `b2`, `b3`, `c`)
et le sweep (variantes `s1` sortie complète au 0,72 du mouvement de
référence, `s2` 0,72 puis premier niveau non balayé — 50/50 avec break-even
par défaut, réglable par `optionsSweepDefaut()` —, `s3` niveau structurel
seul). Une entrée ne porte qu'une famille : les variantes de l'autre setup
sont `sans_objectif` d'emblée. Les setups joués se déduisent de
`variantesActives` : c'est ce qui permet au test de parité de rejouer une
seule variante à la fois, exactement comme le backtest.

Parité (`tests/parite.test.js`, fixtures `niveaux.json` et `sweep.json`
générées par `tests/fixtures/generer.py`) : détection des pivots pour trois
sensibilités, cycle de vie d'un niveau pas à pas, puis le moteur complet
pour `s1`, `s2`, `s3` et pour les deux setups ensemble (`a` + `s1`) — identité
trade par trade, tranche par tranche.

Trois points de portage à connaître, tous vérifiés par cette parité :

* les niveaux nouvellement connus sont ceux dont `connuA` dépasse la **fin**
  de la dernière bougie traitée (pas son ouverture) — sinon un niveau connu
  pile à la fin du tour précédent serait réinséré et balayé deux fois ;
* les clôtures d'unité examinées à chaque bougie M1 sont celles closes dans
  `(fin précédente, fin courante]`, dans l'ordre H1, M30, M15 puis
  chronologique — l'ordre d'insertion des setups en dépend ;
* les cibles structurelles ne voient ni les niveaux en sweep, ni les niveaux
  balayés, ni ceux évincés par le plafond — côté Python, un drapeau `evince`
  reproduit la liste active du scanner.

Le journal D1 porte les colonnes `setup`, `niveau_*`, `sweep_extreme`,
`reference_prix`, `unite_fibo` et les colonnes `*_s1`, `*_s2`, `*_s3` ; la
table `paliers` porte `variante` (`c` ou `s2`). Base existante : appliquer
`migrations/0002_sweep.sql` avant de déployer cette version, sinon les
insertions échouent sur colonne inconnue.

## Déploiement

**Fait.** Worker actif à `https://scanner-or-direct.leonardbille.workers.dev`,
Cron Trigger `* * * * *` (toutes les minutes), base D1 `scanner-or` créée et
peuplée en continu. Procédure suivie (pour référence ou redéploiement) :

```bash
cd worker-scanner
npx wrangler d1 create scanner-or                       # copier l'ID renvoyé dans wrangler.toml
npx wrangler d1 execute scanner-or --file=schema.sql --remote
npx wrangler deploy
```

Vérification post-déploiement (le seul moyen fiable de savoir si une source
de données externe est réellement joignable *depuis Cloudflare*, la
vérification en bac à sable ne suffit pas — voir la mésaventure Binance
ci-dessus) :

```bash
npx wrangler tail scanner-or-direct --format=pretty     # dans un terminal
curl https://scanner-or-direct.leonardbille.workers.dev/declencher-manuellement
```

## État des quatre parties du prompt

- Partie 1 (faisabilité) : faite, avec une correction découverte en
  production (Binance → Kraken, voir ci-dessus).
- Partie 2 (le Worker) : faite, déployée, Cron actif.
- Partie 3 (structure du journal) : faite, c'est `schema.sql`, en production.
- Partie 4 (onglet Trading du site) : faite — route `/journal` ci-dessus,
  et `site/trading.html` / `site/js/trading.js` côté site (fil d'alertes,
  tableau de bord mensuel par variante, comparaison avec le backtest
  historique en multiple de risque R, avertissement permanent).

Ce qui reste réellement : le journal est encore vide (le scanner tourne
depuis peu, aucun signal réel détecté au moment de l'écriture de cette
ligne) — le premier vrai signal validera la chaîne complète, de la
détection à la résolution, sur une donnée réelle plutôt que sur les
fixtures de test.
