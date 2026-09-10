/**
 * Moteur du scanner en direct : avance bougie par bougie, avec un état
 * persisté entre deux exécutions plutôt qu'une boucle en mémoire.
 *
 * Différence avec backtest/moteur.py
 * -----------------------------------
 * Le backtest tient tout son état (zones actives, setups, position) dans
 * des variables locales d'une seule exécution qui parcourt tout
 * l'historique. Le scanner tourne une minute à la fois, dans un Worker qui
 * ne garde rien en mémoire d'une exécution à l'autre : le même état doit
 * donc être lu depuis D1 au début de chaque exécution, avancé d'une (ou de
 * quelques) bougie(s), puis réécrit. `traiterNouvellesBougies` est
 * l'équivalent, bougie par bougie, du corps de boucle de
 * `Backtest.executer()` — mêmes règles, même ordre des étapes, juste
 * rejouées de façon incrémentale plutôt qu'en une seule passe.
 *
 * CHOIX D'INTERPRÉTATION — quatre TP partagent une seule entrée
 * -----------------------------------------------------------------
 * Le backtest joue quatre simulations indépendantes (une par variante de
 * TP), qui peuvent donc diverger sur *quels* trades elles prennent : une
 * variante qui sort plus tôt libère la voie à un nouveau setup plus tôt
 * qu'une variante à l'objectif plus large. Le journal du scanner (partie 3
 * du prompt) demande au contraire une seule ligne par signal, avec quatre
 * statuts et quatre résultats en parallèle — ce qui suppose une seule
 * entrée, quatre sorties. C'est le choix retenu ici : l'entrée (prix, stop,
 * taille) est calculée une fois ; les quatre objectifs sont calculés au
 * même instant ; une nouvelle entrée n'est cherchée que lorsque les
 * **quatre** variantes de la précédente sont résolues. C'est une différence
 * réelle avec le backtest, documentée ici et dans le README, pas une
 * approximation cachée.
 *
 * CHOIX D'INTERPRÉTATION — objectif structurel introuvable
 * -----------------------------------------------------------------
 * Le backtest abandonne tout le setup si l'objectif structurel (variante A)
 * ne trouve aucun niveau de liquidité devant le prix. Ici, l'entrée est
 * partagée : elle a lieu même si la variante A n'a pas d'objectif, avec un
 * quatrième statut explicite, `sans_objectif`, en plus des trois prévus par
 * le prompt (`ouvert`, `gagnant`, `perdant`) — pour ne jamais masquer ce cas
 * plutôt que d'inventer un objectif ou de taire l'entrée.
 *
 * Résultat en dollars, pas en euros
 * -----------------------------------------------------------------
 * Le journal (partie 3) demande resultat_a_usd, etc. — en dollars, pas en
 * euros comme le fait le backtest. Le taux EUR/USD ne sert donc ici qu'au
 * dimensionnement de la taille (combien de lots pour viser 50-60 € de
 * risque), jamais à convertir le résultat affiché.
 */

import {
  BAISSIER,
  HAUSSIER,
  detecterFvg,
  detecterOrderBlocks,
  origineDeJambe,
} from "./ict.js";
import { agreger, fenetreClose, UNITES_FVG, UNITES_ORDER_BLOCK } from "./agregation.js";
import {
  ONCES_PAR_LOT,
  appliquerCoutsEntree,
  appliquerCoutsSortie,
  calculerStop,
  configExecutionDefaut,
  dimensionner,
} from "./execution.js";

/** Fenêtre d'expiration d'un setup, en bougies M1 (identique au backtest). */
export const EXPIRATION_BARRES = 120;

/** Profondeur maximale d'une jambe remontée avant l'order block, en bougies. */
export const PROFONDEUR_JAMBE = 50;

/** Les cinq variantes de TP, dans l'ordre du journal.
 *
 * `c` est la sortie par paliers : elle vise les mêmes zones de liquidité que
 * `a`, mais en sortant en plusieurs fois et en passant à break-even dès la
 * première atteinte. */
export const VARIANTES = Object.freeze(["a", "b15", "b2", "b3", "c"]);

