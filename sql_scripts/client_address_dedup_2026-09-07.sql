-- =============================================================================
-- gold.client_address: make duplicate addresses impossible
-- 2026-09-07
-- =============================================================================
--
-- Before this file, gold.client_address holds 1186 rows of which 502 are
-- formatting variants of an address already present for the same client --
-- 41% of the table. The dedup key in etl_gold_client_address.py compares raw
-- strings, so one physical address survives once per RTA feed spelling.
--
-- This migration:
--
--   1. adds gold.client_address.address_key, GENERATED from the three address
--      lines, matching utils/address_key.py exactly;
--   2. collapses the existing duplicates, merging each group's field values
--      into the most complete row and soft-deleting the rest;
--   3. re-asserts the one-is_main-per-client invariant the collapse can break;
--   4. adds the UNIQUE index that makes step 2 unrepeatable;
--   5. creates gold.client_address_review, the queue for pairs that are the
--      same address but cannot be proven so by exact matching.
--
-- ORDER MATTERS. The unique index cannot build while the duplicates are there,
-- and etl_gold_client_address.py must be deployed WITH this file: an index
-- without the matching loader turns every re-run into a unique violation and
-- gold_loader.py:697 will report client_address FAILED.
--
-- Nothing here hard-deletes. Losing rows are is_deleted = true, so the whole
-- collapse is reversible for as long as you keep them.
--
-- Inspect what will be collapsed before applying:
--   psql ... -f sql_scripts/check_duplicate_client_address.sql
--   psql ... -f sql_scripts/check_similar_client_address.sql
-- =============================================================================

-- =============================================================================
-- TWO WAYS TO RUN THIS
-- =============================================================================
--
-- (a) IN PLACE -- run the file as-is. Step 2 collapses the 502 existing
--     duplicates into 684 rows, soft-deleting the losers so the merge stays
--     reversible. Use this once anything downstream holds a
--     gold.client_address.id.
--
-- (b) TRUNCATE AND REBUILD -- uncomment the TRUNCATE below, run the file, then
--     rebuild from silver:
--
--         cd python_scripts
--         venv/bin/python etl_gold_client_address.py
--         venv/bin/python detect_client_address_duplicates.py
--
--     Cleaner while it is still safe, and it is safe TODAY specifically
--     because nothing consumes these ids yet: no table in this database has a
--     foreign key pointing at gold.client_address (checked 2026-09-07), and
--     the app backend does not sync the entity at all -- gold_sync/
--     gold_models.py defines no GoldClientAddress, and sync_all() does not
--     call one. Every row is derived from silver.investor_master, all 1186
--     share a single created_at, and none has ever been hand-edited
--     (updated_at = created_at everywhere, needs_review nowhere).
--
--     Once the backend starts syncing addresses this stops being true and (a)
--     becomes the only correct option.
--
-- Either way the outcome is the same 684 rows. Steps 2 and 3 simply match
-- nothing on an emptied table.
-- =============================================================================

BEGIN;

-- Uncomment for route (b). Deliberately not the default: it is destructive,
-- and it is only equivalent while the conditions above still hold.
--
-- TRUNCATE gold.client_address;


-- -----------------------------------------------------------------------------
-- 1. The generated key
-- -----------------------------------------------------------------------------
--
-- GENERATED ALWAYS means no INSERT can supply a wrong key: Postgres computes
-- it from line1/line2/line3 on every write. That is what makes this a
-- guarantee rather than a convention the next script may forget.
--
-- The three lines are concatenated with NO separator because different RTAs
-- split the same text across the columns at different points. pincode, city,
-- state, country and mobile_no are deliberately absent -- they are enriched
-- fields, and a key containing a field that arrives later would insert a
-- duplicate instead of conflicting. utils/address_key.py carries the full
-- reasoning and the measurements behind each exclusion.

ALTER TABLE gold.client_address
    ADD COLUMN IF NOT EXISTS address_key text
    GENERATED ALWAYS AS (
        UPPER(REGEXP_REPLACE(
            COALESCE(line1,'') || COALESCE(line2,'') || COALESCE(line3,''),
            '[^A-Za-z0-9]', '', 'g'))
    ) STORED;


