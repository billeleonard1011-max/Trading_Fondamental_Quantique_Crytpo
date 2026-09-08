# Structure des JSON quantique et crypto

Deux rapports quotidiens, écrits par des points d'entrée distincts :

| Commande | Sorties |
|---|---|
| `python -m modules.quantum.run` | `reports/quantum/AAAA-MM-JJ.json` et `latest.json` |
| `python -m modules.crypto.run` | `reports/crypto/AAAA-MM-JJ.json` et `latest.json` |

Ils suivent la même discipline que le moteur or : chaque bloc porte un
`_meta` (source, horodatage de collecte, date de la donnée, âge en jours), un
drapeau `disponible` et, quand il vaut `false`, un `motif` en français.
Aucune valeur de remplacement n'est jamais insérée silencieusement.

## Ce que ces rapports ne contiennent pas

**Aucune recommandation.** Ni achat, ni vente, ni « ce titre est
intéressant ». Ce n'est pas qu'une intention : `modules/quantum/run.py`
soumet le rapport à `verifier_absence_recommandation` **avant de l'écrire**,
et refuse de publier — code de sortie 1 — si un motif interdit apparaît. Le
résultat du contrôle est joint au rapport dans
`meta.controle_anti_recommandation`.

Les contenus repris verbatim d'une source externe (titres de presse, URL)
sont exclus du contrôle : un titre qui contient « buy » est une citation, pas
un avis du système. Sans cette exception, le garde-fou produirait des faux
positifs à répétition et finirait par être désactivé — ce qui serait pire.

## `reports/quantum/latest.json`

```
meta              date, domaine, valeurs_suivies[], sources_en_echec[],
                  donnees_partielles, nature_du_rapport,
                  controle_anti_recommandation{n_infractions, infractions[]}
prix              variations_du_jour{}, clotures{}
contexte_macro    variation_taux_reels_points, variation_marche_pct
mouvements        n_mouvements, seuils_appliques{}, mouvements[]
secteur           tresorerie{}, financements_et_contrats{},
                  nouveaux_entrants{}, correlation_positions{}
```

### Un mouvement

| Champ | Sens |
|---|---|
| `classification` | `sectoriel` ou `specifique` |
| `valeurs_concordantes` | Valeurs ayant bougé dans le même sens avec au moins 40 % de l'amplitude |
| `variations_secteur` | Variation du jour de toutes les autres valeurs suivies |
| `contexte_macro` | Variation des taux réels et de l'indice de référence |
| `signaux_contradictoires` | Éléments allant en sens opposés, **listés sans être arbitrés** |
| `constat` | Description factuelle, sans jugement |

Sur un mouvement `sectoriel`, `actualites` et `depots_sec` restent vides
délibérément : la cause est ailleurs, et afficher des dépêches sur la société
entretiendrait une explication fausse. C'est la leçon de l'épisode Pasqal —
une chute de 58 % qui n'avait rien à voir avec la société, mais tout avec la
remontée des taux réels.

## `reports/crypto/latest.json`

```
meta              date, jetons_suivis[], sources_en_echec[],
                  indicateurs_non_alimentes[], nature_du_rapport
regime            regimes{btc, eth}, offre_stablecoins{}, flux_etf{}
positionnement    funding{}, open_interest{}, positions{}, deblocages_tokens{}
```

### Régime et invalidation

Chaque régime — `accumulation`, `expansion`, `distribution`, `capitulation` —
porte une `invalidation` **non vide** : la variable, le seuil et la condition
en français qui le rendraient caduc. Un classement qu'on ne saurait pas
contredire ne serait pas une lecture de marché, ce serait une opinion. La
propriété est verrouillée par un test.

Le MVRV donne l'axe principal ; l'offre de stablecoins **nuance sans
renverser** — un seul indicateur secondaire ne doit pas suffire à basculer
une lecture, et la nuance est écrite dans un champ à part.

## Le fil d'actualité quantique

`reports/quantum/feed_latest.json` (liste, plus récent en premier) et
`reports/quantum/feed_historique.jsonl` (ajout seul). Produits par
`python -m modules.quantum.feed`, conçu pour tourner toutes les quinze à
trente minutes — le workflow quotidien ne l'appelle donc pas.

### Format unifié, fermé

Ce format est un contrat : les fils crypto et géopolitique à venir le
reprendront tel quel. Un test vérifie qu'un item porte exactement ces clés,
ni plus ni moins.

