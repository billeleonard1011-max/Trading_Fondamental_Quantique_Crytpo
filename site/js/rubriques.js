/**
 * Construction des quatre rubriques, sur la même trame.
 *
 * Chaque fonction prend l'état de chargement d'une source et rend du HTML.
 * Aucune ne suppose que la source a répondu : c'est le cas normal, pas le
 * cas d'erreur.
 */

import { CONFIG } from "./config.js";
import { lire } from "./donnees.js";
import { ABSENT, echapper, compteARebours, nombre, pourcent } from "./format.js";
import {
  rendreAge, rendreBadge, rendreIndisponible, rendreMetrique,
  rendrePrecedents, rendreTrame, tonDuBiais,
} from "./rendu.js";

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

  return `<div class="cles">${morceaux.join("")}</div>`;
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
      if (!c.disponible) {
        return `<li class="composante composante--absente">
          <span>${echapper(c.nom)}</span>
          <span class="composante-motif">${echapper(c.motif)}</span></li>`;
      }
      const largeur = Math.min(100, Math.abs(Number(c.contribution)) * 250);
      const sens = Number(c.contribution) >= 0 ? "positif" : "negatif";
      return `<li class="composante">
        <span>${echapper(c.nom)}</span>
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
 * Rend la rubrique Géopolitique, tirée du rapport or.
 *
 * @param {object} etat État de chargement de la source or.
 * @returns {{resume: string, corps: string, ton: string}} Contenu de la rubrique.
 */
export function rubriqueGeopolitique(etat) {
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

  const intensite = geo.intensite_max;
  const ton = intensite !== null && Number(intensite) >= 2 ? "alerte" : "neutre";
  const resume = [
    rendreBadge(
      intensite === null || intensite === undefined
        ? "intensité inconnue"
        : `${nombre(intensite, 1)}× la normale`,
      ton,
    ),
    `<span class="resume-detail">${geo.n_themes_mesures}/${geo.n_themes_configures} thèmes mesurés</span>`,
  ].join(" ");

  const themes = (geo.themes || [])
    .map((t) => {
      if (!t.disponible) {
        return `<li class="composante--absente"><span>${echapper(t.nom)}</span>
          <span class="composante-motif">${echapper(t.motif)}</span></li>`;
      }
      return `<li><span>${echapper(t.nom)}</span>
        <span>${t.intensite_ratio === null ? ABSENT : `${nombre(t.intensite_ratio, 1)}×`}</span>
        <span class="composante-motif">${echapper(t.trajectoire || "trajectoire non mesurée")}</span></li>`;
    })
    .join("");

  const chaine = lire(geo, "chaine_de_transmission", {});
  const maillons = Object.entries(chaine.maillons || {})
    .map(([cle, m]) => {
      if (!m.disponible) {
        return `<li class="composante--absente"><span>${echapper(m.libelle || cle)}</span>
          <span class="composante-motif">${echapper(m.motif)}</span></li>`;
      }
      const v = m.variation === null || m.variation === undefined
        ? ABSENT
        : `${nombre(m.variation, m.unite_variation === "points de base" ? 0 : 1, true)} ${
            echapper(m.unite_variation || "")
          }`;
      return `<li><span>${echapper(m.libelle || cle)}</span><span>${v}</span></li>`;
    })
    .join("");

  const deja = lire(geo, "deja_dans_les_prix", {});

  return {
    resume,
    corps: rendreTrame({
      etat: `<p>${echapper(geo.theme_dominant || "Aucun thème dominant")} —
        ${chaine.commentaire ? echapper(chaine.commentaire) : "chaîne non mesurée"}</p>`,
      changement: themes ? `<ul class="liste-detail">${themes}</ul>` : "",
      impact: maillons ? `<ul class="liste-detail chaine">${maillons}</ul>` : "",
      invalidation: `<p>${echapper(deja.commentaire || "Prime déjà payée : non évaluée.")}</p>`,
      sources: `<ul><li>${echapper(geo.source || ABSENT)}
        ${rendreAge(lire(etat.donnees, "geopolitique._meta"), CONFIG.seuilsAge.geopolitique)}</li></ul>`,
    }),
    ton,
  };
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
      ${rendreBadge(m.classification, m.classification === "sectoriel" ? "neutre" : "alerte")}
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
    return `<li><strong>${nom}</strong> — ${echapper(bloc.regime)} (MVRV ${nombre(bloc.mvrv, 2)})
      <p class="constat">${echapper(bloc.description || "")}</p>
      ${bloc.avertissement_calibrage
        ? `<p class="avertissement">${echapper(bloc.avertissement_calibrage)}</p>` : ""}
      <p class="constat">${echapper(lire(bloc, "invalidation.condition", ""))}</p></li>`;
  };

  const contributions = (rotation.contributions || [])
    .map((x) => `<li><span>${echapper(x.mesure)}</span>
      <span>${x.vote ? echapper(x.vote) : "abstention"}</span>
      ${x.abstention ? `<span class="composante-motif">${echapper(x.motif_abstention)}</span>` : ""}</li>`)
    .join("");

  const nonAlimentes = (lire(c, "meta.indicateurs_non_alimentes", []) || [])
    .map((x) => `<li>${echapper(x.indicateur)} : ${echapper(x.motif)}</li>`)
    .join("");

  return {
    resume,
    corps: rendreTrame({
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
    }),
    ton: "neutre",
  };
}