/** Nombre maximal de zones de liquidité retenues (identique au backtest). */
export const MAX_ZONES_PALIERS = 3;

/** Part de la position close à la première zone touchée (identique au backtest). */
export const FRACTION_TP1 = 0.5;

/** Motifs de sortie d'une tranche (mêmes noms que backtest/moteur.py). */
export const SORTIE_OBJECTIF = "objectif";
export const SORTIE_STOP = "stop";
export const SORTIE_BREAK_EVEN = "break_even";

/**
 * Répartit la position sur les zones de liquidité retenues.
 *
 * Port de `backtest.moteur.repartir_paliers` : la première zone emporte
 * {@link FRACTION_TP1} de la position, le solde se répartit à parts égales
 * sur les suivantes ; une zone unique prend tout. Les parts sont des
 * fractions, jamais des lots — le moteur mesure une mécanique, il ne place
 * pas d'ordre.
 *
 * @param {number} nZones Nombre de zones retenues, au moins une.
 * @param {number} fractionTp1 Part close à la première zone.
 * @returns {number[]} Les parts, de somme exactement 1.
 * @throws {Error} Si `nZones` est nul ou négatif.
 */
export function repartirPaliers(nZones, fractionTp1 = FRACTION_TP1) {
  if (nZones <= 0) throw new Error("Une sortie par paliers exige au moins une zone.");
  if (nZones === 1) return [1.0];
  const reste = (1.0 - fractionTp1) / (nZones - 1);
  return [fractionTp1, ...Array(nZones - 1).fill(reste)];
}

/** Motifs d'abandon d'un setup (mêmes noms que backtest/moteur.py). */
export const ABANDON_SANS_FVG = "aucun_fvg_trouve";
export const ABANDON_TAILLE = "taille_non_prenable";
export const ABANDON_EXPIRATION = "expiration_sans_confirmation";

/**
 * Construit un état initial vide, pour la toute première exécution.
 * @returns {object} État vide, prêt à recevoir des bougies.
 */
export function etatInitial() {
  return {
    orderBlocksActifs: [],
    setupsEnAttente: [],
    positionOuverte: null,
    derniereBougieTraiteeT: null,
  };
}

/**
 * Calcule les quatre objectifs (TP) d'une entrée.
 *
 * @param {object} setup Setup confirmé (ob, unite_fvg).
 * @param {number} prixEntree Prix d'entrée net.
 * @param {number} distance Distance entrée-stop, en dollars.
 * @param {boolean} achat Sens de la position.
 * @param {Array<object>} orderBlocksActifs OB non mitigés, pour l'objectif structurel.
 * @param {Array} bougiesM1 Fenêtre de bougies M1, pour les extrêmes de session.
 * @param {number} instantMs Instant de l'entrée, en millisecondes UTC.
 * @param {string[]} variantesActives Variantes à calculer ; les autres
 *   reçoivent `null` et sont considérées résolues d'emblée (voir
 *   `traiterNouvellesBougies`, paramètre de même nom — sert notamment au
 *   test de parité, qui dégrade sur une seule variante pour retrouver
 *   exactement le blocage du backtest Python).
 * @returns {{a: number|null, b15: number|null, b2: number|null, b3: number|null}} Les quatre objectifs.
 */
function calculerObjectifs(prixEntree, distance, achat, orderBlocksActifs, bougiesM1, instantMs, variantesActives = VARIANTES) {
  const ratio = (r) => (achat ? prixEntree + distance * r : prixEntree - distance * r);
  const actif = (v) => variantesActives.includes(v);
  return {
    a: actif("a") ? objectifStructurel(prixEntree, achat, orderBlocksActifs, bougiesM1, instantMs) : null,
    b15: actif("b15") ? ratio(1.5) : null,
    b2: actif("b2") ? ratio(2.0) : null,
    b3: actif("b3") ? ratio(3.0) : null,
  };
}

