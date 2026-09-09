-- =============================================================================
-- gold.client_address_alias -- approved merges between address spellings
-- =============================================================================
--
-- uq_client_address_natural already guarantees one client cannot hold the same
-- address_key twice. What it cannot reach is one house spelled differently by
-- two RTA feeds:
--
--   38, ALOK BUNGLOWS      | NERSAL HOSPITAL      | THALTEJ,BODAKDEV
--   NO 38 ALOK BUNGLOWS    | NEAR SUN N STEP CLUB | BODAKDEV
--   38, THALTEJ ALOK BUNGLOWS THALTEJ | NEAR SAL HOSPITAL
--
-- One house, three folios, three rows, and the UI shows three addresses. On
-- the 686 rows live 2026-09-09 this affects 53 of 594 clients and 58 rows.
--
-- WHY A TABLE AND NOT A SOFT DELETE
-- =================================
--
-- Soft-deleting the losers does not hold. silver.investor_master keeps every
-- spelling forever, and etl_gold_client_address.load_client_address excludes
-- deleted rows from existing_keys on purpose (a retired address is allowed to
-- come back), so the row returns on the very next pipeline pass. This table is
-- consulted BEFORE the dedupe instead: an alias key is rewritten to its
-- canonical, so the variant never reaches the INSERT at all, and the values it
-- carried reach the canonical row through enrich_client_address.
--
-- KEYED ON client_id
-- ==================
--
-- gold.clients is insert-only for a new PAN -- etl_gold_clients skips existing
-- PANs for INSERT and only updates them -- so a client's id is assigned once
-- and preserved across runs. pan is carried alongside as a plain column, not
-- as the key: a dump restore regenerates ids, and the FK below would cascade
-- these rows away, so pan is what lets them be re-mapped afterwards.
-- =============================================================================

CREATE TABLE IF NOT EXISTS gold.client_address_alias (

    client_id              uuid        NOT NULL,

    -- Both keys are values gold.client_address.address_key already generated.
    -- Nothing here is authored by hand.
    address_key_alias      text        NOT NULL,
    address_key_canonical  text        NOT NULL,

    pan                    text,

    -- AUTO merges were applied unattended by detect_client_address_duplicates
    -- and are the ones that can be wrong without anyone having looked. Keeping
    -- the distinction is what makes them findable and reversible.
    decision_source        varchar(8)  NOT NULL DEFAULT 'USER',
    decided_by             uuid,
    decided_at             timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT pk_client_address_alias
        PRIMARY KEY (client_id, address_key_alias),

    CONSTRAINT ck_client_address_alias_distinct
        CHECK (address_key_alias <> address_key_canonical),

    CONSTRAINT ck_client_address_alias_source
        CHECK (decision_source IN ('AUTO', 'USER')),

    CONSTRAINT fk_client_address_alias_client
        FOREIGN KEY (client_id) REFERENCES gold.clients (id) ON DELETE CASCADE
);


CREATE INDEX IF NOT EXISTS ix_client_address_alias_canonical
    ON gold.client_address_alias (client_id, address_key_canonical);


-- -----------------------------------------------------------------------------
-- Allow the TOKEN match type in the review queue.
--
-- Trigram similarity compares whole strings and so cannot see two feeds naming
-- different landmarks for one house -- the three rows above score 0.194-0.491
-- against each other. TOKEN pairs are found by shared rare words plus house
-- number instead. Idempotent: drop then re-add.
-- -----------------------------------------------------------------------------

ALTER TABLE gold.client_address_review
    DROP CONSTRAINT IF EXISTS ck_client_address_review_type;

ALTER TABLE gold.client_address_review
    ADD CONSTRAINT ck_client_address_review_type
    CHECK (match_type IN ('PREFIX', 'FUZZY', 'TOKEN'));
