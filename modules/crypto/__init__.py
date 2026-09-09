"""Veille sur les positions crypto : régime de marché et positionnement.

Ce paquet **classe et décrit, il ne prédit ni ne recommande**. Un régime
n'est pas une prévision de prix : c'est une lecture de l'état actuel du
marché, assortie de la condition qui la rendrait caduque.

Modules
-------
``regime``       Classification en accumulation, expansion, distribution ou
                 capitulation, à partir du MVRV et de l'offre de stablecoins.
``positioning``  Funding et open interest des perpétuels, positions suivies.
``rotation``     Synthèse de rotation entre bitcoin et alts.
``feed``         Fil d'actualité crypto, même patron que le fil quantique.
``run``          Orchestration et publication du JSON quotidien.
"""

__all__ = ["regime", "positioning", "rotation", "feed", "run"]