/**
 * Énumère les zones de liquidité devant le prix, de la plus proche à la plus
 * lointaine.
 *
 * Port de `Backtest._niveaux_de_liquidite` : extrêmes de la veille, de la
 * session asiatique (20h-minuit, heure de New York), et order blocks encore
 * actifs (non mitigés, déjà formés). Chaque niveau garde son origine, pour
 * que l'alerte dise *quelle* liquidité elle vise plutôt qu'un simple prix.
 *
 * Les doublons sont écartés, comme côté Python : le haut de la veille et
 * celui de la session asiatique coïncident dès que le sommet du jour
 * précédent a été fait le soir, et les compter deux fois donnerait deux
 * tranches sur un seul niveau.
 *
 * @param {number} prix Prix d'entrée.
 * @param {boolean} achat Sens de la position.
 * @param {Array<object>} orderBlocksActifs OB non mitigés.
 * @param {Array} bougiesM1 Fenêtre de bougies M1, pour les extrêmes de session.
 * @param {number} instantMs Instant de l'entrée, en millisecondes UTC.
 * @param {number} maximum Nombre de zones retenues au plus.
 * @returns {Array<{niveau: number, origine: string}>} Zones ordonnées.
 */
function niveauxDeLiquidite(prix, achat, orderBlocksActifs, bougiesM1, instantMs, maximum = MAX_ZONES_PALIERS) {
  const { veille, asiatique } = niveauxDeSession(bougiesM1, instantMs);
  const candidats = [];
  const originesSession = ["_haut", "_bas"];
  veille.forEach((niveau, i) => candidats.push({ niveau, origine: `veille${originesSession[i]}` }));
  asiatique.forEach((niveau, i) => candidats.push({ niveau, origine: `asie${originesSession[i]}` }));

  for (const zone of orderBlocksActifs) {
    if (zone.mitige || zone.finMotif > instantMs) continue;
    candidats.push({ niveau: achat ? zone.bas : zone.haut, origine: "order_block" });
  }

  const devant = candidats.filter((c) => (achat ? c.niveau > prix : c.niveau < prix));

  // Dédoublonnage sur le niveau, première origine gardée : l'ordre des
  // candidats fait primer les extrêmes de session sur les order blocks.
  const vus = new Map();
  for (const c of devant) {
    if (!vus.has(c.niveau)) vus.set(c.niveau, c.origine);
  }

  const ordonnes = Array.from(vus, ([niveau, origine]) => ({ niveau, origine }))
    .sort((x, y) => (achat ? x.niveau - y.niveau : y.niveau - x.niveau));
  return ordonnes.slice(0, maximum);
}

/**
 * Fait avancer la variante à paliers d'une barre.
 *
 * Port de `Backtest._avancer_paliers`, avec ses deux conventions :
 * le stop est examiné **avant** les zones et emporte tout le solde s'il est
 * touché ; le passage à break-even ne prend effet qu'à la barre suivante,
 * puisque le stop de la barre courante a déjà été évalué quand la première
 * tranche se clôture. C'est l'hypothèse la moins flatteuse des deux, et la
 * seule qui ne suppose rien de l'ordre des évènements dans la minute.
 *
 * Chaque tranche close produit son propre évènement `resolution_palier` ;
 * quand la dernière tombe, un `resolution` agrégé clôt la variante, de sorte
 * que le journal garde à la fois le détail et le total.
 *
 * @param {object} position Position ouverte, mutée sur place.
 * @param {object} bougie Bougie M1 courante.
 * @param {number} finBarre Instant de clôture de la barre.
 * @param {object} config Configuration d'exécution.
 * @param {Array<object>} evenements Journal d'évènements, complété sur place.
 */
