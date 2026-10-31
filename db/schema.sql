-- =====================================================================
--  Schema PostgreSQL — memoria e audit dell'agente Tesoriere
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS tesoreria;
SET search_path = tesoreria;

-- Versioni delle regole: ogni run punta alla versione usata
CREATE TABLE tes_regole_versione (
    id              SERIAL PRIMARY KEY,
    versione        TEXT NOT NULL,
    hash_sha256     TEXT NOT NULL UNIQUE,
    contenuto_yaml  TEXT NOT NULL,
    caricata_il     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Anagrafica fornitori con storico (SCD tipo 2)
CREATE TABLE tes_anagrafica_fornitori (
    id                     SERIAL PRIMARY KEY,
    cod_fornitore          INTEGER NOT NULL,
    ragione_sociale        TEXT,
    in_elenco_acquisto     BOOLEAN NOT NULL DEFAULT false,
    classe_priorita        SMALLINT CHECK (classe_priorita BETWEEN 0 AND 5),
    scaduto_max_tollerato  NUMERIC(14,2),
    slittamento_max_gg     INTEGER,
    pagamento_minimo       NUMERIC(14,2),
    rilevanza              SMALLINT CHECK (rilevanza BETWEEN 1 AND 3),   -- ordine di taglio: 1 strategico · 3 sacrificabile
    note                   TEXT,
    valido_dal             DATE NOT NULL,
    valido_al              DATE,                -- NULL = versione corrente
    modificato_da          TEXT,                -- chi ha chiesto la modifica (es. 'Filippo via Telegram')
    motivo                 TEXT,
    registrato_il          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (cod_fornitore, valido_dal)
);
CREATE INDEX ON tes_anagrafica_fornitori (cod_fornitore) WHERE valido_al IS NULL;

-- Un record per ogni esecuzione del motore
CREATE TABLE tes_run (
    run_id              UUID PRIMARY KEY,
    tipo_run            TEXT NOT NULL CHECK (tipo_run IN ('ufficiale','simulazione')),
    avviato_il          TIMESTAMPTZ NOT NULL,
    terminato_il        TIMESTAMPTZ,
    esito               TEXT NOT NULL CHECK (esito IN ('ok','controlli_ko','errore')),
    data_consuntivo     DATE,
    orizzonte_da        DATE,
    orizzonte_a         DATE,
    regole_versione_id  INTEGER REFERENCES tes_regole_versione(id),
    file_input          TEXT,
    hash_input          TEXT,
    file_output         TEXT,
    override_json       JSONB,                  -- parametri di simulazione
    fabbisogno_picco    NUMERIC(14,2),
    fabbisogno_data     DATE,
    riepilogo_json      JSONB,                  -- copia di ultimo_run.json
    avviato_da          TEXT,                   -- 'tesoriere' (agente) o utente della console
    stato_approvazione  TEXT NOT NULL DEFAULT 'da_rivedere'
                        CHECK (stato_approvazione IN ('da_rivedere','approvata','approvata_con_modifiche','respinta','superata'))
);

-- Esiti dei controlli di input
CREATE TABLE tes_controllo (
    run_id    UUID REFERENCES tes_run(run_id) ON DELETE CASCADE,
    id_ctrl   TEXT NOT NULL,
    esito     TEXT NOT NULL CHECK (esito IN ('ok','avviso','ko')),
    messaggio TEXT,
    PRIMARY KEY (run_id, id_ctrl)
);

-- Una riga per ogni rata valutata (spostata o no): è l'audit log
-- Unità = "partita": documento + scadenza attuale (= granularità del tracciato di import)
CREATE TABLE tes_proposta_riga (
    run_id              UUID REFERENCES tes_run(run_id) ON DELETE CASCADE,
    partita_id          TEXT NOT NULL,          -- S<Protocollo scadenza> oppure prg_documento|AAAAMMGG
    id_scadenza         TEXT,                   -- "Protocollo" del gestionale (chiave della scadenza)
    nota_precedente     TEXT,                   -- nota nel gestionale PRIMA della proposta (l'import la sovrascrive)
    prg_documento       TEXT NOT NULL,
    num_protocollo      INTEGER,                -- NULL se assente nello scadenzario
    scad_originale      DATE NOT NULL,
    cod_fornitore       INTEGER NOT NULL,
    ragione_sociale     TEXT,
    modalita            TEXT,                   -- RB | RID | BO
    classe              SMALLINT,
    origine_classe      TEXT,                   -- regola_auto:<nome> | anagrafica | default
    importo_residuo     NUMERIC(14,2) NOT NULL,
    scadenza_corrente   DATE NOT NULL,
    nuova_data          DATE,                   -- NULL = non collocabile
    giorni_slittamento  INTEGER,                -- nuova_data - scad_originale
    regola              TEXT NOT NULL,          -- protetta_cN | pagata_alla_data | scaduta_pagata | slittata | forzata_slitt_max | forzata_scaduto_max | non_collocabile | non_collocabile_invariata | fuori_orizzonte | sospesa | nota_credito | importo_zero
    motivo              TEXT,                   -- spiegazione leggibile
    nota_amm_proposta   TEXT,
    azione_operativa    TEXT,
    variata             BOOLEAN NOT NULL,       -- nuova_data <> scadenza_corrente
    importabile         BOOLEAN NOT NULL,
    fonte               TEXT,
    conto               TEXT,                   -- conto contabile (des_piacont): per il conto prevalente del fornitore
    PRIMARY KEY (run_id, partita_id)
);
CREATE INDEX ON tes_proposta_riga (cod_fornitore);

-- Saldo giornaliero ante/post per ogni run
CREATE TABLE tes_saldo_giornaliero (
    run_id        UUID REFERENCES tes_run(run_id) ON DELETE CASCADE,
    data          DATE NOT NULL,
    saldo_ante    NUMERIC(14,2) NOT NULL,
    saldo_post    NUMERIC(14,2) NOT NULL,
    limite        NUMERIC(14,2) NOT NULL,       -- -fido + cuscinetto
    capacita      NUMERIC(14,2),
    fabbisogno    NUMERIC(14,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, data)
);

-- Eventi (invio messaggi, approvazioni, errori)
CREATE TABLE tes_audit (
    id        BIGSERIAL PRIMARY KEY,
    run_id    UUID REFERENCES tes_run(run_id),
    evento    TEXT NOT NULL,
    dettaglio JSONB,
    autore    TEXT NOT NULL DEFAULT 'tesoriere',
    quando    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Viste di memoria
-- ---------------------------------------------------------------------

-- Ultimo run ufficiale riuscito
CREATE VIEW tes_v_ultimo_run AS
SELECT * FROM tes_run
WHERE tipo_run = 'ufficiale' AND esito = 'ok'
ORDER BY avviato_il DESC LIMIT 1;

-- Slittamento corrente per fornitore (sull'ultimo run ufficiale)
CREATE VIEW tes_v_slittamento_fornitore AS
SELECT r.cod_fornitore,
       max(r.ragione_sociale)                              AS ragione_sociale,
       count(*) FILTER (WHERE r.giorni_slittamento > 0)    AS n_rate_slittate,
       sum(r.importo_residuo) FILTER (WHERE r.giorni_slittamento > 0) AS importo_slittato,
       max(r.giorni_slittamento)                           AS slittamento_max_gg,
       sum(r.importo_residuo * greatest(r.giorni_slittamento,0))      AS euro_giorno
FROM tes_proposta_riga r
JOIN tes_v_ultimo_run u USING (run_id)
GROUP BY r.cod_fornitore;

-- Storico: quante volte una stessa rata è stata riproposta con data diversa
-- (chiave stabile tra un giorno e l'altro: documento + scadenza originale,
--  perché la scadenza attuale cambia dopo ogni import)
CREATE VIEW tes_v_storia_rata AS
SELECT r.prg_documento, r.scad_originale, r.cod_fornitore,
       count(*)                       AS n_run,
       count(DISTINCT r.nuova_data)   AS n_date_diverse,
       min(r.nuova_data)              AS data_min, max(r.nuova_data) AS data_max
FROM tes_proposta_riga r
JOIN tes_run t USING (run_id)
WHERE t.tipo_run = 'ufficiale' AND t.esito = 'ok'
GROUP BY 1,2,3;

-- Delta tra ultimo run e il precedente (per il commento giornaliero)
CREATE VIEW tes_v_delta_run AS
WITH ult AS (
  SELECT run_id, avviato_il, row_number() OVER (ORDER BY avviato_il DESC) AS n
  FROM tes_run WHERE tipo_run='ufficiale' AND esito='ok'
)
SELECT prg_documento, scad_originale,
       coalesce(a.ragione_sociale,b.ragione_sociale) AS ragione_sociale,
       b.nuova_data AS data_ieri, a.nuova_data AS data_oggi,
       coalesce(a.importo, b.importo) AS importo
FROM (SELECT prg_documento, scad_originale, max(ragione_sociale) ragione_sociale,
             max(nuova_data) nuova_data, sum(importo_residuo) importo
      FROM tes_proposta_riga WHERE run_id=(SELECT run_id FROM ult WHERE n=1) GROUP BY 1,2) a
FULL JOIN (SELECT prg_documento, scad_originale, max(ragione_sociale) ragione_sociale,
             max(nuova_data) nuova_data, sum(importo_residuo) importo
      FROM tes_proposta_riga WHERE run_id=(SELECT run_id FROM ult WHERE n=2) GROUP BY 1,2) b
  USING (prg_documento, scad_originale)
WHERE a.nuova_data IS DISTINCT FROM b.nuova_data;

-- Accordi con i fornitori (congelamento scaduto / pagamento del corrente)
CREATE TABLE tesoreria.tes_accordo (
    cod_fornitore  INTEGER NOT NULL,
    tipo           TEXT NOT NULL DEFAULT 'congelamento_scaduto',
    decorrenza     DATE NOT NULL,
    criterio       TEXT NOT NULL CHECK (criterio IN ('data_doc','scadenza_originale')),
    stato          TEXT NOT NULL DEFAULT 'attivo' CHECK (stato IN ('attivo','proposta','rifiutato')),
    valido_fino    DATE,
    data_pagamento_scaduto DATE,             -- vuota = accordi.data_pagamento_scaduto di regole.yaml
    ragione_sociale TEXT,
    note           TEXT,
    modificato_da  TEXT,
    motivo         TEXT,
    caricato_il    TIMESTAMPTZ NOT NULL DEFAULT now(),
    aggiornato_il  TIMESTAMPTZ,
    PRIMARY KEY (cod_fornitore, decorrenza)
);
