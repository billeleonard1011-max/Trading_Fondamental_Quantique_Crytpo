"""Backtester de la stratégie ICT sur XAUUSD.

Règle qui prime sur toutes les autres
-------------------------------------
**Aucun look-ahead.** Un backtest qui consulte ne serait-ce qu'une bougie
postérieure à la barre courante produit des résultats flatteurs et faux.
Toute l'architecture de ce paquet en découle : le moteur avance barre par
barre, chaque décision ne voit que ce qui est clos, et la propriété est
vérifiée mécaniquement par troncature de l'historique.

Modules
-------
``data``       Agrégation des unités de temps depuis une unique série M1.
``ict``        Order blocks, classification de jambe, FVG, Fibonacci OTE.
``execution``  Coûts, dimensionnement en euros, stop et objectifs.
``propfirm``   Règles FTMO et Monte Carlo par rééchantillonnage.
``run``        Orchestration, comparaison des variantes, journal des trades.

Ce paquet mesure une stratégie, il n'en recommande aucune.
"""

__all__ = ["data", "ict", "execution", "propfirm", "run"]
