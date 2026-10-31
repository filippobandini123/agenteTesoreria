"""File di proposta (Excel) e riepilogo per l'agente (JSON)."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .controlli import Esito
from .modello import Partita, Risultato

FONT = Font(name="Arial", size=10)
BOLD = Font(name="Arial", size=10, bold=True)
HDR = Font(name="Arial", size=10, bold=True, color="FFFFFF")
HDR_FILL = PatternFill("solid", fgColor="1F4E78")
EUR = '#,##0.00;[Red]-#,##0.00;"-"'
DATA = "dd/mm/yyyy"

REGOLE_SPOSTATE = {"slittata", "pagata_alla_data", "scaduta_pagata", "forzata_slitt_max",
                   "forzata_scaduto_max", "non_collocabile"}


def codice_origine(p: Partita, cfg: dict) -> str:
    """Sigla breve dell'origine della classe, da note_amministrative.codici_origine."""
    codici = cfg.get("codici_origine") or {}
    o = p.origine_classe or ""
    if o.startswith("regola_auto:"):
        return codici.get(o.split(":", 1)[1], o.split(":", 1)[1][:6].upper())
    if o.startswith("accordo:"):
        return codici.get("accordo_scaduto", "ACCS") if p.congelata_accordo else codici.get("accordo", "ACC")
    if o.startswith("anagrafica:"):
        return codici.get(o.split(":", 1)[1], "ANAG")
    return codici.get(o or "default", "DEF")


def compatta_nota(nota: str, cfg: dict) -> str:
    """Nota compatta: via le ipotesi superate (HP, HPAG), solo l'ultima TC/TIC/SOL,
    il resto (BLOC, PDR, testo libero) conservato nell'ordine originale."""
    sep = cfg.get("separatore", "_")
    testo = nota or ""
    for pat in cfg.get("sigle_da_togliere") or []:
        testo = re.sub(pat, sep, testo)
    for pat in cfg.get("sigle_solo_ultima") or []:
        trovati = list(re.finditer(pat, testo))
        for m in reversed(trovati[:-1]):          # dal fondo, per non spostare le posizioni
            testo = testo[:m.start()] + sep + testo[m.end():]
    pezzi = [x.strip(" *\n\r\t") for x in testo.split(sep)]
    return sep.join(x for x in pezzi if re.search(r"[0-9A-Za-z]", x))


def nota_proposta(p: Partita, regole: dict, data_run: date) -> str:
    cfg = regole.get("note_amministrative") or {}
    if not cfg.get("genera") or not p.variata:
        return ""
    nuova = cfg.get("formato", "HPAG({data_run:%d/%m}):{nuova_data:%d/%m}(AG)").format(
        data_run=data_run, nuova_data=p.nuova_data, classe=p.classe, origine=codice_origine(p, cfg))
    modo = cfg.get("modalita", "additiva")
    if modo == "compatta":                     # il gestionale SOSTITUISCE la nota: si manda la nota compatta
        base = compatta_nota(p.note, cfg)
        return f"{base}{cfg.get('separatore', '_')}{nuova}" if base else nuova
    if modo == "accoda" and p.note:          # nota completa (gestionale che SOSTITUISCE la nota)
        return f"{p.note}{cfg.get('separatore', '_')}{nuova}"
    if modo == "additiva" and p.note:        # il gestionale accoda: si manda solo il pezzo nuovo
        return f"{cfg.get('separatore', '_')}{nuova}"
    return nuova


def nota_troppo_lunga(p: Partita, regole: dict, data_run: date) -> bool:
    cfg = regole.get("note_amministrative") or {}
    lim = cfg.get("lunghezza_max")
    return bool(lim) and len(nota_proposta(p, regole, data_run)) > int(lim)


def azione_operativa(p: Partita, regole: dict, data_run: date | None = None) -> str:
    if not p.variata:
        return ""
    azioni = []
    testo = (regole.get("azioni_operative") or {}).get(p.modalita)
    if testo:
        azioni.append(testo)
    if not p.importabile:
        azioni.append("Riscadenziare a mano nel gestionale (protocollo mancante o chiave ambigua)")
    cfg = regole.get("note_amministrative") or {}
    for sigla, msg in (cfg.get("sigle_da_segnalare") or {}).items():
        if re.search(rf"(?<![A-Za-z]){re.escape(sigla)}(?![A-Za-z])", p.note or ""):
            azioni.append(msg)
    if data_run and nota_troppo_lunga(p, regole, data_run):
        azioni.append(f"Nota oltre {cfg.get('lunghezza_max')} caratteri: accorciare a mano (esclusa dall'import)")
    return " · ".join(azioni)