function avancerPaliers(position, bougie, finBarre, config, evenements) {
  const paliers = position.paliers || [];
  if (!paliers.length) return;

  const achat = position.sens === HAUSSIER;
  const sensSigne = achat ? 1.0 : -1.0;
  const auBreakEven = position.stopPaliers === position.prixEntree;

  const clore = (palier, prix, motif) => {
    const prixNet = appliquerCoutsSortie(prix, position.sens, config);
    const resultatUsd =
      (prixNet - position.prixEntree) * sensSigne * ONCES_PAR_LOT * position.lots * palier.fraction;
    palier.prixSortie = prixNet;
    palier.motifSortie = motif;
    palier.resultatUsd = resultatUsd;
    palier.horodatageResolution = finBarre;
    evenements.push({
      type: "resolution_palier",
      id: position.id,
      variante: "c",
      rang: palier.rang,
      zone: palier.zone,
      origine: palier.origine,
      fraction: palier.fraction,
      statut: motif === SORTIE_OBJECTIF ? "gagnant" : "perdant",
      motifSortie: motif,
      prixSortie: prixNet,
      resultatUsd,
      horodatageResolution: finBarre,
    });
  };

  const ouverts = paliers.filter((p) => p.prixSortie === null);
  if (!ouverts.length) return;

  // Le stop d'abord : touché, il emporte tout le solde.
  const toucheStop = achat ? bougie.bas <= position.stopPaliers : bougie.haut >= position.stopPaliers;
  if (toucheStop) {
    const motif = auBreakEven ? SORTIE_BREAK_EVEN : SORTIE_STOP;
    for (const palier of ouverts) clore(palier, position.stopPaliers, motif);
  } else {
    // Puis les zones, dans l'ordre : une barre ample peut en franchir
    // plusieurs, chacune se dénouant à son propre niveau.
    for (const palier of ouverts) {
      const atteinte = achat ? bougie.haut >= palier.zone : bougie.bas <= palier.zone;
      if (atteinte) clore(palier, palier.zone, SORTIE_OBJECTIF);
    }
  }

  const reste = paliers.filter((p) => p.prixSortie === null);
  if (reste.length) {
    // Une tranche au moins vient de tomber : le solde passe à break-even,
    // et le stop n'y bougera plus.
    if (reste.length < ouverts.length && !auBreakEven) {
      position.stopPaliers = position.prixEntree;
    }
    return;
  }

  // Dernière tranche close : la variante est résolue. Le total est la somme
  // du détail, jamais un chiffre calculé à part.
  const total = paliers.reduce((somme, p) => somme + p.resultatUsd, 0);
  position.variantesResolues.c = true;
  evenements.push({
    type: "resolution",
    id: position.id,
    variante: "c",
    statut: total > 0 ? "gagnant" : "perdant",
    prixSortie: paliers[paliers.length - 1].prixSortie,
    resultatUsd: total,
    horodatageResolution: finBarre,
  });
}

/**
 * Cherche le niveau de liquidité le plus proche dans le sens du trade.
 *
 * Le premier de {@link niveauxDeLiquidite} — ni le dédoublonnage ni le
 * plafond ne changent quel niveau vient en tête, la variante A garde donc
 * exactement le comportement qu'elle avait avant l'ajout des paliers.
 *
 * @returns {number|null} Le niveau retenu, ou `null` si aucun n'est devant le prix.
 */
function objectifStructurel(prix, achat, orderBlocksActifs, bougiesM1, instantMs) {
  const zones = niveauxDeLiquidite(prix, achat, orderBlocksActifs, bougiesM1, instantMs);
  return zones.length ? zones[0].niveau : null;
}

/**
 * Calcule les extrêmes de la veille et de la session asiatique (New York).
 *
 * La conversion passe par le fuseau nommé "America/New_York" via Intl, ce
 * qui gère seul les changements d'heure — 20h00 à New York reste 20h00
 * toute l'année, même si l'écart avec UTC change entre mars et novembre.
 *
 * @param {Array} bougiesM1 Fenêtre de bougies M1, triée croissant.
 * @param {number} instantMs Instant de l'entrée, en millisecondes UTC.
 * @returns {{veille: number[], asiatique: number[]}} Extrêmes `[haut, bas]` de chaque fenêtre.
 */