| Champ | Type | Sens |
|---|---|---|
| `id` | texte | Empreinte du titre normalisé, stable d'une adresse à l'autre |
| `categorie` | texte | `quantique`, `crypto` ou `geopolitique` — ensemble fermé |
| `titre_affiche` | texte | Titre de l'article, cité verbatim |
| `horodatage_utc` | texte | Date de publication |
| `source_nom` | texte | Nom de la source |
| `url_source` | texte | Adresse de l'article |
| `a_une_analyse_interne` | booléen | Une explication a-t-elle été produite |
| `analyse_interne` | texte ou `null` | L'explication, `null` si l'item était déjà connu |
| `tickers_ou_themes_lies` | liste | Valeurs suivies et acteurs cités |
| `nouveaute` | booléen | Item vu pour la première fois |

L'identifiant repose sur le **titre normalisé**, pas sur l'URL : une même
dépêche circule sous plusieurs adresses, et se fier à l'URL la rendrait
éternellement « nouvelle ».

### Ce qui n'apparaît jamais

`config/universe.yaml` porte une liste `feed_quantique.exclusions`. Pasqal et
PSQL y figurent : le titre sert d'exemple de calibration dans la
documentation du module, il n'est pas dans `quantum_watchlist`, et le voir
surgir dans le fil laisserait croire qu'il est suivi. L'exclusion est
nécessaire parce que Pasqal figure par ailleurs légitimement parmi les
acteurs connus du secteur, ce qui suffirait à rendre l'item pertinent. À
retirer le jour où le titre entre dans la watchlist.

## Rotation BTC / alts

`reports/crypto/latest.json` → `rotation`. Trois mesures indépendantes, dont
la synthèse ne tranche que si au moins deux se prononcent **dans le même
sens** ; sinon l'état reste `indetermine`, ce qui est une réponse.

`synthese.contributions` donne le vote de chaque mesure et, en cas
d'abstention, son motif. Deux réserves y figurent obligatoirement :

- `ratio_eth_btc.fiabilite_historique` vaut toujours `reduite_depuis_2024`,
  même quand la mesure échoue. La relation entre ce ratio et la rotation
  s'est affaiblie : captation de valeur par les Layer 2, divergence des flux
  ETF entre BTC et ETH.
- `largeur_marche.horizon_effectif_jours` peut différer de
  `horizon_demande_jours`. L'indice de référence raisonne à 90 jours, mais
  l'API gratuite de CoinGecko ne renseigne pas ce champ — il revient `null`
  pour les cent actifs. Le module retient le plus long horizon réellement
  disponible et le déclare. La comparaison reste valide, tous les actifs
  étant mesurés sur le même horizon.

Le calcul interne est recoupé avec l'indice public de blockchaincenter, lu
par extraction HTML. Un écart entre les deux est attendu : ni l'horizon ni le
panier ne coïncident exactement.

## Déblocages de jetons

`positionnement.deblocages_tokens`. Aucune source gratuite n'existe —
DefiLlama réserve `/emissions` à son offre payante (402), CryptoRank exige
une clé (401) —, les échéances sont donc saisies à la main dans
`config/universe.yaml`, sur le modèle du calendrier FOMC.

Cinq statuts, et leur distinction est le cœur du bloc :

| Statut | Sens |
|---|---|
| `actif` | Calendrier connu, échéance à venir |
| `vesting_conclu` | Calendrier arrivé à son terme |
| `non_applicable` | Le jeton n'a pas de mécanisme de vesting |
| `inconnu` | Aucune source identifiée — **une ignorance, pas une absence de déblocage** |
| `absent` | Jeton non renseigné |

`inconnu` et `non_applicable` ne doivent jamais être confondus : le premier
dit qu'on ignore, le second qu'il n'y a rien à savoir. Les présenter pareil
rassurerait à tort sur un jeton dont on ne sait rien.

## Les trous restants, déclarés à chaque exécution

`meta.indicateurs_non_alimentes` les remonte à la racine, pour qu'on n'ait
pas à fouiller les blocs pour s'en apercevoir.

| Indicateur | Pourquoi |
|---|---|
| Comportement des détenteurs de long terme | Aucune métrique gratuite d'ancienneté des pièces : le catalogue Community de Coin Metrics en compte 31, dont aucune sur l'âge des pièces. |
| Calendrier de déblocage de certains jetons | Statut `inconnu` pour KNTQ et PONS, non renseigné pour les grandes capitalisations. |

Les **flux des ETF spot** ne sont plus dans cette liste : Farside, réinterrogé
avec des en-têtes de navigateur complets, répond et publie le tableau réel
pour BTC comme pour ETH. Le 403 initial venait de l'empreinte de l'outil
d'appel, pas d'un blocage de la donnée — la leçon vaut d'être retenue pour
les autres sources écartées sur un code d'erreur.

Un troisième manque est déclaré dans `dataio/sec_filings.py` : le **sens** et
le **déposant** d'un Form 4 ne sont pas extraits. Ils figurent dans un XML
dont la structure varie, et les deviner serait pire que de les omettre. Les
dépôts sont signalés avec leur lien, `sens` restant `indetermine`.
