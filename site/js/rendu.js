/**
 * Rendu des blocs, en fonctions pures rendant du HTML.
 *
 * Séparer le rendu du DOM permet de le tester sans navigateur, et surtout
 * de vérifier mécaniquement les deux règles qui comptent ici :
 *
 * * un bloc indisponible affiche **son motif**, jamais une valeur vide ni
 *   un zéro — un zéro se lit comme une mesure, et fait prendre une panne
 *   pour une information ;
 * * aucune donnée ne s'affiche sans son horodatage ou son âge.
 *
 * Ce site étant public, rien de ce qui touche au compte de l'utilisateur —
 * taille de position, montant engagé, solde, règles de la société de
 * financement — n'a le droit d'y figurer. Les rapports consommés n'en
 * contiennent pas, et :func:`champsInterdits` le vérifie au chargement
 * plutôt que de s'en remettre à cette absence.
 */

import { ABSENT, echapper, libelleAge, nombre, pourcent } from "./format.js";
import { explicationPour, libelle } from "./libelles.js";

/**
 * Motifs de champs qui ne doivent jamais paraître sur un site public.
 *
 * La liste vise les grandeurs propres au compte de l'utilisateur. Elle
 * n'inclut pas la trésorerie des sociétés suivies ni la capitalisation des
 * actifs : ce sont des données de marché publiques, et les confondre
 * viderait la page de sa substance.
 */
export const MOTIFS_INTERDITS = [
  /\blots?\b/i,
  /taille_position/i,
  /\bsolde\b/i,
  /capital_/i,
  /perte_eur/i,
  /gain_eur/i,
  /\bpropfirm\b/i,
  /\bbreach\b/i,
  /montant_investi/i,
  /risque_par_trade/i,
];

/**
 * Repère les champs interdits dans une structure.
 *
 * @param {*} objet Structure à contrôler.
 * @param {string} chemin Chemin courant.
 * @returns {string[]} Chemins fautifs, vide si la structure est saine.
 */
export function champsInterdits(objet, chemin = "") {
  const trouves = [];
  if (objet && typeof objet === "object" && !Array.isArray(objet)) {
    for (const [cle, valeur] of Object.entries(objet)) {
      const p = chemin ? `${chemin}.${cle}` : cle;
      if (MOTIFS_INTERDITS.some((m) => m.test(cle))) trouves.push(p);
      trouves.push(...champsInterdits(valeur, p));
    }
  } else if (Array.isArray(objet)) {
    objet.slice(0, 5).forEach((x, i) => trouves.push(...champsInterdits(x, `${chemin}[${i}]`)));
  }
  return trouves;
}

/**
 * Rend l'explication d'une indisponibilité.
 *
 * @param {string} motif Motif publié par le module producteur.
 * @returns {string} HTML du bloc d'indisponibilité.
 */
export function rendreIndisponible(motif) {
  const texte = motif && String(motif).trim()
    ? echapper(motif)
    : "donnée absente, sans motif précisé par la source";
  return `<p class="indisponible"><span class="pastille">indisponible</span> ${texte}</p>`;
}

/**
 * Rend l'étiquette d'âge d'un bloc.
 *
 * @param {object} meta Bloc ``_meta`` du rapport.
 * @param {number} seuil Âge au-delà duquel l'étiquette est mise en évidence.
 * @returns {string} HTML de l'étiquette.
 */
export function rendreAge(meta, seuil) {
  if (!meta) return "";
  const age = meta.age_jours;
  const source = meta.source ? echapper(meta.source) : "source non précisée";
  if (age === null || age === undefined) {
    return `<span class="age" title="${source}">âge inconnu</span>`;
  }
  const datee = Number(age) > Number(seuil);
  return `<span class="age ${datee ? "age--datee" : ""}" title="${source}">${
    echapper(libelleAge(age))
  }</span>`;
}

/**
 * Rend une barre de position sur une échelle historique.
 *
 * Un chiffre seul ne dit rien : « z-score de 1,75 » n'a de sens que si l'on
 * voit où cela tombe dans son histoire. La barre place le curseur sur un
 * percentile déjà calculé par les modules ; elle n'en invente aucun.
 *
 * @param {number|null} percentile Rang, de 0 à 100.
 * @param {string} borneBasse Libellé de la borne gauche.
 * @param {string} borneHaute Libellé de la borne droite.
 * @returns {string} HTML de la barre.
 */
