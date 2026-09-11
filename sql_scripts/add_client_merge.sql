-- ============================================================
-- Client merge: one person, one client row
--
-- WHY
--   Four identity rules guard gold.clients -- PAN, folio,
--   guardian+name+DOB, and whole-name+DOB -- and each has a
--   blind spot. A folio that arrives without its PAN creates a
--   PAN-less twin of somebody who is already here:
--
--     ANIMESH J MEHTA   AAWPM4351N  1950-09-28   2 folios
--     ANIMESH J MEHTA   (no pan)    (no dob)     1 folio
--        ... both holding bank account 00691050067571
--
--   Prevention is in the loader (folio-first resolution). This
--   is the cure for the ones already here, and for anything a
--   future blind spot lets through.
--
-- WHAT A MERGE DOES
--   The loser's folios, banks, addresses and address decisions
--   move to the winner; the winner fills its own NULLs from the
--   loser but never overwrites a value it already has; the
--   loser is marked superseded and drops out of every unique
--   index and out of the loader's view -- but is NOT deleted,
--   so the decision stays auditable and anything still holding
--   the old id can follow superseded_by to the survivor.
-- ============================================================

BEGIN;

ALTER TABLE gold.clients
    ADD COLUMN IF NOT EXISTS superseded_by uuid;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_clients_superseded_by'
    ) THEN
        ALTER TABLE gold.clients
            ADD CONSTRAINT fk_clients_superseded_by
            FOREIGN KEY (superseded_by)
            REFERENCES gold.clients(id)
            ON DELETE SET NULL;
    END IF;
END
$do$;

-- A client cannot supersede itself.
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_clients_superseded_not_self'
    ) THEN
        ALTER TABLE gold.clients
            ADD CONSTRAINT ck_clients_superseded_not_self
            CHECK (superseded_by IS DISTINCT FROM id);
    END IF;
END
$do$;

CREATE INDEX IF NOT EXISTS ix_clients_superseded_by
    ON gold.clients (superseded_by)
    WHERE superseded_by IS NOT NULL;