function niveauxDeSession(bougiesM1, instantMs) {
  // Heure, minute et seconde locales à New York de l'instant : la somme des
  // trois donne exactement le temps écoulé depuis minuit local, qu'il
  // suffit de retrancher à l'instant pour obtenir minuit local en UTC —
  // sans jamais recalculer de date calendaire, ce qui évite tout piège de
  // fuseau nommé. L'heure d'été est déjà reflétée dans ces trois valeurs.
  const formateur = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    hour12: false,
  });
  const parties = Object.fromEntries(
    formateur.formatToParts(new Date(instantMs)).map((p) => [p.type, p.value]),
  );
  // Certaines implémentations rendent "24" plutôt que "00" à minuit pile.
  const heureLocale = Number(parties.hour) % 24;
  const minuteLocale = Number(parties.minute);
  const secondeLocale = Number(parties.second);
  const msDepuisMinuitLocal = heureLocale * 3_600_000 + minuteLocale * 60_000 + secondeLocale * 1_000;
  const minuitLocalEnUtc = instantMs - msDepuisMinuitLocal;

  const debutVeille = minuitLocalEnUtc - 24 * 3_600_000;
  const finVeille = Math.min(minuitLocalEnUtc, instantMs);
  const debutAsie = minuitLocalEnUtc - 4 * 3_600_000; // 20h00 la veille = minuit - 4h
  const finAsie = Math.min(minuitLocalEnUtc, instantMs);

  const extremes = (debut, fin) => {
    const fenetre = bougiesM1.filter((b) => b.t >= debut && b.t < fin);
    if (fenetre.length === 0) return [];
    return [Math.max(...fenetre.map((b) => b.haut)), Math.min(...fenetre.map((b) => b.bas))];
  };

  return {
    veille: extremes(debutVeille, finVeille),
    asiatique: extremes(debutAsie, finAsie),
  };
}

/**
 * Isole la jambe qui a mené le prix jusqu'à l'order block.
 * Port de `Backtest._jambe`.
 */
function isolerJambe(ob, instantMs, cadresParUnite, sensibiliteSwing) {
  let cadre = fenetreClose(cadresParUnite[ob.unite], ob.unite, instantMs);
  if (cadre.length === 0) return [];
  cadre = cadre.slice(-PROFONDEUR_JAMBE);
  const depart = origineDeJambe(cadre, ob.sens, sensibiliteSwing);
  if (depart === null) return [];
  return cadre.slice(depart);
}

/**
 * Cherche un FVG de sens opposé à la zone, en M5 puis M3 puis M1.
 * Port de `Backtest._chercher_fvg`.
 */
function chercherFvg(ob, debutMs, instantMs, cadresParUnite) {
  const sensVoulu = ob.sens === HAUSSIER ? BAISSIER : HAUSSIER;
  for (const unite of UNITES_FVG) {
    let cadre = fenetreClose(cadresParUnite[unite], unite, instantMs);
    cadre = cadre.filter((b) => b.t >= debutMs && b.t <= instantMs);
    const ecarts = detecterFvg(cadre, unite, sensVoulu);
    if (ecarts.length) return { fvg: ecarts[ecarts.length - 1], unite };
  }
  return { fvg: null, unite: "" };
}

/**
 * Fait progresser un état d'une (ou plusieurs) bougie(s) M1 nouvellement closes.
 *
 * @param {object} etat État précédent (voir etatInitial()).
 * @param {Array} bougiesM1Fenetre Fenêtre glissante de bougies M1 closes,
 *   triée croissant, couvrant au moins les nouvelles bougies plus le
 *   contexte nécessaire (jambe, FVG, sessions) — voir README pour le
 *   dimensionnement retenu.
 * @param {object} config Réglages d'exécution (voir configExecutionDefaut()).
 * @param {number} tauxEurusd Taux EUR/USD du jour, pour le dimensionnement.
 * @param {number} sensibiliteSwing Sensibilité du swing (par défaut celle d'ict.js).
 * @param {string[]} variantesActives Variantes suivies (par défaut les
 *   quatre). Une position ne bloque de nouveaux setups que tant qu'au moins
 *   une variante active reste non résolue. Réduire à une seule variante
 *   reproduit exactement le blocage du backtest Python pour cette variante
 *   — c'est ce que fait le test de parité.
 * @returns {{etat: object, evenements: Array<object>}} Le nouvel état, et les
 *   événements survenus (entrée ouverte, variante résolue) que l'appelant
 *   doit journaliser.
 */
