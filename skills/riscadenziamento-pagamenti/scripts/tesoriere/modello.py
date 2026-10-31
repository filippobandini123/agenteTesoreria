"""Strutture dati del motore. Nessuna dipendenza da Excel o DB: il motore
di allocazione lavora solo su questi oggetti, così è testabile con dati sintetici."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Partita:
    """Unità di pianificazione = documento + scadenza corrente.
    Corrisponde a una riga del tracciato di import (Numdoc, Datadoc, Datascad_vecchia).
    Più righe dello scadenzario con la stessa chiave vengono sommate."""
    id: str
    prg_documento: str
    num_protocollo: Optional[int]
    data_doc: Optional[date]
    numero_doc: str
    cod_fornitore: int
    ragione_sociale: str
    conto: str
    causale: str
    modalita: str               # RB | RID | BO | ...
    importo: float              # residuo (>0 debito, <0 nota di credito)
    scad_originale: date
    scad_corrente: date
    note: str = ""
    sospesa: bool = False
    fonte: str = "scadenzario"  # scadenzario | scaduti
    n_righe: int = 1
    importabile: bool = True    # False se la chiave di import è ambigua o il protocollo manca
    id_scadenza: Optional[str] = None   # "Protocollo" del gestionale: chiave univoca della scadenza

    # --- classificazione
    classe: Optional[int] = None
    origine_classe: str = ""
    congelata_accordo: bool = False   # scaduto congelato da un accordo col fornitore
    data_fissa: Optional[date] = None  # data di pagamento in input dello scaduto congelato
    rilevanza: Optional[int] = None    # 1 strategico · 2 normale · 3 sacrificabile (ordine di taglio)

    # --- esito della pianificazione
    data_ingresso: Optional[date] = None   # da quando entra in coda
    nuova_data: Optional[date] = None      # None = nessuna proposta (data invariata)
    regola: str = ""
    motivo: str = ""
    data_regole: Optional[date] = None     # data nel piano da regole (fase 1), prima dei tagli

    @property
    def variata(self) -> bool:
        return self.nuova_data is not None and self.nuova_data != self.scad_corrente

    @property
    def giorni_slittamento(self) -> Optional[int]:
        if self.nuova_data is None:
            return None
        return (self.nuova_data - self.scad_originale).days


@dataclass
class Fornitore:
    cod_fornitore: int
    ragione_sociale: str = ""
    in_elenco_acquisto: bool = False
    classe_priorita: Optional[int] = None
    scaduto_max_tollerato: Optional[float] = None
    slittamento_max_gg: Optional[int] = None
    pagamento_minimo: Optional[float] = None
    rilevanza: Optional[int] = None     # 1 strategico · 2 normale · 3 sacrificabile
    note: str = ""
    valido_dal: Optional[date] = None


@dataclass
class Accordo:
    """Accordo col fornitore: scaduto congelato, corrente pagato alla scadenza dalla decorrenza."""
    cod_fornitore: int
    decorrenza: date
    tipo: str = "congelamento_scaduto"
    criterio: str = "data_doc"          # data_doc | scadenza_originale
    valido_fino: Optional[date] = None
    ragione_sociale: str = ""
    note: str = ""
    stato: str = "attivo"               # attivo | proposta | rifiutato
    data_pagamento_scaduto: Optional[date] = None   # se vuota vale quella di regole.yaml

    def e_corrente(self, p: "Partita") -> bool:
        rif = p.data_doc if self.criterio == "data_doc" else p.scad_originale
        if rif is None:              # senza data documento si usa la scadenza originale
            rif = p.scad_originale
        return rif >= self.decorrenza


@dataclass
class Previsione:
    """Previsione di cassa giornaliera al netto dei pagamenti fornitori."""
    data_consuntivo: date
    saldo_consuntivo: float
    flussi_fissi: dict[date, float]                 # entrate + uscite da budget (con segno)
    righe_fornitori_cache: dict[date, float] = field(default_factory=dict)  # per controllo
    saldo_cache: dict[date, float] = field(default_factory=dict)            # riga saldo del Report
    controllo_totali: list[tuple[date, float]] = field(default_factory=list)  # (data, scarto)
    fisse_per_riga: dict[str, dict[date, float]] = field(default_factory=dict)  # etichetta → data → valore


@dataclass
class ParzialeSuggerito:
    data: date
    cod_fornitore: int
    ragione_sociale: str
    classe: int
    importo: float
    nota: str


@dataclass
class Risultato:
    giorni: list[date]
    primo_pagamento: date
    ultimo_pagamento: date
    limite: float
    saldo_ante: dict[date, float]
    saldo_post: dict[date, float]
    capacita: dict[date, float]
    fabbisogno: dict[date, float]
    quota_classe_0: float
    quota_classe_1: float
    partite: list[Partita]
    parziali: list[ParzialeSuggerito]
    # piano in due fasi: saldo del piano da regole (protetti e tutelati alle loro date, prima dei tagli)
    saldo_regole: dict[date, float] = field(default_factory=dict)
    due_fasi: bool = False
    saldo_dopo_tagli: dict[date, float] = field(default_factory=dict)   # dopo i tagli, prima delle classi 2-5
