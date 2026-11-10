#!/usr/bin/env python3
"""Export Excel dei fornitori (tutti o di un conto contabile) da un run del database.

Stesse definizioni della pagina Fornitori della Console Tesoreria, così i numeri coincidono:
- Conto prevalente: il conto con l'IMPORTO maggiore tra le scadenze del fornitore nel run;
- Debito totale: somma di tutte le scadenze del run (anche oltre l'orizzonte, sospese, scaduto congelato),
  al netto delle note di credito;
- di cui scaduto: scadenze con data DA DOCUMENTO precedente alla data del run;
- Nell'orizzonte: importi positivi con una data di pagamento nel piano;
- Tagliato: scadenze tagliate dal piano da regole (regola tagliata / tagliata_non_collocabile);
- Parcheggiato: scadenze a fine orizzonte (non_collocabile delle classi 2-5 e tagliata_non_collocabile);
- Elenco, Classe, Rilevanza, vincoli: versione dell'anagrafica in vigore oggi.

Uso:
  python export_fornitori_conto.py --out /opt/data/tesoreria/out/Fornitori_Farmaco.xlsx --conto "Fornitori Farmaco e Parafarmaco"
  python export_fornitori_conto.py --elenco-conti            # conti presenti nel run
Opzioni: --run-id <uuid> (default: ultimo run ufficiale riuscito). DSN da TESORERIA_PG_DSN.
Uscita: 0 ok · 1 errore.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

SQL = """
WITH r AS (SELECT run_id, coalesce((riepilogo_json->>'data_run')::date, avviato_il::date) AS data_run
           FROM tes_run WHERE run_id = %(r)s),
pesi AS (SELECT cod_fornitore, conto, sum(abs(importo_residuo)) AS peso FROM tes_proposta_riga
         WHERE run_id = %(r)s AND conto IS NOT NULL GROUP BY 1, 2),
c AS (SELECT DISTINCT ON (cod_fornitore) cod_fornitore, conto FROM pesi ORDER BY cod_fornitore, peso DESC, conto),
nc AS (SELECT cod_fornitore, count(*) AS n_conti FROM pesi GROUP BY 1),
a AS (SELECT * FROM tes_anagrafica_fornitori WHERE valido_al IS NULL)
SELECT p.cod_fornitore, max(p.ragione_sociale) AS fornitore, max(c.conto) AS conto, max(nc.n_conti) AS n_conti,
       sum(p.importo_residuo) AS debito,
       sum(p.importo_residuo) FILTER (WHERE p.scad_originale < (SELECT data_run FROM r)) AS scaduto,
       sum(p.importo_residuo) FILTER (WHERE p.importo_residuo > 0 AND p.nuova_data IS NOT NULL) AS in_orizzonte,
       sum(p.importo_residuo) FILTER (WHERE p.regola LIKE 'tagliata%%') AS tagliato,
       count(*) FILTER (WHERE p.regola LIKE 'tagliata%%') AS tagliato_n,
       sum(p.importo_residuo * greatest(0, p.nuova_data - p.scad_originale))
           FILTER (WHERE p.regola LIKE 'tagliata%%' AND p.nuova_data IS NOT NULL) AS euro_giorni,
       sum(p.importo_residuo) FILTER (WHERE p.regola IN ('non_collocabile', 'tagliata_non_collocabile')) AS parcheggiato,
       count(*) AS n_partite,
       bool_or(a.cod_fornitore IS NOT NULL) AS in_anagrafica,
       bool_or(a.in_elenco_acquisto) AS elenco, max(a.classe_priorita) AS classe, max(a.rilevanza) AS rilevanza,
       max(a.scaduto_max_tollerato) AS scaduto_max, max(a.slittamento_max_gg) AS slitt_max,
       max(a.pagamento_minimo) AS pag_min