NON_COLLOCABILI = ("non_collocabile", "tagliata_non_collocabile")


def verifica_accordi(ris: Risultato) -> dict:
    """Scoperto dopo i tagli (prima delle classi 2-5), escluso l'ultimo giorno dove si parcheggia:
    se c'è, classe 0 e accordi non stanno nel fido nemmeno tagliando tutto il tagliabile."""
    if not ris.saldo_dopo_tagli:
        return {}
    giorni = [d for d in ris.giorni if ris.primo_pagamento <= d < ris.ultimo_pagamento]
    marg = {d: ris.saldo_dopo_tagli[d] - ris.limite for d in giorni}
    neg = [d for d in giorni if marg[d] < -0.005]
    dmin = min(marg, key=marg.get) if marg else None
    return {"primo_giorno_oltre_fido": str(neg[0]) if neg else None,
            "scoperto_massimo": round(max(0.0, -marg[dmin]), 2) if dmin else 0.0,
            "data_scoperto_massimo": str(dmin) if dmin and marg[dmin] < -0.005 else None,
            "giorni_oltre_fido": len(neg)}


def piano_taglio(ris: Risultato) -> dict | None:
    """Sintesi del piano in due fasi: scoperto del piano da regole e tagli proposti."""
    if not ris.due_fasi:
        return None
    marg = {d: ris.saldo_regole[d] - ris.limite for d in ris.giorni}
    neg = [d for d in ris.giorni if marg[d] < -0.005 and d >= ris.primo_pagamento]
    dmin = min(marg, key=marg.get)
    t = [p for p in ris.partite if p.regola in ("tagliata", "tagliata_non_collocabile")]
    per_ril = defaultdict(lambda: {"n": 0, "importo": 0.0, "fornitori": set()})
    for p in t:
        x = per_ril[str(p.rilevanza)]
        x["n"] += 1
        x["importo"] += p.importo
        x["fornitori"].add(p.ragione_sociale)
    return {
        "scoperto_piano_regole": round(max(0.0, -marg[dmin]), 2),
        "data_scoperto_massimo": str(dmin) if marg[dmin] < 0 else None,
        "primo_giorno_oltre_fido_piano_regole": str(neg[0]) if neg else None,
        "tutelati_a_scadenza_documento_n": sum(1 for p in ris.partite if p.data_regole and p.regola in
                                               ("tutela_scad_documento", "tagliata", "tagliata_non_collocabile")
                                               and p.data_regole != p.scad_corrente),
        "tagli_n": len(t), "tagli_importo": round(sum(p.importo for p in t), 2),
        "tagli_ricollocati_n": sum(1 for p in t if p.regola == "tagliata"),
        "tagli_non_collocabili_n": sum(1 for p in t if p.regola == "tagliata_non_collocabile"),
        "tagli_non_collocabili_importo": round(sum(p.importo for p in t if p.regola == "tagliata_non_collocabile"), 2),
        "per_rilevanza": {k: {"n": v["n"], "importo": round(v["importo"], 2), "n_fornitori": len(v["fornitori"])}
                          for k, v in sorted(per_ril.items())},
        "fornitori_strategici_tagliati": sorted({p.ragione_sociale for p in t if p.rilevanza == 1}),
        "accordi_tagliati_n": sum(1 for p in t if (p.origine_classe or "").startswith("accordo:")),
        "dopo_tagli": verifica_accordi(ris),
    }


def _foglio(wb, titolo, intestazioni, righe, larghezze=None, formati=None):
    ws = wb.create_sheet(titolo)
    for c, h in enumerate(intestazioni, 1):
        cell = ws.cell(1, c, h)
        cell.font, cell.fill = HDR, HDR_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for r, riga in enumerate(righe, 2):
        for c, v in enumerate(riga, 1):
            cell = ws.cell(r, c, v)
            cell.font = FONT
            if formati and c - 1 < len(formati) and formati[c - 1]:
                cell.number_format = formati[c - 1]
    for c in range(1, len(intestazioni) + 1):
        ws.column_dimensions[get_column_letter(c)].width = (larghezze or [14] * len(intestazioni))[c - 1]
    ws.freeze_panes = "A2"
    if righe:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(intestazioni))}{len(righe) + 1}"
    ws.row_dimensions[1].height = 30
    return ws


