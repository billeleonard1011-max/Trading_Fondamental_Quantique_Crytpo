/**
 * Construction des quatre rubriques, sur la même trame.
 *
 * Chaque fonction prend l'état de chargement d'une source et rend du HTML.
 * Aucune ne suppose que la source a répondu : c'est le cas normal, pas le
 * cas d'erreur.
 */

import { CONFIG } from "./config.js";
import { lire } from "./donnees.js";
import { ABSENT, echapper, compteARebours, dateHeure, nombre, pourcent } from "./format.js";
import {
  rendreAge, rendreBadge, rendreIndisponible, rendreLibelleAvecInfobulle,
  rendreMetrique, rendrePrecedents, rendreTrame, tonDuBiais,
} from "./rendu.js";
import { libelle } from "./libelles.js";

/**
 * Rend l'en-tête chiffré de la rubrique Or.
 *
 * @param {object} or Rapport or.
 * @returns {string} HTML des métriques clés.
 */
function enTeteOr(or) {
  const jv = lire(or, "juste_valeur", {});
  const cot = lire(or, "positionnement_cot", {});
  const cal = lire(or, "calendrier", {});
  const morceaux = [];

  // Horodatage de la dernière exécution complète du moteur, avant toute
  // métrique : c'est la première question à laquelle un lecteur pressé a
  // droit — « ces chiffres datent de quand ? ». Rendu hors de la grille des
  // métriques clés, sur sa propre ligne.
  const horodatageMoteur = lire(or, "meta.horodatage_utc", null);
  const dateRapport = lire(or, "meta.date", null);
  const ligneFraicheur = `<p class="fraicheur">
    Dernière exécution complète du moteur :
    <strong>${horodatageMoteur ? echapper(dateHeure(horodatageMoteur)) : ABSENT}</strong>
    ${dateRapport ? `(rapport du ${echapper(dateRapport)})` : ""}
  </p>`;

  // Écart à la juste valeur.
  if (jv.disponible && jv.fiable) {
    morceaux.push(`
      <div class="cle">
        <span class="cle-libelle">Écart à la juste valeur</span>
        <span class="cle-valeur">${pourcent(jv.ecart_pct)}</span>
        <span class="cle-detail">${nombre(jv.z_score, 2, true)} écart-type</span>
      </div>`);
  } else if (jv.disponible && !jv.fiable) {
    morceaux.push(`
      <div class="cle cle--avertie">
        <span class="cle-libelle">Écart à la juste valeur</span>
        <span class="cle-valeur">non interprétable</span>
        <span class="cle-detail">R² de ${nombre(jv.r2, 2)}, sous le seuil de ${nombre(jv.seuil_r2, 2)}</span>
      </div>`);
  } else {
    morceaux.push(`<div class="cle cle--absente">
      <span class="cle-libelle">Écart à la juste valeur</span>
      ${rendreIndisponible(jv.motif)}</div>`);
  }

  // Positionnement COT.
  if (cot.disponible && cot.percentile_managed_money !== null) {
    morceaux.push(`
      <div class="cle">
        <span class="cle-libelle">Positionnement spéculatif</span>
        <span class="cle-valeur">${nombre(cot.percentile_managed_money, 0)}<sup>e</sup> pct</span>
        <span class="cle-detail">donnée du ${echapper(cot.date_observation || ABSENT)},
          ${cot.age_jours} jour(s)</span>
      </div>`);
  } else {
    morceaux.push(`<div class="cle cle--absente">
      <span class="cle-libelle">Positionnement spéculatif</span>
      ${rendreIndisponible(cot.motif)}</div>`);
  }

  // Compte à rebours macro.
  const prochaine = (cal.echeances || [])[0];
  if (prochaine) {
    const silence = cal.en_fenetre_de_silence === true;
    morceaux.push(`
      <div class="cle ${silence ? "cle--silence" : ""}">
        <span class="cle-libelle">Prochaine échéance macro</span>
        <span class="cle-valeur">${echapper(compteARebours(prochaine.minutes_restantes))}</span>
        <span class="cle-detail">${echapper(prochaine.nom)}${
          prochaine.heure_conventionnelle ? " — heure d'usage, non garantie" : ""
        }</span>
        ${silence ? '<span class="silence-alerte">Fenêtre de silence : l\'analyse fondamentale n\'a pas de prise sur ces minutes.</span>' : ""}
      </div>`);
  } else {
    morceaux.push(`<div class="cle cle--absente">
      <span class="cle-libelle">Prochaine échéance macro</span>
      ${rendreIndisponible(cal.motif || "aucune échéance connue")}</div>`);
  }

  return `${ligneFraicheur}<div class="cles">${morceaux.join("")}</div>`;
}

