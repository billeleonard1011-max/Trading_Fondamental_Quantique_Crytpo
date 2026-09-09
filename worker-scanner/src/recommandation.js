/**
 * Garde-fou anti-recommandation.
 *
 * Port fidèle de modules/quantum/moves.py::MOTIFS_RECOMMANDATION et
 * verifier_absence_recommandation. Même liste de motifs, même logique de
 * parcours récursif, mêmes clés exclues parce qu'elles citent une source
 * externe ou qu'elles nomment le motif qu'elles servent à écarter. Réutilisé
 * tel quel plutôt que réécrit, comme demandé : ce scanner ne recommande
 * jamais de prendre un trade, et le texte généré (voir alertes.js) est
 * systématiquement passé par ce contrôle avant publication.
 */

// CHOIX DE PORTAGE — frontières Unicode, pas \b -----------------------------
// Python (re) traite \w comme Unicode par défaut : « é » y est un caractère
// de mot, et \b s'ancre correctement après « sous-évalué ». JavaScript, lui,
// définit \w comme [A-Za-z0-9_] même avec le flag /u : un \b copié tel quel
// depuis Python échoue silencieusement à la frontière de la plupart des mots
// accentués de cette liste (« évalué. » n'a alors aucune transition
// mot/non-mot au sens de JS, donc aucun \b, donc aucune correspondance).
// Vérifié en écrivant le test : « Ce titre est sous-évalué. » ne
// déclenchait rien. Les frontières sont donc reconstruites ici avec des
// lookarounds sur \p{L}\p{N}_ (lettre, chiffre ou trait de soulignement
// Unicode), équivalent réel de \w Python, et le flag /u obligatoire pour
// \p{...}.
const AVANT = "(?<![\\p{L}\\p{N}_])";
const APRES = "(?![\\p{L}\\p{N}_])";

/** Corps des motifs, sans frontières — assemblés ci-dessous. */
const CORPS_MOTIFS_RECOMMANDATION = [
  "achet(?:er|ez|ons|é|ée|és)",
  "vend(?:re|ez|ons|u|ue)",
  "position(?:ner|nez|nons)",
  "investi(?:r|ssez|ssons)",
  "renforce(?:r|z|ons)",
  "alléger",
  "allege(?:r|z)",
  "opportunité",
  "à l'achat",
  "à la vente",
  "point d'entrée",
  "niveau d'entrée",
  "prendre position",
  "prise de position",
  "mérite",
  "intéressant(?:e|s|es)?",
  "attractif(?:ve|s|ves)?",
  "sous-évalué(?:e|s|es)?",
  "surévalué(?:e|s|es)?",
  "recommand(?:er|ation|é|ée|ons|ez)",
  "conseill(?:er|é|ée|ons|ez)",
  "buy",
  "sell",
  "hold",
  "strong buy",
  "price target",
  "should (?:buy|sell|invest)",
];

/** Motifs interdits, mêmes cas que modules/quantum/moves.py (voir plus haut). */
export const MOTIFS_RECOMMANDATION = CORPS_MOTIFS_RECOMMANDATION.map(
  (corps) => new RegExp(`${AVANT}${corps}${APRES}`, "iu"),
);

/**
 * Clés dont le contenu est repris verbatim d'une source externe, ou qui
 * nomment elles-mêmes les motifs interdits (l'avertissement qui dit « ce
 * texte ne recommande rien » doit pouvoir écrire les mots qu'il exclut).
 */
export const CLES_EXCLUES = new Set([
  "titre", "url", "resume", "description", "source", "query", "keywords",
  "libelleSource", "titreAffiche", "urlSource",
  "avertissement", "avertissementHeures", "limiteConnue", "infractions", "extrait", "motifsInterdits",
]);

/**
 * Parcourt une structure et signale tout motif de recommandation.
 *
 * @param {*} objet Structure à contrôler (chaîne, tableau, ou objet imbriqué).
 * @param {string} chemin Chemin courant, pour localiser une infraction.
 * @param {Set<string>} clesExclues Clés soustraites au contrôle.
 * @returns {Array<{chemin: string, motif: string, extrait: string}>} Infractions trouvées.
 */
export function verifierAbsenceRecommandation(objet, chemin = "", clesExclues = CLES_EXCLUES) {
  const infractions = [];

  function parcourir(noeud, ou) {
    if (typeof noeud === "string") {
      for (const motif of MOTIFS_RECOMMANDATION) {
        const trouve = noeud.match(motif);
        if (trouve) {
          const debut = Math.max(0, trouve.index - 40);
          infractions.push({
            chemin: ou || "(racine)",
            motif: motif.source,
            extrait: noeud.slice(debut, trouve.index + trouve[0].length + 40).trim(),
          });
        }
      }
    } else if (Array.isArray(noeud)) {
      noeud.forEach((element, i) => parcourir(element, `${ou}[${i}]`));
    } else if (noeud && typeof noeud === "object") {
      for (const [cle, valeur] of Object.entries(noeud)) {
        if (clesExclues.has(cle)) continue;
        parcourir(valeur, ou ? `${ou}.${cle}` : cle);
      }
    }
  }

  parcourir(objet, chemin);
  return infractions;
}
