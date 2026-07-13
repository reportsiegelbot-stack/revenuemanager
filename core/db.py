"""
core/db.py
----------
Gestisce la connessione al database SQLite e la creazione dello schema.
Questo modulo e' volutamente "agnostico": non conosce il concetto di hotel
o camera, solo entita' astratte (capacity_unit, booking, canale, ecc.)
cosi' come definite nello schema del progetto.
"""

import sqlite3
from pathlib import Path

# Nome del file database di default, usato da tutti gli script se non
# viene indicato un percorso diverso da riga di comando.
NOME_DB_DEFAULT = "vault.db"

# Istruzioni SQL che creano tutte le tabelle del sistema, se non esistono
# gia'. Usare "IF NOT EXISTS" rende lo script rieseguibile senza errori.
SCHEMA_SQL = """
-- Unita' di capacita' vendibile (es. una tipologia di camera, un posto,
-- uno spazio: il nome resta astratto perche' il core e' settore-agnostico)
CREATE TABLE IF NOT EXISTS capacity_unit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    total_units  INTEGER NOT NULL
);

-- Fotografia (snapshot) della disponibilita' e del prezzo per una certa
-- data futura, rilevata in un certo giorno. Serve per ricostruire la
-- "curva di prenotazione" nel tempo.
CREATE TABLE IF NOT EXISTS inventory_snapshot (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date     TEXT NOT NULL,   -- giorno in cui e' stata fatta la rilevazione (YYYY-MM-DD)
    target_date       TEXT NOT NULL,   -- giorno a cui si riferisce la disponibilita' (YYYY-MM-DD)
    capacity_unit_id  INTEGER NOT NULL REFERENCES capacity_unit(id),
    units_available   INTEGER NOT NULL,
    price_published   REAL,
    channel           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshot_target
    ON inventory_snapshot (target_date, capacity_unit_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_giorno
    ON inventory_snapshot (snapshot_date);

-- Prenotazione. status puo' valere solo 'confirmed' o 'cancelled'.
CREATE TABLE IF NOT EXISTS booking (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_date      TEXT NOT NULL,   -- giorno in cui la prenotazione e' stata fatta
    arrival_date      TEXT NOT NULL,   -- giorno di inizio utilizzo
    nights            INTEGER NOT NULL,
    capacity_unit_id  INTEGER NOT NULL REFERENCES capacity_unit(id),
    channel           TEXT NOT NULL,
    total_price       REAL NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('confirmed', 'cancelled'))
);
CREATE INDEX IF NOT EXISTS idx_booking_arrivo
    ON booking (arrival_date);
CREATE INDEX IF NOT EXISTS idx_booking_creazione
    ON booking (created_date);

-- Storico delle variazioni di prezzo decise manualmente (o dal sistema).
CREATE TABLE IF NOT EXISTS rate_event (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ts          TEXT NOT NULL,   -- data/ora in cui e' stata fatta la modifica
    target_date       TEXT NOT NULL,   -- data a cui si riferisce il nuovo prezzo
    capacity_unit_id  INTEGER NOT NULL REFERENCES capacity_unit(id),
    channel           TEXT NOT NULL,
    price_before      REAL,
    price_after       REAL NOT NULL,
    author            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate_event_target
    ON rate_event (target_date);

-- Storico di apertura/chiusura di un canale di vendita per una certa data.
CREATE TABLE IF NOT EXISTS channel_event (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ts     TEXT NOT NULL,
    target_date  TEXT NOT NULL,
    channel      TEXT NOT NULL,
    action       TEXT NOT NULL CHECK (action IN ('open', 'close')),
    author       TEXT NOT NULL
);

-- Suggerimenti generati dal motore a regole (report_mattina.py). Ogni riga
-- e' una decisione proposta, che l'utente potra' in futuro segnare come
-- accettata o ignorata (per ora resta 'pending').
-- L'indice unico (creato in crea_schema, dopo aver ripulito eventuali
-- duplicati storici) rende idempotente la registrazione: stesso giorno di
-- generazione + stessa data_target + stessa tipologia + stesso tipo di
-- decisione non genera piu' righe duplicate se report_mattina.py gira piu'
-- volte nello stesso giorno.
CREATE TABLE IF NOT EXISTS decision (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts          TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    capacity_unit_id    INTEGER REFERENCES capacity_unit(id),
    decision_type       TEXT NOT NULL,
    suggestion          TEXT NOT NULL,
    reasoning           TEXT NOT NULL,
    data_snapshot_json  TEXT,
    outcome             TEXT NOT NULL DEFAULT 'pending'
                         CHECK (outcome IN ('pending', 'accepted', 'ignored'))
);

-- Segnali esterni (eventi, festivita', fiere...) che possono influenzare
-- la domanda in una certa data.
CREATE TABLE IF NOT EXISTS external_signal (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_date   TEXT NOT NULL,
    signal_type   TEXT NOT NULL,
    description   TEXT NOT NULL,
    impact_score  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signal_data
    ON external_signal (signal_date);

-- Registro di controllo (P6) di ogni importazione del log disponibilita':
-- una riga per ogni esecuzione di importa_log_disponibilita.py, con il
-- conteggio delle righe lette/accettate/scartate/in warning e l'hash del
-- file, per riconoscere un re-import identico.
CREATE TABLE IF NOT EXISTS import_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    import_ts       TEXT NOT NULL,
    source_file     TEXT NOT NULL,
    file_sha256     TEXT NOT NULL,
    rows_read       INTEGER NOT NULL,
    rows_accepted   INTEGER NOT NULL,
    rows_rejected   INTEGER NOT NULL,
    rows_warning    INTEGER NOT NULL,
    detail_json     TEXT
);

-- Tracciamento esiti (R13) delle decisioni di prezzo generate dal motore
-- (aumento_prezzo, unita_scarse_aumento, ribasso_promo). Una riga per
-- suggerimento generato in un certo giorno; lo stato viene aggiornato in
-- un secondo momento da esiti_decisioni.py, quando ci sono abbastanza
-- rilevazioni successive per misurarlo. Tabella separata da "decision"
-- (che resta invariata): "outcome" li' e' una scelta umana mai aggiornata
-- automaticamente, "status" qui e' un esito misurato dal sistema.
CREATE TABLE IF NOT EXISTS decision_outcome (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_date        TEXT NOT NULL,   -- giorno in cui il report ha generato il suggerimento
    target_date           TEXT NOT NULL,
    capacity_unit_id      INTEGER REFERENCES capacity_unit(id),
    decision_type         TEXT NOT NULL,   -- aumento_prezzo | unita_scarse_aumento | ribasso_promo
    channel               TEXT NOT NULL,   -- canale della rilevazione su cui si basava la decisione
    current_price         REAL,            -- prezzo pubblicato al momento della decisione
    current_units         INTEGER,         -- unita' disponibili al momento della decisione
    suggested_price       REAL,            -- prezzo suggerito (NULL per ribasso_promo: nessun numero fisso)
    reasoning_json        TEXT NOT NULL,   -- i livelli della motivazione, serializzati
    status                TEXT NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending', 'followed', 'not_followed', 'partial', 'not_measurable')),
    measured_ts           TEXT,
    measured_snapshot_date TEXT,           -- data_rilevazione usata per l'ultima misurazione (monotona)
    measurement_detail    TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_outcome_dedup
    ON decision_outcome (generated_date, target_date, COALESCE(capacity_unit_id, -1), decision_type);
"""