/**
 * Rend la rubrique Or.
 *
 * @param {object} etat État de chargement de la source or.
 * @returns {{resume: string, corps: string, ton: string}} Contenu de la rubrique.
 */
export function rubriqueOr(etat) {
  if (!etat || !etat.disponible) {
    return {
      resume: rendreBadge("indisponible", "alerte"),
      corps: rendreIndisponible(etat ? etat.motif : "rapport non chargé"),
      ton: "alerte",
    };
  }
  const or = etat.donnees;
  const biais = lire(or, "biais", {});
  const jv = lire(or, "juste_valeur", {});
  const explications = lire(or, "explications.explications", {});
  const analogues = lire(or, "analogues", {});

  const ton = tonDuBiais(biais.biais);
  const resume = [
    rendreBadge(biais.biais || "inconnu", ton),
    `<span class="resume-detail">conviction ${echapper(biais.conviction || ABSENT)}</span>`,
    biais.donnees_partielles
      ? rendreBadge("données partielles", "alerte")
      : "",
  ].join(" ");

  // Contributions : le détail par composante, sans lequel un score global
  // ne s'explique ni ne s'améliore.
  const composantes = (biais.composantes || [])
    .map((c) => {
      const etiquette = rendreLibelleAvecInfobulle(c.nom);
      if (!c.disponible) {
        // Doit sauter aux yeux que cette ligne ne contribue pas au score :
        // une composante muette comptée comme neutre ferait passer une
        // absence d'information pour un signal d'équilibre. Pour l'écart de
        // juste valeur désactivé pour R² insuffisant, jv.lecture porte déjà
        // l'explication complète — chiffres compris — écrite par le module
        // qui a fait le calcul ; on la réutilise plutôt que d'en réécrire
        // une, générique, côté navigateur.
        const detail = c.nom === "ecart_juste_valeur" && jv.lecture ? jv.lecture : c.motif;
        return `<li class="composante composante--absente composante--desactivee">
          <span>${etiquette}
            <span class="composante-pastille-off">exclue du calcul</span></span>
          <span class="composante-motif">${echapper(detail)}</span></li>`;
      }
      const largeur = Math.min(100, Math.abs(Number(c.contribution)) * 250);
      const sens = Number(c.contribution) >= 0 ? "positif" : "negatif";
      return `<li class="composante">
        <span>${etiquette}</span>
        <span class="composante-barre">
          <i class="composante-remplissage composante-remplissage--${sens}"
             style="width:${largeur}%"></i></span>
        <span class="composante-valeur">${nombre(c.contribution, 3, true)}</span></li>`;
    })
    .join("");

  const invalidations = (biais.invalidations || [])
    .map((i) => `<li>${echapper(i.lecture || `${i.variable} ${i.operateur} ${i.seuil}`)}</li>`)
    .join("");

  const metriqueJv = jv.disponible
    ? rendreMetrique({
        titre: "Écart à la juste valeur",
        valeur: `${pourcent(jv.ecart_pct)} <small>(${nombre(jv.z_score, 2, true)} σ)</small>`,
        percentile: jv.percentile_historique,
        borneBasse: "bon marché",
        borneHaute: "cher",
        explication: lire(explications, "juste_valeur.texte", ""),
        precedents: analogues.agregation,
        cleAide: "Que signifie l'écart actuel à la juste valeur de l'or ?",
      })
    : rendreIndisponible(jv.motif);

  const corps = `
    ${enTeteOr(or)}
    ${rendreTrame({
      etat: `<p>Biais <strong>${echapper(biais.biais || ABSENT)}</strong>,
        score composite ${nombre(biais.score_composite, 3, true)},
        conviction ${echapper(biais.conviction || ABSENT)},
        sur ${nombre(Number(biais.couverture_donnees) * 100, 0)} % des composantes.</p>
        ${biais.avertissement ? `<p class="avertissement">${echapper(biais.avertissement)}</p>` : ""}`,
      changement: composantes
        ? `<ul class="composantes">${composantes}</ul>`
        : "<p>Aucune décomposition publiée.</p>",
      impact: metriqueJv,
      invalidation: invalidations
        ? `<ul class="invalidations">${invalidations}</ul>`
        : "<p>Aucune condition d'invalidation publiée.</p>",
      sources: `<ul>
        <li>Juste valeur : ${echapper(lire(or, "juste_valeur._meta.source", ABSENT))}
          ${rendreAge(lire(or, "juste_valeur._meta"), CONFIG.seuilsAge.juste_valeur)}</li>
        <li>Positionnement : ${echapper(lire(or, "positionnement_cot._meta.source", ABSENT))}
          ${rendreAge(lire(or, "positionnement_cot._meta"), CONFIG.seuilsAge.positionnement_cot)}</li>
        <li>Calendrier : ${echapper(lire(or, "calendrier._meta.source", ABSENT))}</li>
        <li class="note">${echapper(lire(or, "calendrier.avertissement_heures", ""))}</li>
      </ul>`,
    })}`;

  return { resume, corps, ton };
}

