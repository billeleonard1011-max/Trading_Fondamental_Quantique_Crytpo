"""Moteur d'analyse fondamentale de l'or (XAUUSD).

Enchaînement des modules
------------------------
1. :mod:`~modules.gold.fair_value` estime ce que valent l'or selon les taux
   réels et le dollar, et mesure surtout **le résidu** : la prime que le
   marché paie pour autre chose.
2. :mod:`~modules.gold.geopolitics` cherche à nommer ce « autre chose », et
   vérifie s'il est déjà payé.
3. :mod:`~modules.gold.analogues` demande ce qui s'est passé les fois
   précédentes où la configuration ressemblait à celle du jour.
4. :mod:`~modules.gold.bias` agrège le tout en un biais quotidien, avec le
   détail de chaque contribution.
5. :mod:`~modules.gold.explain` met le résultat en français lisible, sous
   contrainte de ne citer aucun chiffre absent des données.
6. :mod:`~modules.gold.run` orchestre et publie le JSON du jour.

Aucun de ces modules ne décide d'entrer ou de sortir d'une position : ils
décrivent un contexte, ils ne donnent pas d'ordre.
"""

__all__ = [
    "fair_value",
    "geopolitics",
    "analogues",
    "bias",
    "explain",
    "run",
]
