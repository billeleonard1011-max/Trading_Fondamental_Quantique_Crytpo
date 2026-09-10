/**
 * Dictionnaire centralisé : identifiant technique → libellé et explication.
 *
 * Toute la page passe par ici pour traduire un champ JSON en texte lisible.
 * Un nouvel identifiant ajouté demain par un module Python n'a qu'un
 * endroit à modifier ; l'oublier ne casse rien — `libelle()` retombe sur une
 * transformation automatique (underscores en espaces, première lettre en
 * majuscule) plutôt que d'afficher l'identifiant brut, et retient chaque cas
 * de repli pour qu'on puisse les lister a posteriori.
 */

/** Libellés lisibles, par identifiant JSON. */
export const LIBELLES = {
  // Onglets du fil d'actualité (site/js/fil.js).
  tout: "Tout",
  quantique: "Quantique",
  crypto: "Crypto",
  geopolitique: "Géopolitique",

  // Composantes du biais or (modules/gold/bias.py).
  ecart_juste_valeur: "Écart à la juste valeur",
  positionnement_cot: "Positionnement COT",
  dynamique_taux_reels: "Dynamique des taux réels",
  tendance_dollar: "Tendance du dollar",
  intensite_geopolitique: "Intensité géopolitique",
  confirmation_minieres: "Confirmation par les minières",

  // Synthèse de rotation BTC / alts (modules/crypto/rotation.py).
  dominance_btc: "Dominance du bitcoin",
  ratio_eth_btc: "Ratio ETH/BTC",
  largeur_marche: "Largeur de marché",
  rotation_alts: "Rotation vers les alts",
  indetermine: "Indéterminé",
  aucune: "Aucune mesure",

  // Régimes de marché crypto (modules/crypto/regime.py).
  accumulation: "Accumulation",
  expansion: "Expansion",
  distribution: "Distribution",
  capitulation: "Capitulation",

  // Statuts de déblocage de jetons (modules/crypto/positioning.py).
  actif: "Calendrier connu",
  vesting_conclu: "Vesting terminé",
  non_applicable: "Non applicable",
  inconnu: "Inconnu",
  absent: "Non suivi",

  // Axes du contexte macro (dataio/macro.py::compute_macro_regime).
  inflation: "Inflation",
  chomage: "Chômage",
  petrole: "Pétrole",
  appetit_risque: "Appétit pour le risque",
  courbe_des_taux: "Courbe des taux",
  stress_credit: "Stress du crédit",
  liquidite_nette: "Liquidité nette",

  // Maillons de la chaîne de transmission géopolitique, en repli si jamais
  // le module ne fournit pas de libellé (voir modules/gold/geopolitics.py).
  "1_evenement": "Intensité de l'événement",
  "2_petrole": "Pétrole",
  "3_inflation_anticipee": "Anticipations d'inflation",
  "4_taux_reels": "Taux réels",
  "5_or": "Or",

  // Classification des mouvements de prix (modules/quantum/moves.py).
  sectoriel: "Sectoriel",
  specifique: "Spécifique à la valeur",

  // Type d'entrée d'un trade (backtest, affiché nulle part pour l'instant,
  // gardé pour que le dictionnaire reste le seul endroit à modifier).
  marche: "Au marché",
  ote: "Zone de retracement (OTE)",
};

