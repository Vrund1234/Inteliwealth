-- =============================================================================
-- gold.client_address, gold.client_address_review, gold.client_bank
-- Live server: create the three tables in their FINAL shape
-- 2026-09-08
-- =============================================================================
--
-- Dev reached this shape in several steps -- client_address.sql and
-- client_bank.sql created the tables, then client_address_dedup_2026-09-07.sql
-- and client_bank_dedup_2026-09-07.sql added the generated keys and the unique
-- indexes, then client_address_review_drop_decision_2026-09-07.sql removed
-- three columns from the review queue. A live database that has none of these
-- tables yet does not need that history: this file is the end state, in one
-- transaction, with nothing to migrate.
--
-- WHICH FILE TO RUN
-- -----------------
--   * gold.client_address / gold.client_bank DO NOT exist on live -> run THIS
--     file. It is create-only and touches no rows.
--   * they DO exist and hold rows -> DO NOT run this file. Run the dedup
--     migrations instead; they collapse the duplicates that are already there,
--     which this file cannot do. Step 4 below would simply fail on them.
--
-- Check first, on the live connection:
--
--   SELECT table_name,
--          (SELECT count(*) FROM information_schema.columns c
--            WHERE c.table_schema='gold' AND c.table_name=t.table_name) AS cols
--   FROM information_schema.tables t
--   WHERE table_schema='gold'
--     AND table_name IN ('client_address','client_address_review','client_bank');
--
-- PREREQUISITES
-- -------------
--   * gold.clients must exist -- all three tables have a FK to gold.clients(id)
--     ON DELETE CASCADE.
--   * gold.clients.beneficiary_ac_no must be present too if you are deploying
--     the clients ETL alongside this -- see
--     gold_clients_beneficiary_ac_2026-09-07.sql.
--   * gen_random_uuid() is built into PostgreSQL 13+. On 12 or older, run
--     CREATE EXTENSION IF NOT EXISTS pgcrypto; first.
--
-- DEPLOY WITH THE LOADERS
-- -----------------------
-- etl_gold_client_address.py and etl_gold_client_bank.py must ship with this
-- file. The address loader refuses to run against a table without address_key
-- (etl_gold_client_address.py:828) and reports the entity FAILED rather than
-- silently re-inserting duplicates; the unique indexes in step 4 turn any
-- older loader's re-run into a unique violation.
--
-- RUN AS: a role that owns gold, e.g.
--   psql -d <dbname> -f sql_scripts/gold_client_tables_live_2026-09-08.sql
-- Execute the WHOLE file (DBeaver: Alt+X / Execute script). Executing one
-- statement at a time leaves the BEGIN open and commits nothing.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. gold.client_address
-- -----------------------------------------------------------------------------
--
-- address_key is GENERATED ALWAYS: Postgres computes it from line1/line2/line3
-- on every write, so no INSERT can supply a wrong key. The three lines are
-- concatenated with NO separator because different RTAs split the same text
-- across the columns at different points. pincode, city, state, country and
-- mobile_no are deliberately excluded -- they are enriched fields, and a key
-- containing a field that arrives later would insert a duplicate instead of
-- conflicting. The expression must stay character-for-character identical to
-- utils/address_key.py, which carries the full reasoning.

CREATE TABLE IF NOT EXISTS gold.client_address
(
    id              uuid NOT NULL DEFAULT gen_random_uuid(),
    organization_id uuid,
    client_id       uuid NOT NULL,
    seq             integer NOT NULL,
    address_type    character varying(15),
    is_main         boolean NOT NULL DEFAULT false,
    line1           character varying(255),
    line2           character varying(255),
    line3           character varying(255),
    area            character varying(120),
    city            character varying(120),
    state           character varying(120),
    country         character varying(60),
    pincode         character varying(10),
    mobile_no       character varying(20),
    whatsapp_no     character varying(20),
    address_key     text GENERATED ALWAYS AS (
                        UPPER(REGEXP_REPLACE(
                            COALESCE(line1,'') || COALESCE(line2,'') || COALESCE(line3,''),
                            '[^A-Za-z0-9]', '', 'g'))
                    ) STORED,
    needs_review    boolean NOT NULL DEFAULT false,
    is_deleted      boolean NOT NULL DEFAULT false,
    deleted_at      timestamp with time zone,
    created_by      uuid,
    updated_by      uuid,
    created_at      timestamp with time zone NOT NULL DEFAULT now(),
    updated_at      timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT pk_client_address PRIMARY KEY (id),
    CONSTRAINT uq_client_address_seq UNIQUE (client_id, seq),
    CONSTRAINT fk_client_address_client FOREIGN KEY (client_id)
        REFERENCES gold.clients (id) MATCH SIMPLE
        ON UPDATE NO ACTION ON DELETE CASCADE
)
TABLESPACE pg_default;

