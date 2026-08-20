-- Tracks gold.sip rows whose enrichment (arn/sub_arn/arn_id from transaction
-- data, client_id from gold.clients) could not be resolved at load time
-- because the sibling WBR2/WBR9 (or KFIN equivalent) file hadn't arrived
-- yet. NULL means either "fully resolved" or "structurally blank" (e.g. a
-- genuine direct-plan SIP with no distributor) — set only when NO match was
-- found at all, not when a match was found with an empty field.
--
-- Idempotent: safe to re-run.

ALTER TABLE gold.sip ADD COLUMN IF NOT EXISTS enrichment_pending_since TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_gold_sip_enrichment_pending_since
    ON gold.sip (enrichment_pending_since)
    WHERE enrichment_pending_since IS NOT NULL;
