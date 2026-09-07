"""Veille sur les valeurs cotées du calcul quantique.

Ce paquet **explique, il ne recommande jamais**. Aucune sortie ne suggère
d'acheter, de vendre, de renforcer ou d'alléger, et aucune ne qualifie un
titre d'intéressant ou d'attractif. Le rôle est de fournir le contexte
factuel le plus complet possible — ce qui a bougé, de combien, pourquoi, et
si c'est propre au titre ou commun à tout le secteur. La décision reste
entièrement à l'utilisateur.

Cette contrainte n'est pas seulement documentaire : elle est vérifiée
mécaniquement par :func:`modules.quantum.moves.verifier_absence_recommandation`,
appelée par le point d'entrée avant publication et par la suite de tests.

Modules
-------
``moves``     Détection et explication des mouvements de prix marqués.
``industry``  Vue d'ensemble du secteur : trésorerie, financements,
              nouveaux entrants, corrélation entre positions.
``run``       Orchestration et publication du JSON quotidien.
"""

__all__ = ["moves", "industry", "run"]
