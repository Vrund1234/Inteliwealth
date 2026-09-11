-- ============================================================
-- gold.client_folio -- client identity, PERSISTED
--
-- WHY THIS EXISTS
--   Client identity is currently re-derived from scratch on
--   every run, in four separate places (load_clients, its own
--   person_key, the address ETL, the bank ETL). Those four
--   copies have drifted twice: once leaving every minor with no
--   address or bank row, once creating a duplicate client for
--   one child ("DHYANI PATEL" vs "Dhyani N Patel").
--
--   Worse, everything it derives from is mutable. 24% of
--   clients arrive under more than one name spelling, the name
--   order reverses between feeds ("MAVJI LALJI PATEL" /
--   "PATEL MAVJI LALJI"), and a minor's PAN changes from absent
--   to present the moment they come of age. Identity cannot be
--   a function of attributes that change.
--
--   The folio can. Measured across bronze (7,048 investor rows)
--   and bronze.transaction_master_new (141,898 rows):
--
--     folios carrying two different PANs .............. 0
--     folios with conflicting date of birth ........... 0
--     folios with conflicting guardian PAN ............ 0
--     folio numbers colliding across CAMS/KFIN ........ 0
--
--   A folio belongs to exactly one person and always has. So
--   the folio is recorded ONCE against the client it resolved
--   to, and from then on it IS the identity -- no re-derivation,
--   and nothing to drift.
--
-- WHAT IT UNLOCKS
--   * A minor coming of age UPDATES their client rather than
--     inserting a second one (5 clients are queued for this).
--   * A name change becomes a mere attribute update.
--   * The address / bank / nominee ETLs can join on a fact
--     instead of each re-deriving identity.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- Order-independent name key.
--
-- IMMUTABLE so it can back a unique index. Tokens are sorted,
-- so "MAVJI LALJI PATEL" and "PATEL MAVJI LALJI" collapse to
-- one key -- the reversal that a first-name rule cannot survive.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION gold.name_token_key(name text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT nullif(
        (SELECT string_agg(tok, ' ' ORDER BY tok)
           FROM unnest(
                    string_to_array(
                        btrim(regexp_replace(
                            upper(coalesce(name, '')),
                            '[^A-Z0-9]+', ' ', 'g')),
                        ' ')
                ) AS tok
          WHERE tok <> ''),
        '')
$$;


-- ------------------------------------------------------------
-- Folio numbers arrive as "1234.0" from some feeds and "1234"
-- from others. Normalised on the way in so the key is stable.
-- Verified a no-op on today's silver (2,174 raw = 2,174
-- normalised, 0 collisions), but transactions do carry the
-- suffix, so the rule stays.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION gold.normalise_folio(folio text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT nullif(regexp_replace(btrim(coalesce(folio, '')), '\.0$', ''), '')
$$;


CREATE TABLE IF NOT EXISTS gold.client_folio (
    source     text NOT NULL,
    folio_no   text NOT NULL,
    client_id  uuid NOT NULL
        REFERENCES gold.clients(id) ON DELETE CASCADE,
    -- Which rule resolved this folio, kept for audit: if a rule
    -- ever proves wrong, the rows it produced are identifiable.
    resolved_by text NOT NULL,
    linked_at  timestamptz NOT NULL DEFAULT now(),

    -- One folio, one client. This is the constraint the whole
    -- design rests on.
    PRIMARY KEY (source, folio_no)
);

CREATE INDEX IF NOT EXISTS ix_client_folio_client
    ON gold.client_folio (client_id);

COMMIT;