export function rendreEchelle(percentile, borneBasse, borneHaute) {
  if (percentile === null || percentile === undefined || Number.isNaN(Number(percentile))) {
    return `<p class="echelle-absente">Position historique non calculée : le module
      n'a pas publié de percentile pour cette métrique.</p>`;
  }
  const p = Math.max(0, Math.min(100, Number(percentile)));
  return `
    <div class="echelle" role="img"
         aria-label="${nombre(p, 0)} sur 100, entre ${echapper(borneBasse)} et ${echapper(borneHaute)}">
      <div class="echelle-piste"><span class="echelle-curseur" style="left:${p}%"></span></div>
      <div class="echelle-bornes">
        <span>${echapper(borneBasse)}</span>
        <span class="echelle-valeur">${nombre(p, 0)}<sup>e</sup> percentile</span>
        <span>${echapper(borneHaute)}</span>
      </div>
    </div>`;
}

/**
 * Rend un libellé accompagné, si une explication existe, d'une infobulle.
 *
 * Au survol sur ordinateur, au tap sur l'icône sur mobile où le survol
 * n'existe pas : {@link installerInfobulles} branche l'équivalent tactile.
 * Sans explication disponible pour cet identifiant, seul le libellé est
 * rendu — pas d'icône vide qui n'ouvrirait rien.
 *
 * @param {string} id Identifiant technique, traduit via libelles.js.
 * @param {string} [texteAffiche] Texte à afficher à la place du libellé
 *   traduit, quand l'appelant a déjà son propre texte (un nom de thème
 *   géopolitique par exemple, déjà lisible depuis la configuration).
 * @returns {string} HTML du libellé, avec ou sans infobulle.
 */
export function rendreLibelleAvecInfobulle(id, texteAffiche = null) {
  const texte = texteAffiche !== null ? texteAffiche : libelle(id);
  const explication = explicationPour(id);
  if (!explication) return echapper(texte);
  return `<span class="infobulle">
    ${echapper(texte)}
    <button type="button" class="infobulle-declencheur"
            aria-label="Qu'est-ce que ${echapper(texte)} ?" aria-expanded="false">i</button>
    <span class="infobulle-bulle" role="tooltip">${echapper(explication)}</span>
  </span>`;
}

/**
 * Branche l'ouverture/fermeture des infobulles au tap, pour le tactile.
 *
 * Le survol fonctionne nativement en CSS sur ordinateur. Sur un écran
 * tactile, il n'y a pas de survol : cette fonction fait du clic sur l'icône
 * un basculement, et referme toute infobulle ouverte dès qu'on touche
 * ailleurs sur la page — sans quoi une bulle resterait affichée
 * indéfiniment après le premier appui.
 *
 * Idempotente : peut être rappelée après chaque nouveau rendu sans
 * dupliquer les écouteurs, puisqu'elle est attachée une seule fois au
 * document au premier appel.
 */
let infobullesInstallees = false;
export function installerInfobulles() {
  if (infobullesInstallees) return;
  infobullesInstallees = true;

  document.addEventListener("click", (evenement) => {
    const declencheur = evenement.target.closest(".infobulle-declencheur");
    const toutes = document.querySelectorAll(".infobulle.infobulle--ouverte");

    if (declencheur) {
      const parent = declencheur.closest(".infobulle");
      const etaitOuverte = parent.classList.contains("infobulle--ouverte");
      for (const autre of toutes) autre.classList.remove("infobulle--ouverte");
      if (!etaitOuverte) {
        parent.classList.add("infobulle--ouverte");
        declencheur.setAttribute("aria-expanded", "true");
      } else {
        declencheur.setAttribute("aria-expanded", "false");
      }
      return;
    }
    // Un clic ailleurs referme tout ce qui était ouvert.
    for (const autre of toutes) {
      autre.classList.remove("infobulle--ouverte");
      const bouton = autre.querySelector(".infobulle-declencheur");
      if (bouton) bouton.setAttribute("aria-expanded", "false");
    }
  });
}

/**
 * Rend une métrique complète : valeur, échelle, sens, précédents.
 *
 * @param {object} options Description de la métrique.
 * @returns {string} HTML de la métrique.
 */
