/**
 * Chargement des rapports, avec dégradation par fichier.
 *
 * Chaque source est chargée indépendamment : un rapport absent ou illisible
 * n'empêche pas les autres de s'afficher. C'est la règle qui vaut dans tout
 * le projet, appliquée ici au navigateur — une panne du module crypto ne
 * doit pas emporter la page de l'or.
 */

/**
 * Ajoute un paramètre anti-cache à une adresse.
 *
 * ``fetch(url, {cache: "no-store"})`` ne contrôle que le cache HTTP local du
 * navigateur : le CDN de GitHub Pages sert le JSON avec un
 * ``Cache-Control: max-age=600`` qui lui est indifférent, et peut donc
 * renvoyer une version vieille de dix minutes malgré cette option. Un
 * paramètre de requête qui change à chaque appel contourne le cache d'arête,
 * puisqu'il fabrique une URL que le CDN n'a encore jamais vue.
 *
 * @param {string} url Adresse d'origine.
 * @returns {string} L'adresse avec un paramètre ``v`` ajouté.
 */
function contournerLeCache(url) {
  const separateur = url.includes("?") ? "&" : "?";
  return `${url}${separateur}v=${Date.now()}`;
}

/**
 * Charge un document JSON, sans jamais lever d'exception à l'appelant.
 *
 * @param {string} url Adresse du document.
 * @param {Function} recuperer Implémentation de fetch, injectable pour les tests.
 * @returns {Promise<{disponible: boolean, donnees: *, motif: string}>} État du chargement.
 */
export async function chargerJson(url, recuperer = globalThis.fetch) {
  try {
    const reponse = await recuperer(contournerLeCache(url), { cache: "no-store" });
    if (!reponse.ok) {
      return {
        disponible: false,
        donnees: null,
        motif: `fichier introuvable ou inaccessible (HTTP ${reponse.status})`,
      };
    }
    const donnees = await reponse.json();
    return { disponible: true, donnees, motif: "" };
  } catch (erreur) {
    // Un JSON tronqué lève ici, tout comme une coupure réseau. Les deux se
    // traitent pareil du point de vue de l'affichage : le bloc dira ce qui
    // manque au lieu de rester vide.
    return {
      disponible: false,
      donnees: null,
      motif: `document illisible (${erreur && erreur.name ? erreur.name : "erreur"})`,
    };
  }
}

/**
 * Charge un document JSON par lignes.
 *
 * Une ligne corrompue est ignorée sans faire perdre les autres : un
 * historique en ajout seul peut se terminer par une écriture partielle.
 *
 * @param {string} url Adresse du document.
 * @param {Function} recuperer Implémentation de fetch.
 * @returns {Promise<{disponible: boolean, lignes: Array, motif: string}>} État du chargement.
 */
export async function chargerJsonl(url, recuperer = globalThis.fetch) {
  try {
    const reponse = await recuperer(contournerLeCache(url), { cache: "no-store" });
    if (!reponse.ok) {
      return {
        disponible: false,
        lignes: [],
        motif: `historique introuvable (HTTP ${reponse.status})`,
      };
    }
    const texte = await reponse.text();
    const lignes = [];
    let ignorees = 0;
    for (const ligne of texte.split("\n")) {
      const propre = ligne.trim();
      if (!propre) continue;
      try {
        lignes.push(JSON.parse(propre));
      } catch {
        ignorees += 1;
      }
    }
    return {
      disponible: lignes.length > 0,
      lignes,
      motif: lignes.length
        ? ""
        : "historique présent mais illisible : aucune ligne exploitable",
      lignesIgnorees: ignorees,
    };
  } catch (erreur) {
    return {
      disponible: false,
      lignes: [],
      motif: `historique illisible (${erreur && erreur.name ? erreur.name : "erreur"})`,
    };
  }
}

/**
 * Lit une valeur imbriquée sans jamais lever d'exception.
 *
 * @param {*} racine Structure source.
 * @param {string} chemin Chemin pointé, par exemple « juste_valeur.z_score ».
 * @param {*} defaut Valeur rendue si le chemin n'existe pas.
 * @returns {*} La valeur trouvée, ou la valeur par défaut.
 */
export function lire(racine, chemin, defaut = null) {
  if (!racine) return defaut;
  let courant = racine;
  for (const cle of String(chemin).split(".")) {
    if (courant === null || courant === undefined || typeof courant !== "object") {
      return defaut;
    }
    courant = courant[cle];
  }
  return courant === undefined || courant === null ? defaut : courant;
}

/**
 * Charge toutes les sources du site en parallèle.
 *
 * @param {object} sources Table des adresses, issue de la configuration.
 * @param {Function} recuperer Implémentation de fetch.
 * @returns {Promise<object>} Un état par source.
 */
export async function chargerTout(sources, recuperer = globalThis.fetch) {
  const cles = Object.keys(sources).filter((c) => c !== "historiqueBiais");
  const resultats = await Promise.all(
    cles.map((cle) => chargerJson(sources[cle], recuperer)),
  );
  const etat = {};
  cles.forEach((cle, i) => {
    etat[cle] = resultats[i];
  });
  if (sources.historiqueBiais) {
    etat.historiqueBiais = await chargerJsonl(sources.historiqueBiais, recuperer);
  }
  return etat;
}