def riepilogo_accordi(ris: Risultato, accordi: dict | None) -> list[dict]:
    out = []
    for cod, a in sorted((accordi or {}).items()):
        ps = [p for p in ris.partite if p.cod_fornitore == cod and p.importo > 0]
        cong = [p for p in ps if p.congelata_accordo]
        corr = [p for p in ps if not p.congelata_accordo and p.origine_classe.startswith("accordo:")]
        nell = [p for p in corr if p.nuova_data is not None]
        in_ritardo = [p for p in corr if p.scad_corrente < ris.primo_pagamento and p.nuova_data is not None]
        d_scop = (verifica_accordi(ris) or {}).get("primo_giorno_oltre_fido")
        fino_scop = [p for p in nell if d_scop and p.nuova_data <= date.fromisoformat(d_scop)]
        altro = [p for p in corr if p.origine_classe.startswith("accordo:") and p.regola not in
                 ("protetta_c1", "fuori_orizzonte", "tutela_scad_documento", "anticipata_tutela")]
        out.append({
            "stato": a.stato,
            "cod_fornitore": cod, "fornitore": a.ragione_sociale or (ps[0].ragione_sociale if ps else ""),
            "decorrenza": a.decorrenza, "criterio": a.criterio, "valido_fino": a.valido_fino,
            "scaduto_congelato_n": len(cong), "scaduto_congelato": round(sum(p.importo for p in cong), 2),
            "corrente_orizzonte_n": len(nell), "corrente_orizzonte": round(sum(p.importo for p in nell), 2),
            "corrente_gia_scaduto_n": len(in_ritardo),
            "corrente_gia_scaduto": round(sum(p.importo for p in in_ritardo), 2),
            "corrente_fino_primo_scoperto": round(sum(p.importo for p in fino_scop), 2),
            "corrente_non_protetto_n": len(altro),
            "partite_con_altra_regola": sorted({p.regola for p in altro})})
    return out


def scenario_proposte(ris: Risultato, ris_prop: Risultato, proposte: dict) -> dict:
    """Confronto piano ufficiale / piano con le proposte di accordo accettate."""
    def num(r):
        f = r.fabbisogno
        picco = max(f.values()) if f else 0.0
        primo = next((d for d in r.giorni if f[d] > 0.005), None)
        nc = [p for p in r.partite if p.regola in NON_COLLOCABILI]
        return picco, primo, len(nc), sum(p.importo for p in nc)
    a, b = num(ris), num(ris_prop)
    det = riepilogo_accordi(ris_prop, proposte)
    return {
        "n_proposte": len(proposte),
        "picco": round(b[0], 2), "picco_ufficiale": round(a[0], 2), "differenza_picco": round(b[0] - a[0], 2),
        "primo_giorno_scoperto": str(b[1]) if b[1] else None,
        "primo_giorno_scoperto_ufficiale": str(a[1]) if a[1] else None,
        "non_collocabili_n": b[2], "non_collocabili_importo": round(b[3], 2),
        "scaduto_che_si_congelerebbe": round(sum(x["scaduto_congelato"] for x in det), 2),
        "corrente_che_si_proteggerebbe": round(sum(x["corrente_orizzonte"] for x in det), 2),
        "dettaglio": [{k: (str(v) if isinstance(v, date) else v) for k, v in x.items()} for x in det],
    }