export function rendreMetrique({
  titre,
  valeur,
  percentile = null,
  borneBasse = "bas",
  borneHaute = "haut",
  explication = "",
  precedents = null,
  cleAide = "",
}) {
  const aide = cleAide
    ? `<button class="aide" data-question="${echapper(cleAide)}"
         aria-label="Poser une question sur ${echapper(titre)}">?</button>`
    : "";
  return `
    <article class="metrique">
      <header><h4>${echapper(titre)}${aide}</h4>
        <p class="metrique-valeur">${valeur}</p></header>
      ${rendreEchelle(percentile, borneBasse, borneHaute)}
      ${explication ? `<p class="metrique-sens">${echapper(explication)}</p>` : ""}
      ${precedents ? rendrePrecedents(precedents) : ""}
    </article>`;
}

/**
 * Rend le résumé des précédents historiques.
 *
 * @param {object} agregation Bloc ``analogues.agregation``.
 * @returns {string} HTML du résumé.
 */
export function rendrePrecedents(agregation) {
  const vingt = agregation && agregation["20j"];
  if (!vingt || !vingt.disponible) {
    const motif = (vingt && vingt.motif) || "pas assez de cas comparables pour conclure";
    return `<p class="precedents precedents--absents">Précédents : ${echapper(motif)}.</p>`;
  }
  const hausse = Math.round(Number(vingt.proportion_haussiers) * 100);
  return `
    <p class="precedents">
      Sur <strong>${vingt.n_cas}</strong> configurations comparables, l'or a varié de
      <strong>${pourcent(vingt.rendement_median_pct)}</strong> en médiane à vingt jours,
      en hausse dans <strong>${hausse} %</strong> des cas,
      entre ${pourcent(vingt.pire_pct)} et ${pourcent(vingt.meilleur_pct)}.
    </p>`;
}

/**
 * Rend la trame commune d'une rubrique.
 *
 * La même structure partout rend la page scannable : on sait où regarder
 * avant d'avoir lu.
 *
 * @param {object} options Contenu des cinq sections.
 * @returns {string} HTML de la trame.
 */
export function rendreTrame({ etat, changement, impact, invalidation, sources }) {
  const section = (titre, contenu, classe = "") =>
    contenu
      ? `<div class="trame-section ${classe}"><h4>${titre}</h4>${contenu}</div>`
      : "";
  return `
    ${section("État actuel", etat, "trame-etat")}
    ${section("Ce qui a changé depuis hier", changement)}
    ${section("Impact chiffré", impact)}
    ${section("Ce qui invaliderait cette lecture", invalidation, "trame-invalidation")}
    ${sources ? `<details class="sources"><summary>Sources</summary>${sources}</details>` : ""}`;
}

/**
 * Rend un badge d'état.
 *
 * @param {string} texte Libellé du badge.
 * @param {string} ton Tonalité : ``neutre``, ``positif``, ``negatif``, ``alerte``.
 * @returns {string} HTML du badge.
 */
export function rendreBadge(texte, ton = "neutre") {
  return `<span class="badge badge--${echapper(ton)}">${echapper(texte)}</span>`;
}

/**
 * Choisit la tonalité d'un biais or.
 *
 * @param {string} biais Valeur du champ ``biais``.
 * @returns {string} Tonalité correspondante.
 */
export function tonDuBiais(biais) {
  if (biais === "haussier") return "positif";
  if (biais === "vendeur") return "negatif";
  if (biais === "indeterminé" || biais === "indetermine") return "alerte";
  return "neutre";
}

/**
 * Rend une ligne de prix de la bande d'accueil.
 *
 * @param {object} options Description de l'actif.
 * @returns {string} HTML de la vignette.
 */
export function rendrePrix({ symbole, prix, variation, horodatage, decimales = 2 }) {
  const dispo = prix !== null && prix !== undefined;
  const ton = variation === null || variation === undefined
    ? "neutre"
    : Number(variation) >= 0 ? "positif" : "negatif";
  return `
    <div class="prix-vignette">
      <span class="prix-symbole">${echapper(symbole)}</span>
      <span class="prix-valeur">${dispo ? nombre(prix, decimales) : ABSENT}</span>
      <span class="prix-variation prix-variation--${ton}">${
        variation === null || variation === undefined ? ABSENT : pourcent(variation)
      }</span>
      <span class="prix-heure">${echapper(horodatage || ABSENT)}</span>
    </div>`;
}
