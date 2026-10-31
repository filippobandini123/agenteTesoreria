-- Migrazione 29/10/2026 (pomeriggio): conto contabile sulle righe della proposta.
-- ESEGUIRE PRIMA di aggiornare il pacchetto: il motore nuovo scrive questa colonna.
SET search_path = tesoreria;
ALTER TABLE tes_proposta_riga ADD COLUMN IF NOT EXISTS conto TEXT;