/** Explications au survol, par identifiant. Vide = pas d'infobulle. */
export const EXPLICATIONS = {
  ecart_juste_valeur:
    "Compare le prix de l'or à ce que justifieraient les taux réels et le " +
    "dollar. Un écart positif important signale une prime payée pour le " +
    "risque géopolitique ou les achats de banques centrales : quand elle " +
    "est déjà élevée, une mauvaise nouvelle supplémentaire fait peu monter " +
    "le prix, mais une détente le fait nettement retomber.",
  positionnement_cot:
    "Mesure à quel point les gros spéculateurs sont déjà engagés à l'achat " +
    "sur les contrats à terme, exprimé en percentile historique. Un " +
    "positionnement extrême signale un marché encombré : il reste peu " +
    "d'acheteurs pour pousser le prix plus haut, et beaucoup de positions " +
    "à déboucler en cas de retournement.",
  dynamique_taux_reels:
    "L'or ne rapporte aucun intérêt. Quand les taux réels montent, détenir " +
    "de l'or coûte plus cher en rendement abandonné, ce qui pèse " +
    "mécaniquement sur son prix. C'est historiquement son déterminant " +
    "macro le plus direct.",
  tendance_dollar:
    "L'or cote en dollars. Un dollar qui se renforce rend le métal plus " +
    "cher pour les acheteurs des autres devises et pèse généralement sur " +
    "la demande, et inversement.",
  intensite_geopolitique:
    "Mesure l'accélération de la couverture médiatique des conflits et " +
    "tensions par rapport à sa moyenne récente. L'or est une valeur " +
    "refuge : une escalade en cours soutient la demande, mais un " +
    "événement déjà largement couvert depuis des semaines n'est plus un " +
    "catalyseur.",
  confirmation_minieres:
    "Compare le comportement des sociétés minières aurifères à celui de " +
    "l'or. Les minières sont un pari à effet de levier sur le métal, " +
    "puisque leur rentabilité dépend de l'écart entre le cours de l'or et " +
    "leur coût d'extraction. Quand l'or monte sans que les minières " +
    "suivent, c'est un signe de méfiance : le marché achète l'or comme " +
    "protection, pas par conviction. Ces divergences précèdent souvent un " +
    "essoufflement.",

  dominance_btc:
    "Part du bitcoin dans la capitalisation totale du marché crypto. " +
    "Elle monte quand le marché se replie sur l'actif le plus liquide, et " +
    "baisse quand l'argent se déplace vers les autres jetons.",
  ratio_eth_btc:
    "Combien d'ether s'échange contre un bitcoin. Servait autrefois de " +
    "signal avancé de rotation vers les alts ; sa fiabilité s'est réduite " +
    "depuis 2024-2025, la valeur captée par les Layer 2 et la divergence " +
    "des flux ETF entre BTC et ETH brouillant la lecture.",
  largeur_marche:
    "Part des grandes capitalisations qui font mieux que le bitcoin sur " +
    "la période. C'est la mesure la plus directe de la rotation : une " +
    "vraie rotation vers les alts se traduit par beaucoup d'actifs qui " +
    "surperforment, pas un seul.",

  accumulation:
    "Le détenteur moyen porte une plus-value latente modérée : peu " +
    "d'incitation à vendre massivement.",
  expansion:
    "Les plus-values latentes s'installent : le marché progresse sans " +
    "excès apparent.",
  distribution:
    "Plus-value latente moyenne élevée : les détenteurs anciens ont un " +
    "intérêt croissant à réaliser leurs gains.",
  capitulation:
    "Le détenteur moyen est en perte latente : ceux qui restent sont ceux " +
    "qui n'ont pas vendu à perte.",

  // --- Métriques repérées sans explication lors de l'audit ------------------
  // Un chiffre affiché sans ce qu'il signifie n'apprend rien : ces entrées
  // comblent les cas relevés rubrique par rubrique.
  score_composite:
    "Moyenne des composantes du biais, chacune ramenée entre -1 et +1 puis " +
    "pondérée. Positif, il penche à la hausse ; négatif, à la baisse. Son " +
    "amplitude compte autant que son signe : à 0,05 les composantes se " +
    "contredisent presque autant qu'elles s'accordent.",
  conviction:
    "Degré de confiance dans le biais, déduit de l'accord entre composantes " +
    "et de la part de données réellement disponibles. Une conviction faible " +
    "sur un biais haussier ne dit pas « ça va monter peu », mais « les " +
    "signaux ne concordent pas assez pour trancher ».",
  couverture_donnees:
    "Part des composantes effectivement mesurées ce jour. À 40 %, le biais " +
    "repose sur moins de la moitié de ce qu'il devrait voir : il reste " +
    "publié, mais il vaut ce que vaut une lecture faite avec la moitié des " +
    "instruments.",
  intensite_couverture:
    "Nombre d'articles des dernières 24 heures rapporté à la moyenne des 30 " +
    "derniers jours. À 1×, la couverture est normale ; à 2×, le sujet fait " +
    "deux fois plus parler que d'habitude. Une intensité qui monte signale " +
    "une escalade en cours ; elle ne dit pas si l'or montera, seulement que " +
    "l'attention se porte là.",
  trajectoire_couverture:
    "Compare les trois derniers jours de couverture aux quatre précédents. " +
    "« En accélération » signale un sujet qui prend de l'ampleur, « en " +
    "essoufflement » un sujet que le marché a fini de digérer — et un sujet " +
    "digéré fait moins bouger les prix qu'un sujet naissant.",
  evenements_bilateraux:
    "Nombre d'événements impliquant les deux parties du conflit dans le " +
    "dernier relevé GDELT, publié toutes les quinze minutes. C'est un " +
    "instantané de l'activité diplomatique ou militaire récente, pas une " +
    "tendance : un chiffre bas peut vouloir dire « calme » comme « relevé " +
    "pris entre deux événements ».",
  chaine_de_transmission:
    "Un choc géopolitique n'atteint pas l'or directement : il passe par le " +
    "pétrole, puis les anticipations d'inflation, puis les taux réels. " +
    "Quand tous les maillons vont dans le sens attendu, la hausse de l'or a " +
    "une explication vérifiable ; quand la chaîne est rompue, elle repose " +
    "sur la seule peur — ce qui tient rarement aussi longtemps.",
  correlation_positions:
    "Corrélation la plus forte entre deux des valeurs suivies. Au-delà de " +
    "0,70, elles montent et descendent ensemble : détenir les trois revient " +
    "largement à détenir la même position en triple, et la diversification " +
    "apparente est trompeuse.",
  mvrv:
    "Rapport entre la valeur de marché et le prix moyen d'achat réel des " +
    "détenteurs. Sous 1, le détenteur moyen est en perte latente ; au-delà " +
    "de 3, il porte une plus-value telle que la tentation de vendre " +
    "augmente. C'est une mesure de pression vendeuse potentielle, pas une " +
    "prévision de prix.",
  part_offre_debloquee:
    "Part de l'offre totale du jeton qui devient négociable à cette " +
    "échéance. Plus elle est élevée, plus le nombre de vendeurs possibles " +
    "augmente d'un coup, sans que la demande change pour autant.",
  actif: "Un calendrier de déblocage est connu, avec une échéance à venir.",
  vesting_conclu: "Le calendrier de déblocage est arrivé à son terme : plus d'échéance à venir.",
  non_applicable: "Ce jeton n'a pas de mécanisme de vesting — rien à surveiller par nature.",
  inconnu: "Aucune source de suivi identifiée : une ignorance, pas la certitude qu'il n'y a rien.",
  absent: "Ce jeton n'a pas été renseigné dans le suivi des déblocages.",

  sectoriel:
    "D'autres valeurs suivies bougent dans le même sens avec une " +
    "amplitude comparable : le mouvement n'est pas propre à cette société.",
  specifique:
    "Aucune autre valeur suivie ne bouge de façon comparable : le " +
    "mouvement paraît propre à cette société.",
};

