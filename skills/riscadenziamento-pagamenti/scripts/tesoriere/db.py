"""Scrittura su PostgreSQL in un'unica transazione: o tutto il run, o niente."""
from __future__ import annotations

import hashlib
import json
from datetime import date

from .controlli import Esito
from .modello import Fornitore, Risultato
from .output import azione_operativa, nota_proposta


def _connetti(dsn: str):
    import psycopg  # import tardivo: il motore funziona anche senza DB (--no-db)
    return psycopg.connect(dsn)


def _regole_versione(cur, regole_testo: str, versione: str) -> int:
    h = hashlib.sha256(regole_testo.encode("utf-8")).hexdigest()
    cur.execute("SELECT id FROM tes_regole_versione WHERE hash_sha256 = %s", (h,))
    r = cur.fetchone()
    if r:
        return r[0]
    cur.execute("INSERT INTO tes_regole_versione (versione, hash_sha256, contenuto_yaml) VALUES (%s,%s,%s) RETURNING id",
                (versione, h, regole_testo))
    return cur.fetchone()[0]


def _sincronizza_anagrafica(cur, anagrafica: dict[int, Fornitore], oggi: date) -> int:
    """SCD tipo 2: una nuova versione solo se qualcosa è cambiato."""
    campi = ("in_elenco_acquisto", "classe_priorita", "scaduto_max_tollerato", "slittamento_max_gg",
             "pagamento_minimo", "note")
    n = 0
    for f in anagrafica.values():
        nuovo = (f.in_elenco_acquisto, f.classe_priorita, f.scaduto_max_tollerato, f.slittamento_max_gg,
                 f.pagamento_minimo, f.note or None)
        cur.execute(f"SELECT id, {', '.join(campi)} FROM tes_anagrafica_fornitori "
                    "WHERE cod_fornitore = %s AND valido_al IS NULL", (f.cod_fornitore,))
        r = cur.fetchone()
        if r:
            attuale = (r[1], r[2], None if r[3] is None else float(r[3]), r[4],
                       None if r[5] is None else float(r[5]), r[6])
            if attuale == nuovo:
                continue
            cur.execute("UPDATE tes_anagrafica_fornitori SET valido_al = %s WHERE id = %s", (oggi, r[0]))
        cur.execute("INSERT INTO tes_anagrafica_fornitori (cod_fornitore, ragione_sociale, "
                    f"{', '.join(campi)}, valido_dal) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (cod_fornitore, valido_dal) DO UPDATE SET valido_al = NULL",
                    (f.cod_fornitore, f.ragione_sociale, *nuovo, max(f.valido_dal or oggi, oggi)))
        n += 1
    return n


def _sincronizza_accordi(cur, accordi: dict) -> None:
    for a in accordi.values():
        cur.execute("""INSERT INTO tes_accordo (cod_fornitore, tipo, decorrenza, criterio, valido_fino, note, stato)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (cod_fornitore, decorrenza) DO UPDATE
                       SET tipo = EXCLUDED.tipo, criterio = EXCLUDED.criterio, stato = EXCLUDED.stato,
                           valido_fino = EXCLUDED.valido_fino, note = EXCLUDED.note, aggiornato_il = now()
                       WHERE (tes_accordo.tipo, tes_accordo.criterio, tes_accordo.valido_fino, tes_accordo.note,
                              tes_accordo.stato)
                             IS DISTINCT FROM (EXCLUDED.tipo, EXCLUDED.criterio, EXCLUDED.valido_fino, EXCLUDED.note,
                              EXCLUDED.stato)""",
                    (a.cod_fornitore, a.tipo, a.decorrenza, a.criterio, a.valido_fino, a.note or None, a.stato))