-- -----------------------------------------------------------------------------
-- 2. Collapse the existing duplicates
-- -----------------------------------------------------------------------------
--
-- Survivor selection is by COMPLETENESS, not by lowest seq. "Keep first" is
-- wrong here: for PAN AIFPD4074J the seq 1 row carries a mobile the others
-- lack, but for AGZPP1978M the is_main row is the one with `state` BLANK while
-- the rows being discarded have 'Gujarat'. Ranking by how many enriched fields
-- are populated keeps the better row in both cases; is_main and then seq break
-- ties so the choice is deterministic.

CREATE TEMP TABLE _dedup_rank ON COMMIT DROP AS
SELECT
    id, client_id, address_key, seq, is_main,
    area, city, state, country, pincode, mobile_no, whatsapp_no, address_type,
    ROW_NUMBER() OVER (
        PARTITION BY client_id, address_key
        ORDER BY
            ( (area        IS NOT NULL)::int
            + (city        IS NOT NULL)::int
            + (state       IS NOT NULL)::int
            + (country     IS NOT NULL)::int
            + (pincode     IS NOT NULL)::int
            + (mobile_no   IS NOT NULL)::int
            + (whatsapp_no IS NOT NULL)::int ) DESC,
            is_main DESC,
            seq ASC,
            id ASC
    ) AS rn
FROM gold.client_address
WHERE is_deleted = false
  AND address_key <> '';          -- a row with no address text is not a duplicate of anything

-- Each group's losers, reduced to one non-null value per field. The value is
-- taken from the most complete loser (lowest rn) that has one, so a merge
-- never picks a blank over a populated cell.
CREATE TEMP TABLE _dedup_donor ON COMMIT DROP AS
SELECT
    client_id,
    address_key,
    (array_agg(area        ORDER BY rn) FILTER (WHERE area        IS NOT NULL))[1] AS area,
    (array_agg(city        ORDER BY rn) FILTER (WHERE city        IS NOT NULL))[1] AS city,
    (array_agg(state       ORDER BY rn) FILTER (WHERE state       IS NOT NULL))[1] AS state,
    (array_agg(country     ORDER BY rn) FILTER (WHERE country     IS NOT NULL))[1] AS country,
    (array_agg(pincode     ORDER BY rn) FILTER (WHERE pincode     IS NOT NULL))[1] AS pincode,
    (array_agg(mobile_no   ORDER BY rn) FILTER (WHERE mobile_no   IS NOT NULL))[1] AS mobile_no,
    (array_agg(whatsapp_no ORDER BY rn) FILTER (WHERE whatsapp_no IS NOT NULL))[1] AS whatsapp_no,
    (array_agg(address_type ORDER BY rn) FILTER (WHERE address_type IS NOT NULL))[1] AS address_type,
    bool_or(is_main) AS any_main
FROM _dedup_rank
WHERE rn > 1
GROUP BY client_id, address_key;

-- Enrich the survivor from its losers. COALESCE(survivor, donor) fills blanks
-- only: a value the survivor already has is never replaced.
UPDATE gold.client_address a
SET area         = COALESCE(a.area,         d.area),
    city         = COALESCE(a.city,         d.city),
    state        = COALESCE(a.state,        d.state),
    country      = COALESCE(a.country,      d.country),
    pincode      = COALESCE(a.pincode,      d.pincode),
    mobile_no    = COALESCE(a.mobile_no,    d.mobile_no),
    whatsapp_no  = COALESCE(a.whatsapp_no,  d.whatsapp_no),
    address_type = COALESCE(a.address_type, d.address_type),
    is_main      = a.is_main OR d.any_main,
    updated_at   = now()
FROM _dedup_rank r
JOIN _dedup_donor d
  ON d.client_id = r.client_id AND d.address_key = r.address_key
WHERE a.id = r.id
  AND r.rn = 1;

