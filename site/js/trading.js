/**
 * Onglet Trading : fil des signaux détectés par le scanner en direct,
 * tableau de bord mensuel, comparaison avec le backtest historique.
 *
 * Règle absolue, rappelée partout où ce module produit du texte : le
 * scanner ne recommande jamais de prendre un trade. Les textes affichés
 * viennent déjà vérifiés du Worker (voir worker-scanner/src/api.js et
 * alertes.js), mais ce module revérifie chaque texte avant affichage —
 * « la vérification ne fait pas confiance à la seule discipline de
 * rédaction », la même règle que worker-scanner/src/alertes.js applique
 * côté serveur. Le site ne publie non plus jamais de donnée de compte, de
 * position ou de montant (voir rendu.js::MOTIFS_INTERDITS) : la route
 * /journal ne renvoie d'ailleurs déjà ni taille de position ni résultat en
 * dollars, la performance est exprimée en multiple de risque (R).
 */

import { CONFIG } from "./config.js";
import { chargerJson } from "./donnees.js";
import { ABSENT, dateHeure, echapper, nombre, pourcent } from "./format.js";
import { champsInterdits, rendreBadge, rendreIndisponible } from "./rendu.js";
import { installerTheme } from "./theme.js";
import { verifierAbsenceRecommandation } from "../../worker-scanner/src/recommandation.js";

/** Les quatre variantes de TP suivies, dans l'ordre d'affichage. */
export const VARIANTES = ["a", "b15", "b2", "b3", "c"];

const LIBELLE_VARIANTE = {
  a: "Structurelle (A)",
  b15: "Ratio 1:1,5 (B)",
  b2: "Ratio 1:2 (B)",
  b3: "Ratio 1:3 (B)",
  c: "Sortie par paliers (C)",
};

/** Libellés des origines de zone de liquidité.
 *
 * Les identifiants techniques (`veille_haut`, `asie_bas`...) ne doivent
 * jamais s'afficher bruts : même règle que pour les autres libellés du site. */
const LIBELLE_ORIGINE_ZONE = {
  veille_haut: "haut de la veille",
  veille_bas: "bas de la veille",
  asie_haut: "haut de la session asiatique",
  asie_bas: "bas de la session asiatique",
  order_block: "order block encore actif",
};

/** Libellés des motifs de sortie d'une tranche. */
const LIBELLE_MOTIF_SORTIE = {
  objectif: "zone atteinte",
  stop: "stop touché",
  break_even: "sorti au prix d'entrée",
};

/** Correspondance avec les clés du rapport de backtest (reports/backtest/synthese.json). */
const VARIANTE_VERS_BACKTEST = {
  a: "A_structurel", b15: "B_ratio_1.5", b2: "B_ratio_2", b3: "B_ratio_3",
  c: "C_paliers",
};

/** Nombre de trades résolus en deçà duquel un taux de réussite ne veut rien dire. */
const SEUIL_ECHANTILLON = 30;

/**
 * Une variante est-elle allée à son terme (gagnante ou perdante) ?
 * @param {string} statut Statut de la variante.
 * @returns {boolean}
 */
export function estResolue(statut) {
  return statut === "gagnant" || statut === "perdant";
}

/** @param {string} statut @returns {string} Tonalité du badge. */
export function tonStatut(statut) {
  if (statut === "gagnant") return "positif";
  if (statut === "perdant") return "negatif";
  if (statut === "sans_objectif") return "alerte";
  return "neutre";
}

/** @param {string} statut @returns {string} Libellé lisible du statut. */
export function libelleStatut(statut) {
  return {
    gagnant: "Objectif atteint", perdant: "Stop touché",
    ouvert: "En cours", sans_objectif: "Sans objectif",
  }[statut] || statut;
}

