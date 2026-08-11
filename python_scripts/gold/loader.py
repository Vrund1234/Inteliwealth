# =====================================================
# GOLD LAYER LOADER
# =====================================================

from gold.pipeline_runner import run_gold_pipeline, make_result

from gold.amc import (
    extract_amc,
    transform_amc,
    load_amc
)

from gold.scheme import (
    extract_scheme,
    transform_scheme,
    load_scheme
)

from gold.scheme_nav import (
    extract_scheme_nav,
    transform_scheme_nav,
    load_scheme_nav
)

from gold.transaction import (
    extract_transactions,
    transform_transactions,
    load_transactions
)

from gold.holdings import (
    extract_holdings,
    transform_holdings,
    load_holdings
)

# =====================================================
# OPTIONAL GOLD SIP
# =====================================================

try:
    from gold.sip import (
        extract_sip,
        transform_sip,
        load_sip
    )
    SIP_AVAILABLE = True
    SIP_IMPORT_ERROR = None
except ImportError as exc:
    SIP_AVAILABLE = False
    # Phase B: keep the reason instead of discarding it — an unimportable Gold
    # module is a real problem, not something to drop on the floor silently.
    SIP_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


# =====================================================
# OPTIONAL GOLD CLIENTS
# =====================================================

try:
    from gold.clients import (
        extract_clients,
        transform_clients,
        load_clients
    )
    CLIENT_AVAILABLE = True
    CLIENT_IMPORT_ERROR = None
except ImportError as exc:
    CLIENT_AVAILABLE = False
    CLIENT_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


# =====================================================
# OPTIONAL FOLIO NOMINEES
# =====================================================

try:
    from gold.folio_nominees import (
        extract_folio_nominees,
        transform_folio_nominees,
        load_folio_nominees
    )
    FOLIO_AVAILABLE = True
    FOLIO_IMPORT_ERROR = None
except ImportError as exc:
    FOLIO_AVAILABLE = False
    FOLIO_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


# =====================================================
# MAIN GOLD LOAD FUNCTION
# =====================================================

def load_gold():
    """Run every Gold domain and return one result dict per domain.

    Phase B: returns ``list[dict]`` — see ``gold.pipeline_runner.make_result``
    for the shape.  Deliberately does NOT raise when a single domain fails:
    domain isolation is intentional, so one bad domain must not abort the
    others.  The caller is now able to detect which ones failed instead of
    having to scrape the console.
    """

    print("=" * 80)
    print("STARTING GOLD LAYER LOAD")
    print("=" * 80)

    results = []

    # AMC
    results.append(
        run_gold_pipeline("AMC", extract_amc, transform_amc, load_amc)
    )

    # SCHEME (Explicit due to 2 dataframes — kept out of the generic runner in
    # Phase 6 because extract_scheme() returns a tuple; it still reports in the
    # same result shape via make_result.)
    try:
        print("\nLoading Gold Scheme")
        transaction_df, investor_df = extract_scheme()

        if not transaction_df.empty or not investor_df.empty:
            scheme_gold_df = transform_scheme(transaction_df, investor_df)

            if not scheme_gold_df.empty:
                load_scheme(scheme_gold_df)
                print("Scheme loaded successfully")
                results.append(
                    make_result("Scheme", "ok", rows=len(scheme_gold_df))
                )
            else:
                print("No new Scheme records to load")
                results.append(make_result("Scheme", "no_data"))
        else:
            print("No Scheme data found")
            results.append(make_result("Scheme", "no_data"))

    except Exception as e:
        print("Scheme Gold Failed")
        print(e)
        results.append(
            make_result("Scheme", "failed", error=f"{type(e).__name__}: {e}")
        )

    # SCHEME NAV
    results.append(
        run_gold_pipeline("Scheme NAV", extract_scheme_nav, transform_scheme_nav, load_scheme_nav)
    )

    # TRANSACTIONS
    results.append(
        run_gold_pipeline("Transactions", extract_transactions, transform_transactions, load_transactions)
    )

    # HOLDINGS
    results.append(
        run_gold_pipeline("Holdings", extract_holdings, transform_holdings, load_holdings)
    )

    # SIP
    if SIP_AVAILABLE:
        results.append(
            run_gold_pipeline("SIP", extract_sip, transform_sip, load_sip)
        )
    else:
        results.append(
            make_result("SIP", "skipped", error=SIP_IMPORT_ERROR)
        )

    # CLIENTS
    if CLIENT_AVAILABLE:
        results.append(
            run_gold_pipeline("Clients", extract_clients, transform_clients, load_clients)
        )
    else:
        results.append(
            make_result("Clients", "skipped", error=CLIENT_IMPORT_ERROR)
        )

    # FOLIO NOMINEES
    if FOLIO_AVAILABLE:
        results.append(
            run_gold_pipeline("Folio Nominees", extract_folio_nominees, transform_folio_nominees, load_folio_nominees)
        )
    else:
        results.append(
            make_result("Folio Nominees", "skipped", error=FOLIO_IMPORT_ERROR)
        )

    # =====================================================
    # SUMMARY
    # =====================================================

    failed = [r for r in results if r["status"] == "failed"]
    skipped = [r for r in results if r["status"] == "skipped"]
    loaded = [r for r in results if r["status"] == "ok"]

    print("=" * 80)
    print("GOLD LAYER LOAD COMPLETED")
    print(
        f"  ok: {len(loaded)}"
        f" | no_data: {len([r for r in results if r['status'] == 'no_data'])}"
        f" | failed: {len(failed)}"
        f" | skipped: {len(skipped)}"
    )

    for r in failed:
        print(f"  FAILED  {r['name']}: {r['error']}")

    for r in skipped:
        print(f"  SKIPPED {r['name']}: {r['error']}")

    print("=" * 80)

    return results


# =====================================================
# RUN
# =====================================================

if __name__ == "__main__":
    load_gold()