-- Retire the losers. Soft delete: the collapse stays reversible.
UPDATE gold.client_address
SET is_deleted = true,
    deleted_at = now(),
    updated_at = now()
WHERE id IN (SELECT id FROM _dedup_rank WHERE rn > 1);


-- -----------------------------------------------------------------------------
-- 3. Restore one main address per client
-- -----------------------------------------------------------------------------
--
-- The collapse can leave a client with two mains (survivors of different
-- groups that were each main) or none (if is_main sat only on a soft-deleted
-- row -- step 2 moves it to the survivor, so this is the belt to that braces).
-- The table has 593 clients and 593 mains today; this keeps it that way.

WITH pick AS (
    SELECT DISTINCT ON (client_id) client_id, id
    FROM gold.client_address
    WHERE is_deleted = false
    ORDER BY client_id, is_main DESC, seq ASC, id ASC
)
UPDATE gold.client_address a
SET is_main = (a.id = pick.id),
    updated_at = CASE WHEN a.is_main <> (a.id = pick.id) THEN now() ELSE a.updated_at END
FROM pick
WHERE a.client_id = pick.client_id
  AND a.is_deleted = false
  AND a.is_main <> (a.id = pick.id);


-- -----------------------------------------------------------------------------
-- 4. The constraint
-- -----------------------------------------------------------------------------
--
-- Partial on is_deleted = false so a soft-deleted row never blocks the same
-- address being re-inserted later. This is what load_client_address()'s
-- ON CONFLICT target resolves against -- the predicate must match there too.

CREATE UNIQUE INDEX IF NOT EXISTS uq_client_address_natural
    ON gold.client_address (client_id, address_key)
    WHERE is_deleted = false;


-- -----------------------------------------------------------------------------
-- 5. The review queue
-- -----------------------------------------------------------------------------
--
-- For pairs that ARE the same address but cannot be proven so by exact
-- matching -- "RETI BUNDER" vs "RETI BUNDER ROAD", "SANSKRUT BUNGL" vs
-- "SANSKRUT BUNGLOWS". 24 such pairs exist today.
--
-- THIS TABLE PUBLISHES A DETECTION, IT DOES NOT RECORD A DECISION.
--
-- There is no decision / decided_by / decided_at column, and that is
-- deliberate. The reviewers are app users -- an organization, a distributor,
-- a relationship manager -- and the app CANNOT write here: its gold
-- connection is read-only by construction
-- (app/modules/gold_sync/gold_db.py, "we never write to it"; zero
-- gold_db.add/commit calls in the whole module). A decision column in this
-- schema would be one nothing is able to fill.
--
-- The decision also needs things gold does not have: organization_id to scope
-- a pair to one tenant, a real users.id for who decided, and the RBAC and
-- audit trail around both. Those live in the app, which already runs this
-- exact pattern for family_import_row.
--
-- So the split is by ownership. Gold DETECTS, because the comparison depends
-- on address_key and utils/address_key.py is its only definition -- computing
-- it in the backend would mean reimplementing that normalisation in another
-- language in another repo, which is the drift the GENERATED column exists to
-- prevent. The app DECIDES, in its own client_address_review table.
--
-- Consequence, and it is the correct trade: gold never learns the decision,
-- so it re-detects the same pairs every run. ON CONFLICT DO NOTHING makes
-- that a no-op and the table stays at its natural size. The alternative --
-- gold reaching into the app for decisions -- inverts the dependency far more
-- damagingly than this costs.
--
-- Keyed on the two address_keys rather than on row ids: ids change when rows
-- are merged or reloaded, keys do not, so the app's decision stays attached
-- to the right pair across a rebuild. Storing them lo/hi (enforced by the
-- CHECK) makes one pair one row whichever order the detector emits.

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

ALTER TABLE IF EXISTS gold.client_address_review OWNER to postgres;

CREATE INDEX IF NOT EXISTS ix_client_address_review_client
    ON gold.client_address_review USING btree (client_id);

COMMIT;
