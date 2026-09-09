/**
 * Réglages du site. Aucun secret ici : ce fichier est servi au navigateur.
 *
 * La clé OpenAI vit dans le Worker Cloudflare, jamais ici. Le site est
 * public : une clé placée dans ce fichier serait lisible et utilisable par
 * n'importe quel visiteur. Voir worker/README.md.
 */

export const CONFIG = {
  /** Adresse du proxy Cloudflare qui relaie les questions à l'assistant. */
  urlAssistant: "",

  /** Chemins des rapports, relatifs à la racine du site publié. */
  sources: {
    or: "../reports/gold/latest.json",
    quantique: "../reports/quantum/latest.json",
    filQuantique: "../reports/quantum/feed_latest.json",
    crypto: "../reports/crypto/latest.json",
    historiqueBiais: "../reports/gold/historique_biais.jsonl",
  },

  /**
   * Âge, en jours, au-delà duquel une donnée est signalée comme datée.
   *
   * Le seuil dépend de la nature de la donnée, pas d'une règle unique : un
   * prix de trois jours est périmé, un rapport COT de trois jours est normal
   * puisque la CFTC publie le vendredi les positions du mardi. Afficher le
   * même avertissement pour les deux apprendrait à l'ignorer.
   */
  seuilsAge: {
    prix: 1,
    juste_valeur: 2,
    positionnement_cot: 8,
    calendrier: 2,
    geopolitique: 2,
    defaut: 3,
  },

  /** Nombre de jours en deçà duquel un score de fiabilité n'a pas de sens. */
  minJoursFiabilite: 30,

  /** Catégories du fil, dans l'ordre des onglets. */
  categories: ["tout", "quantique", "crypto", "geopolitique"],
};
