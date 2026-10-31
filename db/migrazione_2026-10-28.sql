-- Migrazione 28/10/2026: anagrafica e accordi su Postgres come fonte unica.
-- Da eseguire solo su un database creato con la versione precedente di schema.sql.
SET search_path = tesoreria;

ALTER TABLE tes_anagrafica_fornitori
    ADD COLUMN IF NOT EXISTS rilevanza     SMALLINT CHECK (rilevanza BETWEEN 1 AND 3),
    ADD COLUMN IF NOT EXISTS modificato_da TEXT,
    ADD COLUMN IF NOT EXISTS motivo        TEXT,
    ADD COLUMN IF NOT EXISTS registrato_il TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE tes_accordo
    ADD COLUMN IF NOT EXISTS data_pagamento_scaduto DATE,
    ADD COLUMN IF NOT EXISTS ragione_sociale        TEXT,
    ADD COLUMN IF NOT EXISTS modificato_da          TEXT,
    ADD COLUMN IF NOT EXISTS motivo                 TEXT;
