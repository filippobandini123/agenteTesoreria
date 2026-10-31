"""Caricamento dello scadenzario (con e senza chiave della scadenza) e nota additiva."""
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yaml

from tesoriere.carica import carica_partite
from tesoriere.modello import Partita
from tesoriere.output import nota_proposta

FONTI = yaml.safe_load((Path(__file__).resolve().parents[4] / "config" / "fonti_dati.yaml").read_text(encoding="utf-8"))
COLS = ["prg_documento", "des_num_doc", "dat_doc", "des_piacont", "num_protocollo", "ind_clifor", "cod_caucon",
        "cod_tipo_pag", "val_residuo", "des_ragsoc", "ScadOriginale", "dat_scadenza", "NoteAmm", "Sospeso", "cod_clifor"]


def _riga(prg, prot, scad, imp, orig=None, nota=""):
    return [prg, "VX/1", datetime(2026, 7, 6), "Fornitori Locazioni", prot, "F", "31", "BO", imp, "Bennet",
            orig or scad, scad, nota, 0, 1158]


def _scrivi(tmp_path, righe, protocolli=None):
    df = pd.DataFrame(righe, columns=COLS)
    if protocolli is not None:
        df["numero_protocollo_effetto"] = protocolli
    f = tmp_path / "report.xlsx"
    with pd.ExcelWriter(f) as w:
        df.to_excel(w, sheet_name="Scadenzario_Attivo_Passivo", index=False)
        df.iloc[0:0].to_excel(w, sheet_name="Scaduti_Attivo_Passivo", index=False)
    return f


def test_senza_chiave_rate_uguali_si_sommano(tmp_path):
    f = _scrivi(tmp_path, [_riga("202600133000", 5792, datetime(2026, 11, 30), 100),
                           _riga("202600133000", 5792, datetime(2026, 11, 30), 50)])
    ps = carica_partite(f, FONTI)
    assert len(ps) == 1 and ps[0].importo == 150 and ps[0].id_scadenza is None


def test_con_chiave_ogni_scadenza_e_una_partita(tmp_path):
    f = _scrivi(tmp_path, [_riga("202600133000", 5792, datetime(2026, 11, 30), 100),
                           _riga("202600133000", 5792, datetime(2026, 11, 30), 50),
                           _riga("202600133001", None, datetime(2026, 11, 30), 70)],
                protocolli=[36698, 36699, None])
    ps = sorted(carica_partite(f, FONTI), key=lambda p: p.importo)
    assert [p.importo for p in ps] == [50, 70, 100]
    assert {p.id_scadenza for p in ps} == {"36698", "36699", None}
    # importabile = ha la chiave della scadenza, anche se manca il protocollo del documento
    assert [p.importabile for p in ps] == [True, False, True]


def _p(note):
    p = Partita(id="x", prg_documento="1", num_protocollo=1, data_doc=date(2026, 7, 6), numero_doc="1",
                cod_fornitore=1, ragione_sociale="F", conto="", causale="", modalita="BO", importo=10,
                scad_originale=date(2026, 7, 17), scad_corrente=date(2026, 11, 30), note=note)
    p.nuova_data = date(2026, 12, 24)
    return p


REG = {"note_amministrative": {"genera": True, "formato": "HPAG({data_run:%d/%m}):{nuova_data:%d/%m}(AG)",
                               "modalita": "additiva", "separatore": "_"}}


def test_nota_additiva_solo_pezzo_nuovo():
    assert nota_proposta(_p("HPEI:(24/05):12/10(DC)"), REG, date(2026, 10, 26)) == "_HPAG(26/10):24/12(AG)"
    assert nota_proposta(_p(""), REG, date(2026, 10, 26)) == "HPAG(26/10):24/12(AG)"


def test_nota_con_classe_e_origine():
    reg = {"note_amministrative": {"genera": True, "modalita": "additiva", "separatore": "_",
                                   "formato": "HPAG({data_run:%d/%m}):{nuova_data:%d/%m}(AG)C{classe}-{origine}",
                                   "codici_origine": {"classe": "ANAG", "accordo": "ACC", "default": "DEF"}}}
    p = _p("HPEI:(24/05):12/10(DC)")
    p.classe, p.origine_classe = 3, "anagrafica:classe"
    assert nota_proposta(p, reg, date(2026, 10, 26)) == "_HPAG(26/10):24/12(AG)C3-ANAG"
    p.classe, p.origine_classe = 1, "accordo:corrente dal 15/09/2026"
    assert nota_proposta(p, reg, date(2026, 10, 26)).endswith("C1-ACC")
    p.classe, p.origine_classe = 3, "default"
    assert nota_proposta(p, reg, date(2026, 10, 26)).endswith("C3-DEF")


def test_stato_accordi(tmp_path):
    from tesoriere.carica import carica_accordi
    f = tmp_path / "anag.xlsx"
    pd.DataFrame([
        [1, "A", None, datetime(2026, 9, 15), None, None, "", "Attivo"],
        [2, "B", None, datetime(2026, 9, 15), None, None, "", "Proposta"],
        [3, "C", None, datetime(2026, 9, 15), None, None, "", "Rifiutato"],
        [4, "D", None, datetime(2026, 9, 15), None, None, "", None],
        [1, "A", None, datetime(2026, 9, 1), None, None, "", "Proposta"],   # superata dall'accordo attivo
    ], columns=["cod_fornitore", "ragione_sociale", "tipo", "decorrenza", "criterio", "valido_fino", "note", "stato"]
    ).to_excel(f, sheet_name="Accordi", index=False)
    attivi, proposte, ignorati = carica_accordi(f, "Accordi", date(2026, 10, 26))
    assert set(attivi) == {1, 4} and set(proposte) == {2}
    assert {a.cod_fornitore for a in ignorati} == {1, 3}
