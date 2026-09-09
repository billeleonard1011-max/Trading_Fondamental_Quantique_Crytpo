/**
 * Page d'accueil : bande de prix, rubriques, fil, assistant.
 *
 * Le module se contente de câbler le DOM : tout le rendu vient de fonctions
 * pures, testées séparément et sans navigateur.
 */

import { CONFIG } from "./config.js";
import { chargerTout, lire } from "./donnees.js";
import { ABSENT, dateHeure, echapper, heure } from "./format.js";
import { installerInfobulles, rendrePrix } from "./rendu.js";
import {
  rubriqueCrypto, rubriqueGeopolitique, rubriqueOr, rubriqueQuantique,
} from "./rubriques.js";
import { rendreFil } from "./fil.js";
import { construireContexte, demander, suggestions } from "./assistant.js";
import { installerTheme } from "./theme.js";
import { libelle } from "./libelles.js";

/** État global de la page, rempli une fois au chargement. */
let etatGlobal = {};

/**
 * Construit la bande de prix.
 *
 * @param {object} etat États de chargement.
 * @returns {string} HTML de la bande.
 */
function bandePrix(etat) {
  const vignettes = [];

  const or = lire(etat, "or.donnees", {});
  const prixOr = lire(or, "prix", {});
  vignettes.push(rendrePrix({
    symbole: "XAUUSD",
    prix: prixOr.disponible ? prixOr.prix : null,
    variation: prixOr.variation_5j_pct,
    horodatage: lire(or, "prix._meta.date_donnee", ABSENT),
    decimales: 2,
  }));

  const positions = lire(etat, "crypto.donnees.positionnement.positions.positions", []) || [];
  for (const symbole of ["BTC", "ETH"]) {
    const p = positions.find((x) => x.symbole === symbole);
    vignettes.push(rendrePrix({
      symbole,
      prix: p ? p.prix_usd : null,
      variation: p ? p.variation_24h_pct : null,
      horodatage: lire(etat, "crypto.donnees.meta.date", ABSENT),
      decimales: symbole === "BTC" ? 0 : 2,
    }));
  }

  const variations = lire(etat, "quantique.donnees.prix.variations_du_jour", {}) || {};
  const clotures = lire(etat, "quantique.donnees.prix.clotures", {}) || {};
  for (const ticker of ["RGTI", "QBTS", "IONQ"]) {
    vignettes.push(rendrePrix({
      symbole: ticker,
      prix: clotures[ticker] ?? null,
      variation: variations[ticker] ?? null,
      horodatage: lire(etat, "quantique.donnees.prix._meta.date_donnee", ABSENT),
      decimales: 2,
    }));
  }

  return vignettes.join("");
}

/**
 * Construit les quatre rubriques dépliables.
 *
 * L'or vient en premier et ouvert : c'est le seul actif effectivement tradé.
 * Les autres sont réduits à leur badge d'état, pour qu'un coup d'œil suffise
 * à savoir s'il se passe quelque chose.
 *
 * @param {object} etat États de chargement.
 * @returns {string} HTML des rubriques.
 */
function rubriques(etat) {
  const blocs = [
    { id: "or", titre: "Or", contenu: rubriqueOr(etat.or), ouvert: true },
    { id: "geo", titre: "Géopolitique", contenu: rubriqueGeopolitique(etat.or), ouvert: false },
    { id: "quantique", titre: "Quantique", contenu: rubriqueQuantique(etat.quantique), ouvert: false },
    { id: "crypto", titre: "Crypto", contenu: rubriqueCrypto(etat.crypto), ouvert: false },
  ];
  return blocs
    .map(({ id, titre, contenu, ouvert }) => `
      <details class="rubrique" id="rubrique-${id}" ${ouvert ? "open" : ""}>
        <summary class="rubrique-tete">
          <h2>${echapper(titre)}</h2>
          <span class="rubrique-resume">${contenu.resume}</span>
          <span class="rubrique-chevron" aria-hidden="true">›</span>
        </summary>
        <div class="rubrique-corps">${contenu.corps}</div>
      </details>`)
    .join("");
}

