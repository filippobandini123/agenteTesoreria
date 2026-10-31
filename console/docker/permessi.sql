-- =====================================================================
--  Utente PostgreSQL della Console Tesoreria: solo i permessi che servono.
--  Da eseguire una volta come amministratore del database.
--  Sostituire <password> con una password lunga e casuale.
-- =====================================================================
CREATE ROLE tesoreria_console LOGIN PASSWORD '<password>';

GRANT USAGE ON SCHEMA tesoreria TO tesoreria_console;
GRANT SELECT ON ALL TABLES IN SCHEMA tesoreria TO tesoreria_console;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA tesoreria TO tesoreria_console;

-- anagrafica e accordi: nuove versioni e chiusura della versione precedente (mai DELETE)
GRANT INSERT, UPDATE ON tesoreria.tes_anagrafica_fornitori, tesoreria.tes_accordo TO tesoreria_console;
GRANT INSERT ON tesoreria.tes_audit TO tesoreria_console;

-- simulazioni: il motore registra il run di simulazione con righe, saldi e controlli
GRANT INSERT ON tesoreria.tes_run, tesoreria.tes_proposta_riga, tesoreria.tes_saldo_giornaliero,
                tesoreria.tes_controllo, tesoreria.tes_regole_versione TO tesoreria_console;
GRANT UPDATE (stato_approvazione) ON tesoreria.tes_run TO tesoreria_console;

-- login: sola lettura delle colonne necessarie della tabella utenti del gestionale
GRANT USAGE ON SCHEMA public TO tesoreria_console;
GRANT SELECT (idutente, username, nome, cognome, password, stato) ON public.utente TO tesoreria_console;