def scrivi_excel(path: Path, ris: Risultato, controlli: list[Esito], regole: dict, fonti: dict,
                 meta: dict, data_run: date, prev_saldo_cache: dict[date, float], accordi: dict | None = None,
                 proposte: dict | None = None, ris_prop: Risultato | None = None, scen: dict | None = None) -> None:
    wb = Workbook()
    wb.remove(wb.active)
    partite = ris.partite

    # 1. Import_ERP — tracciato esatto, solo partite variate e importabili
    imp_cfg = fonti["output"]["import_erp"]["colonne"]
    def _numdoc(p):
        n = (p.numero_doc or "").strip()
        return int(n) if n.isdigit() and not n.startswith("0") else (n or None)
    sorgente = {"num_protocollo": lambda p: p.num_protocollo, "numero_doc": _numdoc, "dat_doc": lambda p: p.data_doc,
                "dat_scadenza": lambda p: p.scad_corrente, "nuova_data": lambda p: p.nuova_data,
                "id_scadenza": lambda p: int(p.id_scadenza) if (p.id_scadenza or "").isdigit() else p.id_scadenza, "nota_nuova": lambda p: nota_proposta(p, regole, data_run),
                None: lambda p: None}

    def valore(c, p):
        if "costante" in c:
            return c["costante"]
        v = sorgente[c.get("da")](p)
        if v in (None, "", 0) and c.get("ripiego"):     # es. documenti senza protocollo IVA
            v = sorgente[c["ripiego"]](p)
        return v
    tol = int((regole.get("allocazione") or {}).get("tolleranza_variazione_giorni") or 0)
    # note di credito comprese: seguono il pagamento del fornitore e vanno spostate anche nel gestionale
    da_importare = [p for p in partite if p.variata and p.importabile and abs(p.importo) >= 0.005
                    and abs((p.nuova_data - p.scad_corrente).days) > tol
                    and not nota_troppo_lunga(p, regole, data_run)]
    da_importare.sort(key=lambda p: (p.nuova_data, p.ragione_sociale))
    formati = [("@" if c.get("testo") else (c["formato"] if "formato" in c else None)) for c in imp_cfg]
    _foglio(wb, "Import_ERP", [c["nome"] for c in imp_cfg],
            [[valore(c, p) for c in imp_cfg] for p in da_importare],
            [max(10, len(c["nome"]) + 4) if c.get("da") != "nota_nuova" else 28 for c in imp_cfg], formati)

    # 2. Proposta_dettaglio
    righe = []
    for p in sorted(partite, key=lambda p: (not p.variata, p.nuova_data or date.max, p.ragione_sociale)):
        righe.append([p.ragione_sociale, p.cod_fornitore, p.prg_documento, p.num_protocollo, p.numero_doc, p.data_doc,
                      p.conto, p.modalita, p.classe, p.origine_classe, p.importo, p.scad_originale, p.scad_corrente,
                      p.nuova_data, p.giorni_slittamento, "S" if p.variata else "N", p.regola, p.motivo,
                      nota_proposta(p, regole, data_run), azione_operativa(p, regole, data_run),
                      "S" if p.importabile else "N", p.fonte, p.n_righe, "S" if p.sospesa else "N", p.note, p.id_scadenza])
    ws = _foglio(wb, "Proposta_dettaglio",
                 ["Fornitore", "Cod.", "prg_documento", "N. protocollo doc", "N. doc", "Data doc", "Conto", "Mod.",
                  "Classe", "Origine classe", "Importo", "Scad. originale", "Scad. attuale", "Nuova data",
                  "Gg slittamento", "Variata", "Regola", "Motivazione", "Nota da importare (sostituisce)", "Azione operativa",
                  "Importabile", "Fonte", "N. righe", "Sospesa", "Note attuali", "Protocollo scadenza"],
                 righe,
                 [32, 8, 14, 10, 12, 11, 22, 6, 7, 22, 13, 11, 11, 11, 8, 7, 20, 70, 30, 40, 9, 11, 7, 7, 40, 12],
                 [None, None, None, None, None, DATA, None, None, None, None, EUR, DATA, DATA, DATA])
    ws.freeze_panes = "B2"

    # 3. Saldo_giornaliero + grafico
    soglia = float((regole.get("allerte") or {}).get("margine_minimo") or 0)
    righe = []
    for d in ris.giorni:
        m = ris.saldo_post[d] - ris.limite
        righe.append([d, ["lun", "mar", "mer", "gio", "ven", "sab", "dom"][d.weekday()],
                      prev_saldo_cache.get(d), ris.saldo_ante[d], ris.saldo_post[d], ris.limite,
                      m, ris.capacita.get(d), ris.fabbisogno[d],
                      "ROSSO" if m < -0.005 else ("GIALLO" if m < soglia else "")]
                     + ([ris.saldo_regole.get(d), ris.saldo_regole.get(d, 0) - ris.limite,
                         ris.saldo_dopo_tagli.get(d, 0) - ris.limite] if ris.due_fasi else []))
    ws = _foglio(wb, "Saldo_giornaliero",
                 ["Data", "Giorno", "Saldo Report (cache)", "Saldo ante (ricalcolato)", "Saldo post proposta",
                  "Limite (−fido + cuscinetto)", "Margine post", "Capacità del giorno", "Fabbisogno",
                  f"Allerta (margine < {soglia:,.0f} €)".replace(",", ".")]
                 + (["Saldo piano da regole (protetti, prima dei tagli)", "Margine piano da regole",
                     "Margine dopo i tagli (classe 0 e accordi onorati, prima delle classi 2-5)"] if ris.due_fasi else []),
                 righe, [11, 7, 16, 16, 16, 16, 16, 16, 14, 16, 18, 16, 20],
                 [DATA, None, EUR, EUR, EUR, EUR, EUR, EUR, EUR, None, EUR, EUR, EUR])
    n = len(righe) + 1
    ch = LineChart()
    ch.title, ch.height, ch.width = "Saldo proiettato: prima e dopo la proposta", 9, 22
    ch.y_axis.title, ch.y_axis.number_format = "€", "#,##0"
    for col in (4, 5, 6):
        ch.add_data(Reference(ws, min_col=col, min_row=1, max_row=n), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=2, max_row=n))
    ch.x_axis.number_format = "dd/mm"
    if ris.due_fasi:
        ch.add_data(Reference(ws, min_col=11, min_row=1, max_row=n), titles_from_data=True)
    ws.add_chart(ch, "N2" if ris.due_fasi else "K2")

    # 4. Fabbisogno
    s = sintesi(ris, controlli, meta, accordi, regole)
    f = s["fabbisogno"]
    al = s["allerta"]
    ws = wb.create_sheet("Fabbisogno")
    voci = [("Picco di fabbisogno nell'orizzonte", f["picco"], EUR),
            ("Data del picco", _dt(f["data_picco"]), DATA),
            ("Primo giorno scoperto", _dt(f["primo_giorno_scoperto"]), DATA),
            ("di cui da uscite fisse e classe 0", f["quota_classe_0"], EUR),
            ("di cui da fornitori in elenco (classe 1)", f["quota_classe_1"], EUR),
            ("di cui da altri pagamenti (forzati e parcheggiati)", f["quota_altri"], EUR),
            ("Partite non collocabili (n.)", f["non_collocabili_n"], "0"),
            ("Partite non collocabili (importo)", f["non_collocabili_importo"], EUR),
            ("Orizzonte", f"{ris.primo_pagamento:%d/%m/%Y} – {ris.ultimo_pagamento:%d/%m/%Y}", None),
            ("Fido considerato", -ris.limite if ris.limite < 0 else 0, EUR),
            ("Allerta", f"{al['semaforo']} {al['livello']}", None),
            ("Soglia di margine per l'allerta", al["soglia_margine"], EUR),
            ("Margine minimo nell'orizzonte", al["margine_minimo"], EUR),
            ("Data del margine minimo", _dt(al["data_margine_minimo"]), DATA),
            ("Primo giorno sotto soglia", _dt(al["primo_giorno_sotto_soglia"]), DATA),
            ("Giorni sotto soglia (n.)", al["giorni_sotto_soglia"], "0")]
    pt = s.get("piano_taglio")
    if pt:
        voci += [("— Piano in due fasi —", None, None),
                 ("Scoperto massimo del piano da regole (oltre il fido)", pt["scoperto_piano_regole"], EUR),
                 ("Data dello scoperto massimo", _dt(pt["data_scoperto_massimo"]), DATA),
                 ("Primo giorno oltre il fido nel piano da regole", _dt(pt["primo_giorno_oltre_fido_piano_regole"]), DATA),
                 ("Pagamenti tagliati (n.)", pt["tagli_n"], "0"),
                 ("Pagamenti tagliati (importo)", pt["tagli_importo"], EUR),
                 ("di cui ricollocati nell'orizzonte (n.)", pt["tagli_ricollocati_n"], "0"),
                 ("di cui non collocabili (importo)", pt["tagli_non_collocabili_importo"], EUR),
                 ("Fornitori strategici (rilevanza 1) tagliati", ", ".join(pt["fornitori_strategici_tagliati"]) or "nessuno", None),
                 ("— Verifica degli accordi (dopo i tagli, prima delle classi 2-5) —", None, None),
                 ("Primo giorno oltre il fido", _dt(pt["dopo_tagli"].get("primo_giorno_oltre_fido")) or "mai", DATA),
                 ("Scoperto massimo", pt["dopo_tagli"].get("scoperto_massimo"), EUR),
                 ("Data dello scoperto massimo", _dt(pt["dopo_tagli"].get("data_scoperto_massimo")), DATA)]
    ws.cell(1, 1, "Fabbisogno di cassa").font = Font(name="Arial", size=12, bold=True)
    for i, (k, v, fmt) in enumerate(voci, 3):
        ws.cell(i, 1, k).font = FONT
        c = ws.cell(i, 2, v)
        c.font = BOLD
        if fmt:
            c.number_format = fmt
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 50, 22
    r0 = len(voci) + 5
    ws.cell(r0, 1, "Non collocabili per fornitore").font = BOLD
    per_forn = defaultdict(float)
    for p in partite:
        if p.regola in NON_COLLOCABILI:
            per_forn[(p.ragione_sociale, p.classe)] += p.importo
    if scen:  # scenario proposte accettate, accanto al piano ufficiale
        ws.cell(1, 4, "Se le proposte di accordo fossero accettate").font = Font(name="Arial", size=12, bold=True)
        sv = [("Proposte di accordo considerate (n.)", scen["n_proposte"], "0"),
              ("Picco di fabbisogno", scen["picco"], EUR),
              ("Differenza rispetto al piano ufficiale", scen["differenza_picco"], EUR),
              ("Primo giorno scoperto", _dt(scen["primo_giorno_scoperto"]), DATA),
              ("Scaduto che verrebbe congelato", scen["scaduto_che_si_congelerebbe"], EUR),
              ("Corrente che verrebbe protetto (pagato alla scadenza)", scen["corrente_che_si_proteggerebbe"], EUR),
              ("Partite non collocabili (n.)", scen["non_collocabili_n"], "0"),
              ("Partite non collocabili (importo)", scen["non_collocabili_importo"], EUR)]
        for i, (k, v, fmt) in enumerate(sv, 3):
            ws.cell(i, 4, k).font = FONT
            c = ws.cell(i, 5, v)
            c.font = BOLD
            c.number_format = fmt
        ws.column_dimensions["D"].width, ws.column_dimensions["E"].width = 50, 22
    for i, ((nome, k), v) in enumerate(sorted(per_forn.items(), key=lambda t: -t[1]), r0 + 1):
        ws.cell(i, 1, f"{nome} (classe {k})").font = FONT
        ws.cell(i, 2, v).number_format = EUR

    # 5. Riepilogo_fornitori
    agg = defaultdict(lambda: {"nome": "", "classe": None, "n": 0, "orizzonte": 0.0, "variato": 0.0,
                               "parcheggiato": 0.0, "eurogg": 0.0, "slitt_max": 0})
    for p in partite:
        if p.nuova_data is None or p.importo <= 0:
            continue
        a = agg[p.cod_fornitore]
        a["nome"], a["classe"] = p.ragione_sociale, p.classe
        a["n"] += 1
        a["orizzonte"] += p.importo
        if p.variata:
            a["variato"] += p.importo
        if p.regola in NON_COLLOCABILI:
            a["parcheggiato"] += p.importo
        g = max(0, (p.nuova_data - p.scad_corrente).days)
        a["eurogg"] += p.importo * g
        a["slitt_max"] = max(a["slitt_max"], p.giorni_slittamento or 0)
    righe = [[a["nome"], cod, a["classe"], a["n"], a["orizzonte"], a["variato"], a["parcheggiato"],
              (a["eurogg"] / a["orizzonte"]) if a["orizzonte"] else 0, a["slitt_max"]]
             for cod, a in sorted(agg.items(), key=lambda t: -t[1]["variato"])]
    _foglio(wb, "Riepilogo_fornitori",
            ["Fornitore", "Cod.", "Classe", "N. partite", "Importo nell'orizzonte", "Importo con data variata",
             "di cui parcheggiato", "Ritardo medio vs data attuale (gg, ponderato)", "Slittamento max vs originale (gg)"],
            righe, [34, 8, 7, 9, 16, 16, 16, 16, 14], [None, None, None, None, EUR, EUR, EUR, "0.0", "0"])

    # 6. Azioni_operative
    righe = [[p.ragione_sociale, p.num_protocollo, p.numero_doc, p.modalita, p.importo, p.scad_corrente,
              p.nuova_data, azione_operativa(p, regole, data_run)]
             for p in sorted(partite, key=lambda p: (p.scad_corrente, p.ragione_sociale))
             if azione_operativa(p, regole, data_run)]
    _foglio(wb, "Azioni_operative",
            ["Fornitore", "Protocollo", "N. doc", "Mod.", "Importo", "Scad. attuale", "Nuova data", "Azione"],
            righe, [34, 10, 12, 6, 13, 11, 11, 70], [None, None, None, None, EUR, DATA, DATA])

    # 7. Parziali_suggeriti
    _foglio(wb, "Parziali_suggeriti",
            ["Data", "Fornitore", "Cod.", "Classe", "Importo parziale possibile", "Nota (NON importato)"],
            [[x.data, x.ragione_sociale, x.cod_fornitore, x.classe, x.importo, x.nota] for x in ris.parziali],
            [11, 34, 8, 7, 18, 60], [DATA, None, None, None, EUR])

    # 7-ter. Tagli (piano in due fasi)
    if ris.due_fasi:
        tg = [p for p in partite if p.regola in ("tagliata", "tagliata_non_collocabile")]
        tg.sort(key=lambda p: (-(p.rilevanza or 0), p.ragione_sociale, p.data_regole or date.max))
        _foglio(wb, "Tagli",
                ["Rilevanza", "Fornitore", "Cod.", "Classe", "Origine classe", "Protocollo", "N. doc", "Importo",
                 "Scad. originale", "Scad. attuale", "Data da regole", "Nuova data", "Esito", "Motivazione"],
                [[p.rilevanza, p.ragione_sociale, p.cod_fornitore, p.classe, p.origine_classe, p.id_scadenza,
                  p.numero_doc, p.importo, p.scad_originale, p.scad_corrente, p.data_regole, p.nuova_data,
                  "ricollocata" if p.regola == "tagliata" else "non collocabile", p.motivo] for p in tg],
                [9, 32, 8, 7, 24, 11, 12, 13, 11, 11, 11, 11, 14, 90],
                [None, None, None, None, None, None, None, EUR, DATA, DATA, DATA, DATA])

    # 7-bis. Accordi
    if accordi or proposte:
        acc = riepilogo_accordi(ris, accordi) + (riepilogo_accordi(ris_prop, proposte) if ris_prop else [])
        _foglio(wb, "Accordi",
                ["Stato", "Fornitore", "Cod.", "Decorrenza", "Criterio", "Valido fino", "Scaduto congelato (n)",
                 "Scaduto congelato €", "Corrente nell'orizzonte (n)", "Corrente nell'orizzonte €",
                 "Corrente già in ritardo (n)", "Corrente già in ritardo €",
                 "Corrente da pagare fino al primo scoperto €", "Corrente con altra regola (n)", "Regole diverse"],
                [[("Proposta" if x["stato"] == "proposta" else "Attivo"),
                  x["fornitore"], x["cod_fornitore"], x["decorrenza"], x["criterio"], x["valido_fino"],
                  x["scaduto_congelato_n"], x["scaduto_congelato"], x["corrente_orizzonte_n"],
                  x["corrente_orizzonte"], x["corrente_gia_scaduto_n"], x["corrente_gia_scaduto"],
                  x["corrente_fino_primo_scoperto"],
                  x["corrente_non_protetto_n"], ", ".join(x["partite_con_altra_regola"])] for x in acc],
                [22, 32, 8, 11, 16, 11, 10, 15, 10, 15, 10, 15, 18, 10, 30],
                [None, None, None, DATA, None, DATA, "0", EUR, "0", EUR, "0", EUR, EUR, "0"])

    # 8. Controlli_e_parametri
    ws = _foglio(wb, "Controlli_e_parametri", ["Voce", "Valore / esito", "Dettaglio"],
                 [[c.id, c.esito.upper(), c.messaggio] for c in controlli]
                 + [["", "", ""]]
                 + [[k, str(v), ""] for k, v in meta.items()],
                 [26, 40, 110])
    wb.move_sheet("Controlli_e_parametri", offset=-(len(wb.sheetnames) - 1))
    wb.move_sheet("Import_ERP", offset=-(len(wb.sheetnames) - 1))
    wb.active = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _dt(s):
    return date.fromisoformat(s) if s else None