/**
 * Vérifie un texte déjà produit par le Worker avant de l'afficher. Défense
 * en profondeur : le Worker ne devrait jamais laisser passer une
 * formulation de recommandation, mais l'affichage ne s'y fie pas seul.
 *
 * @param {string|null} texte Texte candidat, ou `null`.
 * @returns {string|null} Le texte si sûr, `null` sinon (jamais corrigé à la volée).
 */
export function texteSurAudite(texte) {
  if (!texte) return null;
  const infractions = verifierAbsenceRecommandation({ texte });
  return infractions.length === 0 ? texte : null;
}

/**
 * Agrège les résolutions d'une variante sur un ensemble de signaux.
 *
 * Toutes les grandeurs sont en multiple de risque (R), jamais en dollars :
 * R vaut -1 pile au stop et +ratio pile à l'objectif construit avec ce
 * ratio — voir worker-scanner/src/api.js::calculerR. C'est directement
 * comparable à la colonne `resultat_r` / `resultat_moyen_r` du backtest.
 *
 * @param {Array<object>} signaux Signaux renvoyés par /journal.
 * @param {string} variante Une des VARIANTES.
 * @returns {object} `{n_trades, n_gagnants, n_perdants, taux_reussite, esperance_r, profit_factor}`.
 */
export function agregerSignaux(signaux, variante) {
  const resolues = (signaux || [])
    .map((s) => s.variantes && s.variantes[variante])
    .filter((v) => v && estResolue(v.statut) && Number.isFinite(v.r));

  const nTrades = resolues.length;
  if (nTrades === 0) {
    return { n_trades: 0, n_gagnants: 0, n_perdants: 0, taux_reussite: null, esperance_r: null, profit_factor: null };
  }
  const nGagnants = resolues.filter((v) => v.statut === "gagnant").length;
  const sommeR = resolues.reduce((acc, v) => acc + v.r, 0);
  const gains = resolues.filter((v) => v.r > 0).reduce((acc, v) => acc + v.r, 0);
  const pertes = resolues.filter((v) => v.r < 0).reduce((acc, v) => acc + v.r, 0);

  return {
    n_trades: nTrades,
    n_gagnants: nGagnants,
    n_perdants: nTrades - nGagnants,
    taux_reussite: nGagnants / nTrades,
    esperance_r: sommeR / nTrades,
    profit_factor: pertes < 0 ? gains / Math.abs(pertes) : null,
  };
}

/** @param {string|null} iso Horodatage ISO. @returns {string|null} Clé "AAAA-MM". */
export function cleMois(iso) {
  return iso ? String(iso).slice(0, 7) : null;
}

/**
 * Agrège une variante mois par mois (mois de résolution, le plus récent en tête).
 *
 * @param {Array<object>} signaux Signaux renvoyés par /journal.
 * @param {string} variante Une des VARIANTES.
 * @returns {Array<object>} Une entrée `{mois, ...métriques}` par mois représenté.
 */
export function agregerParMois(signaux, variante) {
  const parMois = new Map();
  for (const s of signaux || []) {
    const v = s.variantes && s.variantes[variante];
    if (!v || !estResolue(v.statut)) continue;
    const mois = cleMois(v.horodatage_resolution_utc);
    if (!mois) continue;
    if (!parMois.has(mois)) parMois.set(mois, []);
    parMois.get(mois).push(s);
  }
  return Array.from(parMois.keys())
    .sort((a, b) => b.localeCompare(a))
    .map((mois) => ({ mois, ...agregerSignaux(parMois.get(mois), variante) }));
}

/**
 * Extrait les métriques d'une variante depuis le rapport de backtest, dans
 * les mêmes unités que {@link agregerSignaux} (R, pas des dollars).
 *
 * @param {object} synthese Contenu de reports/backtest/synthese.json.
 * @param {string} variante Une des VARIANTES.
 * @returns {object} `{disponible, motif?, n_trades?, taux_reussite?, esperance_r?, profit_factor?}`.
 */