def registra_run(dsn: str, schema: str, run: dict, controlli: list[Esito], ris: Risultato | None,
                 regole: dict, regole_testo: str, anagrafica: dict[int, Fornitore], data_run: date,
                 sintesi: dict | None, accordi: dict | None = None, sincronizza_anagrafica: bool = True) -> None:
    with _connetti(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"SET search_path = {schema}")
        rv = _regole_versione(cur, regole_testo, str(regole.get("versione", "")))
        if run["tipo_run"] == "ufficiale" and sincronizza_anagrafica:   # solo con anagrafica da Excel
            _sincronizza_anagrafica(cur, anagrafica, data_run)
            _sincronizza_accordi(cur, accordi or {})
        fab = (sintesi or {}).get("fabbisogno") or {}
        cur.execute(
            """INSERT INTO tes_run (run_id, tipo_run, avviato_il, terminato_il, esito, data_consuntivo,
                   orizzonte_da, orizzonte_a, regole_versione_id, file_input, hash_input, file_output,
                   override_json, fabbisogno_picco, fabbisogno_data, riepilogo_json, avviato_da)
               VALUES (%s,%s,%s,now(),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (run["run_id"], run["tipo_run"], run["avviato_il"], run["esito"], run.get("data_consuntivo"),
             ris.primo_pagamento if ris else None, ris.ultimo_pagamento if ris else None, rv,
             run.get("file_input"), run.get("hash_input"), run.get("file_output"),
             json.dumps(run.get("override") or []), fab.get("picco"), fab.get("data_picco"),
             json.dumps(sintesi, default=str) if sintesi else None, run.get("avviato_da")))
        for c in controlli:
            cur.execute("INSERT INTO tes_controllo (run_id, id_ctrl, esito, messaggio) VALUES (%s,%s,%s,%s)",
                        (run["run_id"], c.id, c.esito, c.messaggio))
        if ris is not None:
            with cur.copy("""COPY tes_proposta_riga (run_id, partita_id, id_scadenza, nota_precedente, prg_documento, num_protocollo,
                     scad_originale, cod_fornitore, ragione_sociale, modalita, classe, origine_classe,
                     importo_residuo, scadenza_corrente, nuova_data, giorni_slittamento, regola, motivo,
                     nota_amm_proposta, azione_operativa, variata, importabile, fonte, conto) FROM STDIN""") as cp:
                for p in ris.partite:
                    cp.write_row((run["run_id"], p.id, p.id_scadenza, p.note or None, p.prg_documento, p.num_protocollo, p.scad_originale,
                                  p.cod_fornitore, p.ragione_sociale, p.modalita, p.classe, p.origine_classe,
                                  p.importo, p.scad_corrente, p.nuova_data, p.giorni_slittamento, p.regola,
                                  p.motivo, nota_proposta(p, regole, data_run) or None,
                                  azione_operativa(p, regole, data_run) or None, p.variata, p.importabile, p.fonte,
                                  p.conto or None))
            with cur.copy("""COPY tes_saldo_giornaliero (run_id, data, saldo_ante, saldo_post, limite,
                     capacita, fabbisogno) FROM STDIN""") as cp:
                for d in ris.giorni:
                    cp.write_row((run["run_id"], d, round(ris.saldo_ante[d], 2), round(ris.saldo_post[d], 2),
                                  ris.limite, None if d not in ris.capacita else round(ris.capacita[d], 2),
                                  round(ris.fabbisogno[d], 2)))
        cur.execute("INSERT INTO tes_audit (run_id, evento, dettaglio, autore) VALUES (%s,'run_motore',%s,'motore')",
                    (run["run_id"], json.dumps({"esito": run["esito"], "tipo": run["tipo_run"]})))
        if run["esito"] != "ok":
            cur.execute("UPDATE tes_run SET stato_approvazione = 'superata' WHERE run_id = %s", (run["run_id"],))
        elif run["tipo_run"] == "ufficiale":
            cur.execute("""UPDATE tes_run SET stato_approvazione = 'superata'
                           WHERE tipo_run = 'ufficiale' AND run_id <> %s AND stato_approvazione = 'da_rivedere'""",
                        (run["run_id"],))