/** Identifiants pour lesquels aucune entrée n'existait dans {@link LIBELLES}. */
const identifiantsSansLibelle = new Set();

/**
 * Transforme un identifiant technique en un texte lisible par défaut.
 *
 * @param {string} id Identifiant brut, par exemple ``ecart_juste_valeur``.
 * @returns {string} Le texte transformé : underscores en espaces, première
 *   lettre en majuscule.
 */
function transformerParDefaut(id) {
  const texte = String(id).replace(/_/g, " ").trim();
  return texte.charAt(0).toUpperCase() + texte.slice(1);
}

/**
 * Traduit un identifiant en libellé lisible.
 *
 * @param {string|null|undefined} id Identifiant brut.
 * @param {string} repli Texte à rendre si ``id`` est vide.
 * @returns {string} Le libellé.
 */
export function libelle(id, repli = "") {
  if (id === null || id === undefined || id === "") return repli;
  const cle = String(id);
  if (Object.prototype.hasOwnProperty.call(LIBELLES, cle)) return LIBELLES[cle];
  identifiantsSansLibelle.add(cle);
  return transformerParDefaut(cle);
}

/**
 * Renvoie l'explication associée à un identifiant, si elle existe.
 *
 * @param {string|null|undefined} id Identifiant brut.
 * @returns {string} L'explication, ou chaîne vide si aucune n'est définie.
 */
export function explicationPour(id) {
  if (id === null || id === undefined) return "";
  return EXPLICATIONS[String(id)] || "";
}

/**
 * Liste les identifiants tombés dans le repli automatique depuis le
 * chargement de la page.
 *
 * Sert à l'audit : après un parcours complet du site, cette liste dit
 * précisément quels identifiants manquent encore au dictionnaire.
 *
 * @returns {string[]} Identifiants sans entrée, dans l'ordre de rencontre.
 */
export function identifiantsEnRepli() {
  return Array.from(identifiantsSansLibelle);
}

/** Remet à zéro le suivi des replis. Utile entre deux exécutions de tests. */
export function reinitialiserSuiviRepli() {
  identifiantsSansLibelle.clear();
}
