-- Hand-curated mappings for schemes no rule can resolve.
--
-- bronze.scheme_mapping_override outranks every algorithmic rule
-- (rules.AUTHORITATIVE_RULES), so each row is a standing assertion rather than
-- a score. The reason column is NOT NULL by design: six months from now
-- "why is an Edelweiss fund mapped to JPMorgan?" is an obvious question, and
-- the answer has to travel with the row.
--
-- Both rows below rest on a UNIQUE exact NAV match in public.nav_master --
-- one scheme in the whole master priced identically on the sampled date --
-- rather than on name similarity, which is why neither could be reached by a
-- matching rule.
--
-- Idempotent: safe to re-run.

INSERT INTO bronze.scheme_mapping_override
    (override_id, rta, rta_scheme_code, amfi_scheme_code, reason, mapped_by, is_active)
VALUES
    -- The RTA appends a "- Series XIX" grouping the AMFI name does not carry,
    -- which leaves the core names unequal. Everything else is identical, and
    -- the NAV settles it: on 2013-10-11 the RTA price 12.0242 matches exactly
    -- one scheme in nav_master, 115890, to six decimal places.
    (gen_random_uuid(), 'CAMS', 'HFSP4G', '115890',
     'NAV 12.0242 on 2013-10-11 matches scheme 115890 uniquely in nav_master. '
     'Names agree apart from a "- Series XIX" suffix present only in the RTA '
     'feed. Blocked from automatic matching because that suffix is a numeric '
     'token, which the matching rules treat as identity.',
     'scheme-mapping-review', TRUE),

    -- Edelweiss AMC acquired JPMorgan's India business in 2016 and the master
    -- still carries the pre-acquisition name. The two names share nothing, so
    -- no name-based rule can or should reach this: the evidence is entirely
    -- the NAV, corroborated by the acquisition date matching the NAV date.
    -- Both sides are the Segregated Asset Growth share class.
    (gen_random_uuid(), 'KFIN', '118TFSS', '135450',
     'NAV 11.4310 on 2016-05-19 matches scheme 135450 uniquely in nav_master. '
     'Edelweiss AMC acquired JPMorgan India in 2016 and the master retains the '
     'pre-acquisition name; both are the Segregated Asset Growth share class. '
     'Unreachable by name matching, which is why it is recorded here.',
     'scheme-mapping-review', TRUE)

ON CONFLICT (rta, rta_scheme_code)
DO UPDATE SET
    amfi_scheme_code = EXCLUDED.amfi_scheme_code,
    reason           = EXCLUDED.reason,
    mapped_by        = EXCLUDED.mapped_by,
    is_active        = EXCLUDED.is_active;


