"""Calendario dei giorni in cui si può disporre un pagamento."""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


def _pasqua(anno: int) -> date:
    # algoritmo di Meeus/Jones/Butcher (calendario gregoriano)
    a = anno % 19
    b, c = divmod(anno, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mese = (h + l - 7 * m + 114) // 31
    giorno = (h + l - 7 * m + 114) % 31 + 1
    return date(anno, mese, giorno)


@lru_cache(maxsize=None)
def festivi_it(anno: int) -> frozenset[date]:
    pasqua = _pasqua(anno)
    fissi = [(1, 1), (1, 6), (4, 25), (5, 1), (6, 2), (8, 15), (11, 1), (12, 8), (12, 25), (12, 26)]
    return frozenset([date(anno, m, g) for m, g in fissi] + [pasqua + timedelta(days=1)])


class Calendario:
    def __init__(self, tipo: str = "giorni_lavorativi_bancari_IT", extra: list[date] | None = None):
        self.tipo = tipo
        self.extra = set(extra or [])

    def lavorativo(self, d: date) -> bool:
        if d.weekday() >= 5 or d in self.extra:
            return False
        if self.tipo == "giorni_lavorativi_bancari_IT" and d in festivi_it(d.year):
            return False
        return True

    def prossimo(self, d: date) -> date:
        """Primo giorno lavorativo >= d."""
        while not self.lavorativo(d):
            d += timedelta(days=1)
        return d

    def precedente(self, d: date) -> date:
        """Ultimo giorno lavorativo <= d."""
        while not self.lavorativo(d):
            d -= timedelta(days=1)
        return d