def connetti(percorso_db=NOME_DB_DEFAULT):
    """Apre (o crea) il file del database e restituisce la connessione.

    Le righe restituite dalle query si comportano come dizionari
    (sqlite3.Row), cosi' nel resto del codice si puo' scrivere riga["campo"]
    invece di doversi ricordare l'ordine delle colonne.
    """
    conn = sqlite3.connect(percorso_db)
    conn.row_factory = sqlite3.Row
    # Attiva il controllo delle chiavi esterne (SQLite lo tiene spento
    # di default per motivi storici).
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _deduplica_decision_esistenti(conn):
    """Ripulisce i duplicati storici della tabella decision (difetto
    emerso nella sessione R13: report_mattina.py non era idempotente e
    generava una riga nuova ad ogni esecuzione, anche piu' volte nello
    stesso giorno). Duplicato = stessa combinazione (giorno di
    generazione, target_date, capacity_unit_id, decision_type); tra righe
    duplicate tiene quella con id piu' alto (la piu' recente). Idempotente:
    se non ci sono duplicati non cambia nulla. Va eseguita PRIMA di creare
    l'indice unico corrispondente, altrimenti la creazione dell'indice
    fallirebbe su un database con duplicati preesistenti."""
    conn.execute(
        """
        DELETE FROM decision
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM decision
            GROUP BY substr(created_ts, 1, 10), target_date, COALESCE(capacity_unit_id, -1), decision_type
        )
        """
    )


def crea_schema(conn):
    """Crea tutte le tabelle del sistema, se non esistono gia'."""
    conn.executescript(SCHEMA_SQL)
    _deduplica_decision_esistenti(conn)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_dedup
            ON decision (substr(created_ts, 1, 10), target_date, COALESCE(capacity_unit_id, -1), decision_type)
        """
    )
    conn.commit()


def db_esiste(percorso_db=NOME_DB_DEFAULT):
    """Controlla se il file del database e' gia' presente su disco."""
    return Path(percorso_db).exists()


def sincronizza_capacity_units(conn, capacity_units):
    """Allinea la tabella capacity_unit al contenuto di config.json.

    Se una unita' (identificata dal "code") non esiste ancora viene creata,
    se esiste gia' viene aggiornata (nome e numero di unita' totali).
    In questo modo lo script di inizializzazione puo' essere rilanciato
    piu' volte senza creare duplicati.
    """
    for unita in capacity_units:
        conn.execute(
            """
            INSERT INTO capacity_unit (code, name, total_units)
            VALUES (:code, :name, :total_units)
            ON CONFLICT(code) DO UPDATE SET
                name = excluded.name,
                total_units = excluded.total_units
            """,
            unita,
        )
    conn.commit()


def mappa_capacity_units(conn):
    """Restituisce un dizionario {code: id} per tradurre i codici di
    configurazione (es. "CLA") negli id numerici usati nelle tabelle."""
    righe = conn.execute("SELECT id, code FROM capacity_unit").fetchall()
    return {riga["code"]: riga["id"] for riga in righe}


def elenco_capacity_units(conn):
    """Restituisce tutte le unita' di capacita', ordinate per id."""
    return conn.execute(
        "SELECT id, code, name, total_units FROM capacity_unit ORDER BY id"
    ).fetchall()
