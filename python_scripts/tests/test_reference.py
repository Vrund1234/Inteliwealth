import pandas as pd

from scheme_matching.reference import load_amc_map
from utils.db import master_engine


def test_amc_map_covers_all_rta_codes_in_use():
    """Every AMC code appearing in the RTA data resolves to a row in the map."""
    amc_map = load_amc_map(master_engine)
    results = pd.read_csv(
        "scheme_mapping_analysis/scheme_mapping_results.csv", dtype=str
    )
    used = set(zip(results.rta, results.rta_amc_code))
    known = set(zip(amc_map.rta, amc_map.rta_amc_code))
    missing = used - known
    assert not missing, f"AMC codes in data but not in rta_amc_code: {missing}"


def test_amfi_amc_code_resolves_for_513_of_515_schemes():
    """The 2 exceptions are KFIN 906 (Altiva) and 908 (Diviniti), absent from AMFI."""
    amc_map = load_amc_map(master_engine)
    resolvable = amc_map[amc_map.amfi_amc_code.notna()]
    results = pd.read_csv(
        "scheme_mapping_analysis/scheme_mapping_results.csv", dtype=str
    )
    merged = results.merge(
        resolvable,
        on=["rta", "rta_amc_code"],
        how="left",
    )
    covered = merged.amfi_amc_code.notna().sum()
    assert covered == 513, f"expected 513 covered, got {covered}"


def test_amc_slug_is_not_a_valid_amfi_code():
    """Regression guard for the original bug: slugs and AMFI codes are disjoint."""
    amc_map = load_amc_map(master_engine)
    amfi_codes = set(
        pd.read_sql(
            "SELECT DISTINCT amc_code FROM public.amfi_scheme_master "
            "WHERE amc_code IS NOT NULL",
            master_engine,
        ).amc_code
    )
    slugs = set(amc_map.amc_slug.dropna())
    overlap = slugs & amfi_codes
    assert len(overlap) <= 1, (
        f"slugs unexpectedly overlap AMFI codes: {overlap}. "
        "Joining on amc_slug is still wrong regardless."
    )
