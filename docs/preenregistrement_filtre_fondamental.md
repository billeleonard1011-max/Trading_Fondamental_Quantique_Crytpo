# Pré-enregistrement — filtre fondamental sur le backtest

**Écrit et commité avant toute exécution.** C'est l'objet de ce document : le
commit qui l'introduit précède celui qui publie les résultats, de sorte que
la liste des règles et le critère de succès ne peuvent pas avoir été ajustés
après coup. Les trois leviers déjà testés (sensibilité du pivot, largeur du
stop, ventilation par heure et unité) ont tous donné le même verdict — un
optimum qui se déplace d'une période à l'autre — précisément parce qu'ils
ont été choisis *après* avoir vu les données.

## La question

La stratégie mécanique n'a pas d'espérance positive sur 188 jours répartis
en deux périodes disjointes. Le contexte fondamental que le reste du projet
calcule (biais or, valorisation, positionnement, appétit pour le risque)
n'a jamais été branché dessus — la synthèse du backtest le dit :
« filtre fondamental volontairement absent : le backtest mesure la version
mécanique pure ».

Hypothèse testée : **un filtre fondamental réduit le nombre de trades en
gardant les meilleurs**, là où les réglages testés jusqu'ici les réduisaient
tous uniformément.

## Ce qui est reconstruit, et comment la causalité est garantie

Pour chaque date de trade `D`, le contexte n'utilise que des données dont la
**publication** précède `D`.

| Grandeur | Source | Décalage appliqué |
|---|---|---|
| Taux réel 10 ans (`DFII10`) | FRED, endpoint CSV public | dernière valeur ≤ `D − 1` |
| Dollar (`DTWEXBGS`) | FRED | dernière valeur ≤ `D − 1` |
| VIX (`VIXCLS`) | FRED | dernière valeur ≤ `D − 1` |
| Spread haut rendement (`BAMLH0A0HYM2`) | FRED | dernière valeur ≤ `D − 1` |
| Prix de l'or | cache Dukascopy | clôture ≤ `D − 1` |
| Juste valeur, z-score | `modules.gold.fair_value.fair_value_history` | régression glissante n'utilisant que la fenêtre **précédant** chaque date |
| Positionnement COT | CFTC Socrata | dernier rapport dont la date de publication ≤ `D` |
| Biais composite | `modules.gold.bias.calculer_biais` | recomposé à partir des blocs ci-dessus |

Deux précisions qui comptent :

* les séries FRED retenues sont toutes des **observations de marché**
  (taux, change, volatilité, spread), non révisées après publication. Les
  séries révisables (CPI, chômage) sont volontairement écartées : les
  utiliser demanderait de connaître le millésime publié à l'époque, que
  l'endpoint CSV ne donne pas ;
* `fair_value_history` est la fonction du projet, dont la docstring dit
  « chaque ligne est estimée comme elle l'aurait été ce jour-là, sur la
  seule fenêtre qui la précède ». Ce n'est pas une réimplémentation.

**Ce qui n'est pas reconstructible, et donc pas testé** : l'intensité
géopolitique. GDELT ne donne d'historique de couverture que par requête et
par fenêtre glissante ; en reconstruire 188 jours × 4 thèmes demanderait
752 appels sur une source qui nous limite déjà à 19. La composante
géopolitique du biais est donc **absente** du score recomposé, qui porte
cinq composantes sur six. C'est une limite, pas une approximation cachée.

## Application du filtre

Le filtre est appliqué **dans le moteur**, en veto avant l'ouverture, et non
par tri des trades après coup. Le moteur ne tient qu'une position à la
fois : refuser un trade libère la place pour un autre, ce qu'un tri a
posteriori ne reproduirait pas.

## Les règles testées — liste fermée, arrêtée avant exécution

Six règles, chacune avec sa justification *ex ante*. Aucune ne sera ajoutée,
retirée ni ajustée après avoir vu les résultats.

| # | Règle | Pourquoi elle est plausible avant de regarder |
|---|---|---|
| **R1** | **Alignement du biais** : n'ouvrir un achat que si le biais est haussier, une vente que si baissier. | C'est la lecture la plus directe de ce que le biais prétend dire — quel côté du marché est favorisé. Si le biais a la moindre valeur, elle doit apparaître ici. |
| **R2** | **Conviction minimale** : refuser tout trade quand la conviction du biais est `faible`. | La conviction mesure l'accord entre composantes. Un jour où elles se contredisent est un jour où le contexte ne dit rien ; le projet publie déjà cette notion. |
| **R3** | **R1 et R2 réunies.** | La conjonction naturelle des deux précédentes ; testée à part pour ne pas avoir à choisir après coup entre « l'une », « l'autre » ou « les deux ». |
| **R4** | **Valorisation** : pas d'achat si l'écart à la juste valeur est ≥ +1 écart-type, pas de vente s'il est ≤ −1. | L'asymétrie déjà écrite dans la synthèse du projet : acheter un actif statistiquement cher laisse peu de place à la hausse et beaucoup au retour à la moyenne. |
| **R5** | **Positionnement non encombré** : pas d'achat si le net spéculatif est au-dessus du 75ᵉ percentile, pas de vente en dessous du 25ᵉ. | Même raisonnement, sur le positionnement : quand tout le monde est déjà acheteur, il reste peu d'acheteurs à convaincre et beaucoup de positions à déboucler. |
| **R6** | **Appétit pour le risque** : n'ouvrir un achat que si le climat est `risk-off`, une vente que si `risk-on`. | L'or est un actif refuge. La synthèse du projet énonce déjà ce lien ; le tester revient à vérifier qu'il se traduit en trades. |

Chaque règle est appliquée séparément à la variante **A (objectif
structurel)** du setup order block — la moins mauvaise des cinq sur les deux
périodes — et mesurée sur les **deux périodes disjointes** (111 jours de
mai-septembre 2026, 77 jours de mars-mai 2025).

## Critère de succès, fixé d'avance

Une règle n'est retenue que si, **sur les deux périodes** :

1. le résultat moyen par trade en R est **strictement positif** ;
2. il **s'améliore** par rapport à la référence sans filtre ;
3. il reste **au moins 30 trades** sur la période — en deçà, aucune
   conclusion n'est tirable, comme les 37 trades du premier backtest l'ont
   déjà montré.

Les trois conditions sont exigées ensemble. Une règle positive sur une seule
période est un échec, pas une piste.

## Ce que le résultat vaudra, dans les deux cas

**Si aucune règle ne passe** : la stratégie n'a pas d'edge exploitable, y
compris filtrée par le contexte fondamental. C'est une conclusion ferme, et
l'étude s'arrête là.

**Si une règle passe** : avec six règles testées et un critère à deux
périodes, le hasard seul en ferait passer environ 1,5 (six règles × la
probabilité qu'un résultat sans edge soit positif des deux côtés). Une
règle qui passe ne prouverait donc rien à elle seule — elle deviendrait une
hypothèse à confirmer sur des données non encore regardées, et devrait être
annoncée comme telle, pas comme un résultat.