/** N'ouvre qu'une rubrique à la fois. */
function installerAccordeon() {
  const toutes = Array.from(document.querySelectorAll("details.rubrique"));
  for (const bloc of toutes) {
    bloc.addEventListener("toggle", () => {
      if (!bloc.open) return;
      for (const autre of toutes) {
        if (autre !== bloc) autre.open = false;
      }
    });
  }
}

/**
 * Installe les onglets du fil.
 *
 * @param {Array} items Items du fil.
 */
function installerFil(items) {
  const onglets = document.getElementById("fil-onglets");
  const contenu = document.getElementById("fil-contenu");
  if (!onglets || !contenu) return;

  const afficher = (categorie) => {
    contenu.innerHTML = rendreFil(items, categorie);
    for (const bouton of onglets.querySelectorAll(".fil-onglet")) {
      bouton.setAttribute("aria-selected", String(bouton.dataset.categorie === categorie));
    }
  };

  onglets.innerHTML = CONFIG.categories
    .map((c) => `<button class="fil-onglet" role="tab" type="button"
      data-categorie="${c}" aria-selected="${c === "tout"}">${echapper(libelle(c))}</button>`)
    .join("");
  onglets.addEventListener("click", (evenement) => {
    const bouton = evenement.target.closest(".fil-onglet");
    if (bouton) afficher(bouton.dataset.categorie);
  });
  afficher("tout");
}

/** Installe l'assistant et ses suggestions. */
function installerAssistant() {
  const zoneSuggestions = document.getElementById("assistant-suggestions");
  const formulaire = document.getElementById("assistant-formulaire");
  const champ = document.getElementById("assistant-question");
  const reponse = document.getElementById("assistant-reponse");
  if (!formulaire || !champ || !reponse) return;

  if (zoneSuggestions) {
    zoneSuggestions.innerHTML = suggestions(etatGlobal)
      .map((q) => `<button class="assistant-suggestion" type="button">${echapper(q)}</button>`)
      .join("");
    zoneSuggestions.addEventListener("click", (evenement) => {
      const bouton = evenement.target.closest(".assistant-suggestion");
      if (bouton) {
        champ.value = bouton.textContent.trim();
        formulaire.requestSubmit();
      }
    });
  }

  // Les icônes d'aide des métriques pré-remplissent la question.
  document.addEventListener("click", (evenement) => {
    const aide = evenement.target.closest("button.aide");
    if (!aide) return;
    champ.value = aide.dataset.question || "";
    champ.focus();
    champ.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  formulaire.addEventListener("submit", async (evenement) => {
    evenement.preventDefault();
    const question = champ.value;
    reponse.hidden = false;
    reponse.textContent = "…";
    const resultat = await demander(
      question, construireContexte(etatGlobal), CONFIG.urlAssistant,
    );
    reponse.textContent = resultat.texte;
  });
}

/** Point d'entrée de la page. */
async function demarrer() {
  installerTheme();
  etatGlobal = await chargerTout(CONFIG.sources);

  const horodatage = document.getElementById("horodatage");
  if (horodatage) {
    const quand = lire(etatGlobal, "or.donnees.meta.horodatage_utc", null);
    horodatage.textContent = quand
      ? `données du ${dateHeure(quand)}`
      : "horodatage indisponible";
  }

  const bande = document.getElementById("bande-prix");
  if (bande) bande.innerHTML = bandePrix(etatGlobal);

  const zone = document.getElementById("rubriques");
  if (zone) {
    zone.innerHTML = rubriques(etatGlobal);
    installerAccordeon();
  }

  // Trois fils indépendants, un par domaine, fusionnés ici : chaque item
  // porte déjà sa propre catégorie, et un fil absent ou vide (crypto et
  // géopolitique peuvent ne pas encore avoir tourné) ne prive pas les
  // autres d'affichage.
  const fils = ["filQuantique", "filCrypto", "filGeopolitique"]
    .map((cle) => lire(etatGlobal, `${cle}.donnees`, []))
    .filter((donnees) => Array.isArray(donnees))
    .flat();
  installerFil(fils);
  installerAssistant();
  installerInfobulles();
}

// Le module s'exécute au chargement dans le navigateur, mais reste importable
// hors navigateur : les tests en vérifient les fonctions pures sans DOM.
if (typeof document !== "undefined") {
  demarrer();
}
