"""Scenari sintetici (vedi test/scenari.md). Scala ridotta: importi in euro 'piccoli'.
Lunedì 02/11/2026 è il primo giorno di pagamento; G1..G10 = giorni lavorativi."""
import copy
from datetime import date, timedelta

import pytest

from tesoriere.alloca import pianifica
from tesoriere.calendario import Calendario
from tesoriere.classifica import classifica
from tesoriere.modello import Accordo, Fornitore, Partita, Previsione

CAL = Calendario()
G = {}
_d = date(2026, 11, 2)
for i in range(1, 25):
    _d = CAL.prossimo(_d)
    G[i] = _d
    _d += timedelta(days=1)

REGOLE = {
    "orizzonte": {"giorni": 14},
    "vincolo_cassa": {"fido_anticipo_pos": 0, "cuscinetto_minimo": 0},
    "perimetro": {"data_desiderata": "scadenza_originale"},
    "classi": {0: {"nome": "Intoccabile", "spostabile": False}, 1: {"nome": "Elenco", "spostabile": False},
               2: {"nome": "c2", "spostabile": True}, 3: {"nome": "c3", "spostabile": True},
               4: {"nome": "c4", "spostabile": True}, 5: {"nome": "c5", "spostabile": True}},
    "classe_default_fornitore_non_classificato": 3,
    "regole_classe_automatiche": [
        {"nome": "trattativa_conclusa", "condizione": {"note_amm_contiene_sigla": "TC"}, "classe": 0},
        {"nome": "piano_di_rientro", "condizione": {"note_amm_contiene_testo": "piano di rientro"}, "classe": 0}],
    "accordi": {"classe_corrente": 1},
    "righe_speciali": {"sospese": {"azione": "escludi_e_segnala"}, "note_credito": {"azione": "compensa_fornitore"}},
    "allocazione": {"frazionamento": {"ammesso": False}, "avanzo_a_classe_successiva": True,
                    "anticipa_riscadenziati_se_cassa_consente": True},
    "non_collocabili": {"azione": "parcheggia", "data_parcheggio": "fine_orizzonte"},
}

_n = [0]


def P(imp, scad, forn=1, classe=None, orig=None, note="", sospesa=False, corrente=None, doc=None):
    _n[0] += 1
    return Partita(id=f"p{_n[0]}", prg_documento=f"D{_n[0]}", num_protocollo=_n[0], data_doc=doc or date(2026, 9, 1),
                   numero_doc=str(_n[0]), cod_fornitore=forn, ragione_sociale=f"F{forn}", conto="Fornitori",
                   causale="31", modalita="BO", importo=imp, scad_originale=orig or scad,
                   scad_corrente=corrente or scad, note=note, sospesa=sospesa, classe=classe)


def run(partite, saldo=0.0, flussi=None, anag=None, accordi=None, **over):
    regole = copy.deepcopy(REGOLE)
    for k, v in over.items():
        sez, chiave = k.split("__")
        regole[sez][chiave] = v
    anag = anag or {}
    classifica(partite, anag, regole, accordi)
    for p in partite:  # classe imposta dal test ha la precedenza
        if getattr(p, "_forza", None) is not None:
            p.classe = p._forza
    prev = Previsione(data_consuntivo=G[1] - timedelta(days=1), saldo_consuntivo=saldo, flussi_fissi=flussi or {})
    return pianifica(partite, prev, anag, regole, G[1], CAL)


def classe(p, k):
    p._forza = k
    return p


def test_01_cassa_abbondante():
    ps = [P(10, G[i]) for i in range(1, 6)]
    r = run(ps, saldo=1000)
    assert all(p.nuova_data == p.scad_corrente and not p.variata for p in ps)
    assert max(r.fabbisogno.values()) == 0


def test_02_scopertura_temporanea():
    a, b = P(75, G[3]), P(75, G[3])
    r = run([a, b], saldo=100, flussi={G[6]: 200})
    assert a.nuova_data == G[3]          # FIFO: la prima rata ci sta
    assert b.nuova_data == G[6]          # la seconda al primo giorno con capienza
    assert max(r.fabbisogno.values()) == 0


def test_03_lookahead_su_protetto():
    stip = classe(P(50, G[5], forn=9), 0)
    x = P(80, G[2], forn=1)
    r = run([stip, x], saldo=80, flussi={G[8]: 100})
    # a G2 la capacità è 30 (80 - 50 di stipendi a G5): la rata da 80 non può passare prima di G8
    assert r.capacita[G[2]] == pytest.approx(30)
    assert stip.nuova_data == G[5]
    assert x.nuova_data == G[8]


def test_03b_lookahead_limitato():
    stip = classe(P(50, G[8], forn=9), 0)
    x = P(80, G[2], forn=1)
    r = run([stip, x], saldo=80, allocazione__lookahead_giorni=3)
    # gli stipendi a G8 sono oltre la finestra di 3 giorni: x si paga subito, lo scoperto diventa fabbisogno
    assert x.nuova_data == G[2]
    assert max(r.fabbisogno.values()) == pytest.approx(50)


