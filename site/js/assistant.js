/**
 * Assistant : envoie une question et le contexte du jour au proxy.
 *
 * La clé OpenAI n'est jamais ici. Le navigateur parle au Worker Cloudflare,
 * qui détient la clé et applique une limitation de débit. Un site public qui
 * porterait la clé la livrerait au premier curieux.
 *
 * Le contexte transmis est **réduit** : seuls les blocs utiles à une
 * question d'analyse partent, ce qui évite d'expédier plusieurs centaines de
 * kilo-octets à chaque question, et garantit qu'aucun champ étranger à
 * l'analyse ne quitte le navigateur.
 */

import { lire } from "./donnees.js";
import { champsInterdits } from "./rendu.js";

/**
 * Construit le contexte transmis à l'assistant.
 *
 * @param {object} etat États de chargement des sources.
 * @returns {object} Contexte réduit aux blocs d'analyse.
 */
export function construireContexte(etat) {
  const or = lire(etat, "or.donnees", {});
  const crypto = lire(etat, "crypto.donnees", {});
  const quantique = lire(etat, "quantique.donnees", {});

  const contexte = {
    date: lire(or, "meta.date", null),
    or: {
      juste_valeur: lire(or, "juste_valeur", null),
      positionnement_cot: lire(or, "positionnement_cot", null),
      biais: lire(or, "biais", null),
      calendrier: lire(or, "calendrier", null),
      geopolitique: lire(or, "geopolitique", null),
      analogues: lire(or, "analogues.agregation", null),
    },
    crypto: {
      regime_btc: lire(crypto, "regime.regime_btc", null),
      regime_eth: lire(crypto, "regime.regime_eth", null),
      rotation: lire(crypto, "rotation.synthese", null),
    },
    quantique: {
      mouvements: lire(quantique, "mouvements", null),
      correlation: lire(quantique, "secteur.correlation_positions", null),
    },
  };

  // Filet : le contexte part sur le réseau, il ne doit contenir aucune
  // grandeur propre au compte. Les rapports n'en portent pas, mais s'en
  // remettre à cette absence serait fragile.
  const fautifs = champsInterdits(contexte);
  for (const chemin of fautifs) {
    const morceaux = chemin.split(".");
    let noeud = contexte;
    for (const cle of morceaux.slice(0, -1)) noeud = noeud && noeud[cle];
    if (noeud) delete noeud[morceaux[morceaux.length - 1]];
  }
  return contexte;
}

/**
 * Suggère trois questions adaptées à l'état du jour.
 *
 * Les suggestions dépendent de ce que les données montrent : proposer
 * « pourquoi l'or a-t-il bougé ? » un jour où rien n'a bougé ferait perdre
 * du temps.
 *
 * @param {object} etat États de chargement.
 * @returns {string[]} Trois questions.
 */
export function suggestions(etat) {
  const or = lire(etat, "or.donnees", {});
  const propositions = [];

  const silence = lire(or, "calendrier.en_fenetre_de_silence", false);
  if (silence) {
    propositions.push("Pourquoi vaut-il mieux s'abstenir en fenêtre de silence ?");
  }
  const jv = lire(or, "juste_valeur", {});
  if (jv.disponible && jv.fiable) {
    propositions.push("Que signifie l'écart actuel à la juste valeur de l'or ?");
  } else if (jv.disponible) {
    propositions.push("Pourquoi le modèle de juste valeur est-il jugé non fiable ?");
  }
  const cot = lire(or, "positionnement_cot", {});
  if (cot.disponible) {
    propositions.push("Le positionnement des spéculatifs est-il extrême ?");
  }
  const deja = lire(or, "geopolitique.deja_dans_les_prix.valeur", null);
  if (deja === true) {
    propositions.push("Pourquoi dit-on que le risque géopolitique est déjà dans les prix ?");
  }
  propositions.push("Quelles sont les conditions qui invalideraient le biais du jour ?");
  propositions.push("Quelles données manquent aujourd'hui, et pourquoi ?");

  return propositions.slice(0, 3);
}

/**
 * Interroge le proxy.
 *
 * @param {string} question Question de l'utilisateur.
 * @param {object} contexte Contexte réduit.
 * @param {string} url Adresse du proxy.
 * @param {Function} recuperer Implémentation de fetch.
 * @returns {Promise<{ok: boolean, texte: string}>} Réponse ou motif d'échec.
 */
export async function demander(question, contexte, url, recuperer = globalThis.fetch) {
  if (!url) {
    return {
      ok: false,
      texte:
        "L'assistant n'est pas configuré : l'adresse du proxy Cloudflare manque dans " +
        "site/js/config.js. Voir worker/README.md pour le déployer.",
    };
  }
  if (!question || !String(question).trim()) {
    return { ok: false, texte: "Posez une question pour obtenir une réponse." };
  }

  try {
    const reponse = await recuperer(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: String(question).trim(), contexte }),
    });
    if (reponse.status === 429) {
      return {
        ok: false,
        texte: "Trop de questions en peu de temps. Réessayez dans une minute.",
      };
    }
    if (!reponse.ok) {
      return { ok: false, texte: `L'assistant n'a pas répondu (HTTP ${reponse.status}).` };
    }
    const charge = await reponse.json();
    return { ok: true, texte: String(charge.reponse || "Réponse vide.") };
  } catch (erreur) {
    return {
      ok: false,
      texte: `L'assistant est injoignable (${erreur && erreur.name ? erreur.name : "erreur"}).`,
    };
  }
}
