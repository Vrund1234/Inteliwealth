-- Hand-reviewed approvals for candidates already sitting in
-- bronze.scheme_mapping_review, awaiting a human decision.
--
-- This file only marks the decision -- it does NOT write bronze.scheme_mapping
-- itself. That write requires deriving scheme_id (amc_code + amfi_scheme_code,
-- with the AMC code resolved from public.amfi_scheme_master and falling back
-- to the RTA's own AMC code when the candidate has none -- see
-- scheme_matching.scheme_id.derive_scheme_id and
-- promote_approved_mappings.load_amfi_amc_codes) and guarding against a
-- scheme another rule already mapped in the meantime. That logic lives in
-- Python on purpose, so it isn't duplicated here where it could drift out of
-- sync with the real implementation.
--
-- After applying this file, run:
--   venv/bin/python promote_approved_mappings.py --dry-run   # preview
--   venv/bin/python promote_approved_mappings.py             # apply
-- (or a full `venv/bin/python scheme_mapping.py` run, which promotes approved
-- reviews as its own last phase). Both are idempotent and safe to re-run.
--
-- Idempotent: safe to re-run. reviewer_decision is only ever set to
-- 'APPROVED' here; a row already carrying a decision is left as EXCLUDED
-- specifies, so re-applying this file cannot un-approve or un-reject a row a
-- later, more specific decision has since overridden.

UPDATE bronze.scheme_mapping_review
SET reviewer_decision = 'APPROVED',
    reviewed_by        = 'scheme-mapping-review',
    reviewed_at        = now()
WHERE reviewer_decision IS NULL
  AND (rta, rta_scheme_code, candidate_rank) IN (
    -- CAMS/P1952 -> 117621 (NAV_FUZZY_MATCH, score 92).
    -- Independently verified against public.nav_master before approval,
    -- beyond the pipeline's own name-similarity score: RTA's last purchase
    -- price on P1952 is 12.9338 on 2015-07-06 (bronze.transaction_master_new);
    -- 117621's terminal NAV in nav_master is 12.933800 on the same date --
    -- an exact date-and-price match on what is a Fixed Maturity Plan's
    -- maturity/redemption event, not a coincidence between two unrelated
    -- schemes. Name evidence corroborates: "ICICI Prudential FMP Series
    -- 63-3 Years Plan L Cumulative" (RTA) vs "ICICI Prudential Fixed
    -- Maturity Plan-Series 63-3 Year Plan L" (117621, identical in both
    -- amfi_scheme_master and scheme_master). Neither side carries a
    -- plan_type/option_type value, so there was nothing for the structured
    -- corroboration check to disagree with.
    ('CAMS', 'P1952', 1)
  );