def test_13b_nota_credito_senza_pagamento():
    nc = P(-30, G[1], forn=5)
    r = run([nc], saldo=0)
    assert nc.regola == "nota_credito_non_compensata" and nc.nuova_data is None
    assert r.saldo_post[G[3]] == pytest.approx(0)


def test_17_data_congelata():
    p = P(500, G[4], forn=1)
    regole_extra = {"date_congelate": [{"data": G[4], "riga_compensazione": "Altro"}]}
    prev = Previsione(data_consuntivo=G[1] - timedelta(days=1), saldo_consuntivo=0, flussi_fissi={G[4]: 480},
                      fisse_per_riga={"Altro": {G[4]: 480}})
    regole = copy.deepcopy(REGOLE)
    regole.update(regole_extra)
    classifica([p], {}, regole)
    r = pianifica([p], prev, {}, regole, G[1], CAL)
    assert p.regola == "congelata" and p.nuova_data is None
    assert r.saldo_post[G[5]] == pytest.approx(0)       # la compensazione non diventa cassa
    assert r.saldo_ante[G[5]] == pytest.approx(-20)     # come nel Report


def test_04_ordine_tra_classi_e_parziale_suggerito():
    c2 = classe(P(40, G[2], forn=1), 2)
    c4 = classe(P(40, G[2], forn=2), 4)
    r = run([c2, c4], saldo=50)
    assert c2.nuova_data == G[2]
    assert c4.regola == "non_collocabile"
    assert any(x.cod_fornitore == 2 and x.importo == pytest.approx(10) for x in r.parziali)


def test_05_pro_quota_a_rate_intere():
    a = [P(50, G[2], forn=1), P(50, G[2], forn=1)]
    b = [P(25, G[2], forn=2), P(25, G[2], forn=2)]
    r = run(a + b, saldo=60)
    pag_g2 = {p.id for p in a + b if p.nuova_data == G[2]}
    assert pag_g2 == {a[0].id}          # A 50, B 0, avanzo 10
    assert r.capacita[G[2]] == pytest.approx(60)


def test_06_fifo_dentro_il_fornitore():
    # due rate scadute del fornitore A (entrano entrambe in coda a G1) + rata di B
    r2 = P(30, G[1] - timedelta(days=5), forn=1)
    r1 = P(30, G[1] - timedelta(days=10), forn=1)
    altro = P(60, G[1] - timedelta(days=10), forn=2)
    run([r2, r1, altro], saldo=45)
    # capacità 45: nessuna rata nella quota (22,5); seconda passata → A paga la rata più vecchia
    assert r1.nuova_data == G[1]
    assert r2.nuova_data != G[1]


def test_06b_nessun_frazionamento():
    ps = [P(33.33, G[2], forn=i) for i in range(1, 6)]
    r = run(ps, saldo=70)
    pagate = [p for p in ps if p.nuova_data == G[2]]
    assert sum(p.importo for p in pagate) <= 70 + 0.01
    assert len(pagate) == 2


def test_07_protetti_non_coperti():
    stip = classe(P(300, G[4], forn=9), 0)
    r = run([stip], saldo=100)
    assert stip.nuova_data == G[4]
    assert max(r.fabbisogno.values()) == pytest.approx(200)
    assert r.quota_classe_0 == pytest.approx(200)


def test_08_fornitore_in_elenco():
    p = P(150, G[3], forn=7)
    r = run([p], saldo=100, anag={7: Fornitore(7, in_elenco_acquisto=True)})
    assert p.classe == 1 and p.nuova_data == G[3]
    assert r.quota_classe_1 == pytest.approx(50)


def test_09_slittamento_massimo():
    p = P(80, G[1], forn=3)
    run([p], saldo=0, flussi={G[9]: 100}, anag={3: Fornitore(3, slittamento_max_gg=5)})
    assert p.regola == "forzata_slitt_max"
    assert (p.nuova_data - p.scad_originale).days <= 5


def test_10_scaduto_massimo():
    ps = [P(40, G[1] - timedelta(days=10), forn=4), P(40, G[1] - timedelta(days=5), forn=4)]
    run(ps, saldo=0, flussi={G[9]: 100}, anag={4: Fornitore(4, scaduto_max_tollerato=50)})
    forzate = [p for p in ps if p.regola == "forzata_scaduto_max"]
    assert len(forzate) == 1 and forzate[0].scad_originale == G[1] - timedelta(days=10)


def test_11_non_collocabile():
    p = P(500, G[2], forn=1)
    r = run([p], saldo=0)
    assert p.regola == "non_collocabile" and p.nuova_data == r.ultimo_pagamento
    assert r.fabbisogno[r.ultimo_pagamento] == pytest.approx(500)


