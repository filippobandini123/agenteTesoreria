"""Cuore del motore: collocazione dei pagamenti nel rispetto del vincolo di cassa.

Logica (vedi regole.yaml §6):
  1. si parte dalla previsione al netto dei pagamenti fornitori (flussi fissi);
  2. si collocano i protetti (classi non spostabili) alla loro data;
  3. giorno lavorativo per giorno lavorativo:
       - pagamenti forzati (slittamento massimo, scaduto massimo);
       - capacità = min_{s >= t} (saldo(s) - limite)   [look-ahead];
       - coda servita per classe crescente: se la classe sta tutta nella capacità
         si paga tutta, altrimenti pro quota per fornitore a rate intere
         (prima passata FIFO entro la quota, seconda passata sugli avanzi);
  4. le partite rimaste in coda sono parcheggiate a fine orizzonte;
  (piano in due fasi, regole.yaml `piano.modalita: due_fasi`: al punto 2 i tutelati vanno alla
   scadenza da documento senza guardare la cassa = piano da regole; poi il piano di taglio toglie,
   dal fornitore meno rilevante, i pagamenti di classe 1 che fanno sforare il fido e li ricolloca
   al primo giorno utile, prima delle classi spostabili);
  5. le note di credito seguono il primo pagamento del fornitore.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .calendario import Calendario
from .modello import Fornitore, ParzialeSuggerito, Partita, Previsione, Risultato

EPS = 0.005  # mezzo centesimo


def _it(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _eur(x: float) -> str:
    s = f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


class _Cassa:
    """Saldo giornaliero mutabile sull'orizzonte."""

    def __init__(self, giorni: list[date], saldo_iniziale: float, flussi: dict[date, float]):
        self.giorni = giorni
        self.idx = {d: i for i, d in enumerate(giorni)}
        s, self.saldo = saldo_iniziale, []
        for d in giorni:
            s += flussi.get(d, 0.0)
            self.saldo.append(s)

    def esborso(self, importo: float, d: date) -> None:
        for j in range(self.idx[d], len(self.giorni)):
            self.saldo[j] -= importo

    def capacita(self, d: date, limite: float, lookahead: int | None = None) -> float:
        i = self.idx[d]
        j = len(self.giorni) if lookahead is None else min(len(self.giorni), i + lookahead + 1)
        return min(self.saldo[i:j]) - limite

    def copia(self) -> list[float]:
        return list(self.saldo)


def _fabbisogno_picco(saldo: list[float], limite: float) -> float:
    return max([0.0] + [limite - s for s in saldo])


