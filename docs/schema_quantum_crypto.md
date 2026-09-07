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

## Les deux trous, déclarés à chaque exécution

`meta.indicateurs_non_alimentes` les remonte à la racine, pour qu'on n'ait
pas à fouiller les blocs pour s'en apercevoir.

| Indicateur | Pourquoi |
|---|---|
| Flux nets des ETF spot BTC | Aucune source gratuite. CoinGlass (500), SoSoValue (404), DefiLlama (400), Farside (403) testés le 7 septembre 2026. |
| Calendrier des déblocages de jetons | DefiLlama réserve `/emissions` à son offre payante (402), CryptoRank exige une clé (401). |

Un troisième manque est déclaré dans `dataio/sec_filings.py` : le **sens** et
le **déposant** d'un Form 4 ne sont pas extraits. Ils figurent dans un XML
dont la structure varie, et les deviner serait pire que de les omettre. Les
dépôts sont signalés avec leur lien, `sens` restant `indetermine`.
