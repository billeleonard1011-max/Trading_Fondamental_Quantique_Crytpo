"""Réglages communs à toute la suite de tests.

Aucun test n'accède au réseau : les appels HTTP sont systématiquement
remplacés par des doublures. L'espacement volontaire des appels GDELT
(``dataio.news.INTERVALLE_MIN_GDELT``, 5,5 s en production) n'a donc rien à
protéger ici — il ne ferait qu'ajouter des minutes d'attente à une suite qui
tourne en deux secondes. Il est neutralisé pour les tests, et seulement pour
eux : la valeur de production reste inchangée dans le module.
"""

from __future__ import annotations

import pytest

from dataio import news


@pytest.fixture(autouse=True)
def _sans_espacement_gdelt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supprime l'attente entre appels GDELT pour la durée d'un test."""
    monkeypatch.setattr(news, "INTERVALLE_MIN_GDELT", 0.0)
