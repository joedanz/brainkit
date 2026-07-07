-- =============================================================================
-- Luxury Travel Agency Client Dossier  —  Hermes Agent structured memory DB
-- -----------------------------------------------------------------------------
-- Target: SQLite 3.35+ (Hermes ships FTS5; this also runs on Postgres with the
--         noted swaps in the header comments). This is the "structured DB" the
--         docs recommend over flat MEMORY.md for facts that must not hallucinate.
--
-- Place at:  ~/.hermes/data/dossier.db   (referenced by skills via the SQLite
--            MCP server or the terminal/execute_code tools).
--
-- Postgres port notes:
--   * TEXT PRIMARY KEY w/ uuid default -> use uuid DEFAULT gen_random_uuid()
--   * strftime('%Y-%m-%dT%H:%M:%fZ','now') -> now()
--   * JSON via json_object()/json()        -> jsonb_build_object()/to_jsonb()
--   * FTS5 virtual tables                   -> tsvector + GIN, or pg_trgm
--   * The updated_at / audit TRIGGERs port 1:1 (PL/pgSQL row triggers)
-- =============================================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- =============================================================================
-- HOUSEHOLDS  — the billing/relationship unit (a family office, a couple, a
--               principal + travelling staff). Constraints/preferences can
--               attach at household level and cascade to members.
-- =============================================================================
CREATE TABLE households (
    id                  TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    name                TEXT NOT NULL,                       -- "The Aldridge Family"
    relationship_tier   TEXT NOT NULL DEFAULT 'standard'
                          CHECK (relationship_tier IN ('prospect','standard','premier','private_office')),
    primary_advisor     TEXT,                                -- internal advisor name/handle
    primary_client_id   TEXT,                                -- FK added after clients table (deferred)
    annual_travel_budget_minor INTEGER CHECK (annual_travel_budget_minor IS NULL OR annual_travel_budget_minor >= 0),
    budget_currency     TEXT NOT NULL DEFAULT 'USD' CHECK (length(budget_currency) = 3),
    discretion_notes    TEXT,                                -- e.g. "never CC the assistant on pricing"
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- =============================================================================
-- CLIENTS  — individual travellers. Belongs to a household.
-- =============================================================================
CREATE TABLE clients (
    id                  TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    household_id        TEXT REFERENCES households(id) ON DELETE SET NULL ON UPDATE CASCADE,
    salutation          TEXT,                                -- Mr / Ms / Dr / Lord
    legal_full_name     TEXT NOT NULL,                       -- must match passport
    preferred_name      TEXT,                                -- "Call me Jamie"
    date_of_birth       TEXT,                                -- ISO 8601 date
    nationality         TEXT,                                -- ISO 3166-1 alpha-2
    primary_email       TEXT,
    primary_phone       TEXT,
    emergency_contact   TEXT,
    home_city           TEXT,
    status              TEXT NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active','dormant','vip','do_not_contact')),
    loyalty_programs    TEXT,                                -- JSON: [{"program":"BA Exec","tier":"Gold","number":"..."}]
    known_traveler_ids  TEXT,                                -- JSON: {"global_entry":"...","tsa_precheck":"..."}
    notes               TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- Deferred FK: a household's primary client must be a real client.
-- (SQLite enforces this lazily; the column was declared above.)
CREATE INDEX idx_clients_household ON clients(household_id);
CREATE INDEX idx_clients_email ON clients(primary_email);

-- =============================================================================
-- HARD_CONSTRAINTS  — non-negotiables. The agent must NEVER book around these.
--   These are the rows that protect you from the "compression drops constraints
--   ~30 turns" failure mode: they live in the DB, not the chat context.
-- =============================================================================
CREATE TABLE hard_constraints (
    id                  TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    -- exactly one of client_id / household_id should be set (scope of the rule)
    client_id           TEXT REFERENCES clients(id) ON DELETE CASCADE ON UPDATE CASCADE,
    household_id        TEXT REFERENCES households(id) ON DELETE CASCADE ON UPDATE CASCADE,
    category            TEXT NOT NULL
                          CHECK (category IN ('medical','dietary','allergy','accessibility',
                                              'security','legal','financial','religious','phobia')),
    constraint_text     TEXT NOT NULL,                       -- "Severe shellfish allergy — EpiPen carried"
    severity            TEXT NOT NULL DEFAULT 'critical'
                          CHECK (severity IN ('critical','high')),     -- hard = always serious
    enforcement         TEXT NOT NULL DEFAULT 'block'
                          CHECK (enforcement IN ('block','require_human_approval')),
    source              TEXT,                                -- "stated by client 2026-02-14"
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK (client_id IS NOT NULL OR household_id IS NOT NULL)
);
CREATE INDEX idx_hard_constraints_client ON hard_constraints(client_id) WHERE client_id IS NOT NULL;
CREATE INDEX idx_hard_constraints_household ON hard_constraints(household_id) WHERE household_id IS NOT NULL;

-- =============================================================================
-- SOFT_PREFERENCES  — weighted likes/dislikes. The agent optimizes toward these
--   but may trade them off. Weight drives ranking when options conflict.
-- =============================================================================
CREATE TABLE soft_preferences (
    id                  TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    client_id           TEXT REFERENCES clients(id) ON DELETE CASCADE ON UPDATE CASCADE,
    household_id        TEXT REFERENCES households(id) ON DELETE CASCADE ON UPDATE CASCADE,
    category            TEXT NOT NULL
                          CHECK (category IN ('airline','cabin','seat','hotel_brand','room_type',
                                              'bed','floor','view','cuisine','wine','transfer',
                                              'destination','activity','pace','spa','climate','other')),
    preference_text     TEXT NOT NULL,                       -- "High floor, away from elevators"
    sentiment           TEXT NOT NULL DEFAULT 'prefer'
                          CHECK (sentiment IN ('prefer','avoid')),
    weight              INTEGER NOT NULL DEFAULT 3 CHECK (weight BETWEEN 1 AND 5),  -- 5 = strong
    source              TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK (client_id IS NOT NULL OR household_id IS NOT NULL)
);
CREATE INDEX idx_soft_prefs_client ON soft_preferences(client_id) WHERE client_id IS NOT NULL;
CREATE INDEX idx_soft_prefs_household ON soft_preferences(household_id) WHERE household_id IS NOT NULL;

-- =============================================================================
-- SUPPLIERS  — airlines, hotels, DMCs, villas, yachts, restaurants, etc.
-- =============================================================================
CREATE TABLE suppliers (
    id                  TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    name                TEXT NOT NULL,
    supplier_type       TEXT NOT NULL
                          CHECK (supplier_type IN ('airline','hotel','dmc','villa','yacht',
                                                   'transfer','restaurant','experience','guide',
                                                   'insurance','tour_operator','other')),
    country             TEXT,                                -- ISO 3166-1 alpha-2
    contact_name        TEXT,
    contact_email       TEXT,
    contact_phone       TEXT,
    preferred_partner   INTEGER NOT NULL DEFAULT 0 CHECK (preferred_partner IN (0,1)),  -- consortium/Virtuoso etc.
    default_commission_rate REAL CHECK (default_commission_rate IS NULL OR (default_commission_rate >= 0 AND default_commission_rate <= 1)),
    payment_terms_days  INTEGER,                             -- net terms for commission
    notes               TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_suppliers_type ON suppliers(supplier_type);

-- =============================================================================
-- SUPPLIER_RELIABILITY_SCORES  — periodic rollups powering supplier ranking.
--   One row per supplier per evaluation period; current() view exposes latest.
-- =============================================================================
CREATE TABLE supplier_reliability_scores (
    id                  TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    supplier_id         TEXT NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE ON UPDATE CASCADE,
    period_start        TEXT NOT NULL,                       -- ISO date
    period_end          TEXT NOT NULL,
    score               REAL NOT NULL CHECK (score BETWEEN 0 AND 100),
    sample_size         INTEGER NOT NULL DEFAULT 0 CHECK (sample_size >= 0),
    on_time_rate        REAL CHECK (on_time_rate IS NULL OR (on_time_rate BETWEEN 0 AND 1)),
    complaint_rate      REAL CHECK (complaint_rate IS NULL OR (complaint_rate BETWEEN 0 AND 1)),
    last_incident_at    TEXT,
    methodology         TEXT,                                -- "weighted: 50% on-time, 30% complaints, 20% recovery"
    computed_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_reliability_supplier ON supplier_reliability_scores(supplier_id, period_end DESC);

CREATE VIEW v_supplier_current_reliability AS
SELECT s.id AS supplier_id, s.name, s.supplier_type,
       r.score, r.sample_size, r.on_time_rate, r.complaint_rate, r.computed_at
FROM suppliers s
LEFT JOIN supplier_reliability_scores r
  ON r.id = (SELECT id FROM supplier_reliability_scores
             WHERE supplier_id = s.id ORDER BY period_end DESC, computed_at DESC LIMIT 1);

-- =============================================================================
-- TRIPS  — a single itinerary/engagement for a household.
-- =============================================================================
CREATE TABLE trips (
    id                  TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    household_id        TEXT NOT NULL REFERENCES households(id) ON DELETE RESTRICT ON UPDATE CASCADE,
    lead_client_id      TEXT REFERENCES clients(id) ON DELETE SET NULL ON UPDATE CASCADE,
    reference           TEXT UNIQUE,                         -- human ref e.g. "ALD-2026-MALDIVES"
    title               TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'inquiry'
                          CHECK (status IN ('inquiry','proposal','confirmed','in_progress',
                                            'completed','cancelled','on_hold')),
    primary_destination TEXT,
    start_date          TEXT,                                -- ISO date
    end_date            TEXT,
    party_size          INTEGER CHECK (party_size IS NULL OR party_size > 0),
    budget_minor        INTEGER CHECK (budget_minor IS NULL OR budget_minor >= 0),  -- store in minor units
    budget_currency     TEXT NOT NULL DEFAULT 'USD' CHECK (length(budget_currency) = 3),
    occasion            TEXT,                                -- "30th anniversary"
    notes               TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);
CREATE INDEX idx_trips_household ON trips(household_id);
CREATE INDEX idx_trips_status ON trips(status);
CREATE INDEX idx_trips_dates ON trips(start_date, end_date);

-- =============================================================================
-- BOOKINGS  — individual reserved components within a trip.
-- =============================================================================
CREATE TABLE bookings (
    id                  TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    trip_id             TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE ON UPDATE CASCADE,
    supplier_id         TEXT REFERENCES suppliers(id) ON DELETE SET NULL ON UPDATE CASCADE,
    booking_type        TEXT NOT NULL
                          CHECK (booking_type IN ('flight','hotel','villa','yacht','cruise',
                                                  'transfer','rail','restaurant','experience',
                                                  'insurance','other')),
    confirmation_number TEXT,
    record_locator      TEXT,                                -- GDS PNR
    status              TEXT NOT NULL DEFAULT 'held'
                          CHECK (status IN ('quoted','held','confirmed','ticketed',
                                            'changed','cancelled','completed')),
    start_datetime      TEXT,
    end_datetime        TEXT,
    currency            TEXT NOT NULL DEFAULT 'USD' CHECK (length(currency) = 3),
    gross_minor         INTEGER NOT NULL DEFAULT 0 CHECK (gross_minor >= 0),   -- client-facing total
    net_minor           INTEGER CHECK (net_minor IS NULL OR net_minor >= 0),   -- supplier net
    commission_rate     REAL CHECK (commission_rate IS NULL OR (commission_rate >= 0 AND commission_rate <= 1)),
    commission_minor    INTEGER CHECK (commission_minor IS NULL OR commission_minor >= 0),
    deposit_due_date    TEXT,
    balance_due_date    TEXT,
    booked_by           TEXT,                                -- advisor or 'hermes-agent'
    notes               TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_bookings_trip ON bookings(trip_id);
CREATE INDEX idx_bookings_supplier ON bookings(supplier_id);
CREATE INDEX idx_bookings_status ON bookings(status);
CREATE INDEX idx_bookings_balance_due ON bookings(balance_due_date) WHERE balance_due_date IS NOT NULL;

-- =============================================================================
-- COMMISSION_LEDGER  — money owed to the agency, tracked from projection to cash.
-- =============================================================================
CREATE TABLE commission_ledger (
    id                  TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    booking_id          TEXT REFERENCES bookings(id) ON DELETE SET NULL ON UPDATE CASCADE,
    trip_id             TEXT REFERENCES trips(id) ON DELETE SET NULL ON UPDATE CASCADE,
    supplier_id         TEXT REFERENCES suppliers(id) ON DELETE SET NULL ON UPDATE CASCADE,
    amount_minor        INTEGER NOT NULL CHECK (amount_minor >= 0),
    currency            TEXT NOT NULL DEFAULT 'USD' CHECK (length(currency) = 3),
    status              TEXT NOT NULL DEFAULT 'projected'
                          CHECK (status IN ('projected','invoiced','received','reconciled','written_off','disputed')),
    expected_date       TEXT,
    invoiced_date       TEXT,
    received_date       TEXT,
    invoice_reference   TEXT,
    notes               TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_commission_status ON commission_ledger(status);
CREATE INDEX idx_commission_expected ON commission_ledger(expected_date) WHERE status IN ('projected','invoiced');
CREATE INDEX idx_commission_supplier ON commission_ledger(supplier_id);

-- =============================================================================
-- DOCUMENTS  — passports, visas, IDs, insurance, vouchers, contracts.
--   doc_number stays in an app-encrypted blob in production; expiry/issue dates
--   are kept in clear so the agent can run renewal-watch cron jobs.
-- =============================================================================
CREATE TABLE documents (
    id                  TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    client_id           TEXT REFERENCES clients(id) ON DELETE CASCADE ON UPDATE CASCADE,
    trip_id             TEXT REFERENCES trips(id) ON DELETE SET NULL ON UPDATE CASCADE,
    doc_type            TEXT NOT NULL
                          CHECK (doc_type IN ('passport','visa','national_id','drivers_license',
                                              'global_entry','insurance','vaccination',
                                              'loyalty_card','voucher','contract','other')),
    doc_number_enc      TEXT,                                -- ciphertext; NEVER store plaintext PII here
    issuing_country     TEXT,                                -- ISO 3166-1 alpha-2
    issue_date          TEXT,
    expiry_date         TEXT,                                -- drives renewal/expiry cron checks
    file_uri            TEXT,                                -- Drive/Supabase storage pointer
    is_verified         INTEGER NOT NULL DEFAULT 0 CHECK (is_verified IN (0,1)),
    notes               TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK (client_id IS NOT NULL OR trip_id IS NOT NULL)
);
CREATE INDEX idx_documents_client ON documents(client_id);
CREATE INDEX idx_documents_expiry ON documents(doc_type, expiry_date) WHERE expiry_date IS NOT NULL;

-- Convenience view: passports/visas approaching expiry (renewal watch).
CREATE VIEW v_documents_expiring_soon AS
SELECT d.id, d.client_id, c.legal_full_name, d.doc_type, d.issuing_country,
       d.expiry_date,
       CAST(julianday(d.expiry_date) - julianday('now') AS INTEGER) AS days_until_expiry
FROM documents d
JOIN clients c ON c.id = d.client_id
WHERE d.expiry_date IS NOT NULL
  AND d.doc_type IN ('passport','visa','national_id','global_entry','insurance')
  AND julianday(d.expiry_date) - julianday('now') <= 270   -- ~9 months
ORDER BY d.expiry_date ASC;

-- =============================================================================
-- AUDIT_LOG  — append-only trail. Critical given the documented approval-gate
--   violation rate: every mutation to sensitive tables is recorded with before/
--   after snapshots so a human can review what the agent actually changed.
-- =============================================================================
CREATE TABLE audit_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    actor               TEXT NOT NULL DEFAULT 'hermes-agent', -- 'hermes-agent' | advisor handle | 'system'
    action              TEXT NOT NULL,                        -- 'insert' | 'update' | 'delete' | custom
    entity_table        TEXT NOT NULL,
    entity_id           TEXT,
    before_json         TEXT,                                 -- NULL on insert
    after_json          TEXT,                                 -- NULL on delete
    reason              TEXT,                                 -- agent-supplied rationale
    session_ref         TEXT,                                 -- Hermes session id, if available
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_audit_entity ON audit_log(entity_table, entity_id);
CREATE INDEX idx_audit_created ON audit_log(created_at);

-- =============================================================================
-- FTS5  — free-text recall over client/trip notes, mirroring Hermes' own
--   FTS5 session store so skills can `MATCH` against the dossier cheaply.
-- =============================================================================
CREATE VIRTUAL TABLE dossier_search USING fts5(
    entity_table,
    entity_id UNINDEXED,
    title,
    body,
    tokenize = 'porter unicode61'
);

-- -----------------------------------------------------------------------------
-- TRIGGERS: keep updated_at fresh
-- -----------------------------------------------------------------------------
CREATE TRIGGER trg_households_updated AFTER UPDATE ON households
BEGIN UPDATE households SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = NEW.id; END;
CREATE TRIGGER trg_clients_updated AFTER UPDATE ON clients
BEGIN UPDATE clients SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = NEW.id; END;
CREATE TRIGGER trg_hard_constraints_updated AFTER UPDATE ON hard_constraints
BEGIN UPDATE hard_constraints SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = NEW.id; END;
CREATE TRIGGER trg_soft_prefs_updated AFTER UPDATE ON soft_preferences
BEGIN UPDATE soft_preferences SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = NEW.id; END;
CREATE TRIGGER trg_suppliers_updated AFTER UPDATE ON suppliers
BEGIN UPDATE suppliers SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = NEW.id; END;
CREATE TRIGGER trg_trips_updated AFTER UPDATE ON trips
BEGIN UPDATE trips SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = NEW.id; END;
CREATE TRIGGER trg_bookings_updated AFTER UPDATE ON bookings
BEGIN UPDATE bookings SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = NEW.id; END;
CREATE TRIGGER trg_commission_updated AFTER UPDATE ON commission_ledger
BEGIN UPDATE commission_ledger SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = NEW.id; END;
CREATE TRIGGER trg_documents_updated AFTER UPDATE ON documents
BEGIN UPDATE documents SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = NEW.id; END;

-- -----------------------------------------------------------------------------
-- TRIGGERS: audit trail on sensitive tables (bookings, commission, documents,
--           hard_constraints). before/after captured as JSON. WHEN-guards keep
--           one audit row per real change (the updated_at self-UPDATE re-fires
--           sibling triggers since recursive_triggers is OFF by default).
-- -----------------------------------------------------------------------------
CREATE TRIGGER trg_audit_bookings_ins AFTER INSERT ON bookings
BEGIN
  INSERT INTO audit_log(action, entity_table, entity_id, after_json)
  VALUES ('insert','bookings', NEW.id,
          json_object('id',NEW.id,'trip_id',NEW.trip_id,'status',NEW.status,
                      'gross_minor',NEW.gross_minor,'commission_minor',NEW.commission_minor));
END;
CREATE TRIGGER trg_audit_bookings_upd AFTER UPDATE ON bookings
WHEN OLD.status IS NOT NEW.status
  OR OLD.gross_minor IS NOT NEW.gross_minor
  OR OLD.net_minor IS NOT NEW.net_minor
  OR OLD.commission_minor IS NOT NEW.commission_minor
  OR OLD.confirmation_number IS NOT NEW.confirmation_number
BEGIN
  INSERT INTO audit_log(action, entity_table, entity_id, before_json, after_json)
  VALUES ('update','bookings', NEW.id,
          json_object('status',OLD.status,'gross_minor',OLD.gross_minor,'commission_minor',OLD.commission_minor),
          json_object('status',NEW.status,'gross_minor',NEW.gross_minor,'commission_minor',NEW.commission_minor));
END;
CREATE TRIGGER trg_audit_bookings_del AFTER DELETE ON bookings
BEGIN
  INSERT INTO audit_log(action, entity_table, entity_id, before_json)
  VALUES ('delete','bookings', OLD.id,
          json_object('id',OLD.id,'trip_id',OLD.trip_id,'status',OLD.status,'gross_minor',OLD.gross_minor));
END;

CREATE TRIGGER trg_audit_commission_upd AFTER UPDATE ON commission_ledger
WHEN OLD.status IS NOT NEW.status
  OR OLD.amount_minor IS NOT NEW.amount_minor
  OR OLD.received_date IS NOT NEW.received_date
  OR OLD.invoiced_date IS NOT NEW.invoiced_date
BEGIN
  INSERT INTO audit_log(action, entity_table, entity_id, before_json, after_json)
  VALUES ('update','commission_ledger', NEW.id,
          json_object('status',OLD.status,'amount_minor',OLD.amount_minor,'received_date',OLD.received_date),
          json_object('status',NEW.status,'amount_minor',NEW.amount_minor,'received_date',NEW.received_date));
END;

CREATE TRIGGER trg_audit_documents_ins AFTER INSERT ON documents
BEGIN
  INSERT INTO audit_log(action, entity_table, entity_id, after_json)
  VALUES ('insert','documents', NEW.id,
          json_object('id',NEW.id,'client_id',NEW.client_id,'doc_type',NEW.doc_type,'expiry_date',NEW.expiry_date));
END;
CREATE TRIGGER trg_audit_documents_upd AFTER UPDATE ON documents
WHEN OLD.doc_type IS NOT NEW.doc_type
  OR OLD.expiry_date IS NOT NEW.expiry_date
  OR OLD.is_verified IS NOT NEW.is_verified
  OR OLD.doc_number_enc IS NOT NEW.doc_number_enc
BEGIN
  INSERT INTO audit_log(action, entity_table, entity_id, before_json, after_json)
  VALUES ('update','documents', NEW.id,
          json_object('doc_type',OLD.doc_type,'expiry_date',OLD.expiry_date,'is_verified',OLD.is_verified),
          json_object('doc_type',NEW.doc_type,'expiry_date',NEW.expiry_date,'is_verified',NEW.is_verified));
END;

CREATE TRIGGER trg_audit_hard_constraints_ins AFTER INSERT ON hard_constraints
BEGIN
  INSERT INTO audit_log(action, entity_table, entity_id, after_json)
  VALUES ('insert','hard_constraints', NEW.id,
          json_object('id',NEW.id,'client_id',NEW.client_id,'category',NEW.category,
                      'constraint_text',NEW.constraint_text,'severity',NEW.severity));
END;
CREATE TRIGGER trg_audit_hard_constraints_del AFTER DELETE ON hard_constraints
BEGIN
  INSERT INTO audit_log(action, entity_table, entity_id, before_json, reason)
  VALUES ('delete','hard_constraints', OLD.id,
          json_object('id',OLD.id,'client_id',OLD.client_id,'category',OLD.category,
                      'constraint_text',OLD.constraint_text),
          'HARD CONSTRAINT REMOVED — review required');
END;

-- -----------------------------------------------------------------------------
-- TRIGGERS: keep FTS index in sync with client + trip notes
-- -----------------------------------------------------------------------------
CREATE TRIGGER trg_fts_clients_ins AFTER INSERT ON clients
BEGIN
  INSERT INTO dossier_search(entity_table, entity_id, title, body)
  VALUES ('clients', NEW.id, NEW.legal_full_name, coalesce(NEW.notes,''));
END;
CREATE TRIGGER trg_fts_clients_upd AFTER UPDATE ON clients
WHEN NEW.legal_full_name IS NOT OLD.legal_full_name OR NEW.notes IS NOT OLD.notes
BEGIN
  DELETE FROM dossier_search WHERE entity_table='clients' AND entity_id=NEW.id;
  INSERT INTO dossier_search(entity_table, entity_id, title, body)
  VALUES ('clients', NEW.id, NEW.legal_full_name, coalesce(NEW.notes,''));
END;
CREATE TRIGGER trg_fts_clients_del AFTER DELETE ON clients
BEGIN
  DELETE FROM dossier_search WHERE entity_table='clients' AND entity_id=OLD.id;
END;

CREATE TRIGGER trg_fts_trips_ins AFTER INSERT ON trips
BEGIN
  INSERT INTO dossier_search(entity_table, entity_id, title, body)
  VALUES ('trips', NEW.id, NEW.title, coalesce(NEW.notes,'') || ' ' || coalesce(NEW.primary_destination,''));
END;
CREATE TRIGGER trg_fts_trips_upd AFTER UPDATE ON trips
WHEN NEW.title IS NOT OLD.title OR NEW.notes IS NOT OLD.notes
  OR NEW.primary_destination IS NOT OLD.primary_destination
BEGIN
  DELETE FROM dossier_search WHERE entity_table='trips' AND entity_id=NEW.id;
  INSERT INTO dossier_search(entity_table, entity_id, title, body)
  VALUES ('trips', NEW.id, NEW.title, coalesce(NEW.notes,'') || ' ' || coalesce(NEW.primary_destination,''));
END;
CREATE TRIGGER trg_fts_trips_del AFTER DELETE ON trips
BEGIN
  DELETE FROM dossier_search WHERE entity_table='trips' AND entity_id=OLD.id;
END;

-- =============================================================================
-- 1099 / HOST-NETWORK MULTI-TENANT OWNERSHIP ADDENDUM  (confirmed model)
-- -----------------------------------------------------------------------------
-- Independent-contractor advisors OWN their book. The host runs the platform but
-- must NOT read an advisor's client relationship data in plaintext. Ownership +
-- scope are enforced at the MCP/ZeroID layer (row-level); the columns/views below
-- are the in-DB backstop.
--   * PREFERRED PROD: run each advisor's dossier as a SEPARATE per-tenant DB
--     encrypted with the advisor's own key (BYOK / SQLCipher) -> this file = one
--     tenant. The columns below ALSO support a pooled DB with query-layer scope.
-- =============================================================================

CREATE TABLE advisors (
    id            TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    handle        TEXT NOT NULL UNIQUE,             -- maps to ~/.hermes/profiles/<handle>/
    legal_name    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','departing','departed')),
    engaged_as    TEXT NOT NULL DEFAULT '1099' CHECK (engaged_as IN ('1099','w2')),
    tenant_key_id TEXT,                              -- KMS/secret ref for this advisor's BYOK key
    joined_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    departed_at   TEXT
);

-- Ownership (the IC) is distinct from who is covering right now. owner_advisor_id
-- changes ONLY on a contractual book transfer, never on coverage.
ALTER TABLE households ADD COLUMN owner_advisor_id    TEXT REFERENCES advisors(id);
ALTER TABLE households ADD COLUMN covering_advisor_id TEXT REFERENCES advisors(id);  -- hot-handoff / break-glass
ALTER TABLE suppliers  ADD COLUMN brought_by_advisor_id TEXT REFERENCES advisors(id); -- advisor-brought => return-on-exit
ALTER TABLE suppliers  ADD COLUMN is_firm_negotiated  INTEGER NOT NULL DEFAULT 0 CHECK (is_firm_negotiated IN (0,1));

-- Per-advisor scope: a personal agent's MCP token resolves to one advisor_id and
-- may ONLY see households it owns or is actively covering. Host/admin role is
-- deliberately NOT granted plaintext SELECT here in the IC model. (bind :me)
CREATE VIEW v_my_households AS
SELECT h.* FROM households h;   -- enforce at query layer: WHERE owner_advisor_id = :me OR covering_advisor_id = :me

-- Opt-in fact-promotion: candidate facts a background agent SUGGESTS for shared
-- knowledge. Nothing promotes on silence (default 'pending' -> expires).
CREATE TABLE fact_promotion_queue (
    id            TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    advisor_id    TEXT NOT NULL REFERENCES advisors(id),
    proposed_fact TEXT NOT NULL,
    source_ref    TEXT,
    status        TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','approved','rejected','expired')),  -- never auto-approves
    decided_by    TEXT,
    decided_at    TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- One-click portability backstop: an advisor's full book as portable JSON
-- (facts + rapport). Implemented as an app export job; this is the canonical join.
CREATE VIEW v_advisor_export AS
SELECT 'application export job: households+clients+hard_constraints+soft_preferences+trips+notes WHERE owner_advisor_id = :me' AS export_spec;

-- NOTE: audit_log.actor carries the advisor handle; every cross-advisor or host
-- read MUST be written here AND mirrored to an external, append-only,
-- principal-uneditable anchor (object-lock storage / transparency log).
