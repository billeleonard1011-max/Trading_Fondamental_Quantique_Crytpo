/**
 * Proxy Cloudflare entre le site public et l'API OpenAI.
 *
 * Pourquoi ce proxy existe
 * ------------------------
 * Le site est servi en fichiers statiques et son code est lisible par
 * quiconque ouvre l'inspecteur. Une clé d'API placée dans ce code serait
 * récupérée et utilisée par n'importe quel visiteur, aux frais du
 * propriétaire. La clé vit donc ici, dans un secret Cloudflare que le
 * navigateur ne voit jamais.
 *
 * Ce que le Worker garantit
 * -------------------------
 * * la clé ne quitte jamais le Worker ;
 * * un visiteur ne peut pas consommer le crédit sans limite : la limitation
 *   de débit est appliquée par adresse IP, dans un espace KV ;
 * * les contraintes de fond — n'utiliser que les chiffres fournis, ne jamais
 *   recommander d'acheter ou de vendre — sont posées côté serveur, donc
 *   hors de portée d'un visiteur qui modifierait le site en local.
 */

/** Consigne système, identique en esprit à celle de modules/gold/explain.py. */
const CONSIGNE = `Tu expliques des données de marché à quelqu'un qui les découvre.

RÈGLES ABSOLUES, sans exception :
1. N'utilise QUE les chiffres présents dans le JSON fourni. Tu peux les arrondir, jamais en inventer d'autres.
2. N'invente aucun pourcentage, aucun percentile, aucune date, aucune statistique absente des données.
3. Ne recommande JAMAIS d'acheter, de vendre, d'entrer, de sortir, ni où placer un stop. Tu expliques, tu ne conseilles pas.
4. N'écris jamais qu'un actif est intéressant, attractif, sous-évalué ou surévalué.
5. Quand une donnée est absente ou marquée indisponible, dis-le explicitement au lieu de la contourner.
6. Français simple, sans jargon non expliqué. Cinq phrases au maximum.
7. Dans le doute sur un chiffre, n'en cite aucun et décris la situation avec des mots.`;

/** Fenêtre de limitation, en secondes. */
const FENETRE_SECONDES = 60;

/** Nombre de questions autorisées par adresse dans cette fenêtre. */
const MAX_PAR_FENETRE = 6;

/** Plafond de taille du corps reçu, en octets. */
const TAILLE_MAX = 200_000;

/**
 * En-têtes de partage entre origines.
 *
 * @param {string} origineAutorisee Origine autorisée, ou ``*``.
 * @returns {object} En-têtes CORS.
 */
function entetesCors(origineAutorisee) {
  return {
    "Access-Control-Allow-Origin": origineAutorisee || "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
  };
}

/**
 * Répond en JSON.
 *
 * @param {object} charge Corps de la réponse.
 * @param {number} statut Code HTTP.
 * @param {object} entetes En-têtes additionnels.
 * @returns {Response} Réponse prête.
 */
function json(charge, statut, entetes) {
  return new Response(JSON.stringify(charge), {
    status: statut,
    headers: { "Content-Type": "application/json; charset=utf-8", ...entetes },
  });
}

/**
 * Applique la limitation de débit par adresse.
 *
 * L'espace KV est optionnel : sans lui le Worker fonctionne, mais sans
 * garde-fou. Le cas est signalé dans la réponse plutôt que passé sous
 * silence — un proxy sans limite finit par coûter cher.
 *
 * @param {object} env Environnement du Worker.
 * @param {string} adresse Adresse IP du visiteur.
 * @returns {Promise<{autorise: boolean, restant: number, actif: boolean}>} Décision.
 */
async function verifierDebit(env, adresse) {
  if (!env.LIMITEUR) {
    return { autorise: true, restant: -1, actif: false };
  }
  const cle = `debit:${adresse}`;
  const brut = await env.LIMITEUR.get(cle);
  const compte = brut ? parseInt(brut, 10) || 0 : 0;

  if (compte >= MAX_PAR_FENETRE) {
    return { autorise: false, restant: 0, actif: true };
  }
  // L'expiration porte la fenêtre glissante : KV supprime la clé tout seul.
  await env.LIMITEUR.put(cle, String(compte + 1), {
    expirationTtl: FENETRE_SECONDES,
  });
  return { autorise: true, restant: MAX_PAR_FENETRE - compte - 1, actif: true };
}

export default {
  /**
   * Point d'entrée du Worker.
   *
   * @param {Request} requete Requête entrante.
   * @param {object} env Variables et secrets du Worker.
   * @returns {Promise<Response>} Réponse.
   */
  async fetch(requete, env) {
    const cors = entetesCors(env.ORIGINE_AUTORISEE);

    if (requete.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }
    if (requete.method !== "POST") {
      return json({ erreur: "Seule la méthode POST est acceptée." }, 405, cors);
    }
    if (!env.OPENAI_API_KEY) {
      return json(
        {
          erreur:
            "La clé OpenAI n'est pas configurée sur le Worker. " +
            "Voir worker/README.md, étape « ajouter le secret ».",
        },
        500,
        cors,
      );
    }

    const adresse = requete.headers.get("CF-Connecting-IP") || "inconnue";
    const debit = await verifierDebit(env, adresse);
    if (!debit.autorise) {
      return json(
        {
          erreur: `Trop de questions : ${MAX_PAR_FENETRE} par ${FENETRE_SECONDES} secondes.`,
        },
        429,
        { ...cors, "Retry-After": String(FENETRE_SECONDES) },
      );
    }

    let charge;
    try {
      const texte = await requete.text();
      if (texte.length > TAILLE_MAX) {
        return json({ erreur: "Contexte trop volumineux." }, 413, cors);
      }
      charge = JSON.parse(texte);
    } catch {
      return json({ erreur: "Corps de requête illisible." }, 400, cors);
    }

    const question = String(charge.question || "").trim();
    if (!question) {
      return json({ erreur: "Question vide." }, 400, cors);
    }

    const messages = [
      { role: "system", content: CONSIGNE },
      {
        role: "user",
        content:
          `Question : ${question}\n\n` +
          `Données du jour (seule source de chiffres autorisée) :\n` +
          JSON.stringify(charge.contexte ?? {}, null, 1),
      },
    ];

    let reponseApi;
    try {
      reponseApi = await fetch("https://api.openai.com/v1/chat/completions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${env.OPENAI_API_KEY}`,
        },
        body: JSON.stringify({
          model: env.MODELE || "gpt-4o-mini",
          messages,
          temperature: 0.2,
          max_tokens: 500,
        }),
      });
    } catch (erreur) {
      return json({ erreur: "L'API OpenAI est injoignable." }, 502, cors);
    }

    if (!reponseApi.ok) {
      // Le détail de l'erreur amont n'est pas relayé : il peut contenir des
      // informations de compte qui n'ont rien à faire dans un navigateur.
      return json(
        { erreur: `L'API a refusé la requête (HTTP ${reponseApi.status}).` },
        502,
        cors,
      );
    }

    let texte = "";
    try {
      const donnees = await reponseApi.json();
      texte = donnees?.choices?.[0]?.message?.content?.trim() || "";
    } catch {
      return json({ erreur: "Réponse de l'API illisible." }, 502, cors);
    }

    return json(
      {
        reponse: texte || "Réponse vide.",
        limitation_active: debit.actif,
        questions_restantes: debit.restant,
      },
      200,
      cors,
    );
  },
};
