-- ============================================================
-- Clients holding a PAN that is really their guardian's
--
-- Hriaan Hrujal Sanghvi and Shavya Bhandari each exist twice in
-- gold.clients: once correctly, PAN-less with guardian_pan set
-- and their folio attached, and once as an older row carrying
-- the GUARDIAN'S PAN as their own, with no folio at all.
--
-- The older rows predate the folio work. They are wrong on the
-- evidence, not by judgement: neither COHPS5922Q nor AATPB8962B
-- appears as a `pan_no` ANYWHERE in bronze or silver. Both occur
-- only as `guardian_pan`. The registry never gave either child
-- a PAN; something upstream filed the parent's in that column.
--
-- The merge sweep will not touch them, and correctly so: its
-- guardian guard sees one row holding the other's PAN and reads
-- a parent and a child, which is exactly the check that stops a
-- minor being absorbed into their guardian. It cannot know that
-- here the PAN itself is the error.
--
-- So this is done explicitly, with the evidence recorded.
--
-- The PAN is released BEFORE the merge: merge_client() fills the
-- winner's gaps from the loser with coalesce(), so a loser still
-- holding the PAN would hand the guardian's PAN to the child and
-- recreate the fault on the surviving row.
--
-- Idempotent: re-running finds nothing to do.
-- ============================================================

DO $do$
DECLARE
    bad  record;
    good uuid;
BEGIN
    FOR bad IN
        SELECT c.id, c.pan, c.full_name, c.date_of_birth
        FROM gold.clients c
        WHERE c.superseded_by IS NULL
          AND c.pan IS NOT NULL
          -- the PAN is never anyone's own in the source ...
          AND NOT EXISTS (
              SELECT 1 FROM silver.investor_master s
              WHERE upper(btrim(s.pan_no)) = upper(btrim(c.pan)))
          -- ... but it IS somebody's guardian
          AND EXISTS (
              SELECT 1 FROM silver.investor_master s
              WHERE upper(btrim(s.guardian_pan)) = upper(btrim(c.pan)))
    LOOP
        -- the same person, kept PAN-less, with the folio on them
        SELECT g.id INTO good
        FROM gold.clients g
        WHERE g.superseded_by IS NULL
          AND g.id <> bad.id
          AND g.pan IS NULL
          AND upper(btrim(g.guardian_pan)) = upper(btrim(bad.pan))
          AND gold.norm_name(g.full_name) = gold.norm_name(bad.full_name)
          AND g.date_of_birth IS NOT DISTINCT FROM bad.date_of_birth;

        IF good IS NULL THEN
            RAISE NOTICE 'no PAN-less twin for % (%) — left alone',
                         bad.full_name, bad.pan;
            CONTINUE;
        END IF;

        RAISE NOTICE 'merging % away from PAN % (the guardian''s)',
                     bad.full_name, bad.pan;

        UPDATE gold.clients SET pan = NULL WHERE id = bad.id;

        PERFORM gold.merge_client(
            bad.id, good,
            'GUARDIAN_PAN_MISFILED_AS_OWN',
            format('%s never appears as a pan_no in silver, only as guardian_pan', bad.pan)
        );
    END LOOP;
END
$do$;