-- No-op on a table just created above; adds the key if live already has an
-- older gold.client_address without it.
ALTER TABLE gold.client_address
    ADD COLUMN IF NOT EXISTS address_key text
    GENERATED ALWAYS AS (
        UPPER(REGEXP_REPLACE(
            COALESCE(line1,'') || COALESCE(line2,'') || COALESCE(line3,''),
            '[^A-Za-z0-9]', '', 'g'))
    ) STORED;

CREATE INDEX IF NOT EXISTS ix_client_address_client_id
    ON gold.client_address USING btree (client_id ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS ix_client_address_is_deleted
    ON gold.client_address USING btree (is_deleted ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS ix_client_address_organization_id
    ON gold.client_address USING btree (organization_id ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True) TABLESPACE pg_default;


-- -----------------------------------------------------------------------------
-- 2. gold.client_address_review
-- -----------------------------------------------------------------------------
--
-- The queue for pairs that ARE the same address but cannot be proven so by
-- exact matching -- "RETI BUNDER" vs "RETI BUNDER ROAD".
--
-- THIS TABLE PUBLISHES A DETECTION, IT DOES NOT RECORD A DECISION. There is no
-- decision / decided_by / decided_at column on purpose: the app's gold
-- connection is read-only by construction, so nothing could ever fill them,
-- and a real decision needs organization_id, a users.id and RBAC, none of
-- which gold has. Gold detects; the app decides, in its own table.
--
-- Keyed on the two address_keys rather than on row ids: ids change when rows
-- are merged or reloaded, keys do not, so the app's decision stays attached to
-- the right pair across a rebuild. The CHECK stores them lo/hi so one pair is
-- one row whichever order the detector emits.

CREATE TABLE IF NOT EXISTS gold.client_address_review
(
    review_id     uuid NOT NULL DEFAULT gen_random_uuid(),
    client_id     uuid NOT NULL,
    address_key_a text NOT NULL,
    address_key_b text NOT NULL,
    similarity    numeric,
    match_type    varchar(10) NOT NULL,
    created_at    timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT pk_client_address_review PRIMARY KEY (review_id),
    CONSTRAINT uq_client_address_review UNIQUE (client_id, address_key_a, address_key_b),
    CONSTRAINT ck_client_address_review_order CHECK (address_key_a < address_key_b),
    CONSTRAINT ck_client_address_review_type CHECK (match_type IN ('PREFIX', 'FUZZY')),
    CONSTRAINT fk_client_address_review_client FOREIGN KEY (client_id)
        REFERENCES gold.clients (id) MATCH SIMPLE
        ON UPDATE NO ACTION ON DELETE CASCADE
)
TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS ix_client_address_review_client
    ON gold.client_address_review USING btree (client_id);


-- -----------------------------------------------------------------------------
-- 3. gold.client_bank
-- -----------------------------------------------------------------------------
--
-- account_key is the account number alone -- the bank's own identifier, unique
-- enough scoped to one client. ifsc, bank_branch, micr, bank_name,
-- account_type and bank_city are ABSENT on purpose: they are enriched fields,
-- not identity, and a key containing a field that arrives later inserts a
-- duplicate instead of matching the row already there. The LTRIM('0') is what
-- makes 00691060000052 and 0691060000052 one account; an all-zero number
-- normalises to '' and is treated as absent, so a placeholder can never merge
-- two clients' rows. Must stay identical to utils/account_key.py.

CREATE TABLE IF NOT EXISTS gold.client_bank
(
    id              uuid NOT NULL DEFAULT gen_random_uuid(),
    organization_id uuid,
    client_id       uuid NOT NULL,
    seq             integer NOT NULL,
    is_main         boolean NOT NULL DEFAULT false,
    bank_name       character varying(120),
    bank_branch     character varying(120),
    bank_address    character varying(255),
    account_number  character varying(30),
    account_type    character varying(20),
    bank_city       character varying(120),
    pincode         character varying(10),
    micr            character varying(15),
    ifsc            character varying(15),
    account_key     text GENERATED ALWAYS AS (
                        COALESCE(NULLIF(LTRIM(UPPER(REGEXP_REPLACE(
                            COALESCE(account_number,''), '[^A-Za-z0-9]', '', 'g')), '0'), ''), '')
                    ) STORED,
    needs_review    boolean NOT NULL DEFAULT false,
    is_deleted      boolean NOT NULL DEFAULT false,
    deleted_at      timestamp with time zone,
    created_by      uuid,
    updated_by      uuid,
    created_at      timestamp with time zone NOT NULL DEFAULT now(),
    updated_at      timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT pk_client_bank PRIMARY KEY (id),
    CONSTRAINT uq_client_bank_seq UNIQUE (client_id, seq),
    CONSTRAINT fk_client_bank_client FOREIGN KEY (client_id)
        REFERENCES gold.clients (id) MATCH SIMPLE
        ON UPDATE NO ACTION ON DELETE CASCADE
)
TABLESPACE pg_default;

ALTER TABLE gold.client_bank
    ADD COLUMN IF NOT EXISTS account_key text
    GENERATED ALWAYS AS (
        COALESCE(NULLIF(LTRIM(UPPER(REGEXP_REPLACE(
            COALESCE(account_number,''), '[^A-Za-z0-9]', '', 'g')), '0'), ''), '')
    ) STORED;

CREATE INDEX IF NOT EXISTS ix_client_bank_client_id
    ON gold.client_bank USING btree (client_id ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS ix_client_bank_is_deleted
    ON gold.client_bank USING btree (is_deleted ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS ix_client_bank_organization_id
    ON gold.client_bank USING btree (organization_id ASC NULLS LAST)
    WITH (fillfactor=100, deduplicate_items=True) TABLESPACE pg_default;


-- -----------------------------------------------------------------------------
-- 4. The constraints that make duplicates impossible
-- -----------------------------------------------------------------------------
--
-- Both are partial on is_deleted = false, so a soft-deleted row never blocks
-- the same address or account being re-inserted later. load_client_address()
-- and load_client_bank() match on the same predicates -- change one and the
-- other has to change with it.
--
-- account_key <> '' keeps a row with no usable account number out of the index
-- entirely, rather than letting several such rows collide on ''.
--
-- On an EMPTY table these build instantly. If either fails here with a unique
-- violation, live already holds duplicates: roll back and run the dedup
-- migrations instead -- this file has no collapse step.

CREATE UNIQUE INDEX IF NOT EXISTS uq_client_address_natural
    ON gold.client_address (client_id, address_key)
    WHERE is_deleted = false;

CREATE UNIQUE INDEX IF NOT EXISTS uq_client_bank_natural
    ON gold.client_bank (client_id, account_key)
    WHERE is_deleted = false AND account_key <> '';


-- -----------------------------------------------------------------------------
-- 5. Ownership
-- -----------------------------------------------------------------------------
--
-- The creating role owns these already. Uncomment only if live needs them
-- owned by something else -- see fix_object_ownership_2026-09-01.sql for the
-- role the app expects and why ownership (not just GRANTs) matters there.
--
-- ALTER TABLE IF EXISTS gold.client_address        OWNER TO postgres;
-- ALTER TABLE IF EXISTS gold.client_address_review OWNER TO postgres;
-- ALTER TABLE IF EXISTS gold.client_bank           OWNER TO postgres;

COMMIT;


-- =============================================================================
-- VERIFY (run after COMMIT)
-- =============================================================================
--
-- SELECT table_name, column_name, data_type, is_generated
-- FROM information_schema.columns
-- WHERE table_schema = 'gold'
--   AND table_name IN ('client_address','client_address_review','client_bank')
--   AND column_name IN ('address_key','account_key')
-- ORDER BY table_name;
--
-- SELECT indexname FROM pg_indexes
-- WHERE schemaname = 'gold'
--   AND tablename IN ('client_address','client_address_review','client_bank')
-- ORDER BY tablename, indexname;
--
-- Expect address_key and account_key with is_generated = ALWAYS, and both
-- uq_client_address_natural and uq_client_bank_natural present.
--
-- Then load, in this order:
--   cd python_scripts
--   venv/bin/python etl_gold_client_address.py
--   venv/bin/python etl_gold_client_bank.py
--   venv/bin/python detect_client_address_duplicates.py