export function traiterNouvellesBougies(
  etat,
  bougiesM1Fenetre,
  config = configExecutionDefaut(),
  tauxEurusd = null,
  sensibiliteSwing = 4,
  variantesActives = VARIANTES,
) {
  const evenements = [];
  let orderBlocksActifs = etat.orderBlocksActifs.map((o) => ({ ...o }));
  let setupsEnAttente = etat.setupsEnAttente.map((s) => ({ ...s }));
  let position = etat.positionOuverte
    ? {
        ...etat.positionOuverte,
        variantesResolues: { ...etat.positionOuverte.variantesResolues },
        paliers: (etat.positionOuverte.paliers || []).map((p) => ({ ...p })),
      }
    : null;

  const cadresParUnite = {};
  for (const unite of new Set([...UNITES_ORDER_BLOCK, ...UNITES_FVG, "M1"])) {
    cadresParUnite[unite] = agreger(bougiesM1Fenetre, unite);
  }

  // Order blocks nouvellement formés depuis la dernière exécution : on ne
  // regarde que les bougies dont le motif se termine après la dernière
  // bougie déjà traitée, pour ne jamais réinsérer une zone déjà connue.
  const seuilConnaissance = etat.derniereBougieTraiteeT ?? -Infinity;
  for (const unite of UNITES_ORDER_BLOCK) {
    const zones = detecterOrderBlocks(cadresParUnite[unite], unite);
    for (const zone of zones) {
      if (zone.finMotif > seuilConnaissance) orderBlocksActifs.push(zone);
    }
  }
  // Backtest.Backtest trie tout order_blocks par fin_motif dès la
  // construction (tri stable), de sorte qu'à motif simultané entre deux
  // unités, celle énumérée en premier dans UNITES_ORDER_BLOCK (H1 puis M30
  // puis M15) l'emporte. Le tri ci-dessus reproduit cet ordre : le tri de
  // JavaScript est stable depuis ES2019, et les zones nouvellement ajoutées
  // le sont déjà dans l'ordre H1, M30, M15. Sans ce tri, une touche
  // simultanée de deux zones d'unités différentes pourrait faire gagner
  // l'une plutôt que l'autre selon un ordre qui n'a rien à voir avec la
  // stratégie — exactement le cas que touches_simultanees documente.
  orderBlocksActifs.sort((a, b) => a.finMotif - b.finMotif);

  const nouvellesBougies = bougiesM1Fenetre.filter(
    (b) => etat.derniereBougieTraiteeT === null || b.t > etat.derniereBougieTraiteeT,
  );

  for (const bougie of nouvellesBougies) {
    const finBarre = bougie.t + 60_000;
    const { haut, bas, cloture } = bougie;

    // 2. Résolution de la position ouverte, sur cette barre seulement.
    if (position) {
      for (const variante of VARIANTES) {
        if (position.variantesResolues[variante]) continue;
        if (variante === "c") {
          avancerPaliers(position, bougie, finBarre, config, evenements);
          continue;
        }
        const objectif = position.objectifs[variante];
        if (objectif === null) continue; // variante A sans objectif structurel
        let sortie = null;
        if (position.sens === HAUSSIER) {
          if (bas <= position.stop) sortie = { prix: position.stop, motif: "stop" };
          else if (haut >= objectif) sortie = { prix: objectif, motif: "objectif" };
        } else {
          if (haut >= position.stop) sortie = { prix: position.stop, motif: "stop" };
          else if (bas <= objectif) sortie = { prix: objectif, motif: "objectif" };
        }
        if (sortie === null) continue;

        const prixNet = appliquerCoutsSortie(sortie.prix, position.sens, config);
        const sensSigne = position.sens === HAUSSIER ? 1.0 : -1.0;
        const resultatUsd = (prixNet - position.prixEntree) * sensSigne * ONCES_PAR_LOT * position.lots;
        position.variantesResolues[variante] = true;
        evenements.push({
          type: "resolution",
          id: position.id,
          variante,
          statut: sortie.motif === "objectif" ? "gagnant" : "perdant",
          prixSortie: prixNet,
          resultatUsd,
          horodatageResolution: finBarre,
        });
      }
      if (VARIANTES.every((v) => position.variantesResolues[v] || position.objectifs[v] === null)) {
        position = null;
      }
    }

    // 3. Mitigation des zones : une zone touchée cesse d'être offerte.
    if (!position) {
      const restantes = [];
      const nouvelles = [];
      for (const zone of orderBlocksActifs) {
        const toucheMaintenant = !zone.mitige && zone.finMotif <= finBarre
          && !(bas > zone.haut || haut < zone.bas);
        if (toucheMaintenant) {
          zone.mitige = true;
          zone.horodatageMitigation = finBarre;
          const jambe = isolerJambe(zone, finBarre, cadresParUnite, sensibiliteSwing);
          if (jambe.length >= 2) {
            nouvelles.push({
              ob: zone, debut: jambe[0].t, fvg: null, uniteFvg: "", fvgTouche: false, barres: 0,
            });
          }
          // Zone consommée par la touche : elle ne reste pas active, comme
          // dans le backtest (continue, jamais réinsérée dans restantes).
          continue;
        }
        if (!zone.mitige) restantes.push(zone);
      }
      orderBlocksActifs = restantes;
      setupsEnAttente.push(...nouvelles);
    } else {
      // Position ouverte : les zones touchées le sont quand même, mais
      // aucun setup n'est ouvert — la stratégie ne prévoit pas de cumuler.
      for (const zone of orderBlocksActifs) {
        if (!zone.mitige && zone.finMotif <= finBarre && !(bas > zone.haut || haut < zone.bas)) {
          zone.mitige = true;
          zone.horodatageMitigation = finBarre;
        }
      }
    }

    // 4. Instruction des setups en cours (seulement si aucune position).
    //
    // CHOIX DE FIDÉLITÉ AU BACKTEST — capital, découvert en écrivant le test
    // de parité (tests/parite.test.js) : le moteur Python arrête sa boucle
    // for avec `break` dès qu'un setup confirme, PUIS fait
    // `setups = encore`. `encore` ne contient que les setups déjà VISITÉS
    // ce tour-ci et renvoyés en attente (`continue`) — tout setup pas
    // encore atteint au moment du `break` (y compris un setup flambant neuf
    // créé à l'étape 3 de ce même tour) n'est jamais ajouté à `encore` et
    // disparaît donc purement et simplement. Ce n'est pas un gel suivi
    // d'une reprise : c'est un abandon silencieux, au même titre qu'une
    // expiration ou un FVG introuvable. Une première version de ce fichier
    // les gelait et les reprenait après coup — plus généreux que le
    // backtest, et faux : vérifié par le test de parité, qui exigeait un
    // trade que le backtest, lui, n'a jamais pris.
    if (!position) {
      const encore = [];
      for (const setup of setupsEnAttente) {
        if (position) break; // équivalent exact du `break` Python : les setups restants sont perdus.
        setup.barres += 1;
        if (setup.barres > EXPIRATION_BARRES) continue; // abandon : expiration_sans_confirmation

        if (setup.fvg === null) {
          const trouve = chercherFvg(setup.ob, setup.debut, finBarre, cadresParUnite);
          setup.fvg = trouve.fvg;
          setup.uniteFvg = trouve.unite;
          if (setup.fvg === null) continue; // abandon : aucun_fvg_trouve
        }

        const achat = setup.ob.sens === HAUSSIER;
        if (!setup.fvgTouche) {
          if (bas <= setup.fvg.haut && haut >= setup.fvg.bas) setup.fvgTouche = true;
          encore.push(setup);
          continue;
        }

        // CHOIX D'INTERPRÉTATION — identique au backtest : « clôturer
        // au-delà » est la borne du FVG dans le sens du trade.
        const confirme = achat ? cloture > setup.fvg.haut : cloture < setup.fvg.bas;
        if (!confirme) { encore.push(setup); continue; }

        // Confirmation acquise : entrée au marché, toujours, sans exception.
        const prixEntree = appliquerCoutsEntree(cloture, setup.ob.sens, config);
        const stop = calculerStop(setup.ob.sens, setup.ob.haut, setup.ob.bas, setup.ob.mecheBougie2, config.margeStop);
        const distance = Math.abs(prixEntree - stop);
        if (tauxEurusd === null || tauxEurusd <= 0) continue; // abandon : taille_non_prenable
        const taille = dimensionner(distance, tauxEurusd, config);
        if (!taille.prenable) continue; // abandon : taille_non_prenable

        const objectifs = calculerObjectifs(
          prixEntree, distance, achat, orderBlocksActifs, bougiesM1Fenetre, finBarre, variantesActives,
        );

        // Zones de liquidité de la variante à paliers. Elles partagent la
        // première cible avec la variante A : `objectifs.a` est le premier
        // niveau de cette même liste (voir objectifStructurel).
        const zones = variantesActives.includes("c")
          ? niveauxDeLiquidite(prixEntree, achat, orderBlocksActifs, bougiesM1Fenetre, finBarre)
          : [];
        const fractions = zones.length ? repartirPaliers(zones.length) : [];
        const paliers = zones.map((zone, i) => ({
          rang: i + 1,
          zone: zone.niveau,
          origine: zone.origine,
          fraction: fractions[i],
          ratioRisque: distance ? Math.abs(zone.niveau - prixEntree) / distance : 0,
          prixSortie: null,
          resultatUsd: null,
          motifSortie: "",
          horodatageResolution: null,
        }));
        objectifs.c = zones.length ? zones[0].niveau : null;

        // Aucune variante active n'a d'objectif : il n'y a rien à surveiller.
        // Ouvrir ici produirait une entrée dégénérée — journalisée comme un
        // signal alors qu'elle se résout dans la foulée — et, le temps d'une
        // barre, elle empêcherait un setup réellement exploitable de se
        // former. Le backtest abandonne ce cas (ABANDON_EXPIRATION) ; on
        // l'abandonne aussi. Trouvé par le test de parité de la partie A :
        // une de ces entrées fantômes masquait un trade que le Python prenait
        // deux minutes plus tard.
        if (variantesActives.every((v) => objectifs[v] === null)) continue;

        // Une variante sans objectif (A sans niveau structurel devant le
        // prix, ou une variante hors de variantesActives) est considérée
        // résolue d'emblée : elle ne bloquera jamais la formation d'un
        // nouveau setup, faute d'avoir quoi que ce soit à surveiller.
        position = {
          id: `${finBarre}-${setup.ob.unite}-${setup.ob.sens}`,
          horodatageDetection: finBarre,
          timeframeOb: setup.ob.unite,
          obHaut: setup.ob.haut,
          obBas: setup.ob.bas,
          sens: setup.ob.sens,
          timeframeFvg: setup.uniteFvg,
          fvgHaut: setup.fvg ? setup.fvg.haut : null,
          fvgBas: setup.fvg ? setup.fvg.bas : null,
          prixEntree,
          stop,
          objectifs,
          lots: taille.lots,
          paliers,
          // Stop propre à la variante à paliers : il passera au prix d'entrée
          // dès la première tranche close, sans toucher au stop des autres.
          stopPaliers: stop,
          variantesResolues: Object.fromEntries(
            VARIANTES.map((v) => [v, objectifs[v] === null]),
          ),
        };
        // Copies indépendantes de variantesResolues et des paliers dans
        // l'événement : sans elles, l'événement partagerait la même référence
        // que `position` et se mettrait à jour tout seul au fil des
        // résolutions futures, rendant le journal d'entrée menteur a posteriori.
        evenements.push({
          type: "entree",
          ...position,
          variantesResolues: { ...position.variantesResolues },
          paliers: position.paliers.map((p) => ({ ...p })),
        });
        // Un trade pris : `break` (garde en tête de boucle) fait perdre les
        // setups pas encore visités, comme dans le backtest.
      }
      setupsEnAttente = encore;
    }
  }

  return {
    etat: {
      orderBlocksActifs,
      setupsEnAttente,
      positionOuverte: position,
      derniereBougieTraiteeT: nouvellesBougies.length
        ? nouvellesBougies[nouvellesBougies.length - 1].t
        : etat.derniereBougieTraiteeT,
    },
    evenements,
  };
}
