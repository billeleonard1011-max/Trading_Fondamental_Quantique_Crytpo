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

/** Les quatre variantes de TP, dans l'ordre du journal. */
export const VARIANTES = Object.freeze(["a", "b15", "b2", "b3"]);

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
 * Cherche le niveau de liquidité le plus proche dans le sens du trade.
 *
 * Port de `Backtest._objectif_structurel` : extrêmes de la veille, de la
 * session asiatique (20h-minuit, heure de New York), et order blocks encore
 * actifs (non mitigés, déjà formés).
 *
 * @returns {number|null} Le niveau retenu, ou `null` si aucun n'est devant le prix.
 */
function objectifStructurel(prix, achat, orderBlocksActifs, bougiesM1, instantMs) {
  const niveaux = [];
  const { veille, asiatique } = niveauxDeSession(bougiesM1, instantMs);
  niveaux.push(...veille, ...asiatique);

  for (const zone of orderBlocksActifs) {
    if (zone.mitige || zone.finMotif > instantMs) continue;
    niveaux.push(achat ? zone.bas : zone.haut);
  }

  if (achat) {
    const devant = niveaux.filter((n) => n > prix);
    return devant.length ? Math.min(...devant) : null;
  }
  const devant = niveaux.filter((n) => n < prix);
  return devant.length ? Math.max(...devant) : null;
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
  let position = etat.positionOuverte ? { ...etat.positionOuverte, variantesResolues: { ...etat.positionOuverte.variantesResolues } } : null;

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
          prixEntree,
          stop,
          objectifs,
          lots: taille.lots,
          variantesResolues: Object.fromEntries(
            VARIANTES.map((v) => [v, objectifs[v] === null]),
          ),
        };
        // Copie indépendante de variantesResolues dans l'événement : sans
        // elle, l'événement partagerait la même référence que `position`
        // et se mettrait à jour tout seul au fil des résolutions futures,
        // rendant le journal d'entrée menteur a posteriori.
        evenements.push({ type: "entree", ...position, variantesResolues: { ...position.variantesResolues } });
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