CREATE TABLE IF NOT EXISTS gold.client_merge_log (
    merge_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    loser_id      uuid NOT NULL,
    winner_id     uuid NOT NULL,
    loser_pan     varchar(10),
    winner_pan    varchar(10),
    loser_name    varchar(255),
    winner_name   varchar(255),
    reason        text NOT NULL,
    evidence      text,
    folios_moved  integer NOT NULL DEFAULT 0,
    banks_moved   integer NOT NULL DEFAULT 0,
    address_moved integer NOT NULL DEFAULT 0,
    merged_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_client_merge_log_loser
    ON gold.client_merge_log (loser_id);

COMMIT;


-- ============================================================
-- A superseded row must not occupy the identity it gave up
--
-- Both unique indexes are re-created to ignore superseded rows.
-- Without this the loser keeps holding its PAN / name key, so
-- the survivor cannot take it and the loader keeps resolving
-- arrivals to a row that is no longer the person.
-- ============================================================

BEGIN;

-- uq_gold_clients_pan stays a PLAIN unique constraint, NOT a
-- partial one. load_clients() upserts with ON CONFLICT (pan),
-- and Postgres cannot infer a PARTIAL index from that -- it
-- raises "no unique or exclusion constraint matching the ON
-- CONFLICT specification" and NO new client can ever be
-- inserted again. Made partial here, this migration silently
-- broke client creation; the suite caught it on the one test
-- that adds a genuinely new person.
--
-- A superseded row therefore must not go on holding a PAN. It
-- does not need to: merge_client() hands the PAN to the winner
-- and clears it from the loser, and client_merge_log.loser_pan
-- keeps the record of what it was.

DROP INDEX IF EXISTS gold.uq_clients_panless_identity;

CREATE UNIQUE INDEX uq_clients_panless_identity
    ON gold.clients (
        coalesce(upper(btrim(guardian_pan)), '~'),
        coalesce(gold.panless_name_key(full_name, guardian_pan), '~'),
        coalesce(date_of_birth, DATE '0001-01-01')
    )
    WHERE pan IS NULL AND superseded_by IS NULL;

COMMIT;


-- ============================================================
-- gold.merge_client(loser, winner, reason, evidence)
-- ============================================================

CREATE OR REPLACE FUNCTION gold.merge_client(
    p_loser    uuid,
    p_winner   uuid,
    p_reason   text,
    p_evidence text DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_folios  integer := 0;
    v_banks   integer := 0;
    v_addr    integer := 0;
    v_seq     integer;
    v_merge   uuid;
    -- captured BEFORE the loser releases them, or the log
    -- records the NULL we are about to write rather than the
    -- identity that was actually given up.
    v_loser_pan  varchar(10);
    v_loser_name varchar(255);
BEGIN
    IF p_loser = p_winner THEN
        RAISE EXCEPTION 'merge_client: loser and winner are the same row (%)', p_loser;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM gold.clients WHERE id = p_winner
                                                AND superseded_by IS NULL) THEN
        RAISE EXCEPTION 'merge_client: winner % does not exist or is itself superseded', p_winner;
    END IF;

    IF EXISTS (SELECT 1 FROM gold.clients WHERE id = p_loser
                                            AND superseded_by IS NOT NULL) THEN
        RAISE EXCEPTION 'merge_client: loser % is already superseded', p_loser;
    END IF;

    SELECT pan, full_name INTO v_loser_pan, v_loser_name
      FROM gold.clients WHERE id = p_loser;

    -- ---- folios: PK is (source, folio_no), so repointing
    -- ---- client_id cannot collide.
    UPDATE gold.client_folio
       SET client_id = p_winner
     WHERE client_id = p_loser;

    GET DIAGNOSTICS v_folios = ROW_COUNT;

    -- ---- banks. UNIQUE (client_id, seq) means the loser's
    -- ---- numbering has to continue after the winner's, and an
    -- ---- account the winner already holds is dropped rather
    -- ---- than duplicated. account_key is generated from the
    -- ---- account number alone, so it survives the repoint.
    SELECT coalesce(max(seq), 0) INTO v_seq
      FROM gold.client_bank WHERE client_id = p_winner;

    DELETE FROM gold.client_bank b
     WHERE b.client_id = p_loser
       AND EXISTS (SELECT 1 FROM gold.client_bank w
                   WHERE w.client_id = p_winner
                     AND w.account_key = b.account_key);

    UPDATE gold.client_bank b
       SET client_id = p_winner,
           seq       = v_seq + r.rn
      FROM (SELECT id, row_number() OVER (ORDER BY seq, id) AS rn
            FROM gold.client_bank WHERE client_id = p_loser) r
     WHERE b.id = r.id;

    GET DIAGNOSTICS v_banks = ROW_COUNT;

    -- ---- addresses, same treatment
    SELECT coalesce(max(seq), 0) INTO v_seq
      FROM gold.client_address WHERE client_id = p_winner;

    DELETE FROM gold.client_address a
     WHERE a.client_id = p_loser
       AND EXISTS (SELECT 1 FROM gold.client_address w
                   WHERE w.client_id = p_winner
                     AND w.address_key = a.address_key);

    UPDATE gold.client_address a
       SET client_id = p_winner,
           seq       = v_seq + r.rn
      FROM (SELECT id, row_number() OVER (ORDER BY seq, id) AS rn
            FROM gold.client_address WHERE client_id = p_loser) r
     WHERE a.id = r.id;

    GET DIAGNOSTICS v_addr = ROW_COUNT;

    -- ---- address decisions
    DELETE FROM gold.client_address_alias l
     WHERE l.client_id = p_loser
       AND EXISTS (SELECT 1 FROM gold.client_address_alias w
                   WHERE w.client_id = p_winner
                     AND w.address_key_alias = l.address_key_alias);

    UPDATE gold.client_address_alias
       SET client_id = p_winner WHERE client_id = p_loser;

    DELETE FROM gold.client_address_review l
     WHERE l.client_id = p_loser
       AND EXISTS (SELECT 1 FROM gold.client_address_review w
                   WHERE w.client_id = p_winner
                     AND w.address_key_a = l.address_key_a
                     AND w.address_key_b = l.address_key_b);

    UPDATE gold.client_address_review
       SET client_id = p_winner WHERE client_id = p_loser;

    -- ---- the winner fills its own gaps from the loser, and
    -- ---- ONLY its gaps: a value the winner already holds is
    -- ---- never replaced by the loser's.
    UPDATE gold.clients w
       SET pan             = coalesce(w.pan,             l.pan),
           guardian_pan    = coalesce(w.guardian_pan,    l.guardian_pan),
           date_of_birth   = coalesce(w.date_of_birth,   l.date_of_birth),
           email           = coalesce(w.email,           l.email),
           mobile          = coalesce(w.mobile,          l.mobile),
           mobile_isd      = coalesce(w.mobile_isd,      l.mobile_isd),
           phone           = coalesce(w.phone,           l.phone),
           occupation      = coalesce(w.occupation,      l.occupation),
           tax_status      = coalesce(w.tax_status,      l.tax_status),
           kyc_status      = coalesce(w.kyc_status,      l.kyc_status),
           investor_type   = coalesce(w.investor_type,   l.investor_type),
           can             = coalesce(w.can,             l.can),
           ckyc_no         = coalesce(w.ckyc_no,         l.ckyc_no),
           dp_id           = coalesce(w.dp_id,           l.dp_id),
           aadhaar_seeding_status =
               coalesce(w.aadhaar_seeding_status, l.aadhaar_seeding_status)
      FROM gold.clients l
     WHERE w.id = p_winner AND l.id = p_loser;

    -- ---- and the loser steps aside, releasing the PAN it
    -- ---- just handed over: uq_gold_clients_pan is a plain
    -- ---- unique constraint (see above), so two rows cannot
    -- ---- both hold it. The value survives in the merge log.
    UPDATE gold.clients
       SET superseded_by = p_winner,
           pan           = NULL
     WHERE id = p_loser;

    INSERT INTO gold.client_merge_log (
        loser_id, winner_id, loser_pan, winner_pan,
        loser_name, winner_name, reason, evidence,
        folios_moved, banks_moved, address_moved
    )
    SELECT p_loser, p_winner, v_loser_pan, w.pan,
           v_loser_name, w.full_name, p_reason, p_evidence,
           v_folios, v_banks, v_addr
      FROM gold.clients w
     WHERE w.id = p_winner
    RETURNING merge_id INTO v_merge;

    RETURN v_merge;
END
$fn$;
