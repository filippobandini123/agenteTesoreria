#!/usr/bin/env python3
"""Anagrafica fornitori e accordi su PostgreSQL.

Ogni comando che scrive mostra prima il confronto prima/dopo e NON scrive senza --conferma.
Il Tesoriere usa --conferma solo dopo l'ok esplicito di Filippo nella conversazione.

  python anagrafica.py cerca "qualifarma"
  python anagrafica.py mostra 101012
  python anagrafica.py storico 101012
  python anagrafica.py modifica 101012 --set rilevanza=3 --autore "Filippo (Telegram)" --motivo "meno strategico" [--dal 2026-10-28] [--conferma]
  python anagrafica.py accordo 2201 --decorrenza 2026-09-15 --set stato=Rifiutato --autore ... --motivo ... [--conferma]
  python anagrafica.py accordo 2201 --decorrenza 2026-11-01 --nuovo --set stato=Proposta --set ragione_sociale="X" ...
  python anagrafica.py accordi [--stato proposta]
  python anagrafica.py importa --da config/anagrafica_fornitori.xlsx --autore ... [--forza] [--conferma]
  python anagrafica.py esporta --a /opt/data/tesoreria/out/anagrafica_AAAAMMGG.xlsx

DSN dalla variabile d'ambiente indicata in fonti_dati.yaml (default TESORERIA_PG_DSN).
Uscita: 0 ok · 3 nessuna scrittura (manca --conferma o nessuna differenza) · 1 errore.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tesoriere import anagrafica_db as adb  # noqa: E402


def _valori(sets: list[str]) -> dict:
    out = {}
    for s in sets or []:
        k, sep, v = s.partition("=")
        if not sep:
            raise ValueError(f"--set '{s}': usare campo=valore")
        out[k.strip()] = v
    return out


def _stampa(x) -> None:
    print(json.dumps(x, ensure_ascii=False, indent=2, default=str))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    home = os.environ.get("TESORIERE_HOME")
    ap.add_argument("--fonti", type=Path,
                    default=(Path(home) if home else Path(__file__).resolve().parents[3]) / "config" / "fonti_dati.yaml")
    ap.add_argument("--oggi", type=date.fromisoformat, default=date.today())
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("cerca"); s.add_argument("testo")
    s = sub.add_parser("mostra"); s.add_argument("cod", type=int)
    s = sub.add_parser("storico"); s.add_argument("cod", type=int)
    s = sub.add_parser("accordi"); s.add_argument("--stato")
    for nome in ("modifica", "accordo"):
        s = sub.add_parser(nome)
        s.add_argument("cod", type=int)
        s.add_argument("--set", action="append", default=[], metavar="campo=valore")
        s.add_argument("--autore", required=True)
        s.add_argument("--motivo", required=True)
        s.add_argument("--conferma", action="store_true")
        if nome == "modifica":
            s.add_argument("--dal", type=date.fromisoformat, help="decorrenza della modifica (default oggi)")
        else:
            s.add_argument("--decorrenza", type=date.fromisoformat, required=True)
            s.add_argument("--nuovo", action="store_true")
    s = sub.add_parser("importa")
    s.add_argument("--da", type=Path, required=True)
    s.add_argument("--autore", required=True)
    s.add_argument("--forza", action="store_true")
    s.add_argument("--conferma", action="store_true")
    s = sub.add_parser("esporta"); s.add_argument("--a", type=Path, required=True)
    a = ap.parse_args(argv)

    fonti = yaml.safe_load(a.fonti.read_text(encoding="utf-8"))
    dbc = fonti.get("database") or {}
    dsn = os.environ.get(dbc.get("dsn_env", "TESORERIA_PG_DSN"))
    if not dsn:
        print(f"Variabile {dbc.get('dsn_env', 'TESORERIA_PG_DSN')} non impostata", file=sys.stderr)
        return 1
    an = fonti.get("anagrafica_fornitori") or {}
    try:
        with adb.connetti(dsn, dbc.get("schema", "tesoreria")) as conn, conn.cursor() as cur:
            if a.cmd == "cerca":
                _stampa(adb.cerca_fornitori(cur, a.testo, a.oggi)); return 0
            if a.cmd == "mostra":
                _stampa(adb.versione_corrente(cur, a.cod) or f"fornitore {a.cod} non presente"); return 0
            if a.cmd == "storico":
                _stampa(adb.storico_fornitore(cur, a.cod)); return 0
            if a.cmd == "accordi":
                r = adb.accordi_tutti(cur)
                _stampa([x for x in r if not a.stato or x["stato"] == a.stato.lower()]); return 0
            if a.cmd == "esporta":
                print(adb.esporta_excel(cur, a.a, a.oggi)); return 0
            if a.cmd == "importa":
                if not a.conferma:
                    print("Import iniziale: rilanciare con --conferma per scrivere.", file=sys.stderr); return 3
                _stampa(adb.importa_excel(cur, a.da, an.get("foglio", "Fornitori"), an.get("foglio_accordi", "Accordi"),
                                          a.oggi, a.autore, a.forza))
                return 0
            if a.cmd == "modifica":
                e = adb.modifica_fornitore(cur, a.cod, _valori(a.set), a.autore, a.motivo, a.dal or a.oggi, a.conferma)
            else:
                e = adb.modifica_accordo(cur, a.cod, a.decorrenza, _valori(a.set), a.autore, a.motivo,
                                         a.conferma, a.nuovo)
            _stampa(e)
            if not e["cambiati"]:
                print("Nessuna differenza: niente da scrivere.", file=sys.stderr); return 3
            if not e["scritto"]:
                print("ANTEPRIMA — niente scritto. Rilanciare con --conferma dopo l'ok di Filippo.", file=sys.stderr)
                return 3
            return 0
    except (ValueError, RuntimeError) as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