export function extraireMetriquesBacktest(synthese, variante) {
  const cle = VARIANTE_VERS_BACKTEST[variante];
  const m = synthese && synthese.variantes && synthese.variantes[cle] && synthese.variantes[cle].metriques;
  if (!m) return { disponible: false, motif: "métriques de backtest indisponibles pour cette variante" };
  return {
    disponible: true,
    n_trades: m.n_trades,
    taux_reussite: m.taux_reussite,
    esperance_r: m.resultat_moyen_r,
    profit_factor: m.profit_factor,
  };
}

/**
 * Avertit quand un échantillon est trop petit pour qu'un taux de réussite
 * signifie quelque chose — même principe que debrief.js::evaluerFiabilite.
 *
 * @param {number} nTrades Nombre de trades résolus.
 * @param {number} seuil Seuil en deçà duquel avertir.
 * @returns {string} HTML de l'avertissement, vide si l'échantillon est suffisant.
 */
export function avertissementEchantillon(nTrades, seuil = SEUIL_ECHANTILLON) {
  if (nTrades >= seuil) return "";
  return `<p class="avertissement">Échantillon réduit : ${nTrades} trade(s) résolu(s) ici, en
    dessous du seuil de ${seuil} à partir duquel un taux de réussite commence à vouloir dire
    quelque chose. Ces chiffres se lisent comme une anecdote, pas comme une performance.</p>`;
}

function ligneMetriques(libelleLigne, m) {
  if (!m || !m.n_trades) {
    return `<tr><td>${echapper(libelleLigne)}</td><td colspan="4">${rendreIndisponible("aucun trade résolu")}</td></tr>`;
  }
  return `<tr>
    <td>${echapper(libelleLigne)}</td>
    <td>${m.n_trades}</td>
    <td>${m.taux_reussite === null ? ABSENT : pourcent(m.taux_reussite * 100, 0, false)}</td>
    <td>${m.esperance_r === null ? ABSENT : `${nombre(m.esperance_r, 2, true)} R`}</td>
    <td>${m.profit_factor === null ? ABSENT : nombre(m.profit_factor, 2)}</td>
  </tr>`;
}

/**
 * Rend le détail des tranches de la sortie par paliers.
 *
 * Le total d'un trade à paliers ne dit pas d'où vient son résultat : ce
 * tableau montre chaque tranche — zone visée, part de la position, sortie
 * effective et R apporté — pour qu'on voie si le résultat tient au premier
 * palier ou aux suivants.
 *
 * @param {Array<object>|undefined} paliers Tranches publiées par /journal.
 * @returns {string} HTML du détail, vide si le signal n'a pas de paliers.
 */
export function rendrePaliers(paliers) {
  if (!Array.isArray(paliers) || paliers.length === 0) return "";
  const lignes = paliers.map((p) => {
    const origine = LIBELLE_ORIGINE_ZONE[p.origine] || p.origine;
    const motif = p.motif_sortie
      ? LIBELLE_MOTIF_SORTIE[p.motif_sortie] || p.motif_sortie
      : "en cours";
    return `<tr>
      <td>${p.rang}</td>
      <td>${echapper(origine)} à ${nombre(p.zone, 2)} $</td>
      <td>1:${nombre(p.ratio_risque, 1)}</td>
      <td>${nombre(p.fraction * 100, 0)} %</td>
      <td>${echapper(motif)}</td>
      <td>${p.r === null || p.r === undefined ? ABSENT : `${nombre(p.r, 2, true)} R`}</td>
    </tr>`;
  }).join("");
  return `<div class="tableau-enveloppe trading-paliers">
    <table class="tableau">
      <thead><tr><th>Palier</th><th>Zone visée</th><th>R/R</th><th>Part</th>
        <th>Sortie</th><th>Apport</th></tr></thead>
      <tbody>${lignes}</tbody>
    </table>
  </div>`;
}

/**
 * Rend le fil des signaux détectés, du plus récent au plus ancien.
 *
 * @param {Array<object>} signaux Signaux renvoyés par /journal.
 * @param {number} limite Nombre maximal de signaux affichés.
 * @returns {string} HTML du fil.
 */
