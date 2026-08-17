-- Seed bronze.scheme_name_alias.
--
-- The table has been empty since it was created, which is why three tests in
-- tests/test_aliases.py::TestConfiguredAliases fail: they assert rows that were
-- specified but never inserted.
--
-- All five rows below are GLOBAL (amc_code IS NULL) rather than AMC-scoped.
-- That is deliberate. The AMC renames have to apply to both sides of a name
-- comparison, and the historical side comes from public.scheme_master, which
-- has no amc_code column. An AMC-scoped rename would rewrite the RTA name and
-- leave the scheme_master name alone, so the two could never match. The raw
-- terms are AMC-specific enough that a global rewrite is safe: no fund outside
-- the named house is called "Nippon India", "Bandhan" or "DSP BlackRock".
--
-- NOT included, deliberately: CUM -> CUMULATIVE. It looks harmless but it
-- rewrites SEBI's "Income Distribution cum Capital Withdrawal", which
-- scheme_key._IDCW_PHRASE deletes as a phrase before building the core name.
-- Broken, the phrase survives into the core: "SBI MAGNUM LOW DURATION" becomes
-- "SBI MAGNUM LOW DURATION INCOME DISTRIBUTION CUMULATIVE CAPITAL WITHDRAWAL",
-- corrupting the key of all 654 AMFI schemes named that way.

INSERT INTO bronze.scheme_name_alias
    (alias_id, raw_term, normalized_term, alias_type, amc_code, is_active)
VALUES
    -- Reliance MF became Nippon India in 2019. The RTA emits the new name;
    -- AMFI and scheme_master still carry the old one on pre-rebrand schemes.
    (gen_random_uuid(), 'NIPPON INDIA', 'RELIANCE', 'FUND_RENAME', NULL, TRUE),

    -- IDFC MF became Bandhan MF in 2023. Same direction as above.
    (gen_random_uuid(), 'BANDHAN', 'IDFC', 'FUND_RENAME', NULL, TRUE),

    -- Birla Sun Life became Aditya Birla Sun Life in 2017. Here the RTA is the
    -- longer, newer form, so the rename runs the other way to reach the
    -- historical spelling.
    (gen_random_uuid(), 'ADITYA BIRLA SUN LIFE', 'BIRLA SUN LIFE', 'FUND_RENAME', NULL, TRUE),

    -- DSP BlackRock became DSP in 2018 when BlackRock exited the JV. Longer
    -- term normalises to shorter, matching the RTA spelling.
    (gen_random_uuid(), 'DSP BLACKROCK', 'DSP', 'FUND_RENAME', NULL, TRUE),

    -- "Gr." is how CAMS abbreviates the growth option on closed-ended plans.
    -- Whole-word only, so GROWTH and GREEN are untouched.
    (gen_random_uuid(), 'GR', 'GROWTH', 'TOKEN', NULL, TRUE)

ON CONFLICT (alias_type, raw_term, COALESCE(amc_code, ''))
DO UPDATE SET
    normalized_term = EXCLUDED.normalized_term,
    is_active       = EXCLUDED.is_active;

-- ---------------------------------------------------------------------------
-- Batch 2. Derived by suggest_aliases.py and trialled before insertion:
-- together they resolve 2 further schemes with 0 lost, 0 retargeted and 0 new
-- key collisions across the 39,640-name universe.
--
-- FMP and the singular/plural pairs must go in together. ICICI writes
-- "FMP Series 58 - 2 Years Plan A" where the master writes "Fixed Maturity
-- Plan - Series 58 - 2 Year Plan A": two separate edits, so neither alias
-- resolves the scheme on its own and the suggester correctly refuses to
-- propose either in isolation.
INSERT INTO bronze.scheme_name_alias
    (alias_id, raw_term, normalized_term, alias_type, amc_code, is_active)
VALUES
    (gen_random_uuid(), 'FMP', 'FIXED MATURITY PLAN', 'TOKEN', NULL, TRUE),
    (gen_random_uuid(), 'YEARS', 'YEAR', 'TOKEN', NULL, TRUE),
    (gen_random_uuid(), 'MONTHS', 'MONTH', 'TOKEN', NULL, TRUE),

    -- Tata abbreviates its own fixed maturity plans in the master. Applied
    -- after the FMP expansion above, so the raw term is the expanded form.
    (gen_random_uuid(), 'TATA FIXED MATURITY PLAN', 'TFMP', 'FUND_RENAME', NULL, TRUE)

ON CONFLICT (alias_type, raw_term, COALESCE(amc_code, ''))
DO UPDATE SET
    normalized_term = EXCLUDED.normalized_term,
    is_active       = EXCLUDED.is_active;

-- ---------------------------------------------------------------------------
-- Batch 3. Two abbreviations IDFC uses in the master but not in its RTA feed.
--
-- Both forms of the EMS phrase are listed because FUND_RENAME rows are applied
-- BEFORE token rows: by the time MONTHS -> MONTH would normalise the plural,
-- the phrase rename has already had its only chance to fire.
--
-- YS appears 121 times in the master and is "Yearly Series" in every one of
-- them (IDFC and Standard Chartered fixed maturity plans). Expanding it also
-- restores the frequency: "Yearly" parses to ANNUAL, which the bare
-- abbreviation does not, and a frequency mismatch alone blocks a match.
INSERT INTO bronze.scheme_name_alias
    (alias_id, raw_term, normalized_term, alias_type, amc_code, is_active)
VALUES
    (gen_random_uuid(), 'EIGHTEEN MONTHS SERIES', 'EMS', 'FUND_RENAME', NULL, TRUE),
    (gen_random_uuid(), 'EIGHTEEN MONTH SERIES',  'EMS', 'FUND_RENAME', NULL, TRUE),
    (gen_random_uuid(), 'YS', 'YEARLY SERIES', 'TOKEN', NULL, TRUE)

ON CONFLICT (alias_type, raw_term, COALESCE(amc_code, ''))
DO UPDATE SET
    normalized_term = EXCLUDED.normalized_term,
    is_active       = EXCLUDED.is_active;

-- ---------------------------------------------------------------------------
-- Batch 4. "Regular Savings" is a fund name, not a plan marker.
--
-- scheme_key treats REGULAR as filler because it usually marks the plan, which
-- collapses "Regular Savings Fund" onto the unrelated "Savings Fund". Both
-- ABSL and ICICI run the two as separate funds, so the engine saw two AMFI
-- schemes with identical keys and correctly routed B313G and PIMPG to review
-- rather than guess between them.
--
-- Joining the words into one token puts the phrase out of reach of the filler
-- while leaving a trailing "Regular Plan" / "Direct Plan" to be parsed as the
-- plan exactly as before. Guarded by
-- tests/test_aliases.py::TestConfiguredAliases, which specified this row long
-- before it existed.
INSERT INTO bronze.scheme_name_alias
    (alias_id, raw_term, normalized_term, alias_type, amc_code, is_active)
VALUES
    (gen_random_uuid(), 'REGULAR SAVINGS', 'REGULARSAVINGS', 'FUND_RENAME', NULL, TRUE)

ON CONFLICT (alias_type, raw_term, COALESCE(amc_code, ''))
DO UPDATE SET
    normalized_term = EXCLUDED.normalized_term,
    is_active       = EXCLUDED.is_active;