def pianifica(partite: list[Partita], prev: Previsione, anagrafica: dict[int, Fornitore],
              regole: dict, oggi: date, cal: Calendario) -> Risultato:
    vc = regole["vincolo_cassa"]
    limite = -float(vc["fido_anticipo_pos"]) + float(vc.get("cuscinetto_minimo") or 0)
    n_giorni = int(regole["orizzonte"]["giorni"])
    alloc = regole["allocazione"]
    spec = regole.get("righe_speciali") or {}
    perim = regole.get("perimetro") or {}
    classi_cfg = regole["classi"]
    spostabile = {int(k): bool(v.get("spostabile")) for k, v in classi_cfg.items()}
    avanzo_succ = bool(alloc.get("avanzo_a_classe_successiva", True))
    lookahead = alloc.get("lookahead_giorni")
    lookahead = None if lookahead in (None, "orizzonte") else int(lookahead)
    frazionamento = bool((alloc.get("frazionamento") or {}).get("ammesso", False))
    if frazionamento:
        raise NotImplementedError("Frazionamento non implementato: il tracciato di import non gestisce importi parziali.")

    inizio = prev.data_consuntivo + timedelta(days=1)
    primo = cal.prossimo(max(inizio, oggi))
    fine = primo + timedelta(days=n_giorni - 1)
    ultimo = cal.precedente(fine)
    giorni = [inizio + timedelta(days=i) for i in range((fine - inizio).days + 1)]
    nel = lambda d: inizio <= d <= fine  # noqa: E731

    # date congelate: si toglie la compensazione dai flussi e le partite restano ferme
    congelate = {}
    flussi = dict(prev.flussi_fissi)
    for x in regole.get("date_congelate") or []:
        dc = x["data"] if isinstance(x["data"], date) else date.fromisoformat(str(x["data"]))
        comp = prev.fisse_per_riga.get(str(x.get("riga_compensazione", "")).strip(), {}).get(dc, 0.0)
        flussi[dc] = flussi.get(dc, 0.0) - comp
        congelate[dc] = comp
    cassa = _Cassa(giorni, prev.saldo_consuntivo, flussi)

    # --- saldo "ante": come oggi, ogni partita alla sua scadenza corrente
    ante = _Cassa(giorni, prev.saldo_consuntivo, prev.flussi_fissi)
    for p in partite:
        if nel(p.scad_corrente):
            ante.esborso(p.importo, p.scad_corrente)

    # --- suddivisione del perimetro
    protette, spostabili, note_credito = [], [], []
    for p in partite:
        p.nuova_data, p.regola, p.motivo, p.data_ingresso = None, "", "", None
        if p.congelata_accordo:
            p.regola = "congelata_accordo"
            if p.data_fissa is None:
                p.motivo = (f"Scaduto congelato da accordo col fornitore ({p.origine_classe.split(':', 1)[1]}): "
                            "escluso dal piano, data invariata, nessun effetto di cassa.")
            else:
                d = max(p.data_fissa, primo)
                p.nuova_data = d
                if nel(d):
                    cassa.esborso(p.importo, d)
                p.motivo = (f"Scaduto congelato da accordo col fornitore ({p.origine_classe.split(':', 1)[1]}): "
                            f"pagamento ipotizzato al {_it(d)} (data in input)"
                            + (", pagamento fisso dentro l'orizzonte." if nel(d) else ", fuori orizzonte: nessun effetto sulla cassa dei prossimi giorni."))
        elif p.scad_corrente in congelate:
            p.regola = "congelata"
            p.motivo = (f"Scadenza {_it(p.scad_corrente)} congelata: compensata nella previsione "
                        f"({_eur(congelate[p.scad_corrente])}), esclusa dal piano, data invariata.")
        elif p.sospesa and (spec.get("sospese") or {}).get("azione", "escludi_e_segnala") == "escludi_e_segnala":
            p.regola, p.motivo = "sospesa", "Partita sospesa: esclusa dal piano, data invariata."
        elif abs(p.importo) < EPS:
            p.regola, p.motivo = "importo_zero", "Residuo nullo."
        elif p.importo < 0 and (spec.get("note_credito") or {}).get("azione") == "compensa_fornitore":
            note_credito.append(p)
        elif not spostabile.get(p.classe, True) or p.importo < 0:
            protette.append(p)
        else:
            spostabili.append(p)

    # --- 1. protetti, classe per classe (per misurare il fabbisogno dovuto a ciascuna)
    #     la "classe 0" comprende anche le uscite fisse da budget (stipendi, F24, ...)
    picco_per_classe = {0: _fabbisogno_picco(cassa.saldo, limite)}
    tutela_cfg = regole.get("tutela") or {}
    tutela_origini = set(tutela_cfg.get("origini") or [])

    def in_tutela(p: Partita) -> bool:
        if not tutela_cfg.get("anticipo_se_cassa_consente") or p.importo <= 0:
            return False
        o = p.origine_classe or ""
        chiave = ("accordo" if o.startswith("accordo:") else
                  "elenco_acquisto" if o == "anagrafica:elenco_acquisto" else
                  f"anagrafica_classe{p.classe}" if o == "anagrafica:classe" else o)
        return chiave in tutela_origini and p.scad_originale < p.scad_corrente

    due_fasi = (regole.get("piano") or {}).get("modalita", "cassa") == "due_fasi"
    tutela = [p for p in protette if in_tutela(p)]
    id_tutela = {p.id for p in tutela}
    for k in sorted({p.classe for p in protette}):
        for p in [q for q in protette if q.classe == k]:
            # fase 1 del piano in due fasi: il tutelato si paga alla scadenza da documento, senza guardare la cassa
            rif = p.scad_originale if (due_fasi and p.id in id_tutela) else p.scad_corrente
            if rif > fine:
                p.regola = "fuori_orizzonte"
                p.motivo = f"Classe {k} non spostabile, scadenza oltre l'orizzonte ({_it(fine)})."
                continue
            d = rif if rif >= primo else primo
            cassa.esborso(p.importo, d)
            p.nuova_data = p.data_regole = d
            if rif != p.scad_corrente:
                p.regola = "tutela_scad_documento"
                p.motivo = (f"Classe {k}, fornitore da tutelare: pagata alla scadenza da documento "
                            + (f"{_it(d)}" if d == rif else f"{_it(rif)} (già passata), al primo giorno utile {_it(d)}")
                            + f" invece che al {_it(p.scad_corrente)}.")
                continue
            p.regola = f"protetta_c{k}"
            p.motivo = (f"Classe {k} ({classi_cfg[k]['nome'] if k in classi_cfg else ''}): non spostabile, "
                        + (f"pagata alla scadenza {_it(d)}." if d == p.scad_corrente
                           else f"scaduta il {_it(p.scad_corrente)}, collocata al primo giorno utile {_it(d)}."))
        picco_per_classe[k] = _fabbisogno_picco(cassa.saldo, limite)
    q0 = picco_per_classe[0]
    q1 = max(0.0, picco_per_classe.get(1, q0) - q0)
    saldo_regole = cassa.copia()
    nc_cfg = regole.get("non_collocabili") or {}
    parcheggio = nc_cfg.get("data_parcheggio", "fine_orizzonte")
    if parcheggio == "fine_orizzonte":
        d_park = ultimo
    elif parcheggio == "fine_orizzonte_piu_1":
        d_park = cal.prossimo(fine + timedelta(days=1))
    else:
        d_park = parcheggio if isinstance(parcheggio, date) else date.fromisoformat(str(parcheggio))
    giorni_pag = [d for d in giorni if primo <= d <= ultimo and cal.lavorativo(d)]

    # --- 1-bis. piano di taglio (solo in due fasi): dove il piano da regole sfora il fido si
    #     tolgono i pagamenti tagliabili, dal fornitore meno rilevante, finché il margine torna >= 0;
    #     i tagliati si ricollocano al primo giorno in cui la cassa li regge (prima delle classi 2-5)
    if due_fasi:
        tagl_cfg = regole.get("taglio") or {}
        classi_tagl = {int(x) for x in (tagl_cfg.get("classi") or [1])}
        ril_def = int(tagl_cfg.get("rilevanza_default", 2))
        for p in protette:
            f = anagrafica.get(p.cod_fornitore)
            p.rilevanza = (f.rilevanza if f and f.rilevanza else ril_def)
        accordi_tagliabili = bool(tagl_cfg.get("accordi", False))
        tagliabili = [p for p in protette if p.classe in classi_tagl and p.importo > 0
                      and (accordi_tagliabili or not (p.origine_classe or "").startswith("accordo:"))
                      and p.nuova_data is not None and nel(p.nuova_data)]
        ordine = lambda p: (-p.rilevanza, -p.classe, -p.scad_originale.toordinal(),  # noqa: E731
                            -(p.data_doc or date.min).toordinal(), p.id)
        tagliate: dict[str, tuple[date, float]] = {}
        for i, d0 in enumerate(giorni):
            if d0 < primo:
                continue
            while cassa.saldo[i] - limite < -EPS:
                cand = sorted((p for p in tagliabili if p.id not in tagliate and p.nuova_data <= d0), key=ordine)
                if not cand:
                    break
                p = cand[0]
                tagliate[p.id] = (d0, limite - cassa.saldo[i])
                cassa.esborso(-p.importo, p.nuova_data)
        # ricollocazione: prima i più rilevanti, poi FIFO sulla scadenza da documento
        for p in sorted((q for q in tagliabili if q.id in tagliate),
                        key=lambda q: (q.rilevanza, q.scad_originale, q.data_doc or date.min, q.id)):
            d_taglio, scop = tagliate[p.id]
            nuova = None
            for d in giorni_pag:
                if d <= d_taglio:
                    continue
                fine_fin = fine if lookahead is None else min(fine, d + timedelta(days=lookahead))
                if min(cassa.saldo[cassa.idx[d]:cassa.idx[fine_fin] + 1]) - limite >= p.importo - EPS:
                    nuova = d
                    break
            premessa = (f"Tagliata dal piano da regole (data da regole {_it(p.data_regole)}, rilevanza {p.rilevanza}): "
                        f"il {_it(d_taglio)} il piano sforava il fido di {_eur(scop)}. ")
            if nuova is not None:
                cassa.esborso(p.importo, nuova)
                p.nuova_data, p.regola = nuova, "tagliata"
                p.motivo = premessa + f"Ricollocata al {_it(nuova)}, primo giorno in cui la cassa la regge."
            else:
                p.nuova_data, p.regola = d_park, "tagliata_non_collocabile"
                if nel(d_park):
                    cassa.esborso(p.importo, d_park)
                p.motivo = premessa + (f"Nessun giorno dell'orizzonte la regge: parcheggiata al {_it(d_park)} "
                                       "(genera fabbisogno, con priorità sulle classi 2-5).")
        tutela = []   # in due fasi l'anticipo è già nel piano da regole
    # cassa dopo i tagli e prima delle classi 2-5: se qui il fido è sforato, nemmeno tagliando tutto il
    # tagliabile si onorano classe 0 e accordi → è la verifica di sostenibilità degli accordi
    saldo_dopo_tagli = cassa.copia()

    # --- 2. data di ingresso in coda delle spostabili
    da_originale = perim.get("data_desiderata", "scadenza_originale") == "scadenza_originale"
    anticipa = bool(alloc.get("anticipa_riscadenziati_se_cassa_consente", True))
    coda_tot = []
    desid: dict[str, date] = {}
    for p in spostabili:
        desiderata = p.scad_originale if da_originale else p.scad_corrente
        if not anticipa:
            desiderata = max(desiderata, p.scad_corrente)
        if desiderata > fine and p.scad_corrente <= fine:
            desiderata = p.scad_corrente
        if desiderata > fine:
            p.regola = "fuori_orizzonte"
            p.motivo = f"Scadenza oltre l'orizzonte ({_it(fine)}): non valutata."
            continue
        p.data_ingresso = max(desiderata, primo)
        desid[p.id] = desiderata
        coda_tot.append(p)

    def chiave_fifo(p: Partita):
        return (p.scad_originale, p.data_doc or date.min, p.id)

    pagate: set[str] = set()
    capacita_giorno: dict[date, float] = {}
    parziali: list[ParzialeSuggerito] = []

    def paga(p: Partita, d: date, regola: str, dettaglio: str) -> None:
        cassa.esborso(p.importo, d)
        p.nuova_data = d
        pagate.add(p.id)
        if regola:
            p.regola = regola
        elif d == p.data_ingresso:
            p.regola = "pagata_alla_data" if desid[p.id] >= primo else "scaduta_pagata"
        else:
            p.regola = "slittata"
        rit = (d - p.data_ingresso).days
        p.motivo = (f"Classe {p.classe}: in coda dal {_it(p.data_ingresso)}, pagata il {_it(d)}"
                    + (f" (+{rit} gg)" if rit else "") + f". {dettaglio}")
        if d < p.scad_corrente:
            p.motivo += f" Anticipata rispetto alla data attuale {_it(p.scad_corrente)}: la cassa lo consente."

    anticipate: set[str] = set()
    for d in giorni_pag:
        # --- tutela compatibile con la cassa: i protetti posticipati tornano verso la scadenza
        #     da documento, per primi, solo se la cassa lo consente (rate intere, FIFO)
        for p in sorted(tutela, key=lambda q: (q.scad_originale, q.data_doc or date.min, q.id)):
            if p.id in anticipate or max(p.scad_originale, primo) > d:
                continue
            vecchia = p.nuova_data if p.nuova_data is not None else p.scad_corrente
            if d >= vecchia:
                continue
            fine_fin = min(vecchia - timedelta(days=1), fine)
            if lookahead is not None:
                fine_fin = min(fine_fin, d + timedelta(days=lookahead))
            i, j = cassa.idx[d], cassa.idx[fine_fin]
            if min(cassa.saldo[i:j + 1]) - limite < p.importo - EPS:
                continue
            if nel(vecchia) and p.nuova_data is not None:
                cassa.esborso(-p.importo, vecchia)
            cassa.esborso(p.importo, d)
            anticipate.add(p.id)
            p.nuova_data = d
            p.regola = "anticipata_tutela"
            p.motivo = (f"Classe {p.classe}, fornitore da tutelare: anticipata dal {_it(vecchia)} al {_it(d)} "
                        f"(scadenza da documento {_it(p.scad_originale)}); la cassa lo consente senza sforare il fido.")
        coda = [p for p in coda_tot if p.id not in pagate and p.data_ingresso <= d]
        if not coda:
            capacita_giorno[d] = max(0.0, cassa.capacita(d, limite, lookahead))
            continue
        prossimo = cal.prossimo(d + timedelta(days=1))

        # --- pagamenti forzati
        for p in sorted(coda, key=chiave_fifo):
            f = anagrafica.get(p.cod_fornitore)
            if f and f.slittamento_max_gg is not None:
                lim = p.scad_originale + timedelta(days=int(f.slittamento_max_gg))
                if prossimo > lim:
                    paga(p, d, "forzata_slitt_max",
                         f"Forzata: slittamento massimo {f.slittamento_max_gg} gg dalla scadenza originale "
                         f"{_it(p.scad_originale)} (limite {_it(lim)}).")
        per_forn = defaultdict(list)
        for p in coda:
            if p.id not in pagate and p.scad_originale < d:
                per_forn[p.cod_fornitore].append(p)
        for cod, items in per_forn.items():
            f = anagrafica.get(cod)
            if not f or f.scaduto_max_tollerato is None:
                continue
            scaduto = sum(p.importo for p in items)
            for p in sorted(items, key=chiave_fifo):
                if scaduto <= float(f.scaduto_max_tollerato) + EPS:
                    break
                paga(p, d, "forzata_scaduto_max",
                     f"Forzata: scaduto del fornitore {_eur(scaduto)} oltre il massimo tollerato "
                     f"{_eur(float(f.scaduto_max_tollerato))}.")
                scaduto -= p.importo

        # --- capacità con look-ahead
        cap = max(0.0, cassa.capacita(d, limite, lookahead))
        capacita_giorno[d] = cap
        residue = [p for p in coda if p.id not in pagate]
        gap_taglio: dict[int, tuple[int, float]] = {}
        for k in sorted({p.classe for p in residue}):
            items = [p for p in residue if p.classe == k]
            tot = sum(p.importo for p in items)
            if tot <= cap + EPS:
                for p in items:
                    paga(p, d, "", f"Capacità sufficiente per tutta la classe {k}.")
                cap -= tot
                continue
            # pro quota a rate intere
            gruppi = defaultdict(list)
            for p in sorted(items, key=chiave_fifo):
                gruppi[p.cod_fornitore].append(p)
            quota = {s: cap * sum(p.importo for p in g) / tot for s, g in gruppi.items()}
            pagato = {s: 0.0 for s in gruppi}
            ptr = {s: 0 for s in gruppi}
            passo = {}
            for s, g in gruppi.items():  # prima passata
                while ptr[s] < len(g) and pagato[s] + g[ptr[s]].importo <= quota[s] + EPS:
                    pagato[s] += g[ptr[s]].importo
                    passo[g[ptr[s]].id] = 1
                    ptr[s] += 1
            r = cap - sum(pagato.values())
            while True:  # seconda passata sugli avanzi
                cand = [s for s, g in gruppi.items() if ptr[s] < len(g) and g[ptr[s]].importo <= r + EPS]
                if not cand:
                    break
                s = max(cand, key=lambda x: (quota[x] - pagato[x], -x))
                p = gruppi[s][ptr[s]]
                pagato[s] += p.importo
                passo[p.id] = 2
                ptr[s] += 1
                r -= p.importo
            # pagamento minimo per fornitore
            for s, g in gruppi.items():
                f = anagrafica.get(s)
                if not f or not f.pagamento_minimo or pagato[s] <= EPS or pagato[s] >= float(f.pagamento_minimo) - EPS:
                    continue
                while ptr[s] < len(g) and pagato[s] < float(f.pagamento_minimo) - EPS and g[ptr[s]].importo <= r + EPS:
                    pagato[s] += g[ptr[s]].importo
                    passo[g[ptr[s]].id] = 3
                    r -= g[ptr[s]].importo
                    ptr[s] += 1
                if pagato[s] < float(f.pagamento_minimo) - EPS:  # non raggiungibile: il fornitore oggi non riceve nulla
                    for p in g[:ptr[s]]:
                        passo.pop(p.id, None)
                    r += pagato[s]
                    pagato[s], ptr[s] = 0.0, 0
            descr = {1: "pro quota (quota del fornitore {q} su capacità {c})",
                     2: "seconda passata sugli avanzi di quota (quota {q} su capacità {c})",
                     3: "integrazione per pagamento minimo del fornitore"}
            for s, g in gruppi.items():
                for p in g:
                    if p.id in passo:
                        testo = descr[passo[p.id]].format(q=_eur(quota[s]), c=_eur(cap))
                        paga(p, d, "", testo[0].upper() + testo[1:] + ".")
            for s, g in gruppi.items():
                if ptr[s] < len(g) and quota[s] - pagato[s] > EPS:
                    gap_taglio[s] = (k, quota[s] - pagato[s])
            cap = r
            if not avanzo_succ:
                break
        if cap > 1.0 and gap_taglio:
            tot_gap = sum(v for _, v in gap_taglio.values())
            nomi = {p.cod_fornitore: p.ragione_sociale for p in residue}
            for s, (k, g) in gap_taglio.items():
                imp = round(cap * g / tot_gap, 2)
                if imp >= 1.0:
                    parziali.append(ParzialeSuggerito(d, s, nomi.get(s, ""), k, imp,
                                                      "Cassa rimasta inutilizzata perché nessuna rata intera ci stava."))

    # --- 3. partite rimaste in coda: parcheggio
    for p in coda_tot:
        if p.id in pagate:
            continue
        if p.scad_corrente > fine:
            p.regola = "non_collocabile_invariata"
            p.motivo = (f"Classe {p.classe}: capacità insufficiente nell'orizzonte; la scadenza attuale "
                        f"{_it(p.scad_corrente)} è già oltre l'orizzonte e resta invariata.")
            continue
        p.nuova_data = d_park
        if nel(d_park):
            cassa.esborso(p.importo, d_park)
        p.regola = "non_collocabile"
        p.motivo = (f"Classe {p.classe}: in coda dal {_it(p.data_ingresso)}, nessun giorno con capacità "
                    f"sufficiente entro il {_it(ultimo)}: parcheggiata al {_it(d_park)} (genera fabbisogno).")

    # --- 4. note di credito
    pagamenti_forn = defaultdict(list)
    for p in partite:
        if p.nuova_data is not None and p.importo > 0:
            pagamenti_forn[p.cod_fornitore].append(p.nuova_data)
    for nc in note_credito:
        rif = max(nc.scad_corrente, primo)
        dopo = sorted(x for x in pagamenti_forn[nc.cod_fornitore] if x >= rif)
        if dopo:
            d = dopo[0]
            nc.motivo = f"Nota di credito compensata con il pagamento del fornitore del {_it(d)}."
        else:
            # senza un pagamento da compensare la nota di credito non genera cassa
            nc.regola = "nota_credito_non_compensata"
            nc.motivo = "Nessun pagamento del fornitore nell'orizzonte da compensare: nessun effetto di cassa, data invariata."
            continue
        if nel(d):
            cassa.esborso(nc.importo, d)
        nc.nuova_data, nc.regola = d, "nota_credito"

    saldo_post = dict(zip(giorni, cassa.saldo))
    fabb = {d: max(0.0, limite - s) for d, s in saldo_post.items()}
    return Risultato(
        giorni=giorni, primo_pagamento=primo, ultimo_pagamento=ultimo, limite=limite,
        saldo_ante=dict(zip(giorni, ante.saldo)), saldo_post=saldo_post,
        capacita=capacita_giorno, fabbisogno=fabb,
        quota_classe_0=q0, quota_classe_1=q1, partite=partite, parziali=parziali,
        saldo_regole=dict(zip(giorni, saldo_regole)), due_fasi=due_fasi,
        saldo_dopo_tagli=dict(zip(giorni, saldo_dopo_tagli)))