def allerta(ris: Risultato, regole: dict | None, quota_c0: float, accordi_riep: list[dict]) -> dict:
    """Semaforo sul margine = saldo + fido - cuscinetto. Parametro: allerte.margine_minimo."""
    soglia = float(((regole or {}).get("allerte") or {}).get("margine_minimo") or 0)
    marg = {d: ris.saldo_post[d] - ris.limite for d in ris.giorni}
    dmin = min(marg, key=marg.get)
    sotto = [d for d in ris.giorni if marg[d] < soglia]
    neg = [d for d in ris.giorni if marg[d] < -0.005]
    accordi_ritardo = [x["fornitore"] for x in accordi_riep if x.get("corrente_gia_scaduto_n")]
    va = verifica_accordi(ris)
    strategici = sorted({p.ragione_sociale for p in ris.partite
                         if p.regola in ("tagliata", "tagliata_non_collocabile") and p.rilevanza == 1})
    if quota_c0 > 0.5 or accordi_ritardo:
        livello = "rosso_prioritario"
    elif neg or strategici:
        livello = "rosso"
    elif sotto:
        livello = "giallo"
    else:
        livello = "verde"
    return {"livello": livello, "semaforo": {"verde": "🟢", "giallo": "🟡"}.get(livello, "🔴"),
            "soglia_margine": soglia, "margine_minimo": round(marg[dmin], 2), "data_margine_minimo": str(dmin),
            "primo_giorno_sotto_soglia": str(sotto[0]) if sotto else None, "giorni_sotto_soglia": len(sotto),
            "primo_giorno_oltre_fido": str(neg[0]) if neg else None,
            "stipendi_tributi_scoperti": quota_c0 > 0.5, "accordi_con_corrente_in_ritardo": accordi_ritardo,
            "fornitori_strategici_tagliati": strategici,
            "accordi_non_sostenibili_dal": va.get("primo_giorno_oltre_fido"),
            "scoperto_dopo_tagli": va.get("scoperto_massimo")}