export function rendreFilAlertes(signaux, limite = 30) {
  if (!signaux || !signaux.length) {
    return `<p class="fil-vide">Aucun signal détecté par la mécanique pour l'instant.</p>`;
  }
  const items = signaux.slice(0, limite).map((s) => {
    const detection = texteSurAudite(s.texte_detection);
    const variantesHtml = VARIANTES.map((v) => {
      // Un signal journalisé avant l'ajout d'une variante ne la porte pas :
      // on l'affiche comme non suivie plutôt que de casser tout le fil.
      const variante = (s.variantes && s.variantes[v]) || { statut: "sans_objectif", texte: null };
      const texte = texteSurAudite(variante.texte);
      return `<li class="trading-variante">
        <span class="trading-variante-entete">${echapper(LIBELLE_VARIANTE[v])}
          ${rendreBadge(libelleStatut(variante.statut), tonStatut(variante.statut))}</span>
        ${texte ? `<p class="trading-variante-texte">${echapper(texte)}</p>` : ""}
      </li>`;
    }).join("");

    return `<li class="fil-item trading-signal">
      <div class="trading-signal-entete">
        <span>${echapper(s.sens === "haussier" ? "Achat" : "Vente")} — order block ${echapper(s.timeframe_ob)}</span>
        <span class="fil-meta">${echapper(dateHeure(s.horodatage_detection_utc))}</span>
      </div>
      <p>${detection ? echapper(detection) : "Texte de détection indisponible."}</p>
      ${rendrePaliers(s.paliers)}
      <ul class="trading-variantes">${variantesHtml}</ul>
    </li>`;
  }).join("");
  return `<ul class="fil-liste">${items}</ul>`;
}

/**
 * Rend le tableau de bord mensuel, une section par variante.
 *
 * @param {Array<object>} signaux Signaux renvoyés par /journal.
 * @returns {string} HTML du tableau de bord.
 */
export function rendreTableauBordMensuel(signaux) {
  return VARIANTES.map((v) => {
    const ensemble = agregerSignaux(signaux, v);
    if (ensemble.n_trades === 0) {
      return `<div class="trame-section"><h4>${echapper(LIBELLE_VARIANTE[v])}</h4>
        <p class="fil-vide">Aucun trade résolu pour cette variante pour l'instant.</p></div>`;
    }
    const lignesMois = agregerParMois(signaux, v).map((l) => ligneMetriques(l.mois, l)).join("");
    return `<div class="trame-section">
      <h4>${echapper(LIBELLE_VARIANTE[v])}</h4>
      ${avertissementEchantillon(ensemble.n_trades)}
      <div class="tableau-enveloppe">
        <table class="tableau">
          <thead><tr><th>Mois</th><th>Trades</th><th>Taux de réussite</th><th>Espérance</th><th>Profit factor</th></tr></thead>
          <tbody>${lignesMois}${ligneMetriques("Ensemble", ensemble)}</tbody>
        </table>
      </div>
    </div>`;
  }).join("");
}

/**
 * Rend la comparaison scanner en direct / backtest historique, variante par variante.
 *
 * @param {Array<object>} signaux Signaux renvoyés par /journal.
 * @param {{disponible: boolean, donnees: object, motif: string}} backtest État de chargement du rapport.
 * @returns {string} HTML de la comparaison.
 */