/**
 * Réduit un texte à une forme comparable : minuscules, sans accent.
 *
 * Même normalisation que ``modules.geopolitique.feed._normaliser`` côté
 * Python — reproduite ici plutôt que partagée, JS et Python ne pouvant pas
 * exécuter le même code source (voir worker-scanner/README.md pour la même
 * contrainte, déjà rencontrée pour le scanner en direct).
 *
 * @param {string} texte Texte à normaliser.
 * @returns {string} Le texte en minuscules, sans accent ni ponctuation.
 */
export function normaliserTexteGeo(texte) {
  return String(texte || "")
    .toLowerCase()
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[^\w\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Dit si un titre relève d'un des dossiers de conflit configurés.
 *
 * @param {string} titre Titre de l'article.
 * @param {Array<object>} dossiers Dossiers du rapport, avec leurs ``mots_cles``.
 * @returns {boolean} ``true`` si un mot-clé d'un dossier apparaît dans le titre.
 */
export function estLieAUnDossier(titre, dossiers) {
  const normalise = normaliserTexteGeo(titre);
  if (!normalise) return false;
  return (dossiers || []).some((d) =>
    (d.mots_cles || []).some((mc) => {
      const cle = normaliserTexteGeo(mc);
      return cle && normalise.includes(cle);
    }),
  );
}

/**
 * Rend la liste des maillons de la chaîne de transmission d'un dossier.
 *
 * @param {object} chaine Bloc ``chaine_de_transmission`` d'un dossier.
 * @returns {string} HTML de la liste, vide si aucun maillon.
 */
function rendreMaillonsDossier(chaine) {
  const maillons = Object.entries((chaine || {}).maillons || {})
    .map(([cle, m]) => {
      // m.libelle est toujours renseigné par modules/gold/geopolitics.py en
      // pratique ; le repli sur libelle(cle) est défensif pour un maillon
      // futur qui oublierait de le fournir.
      const etiquette = echapper(m.libelle || libelle(cle));
      if (!m.disponible) {
        return `<li class="composante--absente"><span>${etiquette}</span>
          <span class="composante-motif">${echapper(m.motif)}</span></li>`;
      }
      const v = m.variation === null || m.variation === undefined
        ? ABSENT
        : `${nombre(m.variation, m.unite_variation === "points de base" ? 0 : 1, true)} ${
            echapper(m.unite_variation || "")
          }`;
      return `<li><span>${etiquette}</span><span>${v}</span></li>`;
    })
    .join("");
  return maillons ? `<ul class="liste-detail chaine">${maillons}</ul>` : "";
}

/**
 * Rend une liste de développements (articles nommés et sourcés).
 *
 * @param {Array<object>} items Articles, avec ``titre``, ``url``, ``source``,
 *   ``horodatage_utc``.
 * @param {Set<string>} nouveaux Identifiants des items à mettre en évidence
 *   comme nouveaux depuis la dernière vérification.
 * @returns {string} HTML de la liste, ou un message explicite si elle est vide.
 */
function rendreDeveloppements(items, nouveaux = new Set()) {
  if (!items || !items.length) {
    return `<p class="fil-vide">Aucun développement récent trouvé.</p>`;
  }
  const lignes = items.map((it) => `
    <li class="fil-item ${nouveaux.has(it.id) ? "fil-item--nouveau" : ""}">
      <a href="${echapper(it.url)}" target="_blank" rel="noopener noreferrer">${echapper(it.titre)}</a>
      <div class="fil-meta"><span>${echapper(it.source || "")}</span>
        <span>${echapper(dateHeure(it.horodatage_utc))}</span></div>
    </li>`).join("");
  return `<ul class="fil-liste">${lignes}</ul>`;
}

/**
 * Rend le panneau d'un dossier de conflit.
 *
 * @param {object} dossier Dossier mesuré (voir modules/gold/geopolitics.py::Dossier).
 * @param {object} meta Bloc ``_meta`` du rapport géopolitique, pour l'âge de la donnée.
 * @param {string} source Source déclarée du bloc géopolitique.
 * @returns {string} HTML du panneau.
 */
function rendreDossierPanneau(dossier, meta, source) {
  if (!dossier.disponible) {
    return rendreIndisponible(dossier.motif);
  }
  const nouveaux = new Set((dossier.nouveaux_developpements || []).map((n) => n.id));
  const evenements = dossier.n_evenements_bilateraux === null || dossier.n_evenements_bilateraux === undefined
    ? `<p class="composante-motif">${echapper(dossier.motif_events || "activité par acteur non mesurée")}</p>`
    : `<p>${dossier.n_evenements_bilateraux} événement(s) bilatéral(aux) dans le dernier
        relevé GDELT Events (instantané, pas une tendance).</p>`;

  return rendreTrame({
    etat: `<p>${echapper(dossier.etat_actuel)}</p>`,
    changement: `${evenements}${rendreDeveloppements(dossier.developpements_recents, nouveaux)}`,
    impact: `${rendreMaillonsDossier(dossier.chaine_de_transmission)}
      <p class="metrique-sens">${echapper((dossier.deja_dans_les_prix || {}).commentaire || "Prime déjà payée : non évaluée.")}</p>`,
    invalidation: `<p>${echapper(dossier.invalidation)}</p>`,
    sources: `<ul><li>${echapper(source || ABSENT)}
      ${rendreAge(meta, CONFIG.seuilsAge.geopolitique)}</li></ul>`,
  });
}

/**
 * Rend le panneau « Autres » : actualités géopolitiques hors des dossiers suivis.
 *
 * @param {Array<object>} filGeopolitique Items du fil géopolitique général.
 * @param {Array<object>} dossiers Dossiers configurés, pour exclure ce qui leur est déjà lié.
 * @returns {string} HTML du panneau.
 */
function rendreAutresPanneau(filGeopolitique, dossiers) {
  const autres = (filGeopolitique || []).filter(
    (item) => !estLieAUnDossier(item.titre_affiche || item.titre, dossiers),
  );
  if (!autres.length) {
    return `<p class="fil-vide">Aucune actualité géopolitique hors des dossiers suivis pour l'instant.</p>`;
  }
  const lignes = autres.slice(0, 15).map((it) => `
    <li class="fil-item">
      <a href="${echapper(it.url_source || it.url || "#")}" target="_blank" rel="noopener noreferrer">
        ${echapper(it.titre_affiche || it.titre)}</a>
      <div class="fil-meta"><span>${echapper(it.source_nom || it.source || "")}</span>
        <span>${echapper(dateHeure(it.horodatage_utc))}</span></div>
    </li>`).join("");
  return `<ul class="fil-liste">${lignes}</ul>
    <p class="metrique-sens">Ne relève d'aucun dossier suivi pour l'or (voir
    config/geopolitique_dossiers.yaml) — affiché à titre d'information, sans impact chiffré.</p>`;
}

/**
 * Rend la rubrique Géopolitique : un onglet par dossier de conflit suivi,
 * plus un onglet « Autres » pour ce qui n'entre dans aucun dossier configuré.
 *
 * Remplace l'ancienne organisation par thèmes économiques abstraits
 * (tensions énergétiques, sanctions...) : l'utilisateur suit des conflits
 * nommés dans la durée, pas des scores d'intensité déconnectés de tout
 * narratif — voir modules/gold/geopolitics.py::analyser_dossiers.
 *
 * @param {object} etat État de chargement de la source or.
 * @param {Array<object>} filGeopolitique Items du fil géopolitique général,
 *   pour l'onglet « Autres ». Tableau vide si non chargé.
 * @returns {{resume: string, corps: string, ton: string}} Contenu de la rubrique.
 */
export function rubriqueGeopolitique(etat, filGeopolitique = []) {
  if (!etat || !etat.disponible) {
    return {
      resume: rendreBadge("indisponible", "alerte"),
      corps: rendreIndisponible(etat ? etat.motif : "rapport non chargé"),
      ton: "alerte",
    };
  }
  const geo = lire(etat.donnees, "geopolitique", {});
  if (!geo.disponible) {
    return {
      resume: rendreBadge("indisponible", "alerte"),
      corps: rendreIndisponible(geo.motif),
      ton: "alerte",
    };
  }

  const dossiers = geo.dossiers || [];
  const intensite = geo.intensite_max;
  const ton = intensite !== null && Number(intensite) >= 2 ? "alerte" : "neutre";
  const dominant = dossiers.find((d) => d.id === geo.dossier_dominant);
  const resume = [
    rendreBadge(
      intensite === null || intensite === undefined
        ? "intensité inconnue"
        : `${nombre(intensite, 1)}× la normale`,
      ton,
    ),
    `<span class="resume-detail">${
      dominant ? echapper(dominant.nom_affiche) : `${geo.n_dossiers_mesures}/${geo.n_dossiers_configures} dossiers mesurés`
    }</span>`,
  ].join(" ");

  const meta = lire(etat.donnees, "geopolitique._meta");
  const onglets = [
    ...dossiers.map((d) => ({ id: d.id, libelle: d.nom_affiche })),
    { id: "autres", libelle: "Autres" },
  ];
  const boutonsHtml = onglets
    .map((o, i) => `<button class="fil-onglet geo-onglet" type="button" role="tab"
      data-dossier="${echapper(o.id)}" aria-selected="${i === 0}">${echapper(o.libelle)}</button>`)
    .join("");
  const panneauxHtml = [
    ...dossiers.map((d, i) => `<div class="geo-panneau" data-dossier="${echapper(d.id)}" ${i === 0 ? "" : "hidden"}>
      ${rendreDossierPanneau(d, meta, geo.source)}</div>`),
    `<div class="geo-panneau" data-dossier="autres" hidden>
      ${rendreAutresPanneau(filGeopolitique, dossiers)}</div>`,
  ].join("");

  return {
    resume,
    corps: `<div class="geo-onglets fil-onglets" id="geo-onglets" role="tablist">${boutonsHtml}</div>
      <div class="geo-panneaux">${panneauxHtml}</div>`,
    ton,
  };
}

/**
 * Installe la bascule entre les onglets de dossiers de la rubrique Géopolitique.
 *
 * Même principe que ``installerFil`` (accueil.js) : un seul écouteur posé
 * une fois, qui bascule la visibilité plutôt que de reconstruire le HTML —
 * les panneaux sont déjà tous rendus, plus léger à cacher qu'à reconstruire
 * au clic.
 */
export function installerOngletsGeopolitique() {
  const conteneur = document.getElementById("geo-onglets");
  if (!conteneur) return;
  conteneur.addEventListener("click", (evenement) => {
    const bouton = evenement.target.closest(".geo-onglet");
    if (!bouton) return;
    const cible = bouton.dataset.dossier;
    for (const b of conteneur.querySelectorAll(".geo-onglet")) {
      b.setAttribute("aria-selected", String(b.dataset.dossier === cible));
    }
    const panneaux = conteneur.parentElement.querySelectorAll(".geo-panneau");
    for (const p of panneaux) {
      p.hidden = p.dataset.dossier !== cible;
    }
  });
}

/**
 * Rend la rubrique Quantique.
 *
 * @param {object} etat État de chargement de la source quantique.
 * @returns {{resume: string, corps: string, ton: string}} Contenu de la rubrique.
 */
export function rubriqueQuantique(etat) {
  if (!etat || !etat.disponible) {
    return {
      resume: rendreBadge("indisponible", "alerte"),
      corps: rendreIndisponible(etat ? etat.motif : "rapport non chargé"),
      ton: "alerte",
    };
  }
  const q = etat.donnees;
  const mouvements = lire(q, "mouvements", {});
  const secteur = lire(q, "secteur", {});
  const correlation = lire(secteur, "correlation_positions", {});

  const n = mouvements.n_mouvements || 0;
  const ton = n > 0 ? "alerte" : "neutre";
  const resume = [
    rendreBadge(n === 0 ? "aucun mouvement marqué" : `${n} mouvement(s)`, ton),
    correlation.correlation_elevee
      ? rendreBadge(`corrélation ${nombre(correlation.correlation_max, 2)}`, "alerte")
      : "",
  ].join(" ");

  const listeMouvements = (mouvements.mouvements || [])
    .map((m) => `<li>
      <strong>${echapper(m.ticker)}</strong> ${pourcent(m.variation_pct)}
      ${rendreBadge(libelle(m.classification, m.classification), m.classification === "sectoriel" ? "neutre" : "alerte")}
      <p class="constat">${echapper(m.constat)}</p>
      ${(m.signaux_contradictoires || []).map((s) =>
        `<p class="contradiction"><strong>${echapper(s.nature)}</strong> — ${echapper(s.constat)}</p>`).join("")}
    </li>`)
    .join("");

  return {
    resume,
    corps: rendreTrame({
      etat: `<p>${n === 0
        ? "Aucune valeur suivie ne dépasse son seuil de mouvement du jour."
        : `${n} valeur(s) au-delà de leur seuil.`}</p>`,
      changement: listeMouvements
        ? `<ul class="liste-detail mouvements">${listeMouvements}</ul>`
        : "<p>Aucun mouvement à expliquer aujourd'hui.</p>",
      impact: correlation.disponible
        ? `<p>${echapper(correlation.avertissement || `Corrélation maximale de ${nombre(correlation.correlation_max, 2)} sur ${correlation.n_seances_effectives} séances.`)}</p>`
        : rendreIndisponible(correlation.motif),
      invalidation: `<p>Un mouvement classé sectoriel cesse de l'être si les autres
        valeurs suivies ne l'accompagnent plus avec une amplitude comparable.</p>`,
      sources: `<ul><li>${echapper(lire(q, "mouvements._meta.source", ABSENT))}
        ${rendreAge(lire(q, "mouvements._meta"), CONFIG.seuilsAge.defaut)}</li></ul>`,
    }),
    ton,
  };
}

/**
 * Rend la liste de suivi des dix cryptos de ``config/universe.yaml``.
 *
 * Chaque jeton affiche son prix, ses variations et son statut de déblocage
 * de jetons. Les statuts ne se valent pas : un calendrier connu (``actif``),
 * un vesting achevé, un mécanisme inexistant par nature (``non_applicable``)
 * et une simple absence de source (``inconnu``) racontent quatre histoires
 * différentes — les confondre ferait passer une ignorance pour une garantie
 * d'absence de risque. Chacun porte donc sa propre classe visuelle.
 *
 * @param {object} c Rapport crypto complet (``etat.donnees``).
 * @returns {string} HTML de la grille de suivi, ou un message d'absence.
 */
function rendreWatchlistCrypto(c) {
  const positions = lire(c, "positionnement.positions", {});
  if (!positions.disponible) {
    return `<p class="avertissement">Suivi des positions crypto indisponible :
      ${echapper(positions.motif || "motif non précisé")}.</p>`;
  }

  const jetons = positions.positions || [];
  const deblocages = lire(c, "positionnement.deblocages_tokens", {});
  const statutsParSymbole = new Map(
    (deblocages.jetons || []).map((j) => [j.symbole, j]),
  );

  const cartes = jetons
    .map((j) => {
      if (!j.disponible) {
        return `<li class="watchlist-jeton">
          <div class="watchlist-entete">
            <span class="watchlist-symbole">${echapper(j.symbole)}</span>
          </div>
          <p class="composante-motif">${echapper(j.motif || "donnée indisponible")}</p>
        </li>`;
      }

      const d = statutsParSymbole.get(j.symbole) || null;
      const statut = d ? d.statut : null;
      // signification_statut est déjà écrit par modules/crypto/positioning.py
      // pour ce jeton précis (« calendrier connu, échéance à venir », etc.) ;
      // on l'affiche tel quel plutôt que de le réduire à une info-bulle, pour
      // qu'un statut ne se lise jamais seul. La classe watchlist-statut--X
      // distingue visuellement les cinq statuts : les confondre ferait
      // passer une simple absence de source (inconnu) pour une garantie
      // qu'aucun déblocage n'est prévu (non_applicable).
      const explicationStatut = d && d.signification_statut ? d.signification_statut : "";
      const badgeStatut = statut
        ? `<p class="watchlist-statut watchlist-statut--${echapper(statut)}">
            <strong>${echapper(libelle(statut))}</strong>${
              explicationStatut ? ` — ${echapper(explicationStatut)}` : ""
            }
          </p>`
        : "";

      const prochain = d && d.prochain_deblocage;
      const ligneProchain = prochain
        ? `<p class="composante-motif">Prochaine échéance : ${echapper(prochain.date)}
            (${nombre(prochain.part_offre_pct, 1)} % de l'offre)</p>`
        : "";

      const avertissement = j.avertissement
        ? `<p class="watchlist-avertissement">${echapper(j.avertissement)}</p>`
        : "";

      return `<li class="watchlist-jeton">
        <div class="watchlist-entete">
          <span class="watchlist-symbole">${echapper(j.symbole)}</span>
          <span class="watchlist-prix">${nombre(j.prix_usd, j.prix_usd < 1 ? 4 : 2)} $</span>
        </div>
        <div class="watchlist-variations">
          <span>24h <strong>${pourcent(j.variation_24h_pct)}</strong></span>
          <span>7j <strong>${pourcent(j.variation_7j_pct)}</strong></span>
          <span>30j <strong>${pourcent(j.variation_30j_pct)}</strong></span>
        </div>
        ${badgeStatut}
        ${ligneProchain}
        ${avertissement}
      </li>`;
    })
    .join("");

  const limite = deblocages.limite_connue
    ? `<p class="composante-motif">${echapper(deblocages.limite_connue)}</p>`
    : "";

  return `<ul class="watchlist">${cartes}</ul>${limite}`;
}

/**
 * Rend la rubrique Crypto.
 *
 * @param {object} etat État de chargement de la source crypto.
 * @returns {{resume: string, corps: string, ton: string}} Contenu de la rubrique.
 */
export function rubriqueCrypto(etat) {
  if (!etat || !etat.disponible) {
    return {
      resume: rendreBadge("indisponible", "alerte"),
      corps: rendreIndisponible(etat ? etat.motif : "rapport non chargé"),
      ton: "alerte",
    };
  }
  const c = etat.donnees;
  const btc = lire(c, "regime.regime_btc", {});
  const eth = lire(c, "regime.regime_eth", {});
  const rotation = lire(c, "rotation.synthese", {});

  const resume = [
    rendreBadge(`BTC ${btc.regime || "indéterminé"}`, "neutre"),
    rendreBadge(`ETH ${eth.regime || "indéterminé"}`, "neutre"),
    rendreBadge(`rotation ${rotation.etat || "inconnue"}`,
      rotation.etat === "indetermine" ? "alerte" : "neutre"),
  ].join(" ");

  const regime = (bloc, nom) => {
    if (!bloc.disponible) return `<li>${nom} : ${echapper(bloc.motif || "indisponible")}</li>`;
    return `<li><strong>${nom}</strong> — ${rendreLibelleAvecInfobulle(bloc.regime)} (MVRV ${nombre(bloc.mvrv, 2)})
      <p class="constat">${echapper(bloc.description || "")}</p>
      ${bloc.avertissement_calibrage
        ? `<p class="avertissement">${echapper(bloc.avertissement_calibrage)}</p>` : ""}
      <p class="constat">${echapper(lire(bloc, "invalidation.condition", ""))}</p></li>`;
  };

  const contributions = (rotation.contributions || [])
    .map((x) => `<li><span>${rendreLibelleAvecInfobulle(x.mesure)}</span>
      <span>${x.vote ? echapper(libelle(x.vote)) : "abstention"}</span>
      ${x.abstention ? `<span class="composante-motif">${echapper(x.motif_abstention)}</span>` : ""}</li>`)
    .join("");

  const nonAlimentes = (lire(c, "meta.indicateurs_non_alimentes", []) || [])
    .map((x) => `<li>${echapper(x.indicateur)} : ${echapper(x.motif)}</li>`)
    .join("");

  const dateRapport = lire(c, "meta.date", null);
  const ligneFraicheurCrypto = `<p class="fraicheur">
    Dernière exécution complète du moteur crypto :
    <strong>${echapper(dateHeure(lire(c, "meta.horodatage_utc", null)))}</strong>
    ${dateRapport ? `(rapport du ${echapper(dateRapport)})` : ""}
  </p>`;

  return {
    resume,
    corps: `
      ${ligneFraicheurCrypto}
      <h3>Suivi des dix cryptos</h3>
      ${rendreWatchlistCrypto(c)}
      ${rendreTrame({
        etat: `<ul class="liste-detail">${regime(btc, "Bitcoin")}${regime(eth, "Ether")}</ul>`,
        changement: contributions
          ? `<ul class="liste-detail">${contributions}</ul>`
          : "",
        impact: `<p>${echapper(rotation.justification || "Rotation non évaluée.")}</p>`,
        invalidation: `<p>${echapper(lire(rotation, "invalidation.condition", "Non publiée."))}</p>`,
        sources: `<ul>
          <li>${echapper(lire(c, "regime._meta.source", ABSENT))}
            ${rendreAge(lire(c, "regime._meta"), CONFIG.seuilsAge.defaut)}</li>
          ${nonAlimentes ? `<li>Indicateurs non alimentés :<ul>${nonAlimentes}</ul></li>` : ""}
        </ul>`,
      })}`,
    ton: "neutre",
  };
}