def test_11b_non_collocabile_gia_oltre_orizzonte():
    p = P(500, G[20], forn=1, orig=G[2])
    run([p], saldo=0)
    assert p.regola == "non_collocabile_invariata" and p.nuova_data is None


def test_12_sospesa():
    p = P(50, G[2], sospesa=True)
    r = run([p], saldo=0)
    assert p.regola == "sospesa" and p.nuova_data is None
    assert max(r.fabbisogno.values()) == 0


def test_13_nota_credito():
    f = P(100, G[2], forn=5)
    nc = P(-30, G[1], forn=5)
    r = run([f, nc], saldo=100)
    assert f.nuova_data == G[2] and nc.nuova_data == G[2]
    assert r.saldo_post[G[2]] == pytest.approx(30)


def test_14_trattativa_conclusa():
    p = P(80, G[2], forn=1, note="TC 12/10 accordo")
    run([p], saldo=0)
    assert p.classe == 0 and p.nuova_data == G[2]
    q = P(80, G[2], forn=1, note="TIC 12/10")
    run([q], saldo=0)
    assert q.classe == 3


def test_15_anticipo_dopo_miglioramento():
    p = P(80, G[8], forn=1, orig=G[2], corrente=G[8])
    run([p], saldo=0, flussi={G[3]: 100})
    assert p.nuova_data == G[3] and p.variata


def test_16_determinismo():
    def mk():
        _n[0] = 1000
        return [P(37, G[2], forn=i % 4) for i in range(12)]
    a, b = mk(), mk()
    run(a, saldo=150, flussi={G[5]: 100})
    run(b, saldo=150, flussi={G[5]: 100})
    assert [(p.id, p.nuova_data, p.regola) for p in a] == [(p.id, p.nuova_data, p.regola) for p in b]


def test_vincolo_rispettato_quando_possibile():
    import random
    random.seed(7)
    ps = [P(round(random.uniform(5, 60), 2), G[random.randint(1, 8)], forn=random.randint(1, 6)) for _ in range(40)]
    flussi = {G[i]: random.uniform(20, 120) for i in range(1, 10)}
    r = run(ps, saldo=50, flussi=flussi)
    parcheggiate = sum(p.importo for p in ps if p.regola == "non_collocabile")
    for d, s in r.saldo_post.items():
        if d < r.ultimo_pagamento:
            assert s >= r.limite - 0.01, (d, s)
    assert parcheggiate >= 0


ACC = {8: Accordo(cod_fornitore=8, decorrenza=date(2026, 9, 1))}


def test_18_accordo_scaduto_congelato_corrente_protetto():
    vecchia = P(200, G[3], forn=8, doc=date(2026, 6, 10), orig=date(2026, 8, 9))
    nuova = P(100, G[4], forn=8, doc=date(2026, 9, 15))
    altro = P(50, G[2], forn=2)
    r = run([vecchia, nuova, altro], saldo=60, accordi=ACC, anag={8: Fornitore(8, classe_priorita=5)})
    assert vecchia.regola == "congelata_accordo" and vecchia.nuova_data is None
    assert nuova.classe == 1 and nuova.regola == "protetta_c1" and nuova.nuova_data == G[4]
    # il corrente protetto riserva la cassa: il fornitore 2 non può essere pagato prima
    assert altro.nuova_data != G[2]
    assert r.quota_classe_1 == pytest.approx(40)


def test_19_accordo_criterio_scadenza_originale():
    acc = {8: Accordo(cod_fornitore=8, decorrenza=date(2026, 11, 1), criterio="scadenza_originale")}
    a = P(10, G[2], forn=8, doc=date(2026, 9, 1))   # documento vecchio ma rata che scade dopo la decorrenza
    b = P(10, G[2], forn=8, doc=date(2026, 9, 1), orig=date(2026, 10, 15))
    run([a, b], saldo=100, accordi=acc)
    assert a.regola == "protetta_c1" and b.regola == "congelata_accordo"


def test_20_tc_e_piano_di_rientro_prevalgono_sull_accordo():
    tc = P(30, G[3], forn=8, doc=date(2026, 6, 1), note="TC 10/10")
    pdr = P(30, G[5], forn=8, doc=date(2026, 6, 1), note="piano di rientro del 26/08/2026(DC)")
    run([tc, pdr], saldo=100, accordi=ACC)
    assert tc.classe == 0 and tc.nuova_data == G[3]
    assert pdr.classe == 0 and pdr.nuova_data == G[5]


def test_21_scaduto_congelato_non_conta_per_scaduto_max():
    vecchia = P(500, G[1] - timedelta(days=30), forn=8, doc=date(2026, 6, 1))
    corrente = P(20, G[2], forn=8, doc=date(2026, 10, 1))
    run([vecchia, corrente], saldo=0, accordi=ACC, anag={8: Fornitore(8, scaduto_max_tollerato=10)})
    assert vecchia.regola == "congelata_accordo"
    assert corrente.regola == "protetta_c1"