export function rendreComparaisonBacktest(signaux, backtest) {
  if (!backtest || !backtest.disponible) {
    return rendreIndisponible((backtest && backtest.motif) || "rapport de backtest indisponible");
  }
  const lignes = VARIANTES.map((v) => {
    const scanner = agregerSignaux(signaux, v);
    const historique = extraireMetriquesBacktest(backtest.donnees, v);
    const ligneScanner = ligneMetriques(`${LIBELLE_VARIANTE[v]} — scanner en direct`, scanner);
    const ligneBacktest = historique.disponible
      ? ligneMetriques(`${LIBELLE_VARIANTE[v]} — backtest 2025-2026`, historique)
      : `<tr><td>${echapper(LIBELLE_VARIANTE[v])} — backtest 2025-2026</td>
          <td colspan="4">${rendreIndisponible(historique.motif)}</td></tr>`;
    return ligneScanner + ligneBacktest;
  }).join("");
  return `<div class="tableau-enveloppe">
    <table class="tableau">
      <thead><tr><th>Variante</th><th>Trades</th><th>Taux de réussite</th><th>Espérance</th><th>Profit factor</th></tr></thead>
      <tbody>${lignes}</tbody>
    </table>
  </div>`;
}

/** Point d'entrée de la page. */
async function demarrer() {
  installerTheme();
  const zoneFil = document.getElementById("trading-fil");
  if (!zoneFil) return;
  const zoneDashboard = document.getElementById("trading-dashboard");
  const zoneComparaison = document.getElementById("trading-comparaison");
  const horodatage = document.getElementById("horodatage");
  const fraicheur = document.getElementById("trading-fraicheur");

  const [journal, backtest] = await Promise.all([
    chargerJson(CONFIG.urlScanner),
    chargerJson(CONFIG.sources.backtest),
  ]);

  if (!journal.disponible) {
    const indispo = rendreIndisponible(journal.motif);
    zoneFil.innerHTML = indispo;
    if (zoneDashboard) zoneDashboard.innerHTML = indispo;
    if (zoneComparaison) zoneComparaison.innerHTML = indispo;
    if (horodatage) horodatage.textContent = "scanner indisponible";
    return;
  }

  // Défense en profondeur : même si la route /journal ne sélectionne déjà
  // pas ces colonnes (voir worker-scanner/src/index.js), on refuse
  // d'afficher quoi que ce soit d'une réponse qui en contiendrait.
  const fautifs = champsInterdits(journal.donnees);
  if (fautifs.length > 0) {
    console.error("Champs interdits détectés dans la réponse du scanner :", fautifs);
    const indispo = rendreIndisponible("réponse du scanner rejetée : champ non autorisé détecté");
    zoneFil.innerHTML = indispo;
    if (zoneDashboard) zoneDashboard.innerHTML = indispo;
    if (zoneComparaison) zoneComparaison.innerHTML = indispo;
    return;
  }

  const signaux = Array.isArray(journal.donnees.signaux) ? journal.donnees.signaux : [];
  const meta = journal.donnees.meta || {};

  if (horodatage) {
    horodatage.textContent = meta.derniere_execution_utc
      ? `dernière vérification du moteur : ${dateHeure(meta.derniere_execution_utc)}`
      : "horodatage de la dernière exécution indisponible";
  }
  if (fraicheur) {
    const minutes = meta.derniere_execution_utc
      ? (Date.now() - new Date(meta.derniere_execution_utc).getTime()) / 60000
      : null;
    if (minutes !== null && minutes > CONFIG.seuilRetardScannerMinutes) {
      fraicheur.hidden = false;
      fraicheur.textContent = `Le moteur ne semble plus s'être exécuté depuis ${Math.round(minutes)} ` +
        `minutes (Cron Trigger attendu chaque minute) : les données ci-dessous peuvent être en retard.`;
    } else {
      fraicheur.hidden = true;
    }
  }

  zoneFil.innerHTML = rendreFilAlertes(signaux);
  if (zoneDashboard) zoneDashboard.innerHTML = rendreTableauBordMensuel(signaux);
  if (zoneComparaison) zoneComparaison.innerHTML = rendreComparaisonBacktest(signaux, backtest);
}

// Le module s'exécute au chargement dans le navigateur, mais reste importable
// hors navigateur : les tests en vérifient les fonctions pures sans DOM.
if (typeof document !== "undefined") {
  demarrer();
}
