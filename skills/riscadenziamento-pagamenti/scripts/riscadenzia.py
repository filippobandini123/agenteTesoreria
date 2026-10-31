#!/usr/bin/env python3
"""Motore di riscadenziamento pagamenti — punto d'ingresso.

Exit code: 0 ok · 1 errore tecnico · 2 controlli bloccanti KO (nessuna proposta)

Esempio:
  python riscadenzia.py --regole ../../../config/regole.yaml --fonti ../../../config/fonti_dati.yaml \
      --anagrafica ../../../config/anagrafica_fornitori.xlsx --input /opt/data/tesoreria/in \
      --out /opt/data/tesoreria/out --json /opt/data/tesoreria/out/ultimo_run.json
Simulazione:
  ... --simula --override classe:1207=2 --override elenco:1280=S --override fido=1500000
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import os
import sys
import traceback
import uuid
import warnings
from datetime import date, datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tesoriere import controlli as ctl  # noqa: E402
from tesoriere.alloca import pianifica  # noqa: E402
from tesoriere.calendario import Calendario  # noqa: E402
from tesoriere.carica import carica_accordi, carica_anagrafica, carica_partite, carica_previsione, trova_input  # noqa: E402
from tesoriere.classifica import classifica, fornitori_nuovi  # noqa: E402
from tesoriere.modello import Fornitore  # noqa: E402
from tesoriere.output import scrivi_excel, scrivi_json, sintesi  # noqa: E402

warnings.filterwarnings("ignore", module="openpyxl")


def applica_override(overrides: list[str], regole: dict, anagrafica: dict[int, Fornitore]) -> None:
    for o in overrides:
        chiave, _, valore = o.partition("=")
        if chiave.startswith("classe:"):
            cod = int(chiave.split(":")[1])
            anagrafica.setdefault(cod, Fornitore(cod)).classe_priorita = int(valore)
        elif chiave.startswith("elenco:"):
            cod = int(chiave.split(":")[1])
            anagrafica.setdefault(cod, Fornitore(cod)).in_elenco_acquisto = valore.strip().upper() == "S"
        elif chiave.startswith("slittamento_max:"):
            cod = int(chiave.split(":")[1])
            anagrafica.setdefault(cod, Fornitore(cod)).slittamento_max_gg = int(valore)
        elif chiave.startswith("scaduto_max:"):
            cod = int(chiave.split(":")[1])
            anagrafica.setdefault(cod, Fornitore(cod)).scaduto_max_tollerato = float(valore)
        elif chiave.startswith("rilevanza:"):
            cod = int(chiave.split(":")[1])
            anagrafica.setdefault(cod, Fornitore(cod)).rilevanza = int(valore)
        elif chiave == "piano":
            if valore not in ("due_fasi", "cassa"):
                raise ValueError("piano: due_fasi | cassa")
            regole.setdefault("piano", {})["modalita"] = valore
        elif chiave == "fido":
            regole["vincolo_cassa"]["fido_anticipo_pos"] = float(valore)
        elif chiave == "allerta":
            regole.setdefault("allerte", {})["margine_minimo"] = float(valore)
        elif chiave == "lookahead":
            regole["allocazione"]["lookahead_giorni"] = None if valore == "orizzonte" else int(valore)
        elif chiave == "cuscinetto":
            regole["vincolo_cassa"]["cuscinetto_minimo"] = float(valore)
        elif chiave == "orizzonte":
            g = int(valore)
            o_cfg = regole["orizzonte"]
            if not int(o_cfg.get("giorni_min", 1)) <= g <= int(o_cfg.get("giorni_max", 365)):
                raise ValueError(f"orizzonte {g} fuori dal range ammesso")
            o_cfg["giorni"] = g
        elif chiave.startswith("stato_accordo:"):
            pass                                      # applicato agli accordi da applica_override_accordi
        else:
            raise ValueError(f"Override non riconosciuto: {o}")


def applica_override_accordi(overrides: list[str], accordi: dict, proposte: dict, ignorati: list) -> None:
    """Simulazione sullo stato degli accordi: stato_accordo:<cod>=Rifiutato toglie l'accordo dal piano,
    Attivo/Proposta lo lascia (le proposte valgono già come accordi certi)."""
    for o in overrides:
        chiave, _, valore = o.partition("=")
        if not chiave.startswith("stato_accordo:"):
            continue
        cod = int(chiave.split(":")[1])
        v = valore.strip().lower()
        if v.startswith("rifiut"):
            for d in (accordi, proposte):
                if cod in d:
                    ignorati.append(d.pop(cod))
        elif v not in ("attivo", "proposta"):
            raise ValueError(f"stato_accordo:{cod}: Attivo, Proposta o Rifiutato")


def archivia_input(file_in: Path, archivio: Path, run: dict, oggi: date, salva: bool) -> list:
    """Copia il Report ricevuto (es. allegato Telegram) nell'archivio, con data e impronta nel nome.
    Avvisa se lo stesso file (stessa impronta) è già stato elaborato."""
    import shutil
    from tesoriere.controlli import Esito
    h8 = run["hash_input"][:8]
    archivio.mkdir(parents=True, exist_ok=True)
    gia = sorted(archivio.glob(f"*_{h8}_*"))
    out = []
    if gia:
        run["file_input"] = str(gia[0])
        out.append(Esito("file_gia_elaborato", "avviso",
                         f"Stesso file (impronta {h8}) già ricevuto: {gia[0].name}. Il Report è stato aggiornato?"))
    if salva and not gia:
        dest = archivio / f"{oggi:%Y%m%d}_{h8}_{file_in.name}"
        shutil.copy2(file_in, dest)
        run["file_input"] = str(dest)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Riscadenziamento pagamenti fornitori")
    ap.add_argument("--regole", required=True, type=Path)
    ap.add_argument("--fonti", required=True, type=Path)
    ap.add_argument("--anagrafica", type=Path)
    ap.add_argument("--input", required=True, type=Path, help="file o cartella con il report di cassa")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--oggi", type=date.fromisoformat, default=date.today(), help="data del run (AAAA-MM-GG)")
    ap.add_argument("--simula", action="store_true")
    ap.add_argument("--override", action="append", default=[])
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--autore", default="tesoriere", help="chi ha avviato il run (es. utente della console)")
    a = ap.parse_args(argv)

    run_id = str(uuid.uuid4())
    avvio = datetime.now().astimezone()
    tipo = "simulazione" if a.simula or a.override else "ufficiale"
    json_path = a.json or (a.out / "ultimo_run.json")
    regole_testo = a.regole.read_text(encoding="utf-8")
    regole = yaml.safe_load(regole_testo)
    fonti = yaml.safe_load(a.fonti.read_text(encoding="utf-8"))
    meta = {"run_id": run_id, "tipo_run": tipo, "data_run": a.oggi, "avviato_il": avvio.isoformat(timespec="seconds"),
            "versione_regole": regole.get("versione"), "override": a.override}
    dsn = None if a.no_db else os.environ.get(fonti.get("database", {}).get("dsn_env", "TESORERIA_PG_DSN"))
    schema = fonti.get("database", {}).get("schema", "tesoreria")
    run = {"run_id": run_id, "tipo_run": tipo, "avviato_il": avvio, "override": a.override, "avviato_da": a.autore}

    try:
        file_in = trova_input(a.input, fonti["previsione"]["file"])
        run["file_input"] = str(file_in)
        run["hash_input"] = hashlib.sha256(file_in.read_bytes()).hexdigest()
        meta.update(file_input=file_in.name, hash_input=run["hash_input"][:16])

        orz = regole["orizzonte"]
        cal = Calendario(orz.get("calendario_pagamenti", "giorni_lavorativi_bancari_IT"),
                         [date.fromisoformat(str(x)) for x in orz.get("festivi_extra") or []])
        prev = carica_previsione(file_in, fonti)
        partite = carica_partite(file_in, fonti)
        an_cfg = fonti["anagrafica_fornitori"]
        crit_def = (regole.get("accordi") or {}).get("criterio_default", "data_doc")
        if a.anagrafica:      # Excel esplicito: test, simulazioni offline, sorgente "excel"
            fonte_anag = "excel"
            anagrafica = carica_anagrafica(a.anagrafica, an_cfg["foglio"], a.oggi)
            accordi, proposte, accordi_ignorati = carica_accordi(
                a.anagrafica, an_cfg.get("foglio_accordi", "Accordi"), a.oggi, crit_def)
        elif an_cfg.get("sorgente", "excel") == "db":
            if not dsn:
                raise RuntimeError("Anagrafica su database ma connessione assente (--no-db o variabile "
                                   f"{fonti.get('database', {}).get('dsn_env', 'TESORERIA_PG_DSN')} vuota): "
                                   "passare --anagrafica <file.xlsx> per lavorare offline.")
            from tesoriere.anagrafica_db import carica_da_db
            fonte_anag = "database"
            anagrafica, (accordi, proposte, accordi_ignorati) = carica_da_db(dsn, schema, a.oggi, crit_def)
        else:
            fonte_anag = "nessuna"
            anagrafica, accordi, proposte, accordi_ignorati = {}, {}, {}, []
        meta["fonte_anagrafica"] = fonte_anag
        regole_eff = copy.deepcopy(regole)
        applica_override(a.override, regole_eff, anagrafica)
        applica_override_accordi(a.override, accordi, proposte, accordi_ignorati)
        run["data_consuntivo"] = prev.data_consuntivo
        meta["data_consuntivo"] = prev.data_consuntivo

        esiti = ctl.esegui(file_in, prev, partite, fonti, regole_eff, a.oggi, cal, simulazione=(tipo == "simulazione"))
        archivio = fonti["previsione"].get("archivio")
        if archivio:
            esiti += archivia_input(file_in, Path(archivio), run, a.oggi, salva=(tipo == "ufficiale"))
        esiti += ctl.controlli_accordi(accordi, accordi_ignorati, partite, prev, regole_eff, a.oggi, proposte)
        if any(e.esito == "ko" for e in esiti):
            run["esito"] = "controlli_ko"
            dati = {**meta, "esito": "controlli_ko",
                    "controlli": [{"id": e.id, "esito": e.esito, "messaggio": e.messaggio} for e in esiti]}
            scrivi_json(json_path, dati)
            if dsn:
                from tesoriere.db import registra_run
                registra_run(dsn, schema, run, esiti, None, regole_eff, regole_testo, anagrafica, a.oggi, dati,
                             sincronizza_anagrafica=(fonte_anag == "excel"))
            print(f"CONTROLLI KO — vedi {json_path}", file=sys.stderr)
            return 2

        # le proposte di accordo valgono come accordi certi (deciso 26/10); lo stato resta come etichetta
        accordi = {**proposte, **accordi}
        classifica(partite, anagrafica, regole_eff, accordi)
        nuovi = fornitori_nuovi(partite, anagrafica)
        ris = pianifica(partite, prev, anagrafica, regole_eff, a.oggi, cal)

        nome = fonti["output"]["nome_file"].format(data_run=a.oggi, run_id=run_id[:8])
        if tipo == "simulazione":
            nome = "SIM_" + nome
        file_out = a.out / nome
        run["file_output"] = str(file_out)
        meta["fornitori_non_in_anagrafica"] = len(nuovi)
        meta["accordi_attivi"] = len(accordi)
        meta["accordi_proposti"] = len(proposte)
        scrivi_excel(file_out, ris, esiti, regole_eff, fonti, meta, a.oggi, prev.saldo_cache, accordi)
        dati = {**sintesi(ris, esiti, meta, accordi, regole_eff), "esito": "ok", "file_proposta": str(file_out),
                "fornitori_nuovi": [{"cod": c, "nome": n} for c, n in nuovi[:50]]}
        scrivi_json(json_path, dati)
        run["esito"] = "ok"
        if dsn:
            from tesoriere.db import registra_run
            registra_run(dsn, schema, run, esiti, ris, regole_eff, regole_testo, anagrafica, a.oggi, dati, accordi,
                         sincronizza_anagrafica=(fonte_anag == "excel"))
        print(f"OK — {file_out}")
        return 0
    except Exception as exc:  # errore tecnico: lasciare traccia leggibile all'agente
        scrivi_json(json_path, {**meta, "esito": "errore", "errore": f"{type(exc).__name__}: {exc}",
                                "traceback": traceback.format_exc(limit=5)})
        print(traceback.format_exc(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