-- ---------------------------------------------------------------------------
-- Added 2026-08-17: collision on AMFI 153419.
--
-- KFIN 118LDRG and 118TFSG carry byte-identical RTA names ("Edelweiss Low
-- Duration Fund - Regular Plan Growth"), so PRODUCT_MATCH scored both to
-- 153419 with confidence 100 and no rule could separate them. They are two
-- different scheme records, and the NAV series prove it:
--
--   140207  2016-11-28 .. 2021-02-12  (1024 obs, last NAV 2008.1785)
--   153419  2025-03-21 .. 2026-08-04  ( 331 obs, last NAV 1087.1782)
--
-- Disjoint date ranges and NAVs an order of magnitude apart -- one fund's
-- history cannot be the other's. The RTA NAV feed carries both series under
-- distinguishable names ("...(Formerly Ultra Short Term Fund) - Regular Plan
-- Growth" = 2008.1785 on 2021-02-12; "Edelweiss Low Duration - Regular Plan
-- Growth" = 1089.4039 on 2026-08-13), but the scheme feed flattens that
-- distinction away, which is why this has to be asserted by hand.
--
-- Which code belongs to which lineage is settled by the sibling above:
-- 118TFSS is the "Formerly Ultra Short Term Fund" Segregated Asset class
-- (JPMorgan India Treasury Fund lineage). The whole 118TFS* family is that
-- older lineage, so 118TFSG is its Regular Growth class -> 140207.
-- 118LDR* is the current Edelweiss Low Duration naming and keeps 153419,
-- which the algorithmic rules already get right and this file leaves alone.
INSERT INTO bronze.scheme_mapping_override
    (override_id, rta, rta_scheme_code, amfi_scheme_code, reason, mapped_by, is_active)
VALUES
    (gen_random_uuid(), 'KFIN', '118TFSG', '140207',
     'Collided with 118LDRG on 153419 under identical RTA names. NAV series '
     'are disjoint: 140207 runs 2016-11-28..2021-02-12 (last 2008.1785), '
     '153419 runs 2025-03-21..2026-08-04 (last 1087.1782), so they are '
     'different scheme records. 118TFS* is the "Formerly Ultra Short Term '
     'Fund" (ex-JPMorgan India Treasury) lineage -- cf. sibling 118TFSS -- '
     'which is 140207. 118LDRG keeps 153419.',
     'scheme-mapping-review', TRUE)

ON CONFLICT (rta, rta_scheme_code)
DO UPDATE SET
    amfi_scheme_code = EXCLUDED.amfi_scheme_code,
    reason           = EXCLUDED.reason,
    mapped_by        = EXCLUDED.mapped_by,
    is_active        = EXCLUDED.is_active;


-- ---------------------------------------------------------------------------
-- Added 2026-08-18: two matured schemes settled by maturity-date equality.
--
-- Neither is reachable by a name-based rule -- both are re-verified below
-- against nav_master / scheme_master / bronze.transaction_master_new before
-- insertion, not taken on the report's word alone. Method: for a matured
-- scheme, the last RTA transaction is the redemption at maturity, so it must
-- fall on the scheme's terminal NAV date. A hard date match, not a score.
--
-- G950 -- exact date match, no caveat.
--   Last RTA transaction on G950: 2015-08-20 (bronze.transaction_master_new).
--   117773's terminal NAV in nav_master: 2015-08-20 (720 obs, 2012-08-21 to
--   2015-08-20). Unreachable by name matching for two independent reasons at
--   once: the RTA writes "FTP" / "Series 1", AMFI's scheme_master spells out
--   "IDFC FTP SERIES I (36 Months) - Growth" -- Roman numeral, not Arabic --
--   and AMFI never picked up the Bandhan rebrand, so it is also still filed
--   under IDFC. Either mismatch alone would block a match; both apply here.
--
-- L555G -- exact date match, plan-type caveat.
--   Last RTA transaction on L555G: 2022-04-13. 145644's terminal NAV in
--   nav_master: 2022-04-13 (1046 obs, 2018-12-10 to 2022-04-13). The day
--   count agrees -- scheme_master's own name for 145644 is "SBI Debt Fund
--   Series - C - 30 (1228 Days) - Regular Plan - Income Distribution cum
--   Capital Withdrawal Option (IDCW)", matching the RTA's "(1228 Days)"
--   exactly -- but the plan label does not: scheme_master says IDCW, the RTA
--   says Regular Growth. 145644 is absent from amfi_scheme_master (0 rows --
--   delisted at maturity, as expected) so there is no live AMFI record to
--   cross-check the label against. No live holding prices off this scheme
--   either way (matured 2022), so the cost of being wrong here is zero until
--   someone reads the plan label off this row -- get a second reviewer on
--   this one specifically before treating it as final.
INSERT INTO bronze.scheme_mapping_override
    (override_id, rta, rta_scheme_code, amfi_scheme_code, reason, mapped_by, is_active)
VALUES
    (gen_random_uuid(), 'CAMS', 'G950', '117773',
     'Maturity-date match: last RTA transaction on G950 is 2015-08-20, exactly '
     'the terminal NAV date of 117773 in nav_master (720 obs, 2012-08-21 to '
     '2015-08-20). Unreachable by name matching: RTA writes "Bandhan Fixed '
     'Term Plan Series 1-Growth (erstwhile IDFC Fixed Term Plan Series '
     '1-Growth)", scheme_master writes "IDFC FTP SERIES I (36 Months) - '
     'Growth" -- Arabic vs Roman series numbering, FTP vs spelled-out Fixed '
     'Term Plan, and AMFI never picked up the Bandhan rebrand.',
     'scheme-mapping-review', TRUE),

    (gen_random_uuid(), 'CAMS', 'L555G', '145644',
     'Maturity-date match: last RTA transaction on L555G is 2022-04-13, '
     'exactly the terminal NAV date of 145644 in nav_master (1046 obs, '
     '2018-12-10 to 2022-04-13). Day count agrees -- scheme_master names '
     '145644 "SBI Debt Fund Series - C - 30 (1228 Days) - Regular Plan - '
     'Income Distribution cum Capital Withdrawal Option (IDCW)", matching '
     'the RTA''s "(1228 Days)" -- but the plan label does not: scheme_master '
     'says IDCW, the RTA says Regular Growth. 145644 is absent from '
     'amfi_scheme_master (matured/delisted), so there is no active AMFI '
     'record to arbitrate the label against. CAVEAT: date and day-count both '
     'point at 145644; only the plan label dissents. No live holding prices '
     'off this scheme (matured 2022), so get a second reviewer to confirm '
     'the plan-label discrepancy before relying on this row.',
     'scheme-mapping-review', TRUE)

ON CONFLICT (rta, rta_scheme_code)
DO UPDATE SET
    amfi_scheme_code = EXCLUDED.amfi_scheme_code,
    reason           = EXCLUDED.reason,
    mapped_by        = EXCLUDED.mapped_by,
    is_active        = EXCLUDED.is_active;