def sintesi(ris: Risultato, controlli: list[Esito], meta: dict, accordi: dict | None = None,
            regole: dict | None = None) -> dict:
    partite = ris.partite
    fab = ris.fabbisogno
    picco = max(fab.values()) if fab else 0.0
    data_picco = max(fab, key=fab.get) if picco > 0 else None
    primo_scop = next((d for d in ris.giorni if fab[d] > 0.005), None)
    nc = [p for p in partite if p.regola in NON_COLLOCABILI]
    var = [p for p in partite if p.variata and p.importo > 0]
    saldo_min_d = min(ris.saldo_post, key=ris.saldo_post.get)
    per_classe = defaultdict(lambda: {"n": 0, "importo": 0.0})
    for p in var:
        per_classe[str(p.classe)]["n"] += 1
        per_classe[str(p.classe)]["importo"] = round(per_classe[str(p.classe)]["importo"] + p.importo, 2)
    regole_cnt = defaultdict(int)
    for p in partite:
        regole_cnt[p.regola] += 1
    return {
        **{k: (str(v) if isinstance(v, (date, datetime)) else v) for k, v in meta.items()},
        "controlli": [{"id": c.id, "esito": c.esito, "messaggio": c.messaggio} for c in controlli],
        "orizzonte": {"primo_pagamento": str(ris.primo_pagamento), "ultimo_pagamento": str(ris.ultimo_pagamento)},
        "saldo_minimo_post": {"importo": round(ris.saldo_post[saldo_min_d], 2), "data": str(saldo_min_d),
                              "fido_incluso": round(-ris.limite, 2)},
        "fabbisogno": {
            "picco": round(picco, 2), "data_picco": str(data_picco) if data_picco else None,
            "primo_giorno_scoperto": str(primo_scop) if primo_scop else None,
            "quota_classe_0": round(min(ris.quota_classe_0, picco), 2),
            "quota_classe_1": round(min(ris.quota_classe_1, max(0.0, picco - ris.quota_classe_0)), 2),
            "quota_altri": round(max(0.0, picco - ris.quota_classe_0 - ris.quota_classe_1), 2),
            "non_collocabili_n": len(nc), "non_collocabili_importo": round(sum(p.importo for p in nc), 2)},
        "spostamenti": {"n_righe": len(var), "importo_totale": round(sum(p.importo for p in var), 2),
                        "n_fornitori": len({p.cod_fornitore for p in var}), "per_classe": dict(per_classe),
                        "anticipati": sum(1 for p in var if p.nuova_data < p.scad_corrente)},
        "forzati": [{"fornitore": p.ragione_sociale, "importo": p.importo, "data": str(p.nuova_data),
                     "regola": p.regola} for p in partite if p.regola.startswith("forzata")],
        "azioni_operative": {"riba": sum(1 for p in var if p.modalita == "RB"),
                             "rid": sum(1 for p in var if p.modalita == "RID"),
                             "manuali": sum(1 for p in var if not p.importabile)},
        "conteggio_regole": dict(regole_cnt),
        "parziali_suggeriti": {"n": len(ris.parziali), "importo": round(sum(x.importo for x in ris.parziali), 2)},
        "accordi": [{k: (str(v) if isinstance(v, date) else v) for k, v in x.items()}
                    for x in riepilogo_accordi(ris, accordi)],
        "allerta": allerta(ris, regole, ris.quota_classe_0, riepilogo_accordi(ris, accordi)),
        "piano_taglio": piano_taglio(ris),
    }


def scrivi_json(path: Path, dati: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dati, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
