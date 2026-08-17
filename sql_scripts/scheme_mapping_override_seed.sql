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
