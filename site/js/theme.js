/**
 * Bascule de thème, mémorisée localement.
 *
 * La préférence est relue avant le premier rendu pour éviter que la page
 * s'affiche en clair puis bascule en sombre sous les yeux du lecteur.
 * ``localStorage`` peut lever — navigation privée, stockage bloqué —, d'où
 * les gardes : le site doit rester utilisable sans mémoire.
 */

const CLE = "theme-suivi";

/**
 * Lit la préférence enregistrée.
 *
 * @returns {string} ``clair`` ou ``sombre``.
 */
export function themeEnregistre() {
  try {
    const valeur = localStorage.getItem(CLE);
    if (valeur === "clair" || valeur === "sombre") return valeur;
  } catch {
    // Stockage indisponible : on retombe sur le thème par défaut.
  }
  return "clair";
}

/**
 * Applique un thème au document.
 *
 * @param {string} theme Thème à appliquer.
 */
export function appliquerTheme(theme) {
  const choisi = theme === "sombre" ? "sombre" : "clair";
  document.documentElement.setAttribute("data-theme", choisi);
  const bouton = document.querySelector("button.theme");
  if (bouton) {
    bouton.textContent = choisi === "sombre" ? "Thème clair" : "Thème sombre";
    bouton.setAttribute("aria-label", `Basculer vers le thème ${
      choisi === "sombre" ? "clair" : "sombre"
    }`);
  }
  try {
    localStorage.setItem(CLE, choisi);
  } catch {
    // Sans mémoire, le thème vaut pour la session : acceptable.
  }
}

/** Branche le bouton de bascule. */
export function installerTheme() {
  appliquerTheme(themeEnregistre());
  const bouton = document.querySelector("button.theme");
  if (bouton) {
    bouton.addEventListener("click", () => {
      const courant = document.documentElement.getAttribute("data-theme");
      appliquerTheme(courant === "sombre" ? "clair" : "sombre");
    });
  }
}
