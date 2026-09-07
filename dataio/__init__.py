"""Couche d'accès aux données externes (prix, macro, actualités, crypto).

Chaque module de ce paquet suit les mêmes règles :

* aucune clé d'API en dur, tout passe par ``os.environ`` ;
* tout appel réseau porte un ``timeout`` explicite ;
* une source indisponible journalise un avertissement et renvoie une
  structure vide, elle ne fait jamais tomber le programme appelant.
"""

__all__ = ["market", "macro", "news", "crypto"]