FROM tes_proposta_riga p
LEFT JOIN c USING (cod_fornitore) LEFT JOIN nc USING (cod_fornitore) LEFT JOIN a USING (cod_fornitore)
WHERE p.run_id = %(r)s
GROUP BY p.cod_fornitore
"""

COLONNE = ["Cod.", "Fornitore", "Conto prevalente", "Debito totale €", "di cui scaduto €", "Elenco", "Classe",
           "Rilevanza", "Scaduto max €", "Slitt. max (gg)", "Pag. min €", "Nell'orizzonte €", "Tagliato €",
           "Parcheggiato €", "Tagliato (n.)", "Ritardo medio tagliato (gg)", "N. partite", "In anagrafica"]


def _f(v):
    return float(v) if isinstance(v, Decimal) else v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--conto", help="conto prevalente esatto (vuoto = tutti i fornitori)")
    ap.add_argument("--run-id", help="run da esportare (default: ultimo ufficiale riuscito)")
    ap.add_argument("--elenco-conti", action="store_true")
    a = ap.parse_args(argv)
    dsn = os.environ.get("TESORERIA_PG_DSN")
    if not dsn:
        print("TESORERIA_PG_DSN non impostata", file=sys.stderr)
        return 1
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET search_path = tesoreria")
        run = conn.execute(
            """SELECT run_id, avviato_il, riepilogo_json->>'data_run' AS data_run FROM tes_run
               WHERE (%(id)s::text IS NULL AND tipo_run = 'ufficiale' AND esito = 'ok') OR run_id::text = %(id)s
               ORDER BY avviato_il DESC LIMIT 1""", {"id": a.run_id}).fetchone()
        if not run:
            print("Nessun run trovato", file=sys.stderr)
            return 1
        righe = [{k: _f(v) for k, v in r.items()} for r in conn.execute(SQL, {"r": run["run_id"]}).fetchall()]
    if a.elenco_conti:
        tot: dict[str, list] = {}
        for r in righe:
            t = tot.setdefault(r["conto"] or "(senza conto)", [0, 0.0])
            t[0] += 1
            t[1] += r["debito"] or 0
        for k, (n, d) in sorted(tot.items(), key=lambda x: -x[1][1]):
            print(f"{k}: {n} fornitori, debito " + f"{d:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €")
        return 0
    if not a.out:
        print("--out obbligatorio", file=sys.stderr)
        return 1
    if a.conto:
        righe = [r for r in righe if (r["conto"] or "") == a.conto]
    righe.sort(key=lambda r: -abs(r["debito"] or 0))

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = "Fornitori"
    ws.append(COLONNE)
    for j in range(1, len(COLONNE) + 1):
        c = ws.cell(1, j)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E79")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for r in righe:
        rit = round(r["euro_giorni"] / r["tagliato"], 1) if r["euro_giorni"] and r["tagliato"] else None
        ws.append([r["cod_fornitore"], r["fornitore"], r["conto"], r["debito"], r["scaduto"] or 0,
                   ("S" if r["elenco"] else "N") if r["in_anagrafica"] else None, r["classe"], r["rilevanza"],
                   r["scaduto_max"], r["slitt_max"], r["pag_min"], r["in_orizzonte"] or 0, r["tagliato"] or 0,
                   r["parcheggiato"] or 0, r["tagliato_n"], rit, r["n_partite"],
                   "S" if r["in_anagrafica"] else "N"])
    n = len(righe) + 1
    somma = lambda k: round(sum(r[k] or 0 for r in righe), 2)  # noqa: E731
    ws.append(["", "TOTALE", "", somma("debito"), somma("scaduto"), "", "", "", "", "", "", somma("in_orizzonte"),
               somma("tagliato"), somma("parcheggiato"), int(somma("tagliato_n")), None, int(somma("n_partite")), ""])
    for j in range(1, len(COLONNE) + 1):
        ws.cell(n + 1, j).font = Font(bold=True)
    for j in (4, 5, 9, 11, 12, 13, 14):
        for i in range(2, n + 2):
            ws.cell(i, j).number_format = "#,##0.00"
    for j, w in enumerate([8, 40, 30, 15, 15, 8, 7, 9, 13, 10, 12, 15, 14, 15, 10, 13, 10, 11], 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLONNE))}{n}"

    ns = wb.create_sheet("Note")
    data_run = run["data_run"] or run["avviato_il"].date().isoformat()
    for riga in [
        ["Oggetto", f"Fornitori con conto prevalente «{a.conto}»" if a.conto else "Tutti i fornitori del run"],
        ["Run", str(run["run_id"])],
        ["Data del run", date.fromisoformat(data_run[:10]).strftime("%d/%m/%Y")],
        ["Fornitori esportati", len(righe)],
        ["Fornitori non in anagrafica", sum(1 for r in righe if not r["in_anagrafica"])],
        ["", ""],
        ["Colonna", "Definizione (uguale alla pagina Fornitori della Console Tesoreria)"],
        ["Conto prevalente", "Conto contabile con l'importo maggiore tra le scadenze del fornitore nel run"],
        ["Debito totale", "Somma di tutte le scadenze del run, anche oltre l'orizzonte, sospese e scaduto congelato, "
                          "al netto delle note di credito"],
        ["di cui scaduto", "Scadenze con data da documento (ScadOriginale) precedente alla data del run"],
        ["Elenco, Classe, Rilevanza, vincoli", "Anagrafica fornitori su PostgreSQL, versione in vigore oggi"],
        ["Nell'orizzonte", "Importi positivi con una data di pagamento nel piano"],
        ["Tagliato", "Scadenze tagliate dal piano da regole (tagliata, tagliata_non_collocabile)"],
        ["Parcheggiato", "Scadenze a fine orizzonte: non collocabili delle classi 2-5 e tagliate non collocabili"],
        ["Ritardo medio tagliato", "Media ponderata sugli importi tagliati di (nuova data − scadenza da documento)"],
        ["Attenzione", "Il gestionale mostra lo stato importato, la proposta lo stato calcolato: "
                       "differenze con le schermate del gestionale sono attese finché la proposta non è importata"],
    ]:
        ns.append(riga)
    ns.cell(7, 1).font = Font(bold=True)
    ns.cell(7, 2).font = Font(bold=True)
    ns.column_dimensions["A"].width = 36
    ns.column_dimensions["B"].width = 110
    a.out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(a.out)
    it = lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")  # noqa: E731
    print(f"FILE: {a.out}")
    print(f"fornitori: {len(righe)} · debito {it(somma('debito'))} € · tagliato {it(somma('tagliato'))} € · "
          f"parcheggiato {it(somma('parcheggiato'))} €")
    return 0


if __name__ == "__main__":
    sys.exit(main())
