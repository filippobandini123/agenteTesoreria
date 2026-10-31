"""Accesso a Postgres per la console: una connessione per richiesta, schema tesoreria."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from . import config


@contextmanager
def connessione():
    with psycopg.connect(config.DSN, row_factory=dict_row) as conn:
        conn.execute(f"SET search_path = {config.SCHEMA}, public")
        yield conn


def _pulisci(r: dict) -> dict:
    return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in r.items()}


def tutti(sql: str, par=()) -> list[dict]:
    with connessione() as c:
        return [_pulisci(r) for r in c.execute(sql, par).fetchall()]


def uno(sql: str, par=()) -> dict | None:
    r = tutti(sql, par)
    return r[0] if r else None
