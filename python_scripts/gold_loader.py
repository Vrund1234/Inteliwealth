# =====================================================
# GOLD LAYER LOADER
# =====================================================

from utils.gold_result import load_result

from etl_gold_amc import (
    extract_amc,
    transform_amc,
    load_amc
)

from etl_gold_scheme import (
    extract_scheme,
    transform_scheme,
    load_scheme
)

from etl_gold_scheme_nav import (
    extract_scheme_nav,
    transform_scheme_nav,
    load_scheme_nav
)

from etl_gold_transaction import (
    extract_transactions,
    transform_transactions,
    load_transactions
)

from etl_gold_holdings import (
    extract_holdings,
    transform_holdings,
    load_holdings
)


# =====================================================
# OPTIONAL GOLD SIP
# =====================================================

try:

    from etl_gold_sip import (
        extract_sip,
        transform_sip,
        load_sip,
        reconcile_pending_sip
    )

    SIP_AVAILABLE = True

except ImportError:

    SIP_AVAILABLE = False


# =====================================================
# OPTIONAL GOLD CLIENTS
# =====================================================

try:

    from etl_gold_clients import (
        extract_clients,
        transform_clients,
        load_clients
    )

    CLIENT_AVAILABLE = True

except ImportError:

    CLIENT_AVAILABLE = False


# =====================================================
# OPTIONAL FOLIO NOMINEES
# =====================================================

try:

    from etl_gold_folio_nominees import (
        extract_folio_nominees,
        transform_folio_nominees,
        load_folio_nominees
    )

    FOLIO_AVAILABLE = True

except ImportError:

    FOLIO_AVAILABLE = False


# =====================================================
# MAIN GOLD LOAD FUNCTION
# =====================================================

def load_gold():

    print("=" * 80)
    print("STARTING GOLD LAYER LOAD")
    print("=" * 80)

    results = {}

    # =====================================================
    # GOLD AMC
    # =====================================================

    try:

        print("\nLoading Gold AMC")

        amc_df = extract_amc()

        if not amc_df.empty:

            amc_gold_df = transform_amc(
                amc_df
            )

            if not amc_gold_df.empty:

                results["amc"] = load_amc(
                    amc_gold_df
                )

                print("AMC loaded successfully")

            else:

                results["amc"] = load_result("skipped", 0)

        else:

            print("No AMC data found")
            results["amc"] = load_result("skipped", 0)

    except Exception as e:

        print("AMC Gold Failed")
        print(e)
        results["amc"] = load_result("error", 0, str(e))


    # =====================================================
    # GOLD SCHEME
    # =====================================================

    try:

        print("\nLoading Gold Scheme")

        transaction_df, investor_df = extract_scheme()

        if (
            not transaction_df.empty
            or not investor_df.empty
        ):

            scheme_gold_df = transform_scheme(
                transaction_df,
                investor_df
            )

            if not scheme_gold_df.empty:

                results["scheme"] = load_scheme(
                    scheme_gold_df
                )

                print("Scheme loaded successfully")

            else:

                results["scheme"] = load_result("skipped", 0)

        else:

            print("No Scheme data found")
            results["scheme"] = load_result("skipped", 0)

    except Exception as e:

        print("Scheme Gold Failed")
        print(e)
        results["scheme"] = load_result("error", 0, str(e))


    # =====================================================
    # GOLD SCHEME NAV
    # =====================================================

    try:

        print("\nLoading Gold Scheme NAV")

        nav_df = extract_scheme_nav()

        if not nav_df.empty:

            nav_gold_df = transform_scheme_nav(
                nav_df
            )

            if not nav_gold_df.empty:

                results["scheme_nav"] = load_scheme_nav(
                    nav_gold_df
                )

                print("Scheme NAV loaded successfully")

            else:

                results["scheme_nav"] = load_result("skipped", 0)

        else:

            print("No Scheme NAV data found")
            results["scheme_nav"] = load_result("skipped", 0)

    except Exception as e:

        print("Scheme NAV Gold Failed")
        print(e)
        results["scheme_nav"] = load_result("error", 0, str(e))


    # =====================================================
    # GOLD TRANSACTIONS
    # =====================================================

    try:

        print("\nLoading Gold Transactions")

        transaction_df = extract_transactions()

        if not transaction_df.empty:

            transaction_gold_df = transform_transactions(
                transaction_df
            )

            if not transaction_gold_df.empty:

                results["transactions"] = load_transactions(
                    transaction_gold_df
                )

                print(
                    "Transactions loaded successfully"
                )

            else:

                results["transactions"] = load_result("skipped", 0)

        else:

            print("No Transaction data found")
            results["transactions"] = load_result("skipped", 0)

    except Exception as e:

        print("Transaction Gold Failed")
        print(e)
        results["transactions"] = load_result("error", 0, str(e))


    # =====================================================
    # GOLD HOLDINGS
    # =====================================================

    try:

        print("\nLoading Gold Holdings")

        holdings_df = extract_holdings()

        if not holdings_df.empty:

            holdings_gold_df = transform_holdings(
                holdings_df
            )

            if not holdings_gold_df.empty:

                results["holdings"] = load_holdings(
                    holdings_gold_df
                )

                print(
                    "Holdings loaded successfully"
                )

            else:

                results["holdings"] = load_result("skipped", 0)

        else:

            print("No Holdings data found")
            results["holdings"] = load_result("skipped", 0)

    except Exception as e:

        print("Holdings Gold Failed")
        print(e)
        results["holdings"] = load_result("error", 0, str(e))


    # =====================================================
    # GOLD SIP
    # =====================================================

    if SIP_AVAILABLE:

        try:

            print("\nLoading Gold SIP")

            sip_df = extract_sip()

            if not sip_df.empty:

                sip_gold_df = transform_sip(
                    sip_df
                )

                if not sip_gold_df.empty:

                    results["sip"] = load_sip(
                        sip_gold_df
                    )

                    print(
                        "SIP loaded successfully"
                    )

                else:

                    results["sip"] = load_result("skipped", 0)

            else:

                print(
                    "No SIP data found"
                )
                results["sip"] = load_result("skipped", 0)

        except Exception as e:

            print("SIP Gold Failed")
            print(e)
            results["sip"] = load_result("error", 0, str(e))

        # =====================================================
        # SIP ENRICHMENT RECONCILIATION
        #
        # Retries any previously-pending rows (their sibling WBR2/WBR9
        # arrived since) alongside every normal gold_loader run. Folded
        # into the same "sip" result entry — a plain load with nothing new
        # AND nothing pending to reconcile still reports "skipped".
        # =====================================================

        try:

            reconcile_result = reconcile_pending_sip()

            combined_rows = results["sip"]["rows_loaded"] + reconcile_result.get("rows_loaded", 0)

            if reconcile_result["status"] == "error":
                print("SIP reconciliation failed:", reconcile_result["error"])
                results["sip"] = load_result("error", combined_rows, reconcile_result["error"])

            elif results["sip"]["status"] == "error":
                # Reconciliation succeeded, but this run's PRIMARY load already
                # failed — don't let a successful reconciliation mask that.
                print("SIP reconciliation resolved", reconcile_result["rows_loaded"], "row(s), "
                      "but the primary SIP load this run still failed:", results["sip"]["error"])
                results["sip"] = load_result("error", combined_rows, results["sip"]["error"])

            elif reconcile_result["rows_loaded"]:
                print("SIP reconciliation resolved", reconcile_result["rows_loaded"], "row(s)")
                results["sip"] = load_result("ok", combined_rows)

        except Exception as e:

            print("SIP reconciliation failed")
            print(e)
            results["sip"] = load_result("error", results["sip"]["rows_loaded"], str(e))

    else:

        print(
            "\nGold SIP module not available"
        )
        results["sip"] = load_result("skipped", 0)


    # =====================================================
    # GOLD CLIENTS
    # =====================================================

    if CLIENT_AVAILABLE:

        try:

            print("\nLoading Gold Clients")

            client_df = extract_clients()

            if not client_df.empty:

                client_gold_df = transform_clients(
                    client_df
                )

                if not client_gold_df.empty:

                    results["clients"] = load_clients(
                        client_gold_df
                    )

                    print(
                        "Clients loaded successfully"
                    )

                else:

                    results["clients"] = load_result("skipped", 0)

            else:

                print(
                    "No Client data found"
                )
                results["clients"] = load_result("skipped", 0)

        except Exception as e:

            print("Clients Gold Failed")
            print(e)
            results["clients"] = load_result("error", 0, str(e))

    else:

        results["clients"] = load_result("skipped", 0)


    # =====================================================
    # GOLD FOLIO NOMINEES
    # =====================================================

    if FOLIO_AVAILABLE:

        try:

            print("\nLoading Gold Folio Nominees")

            folio_df = extract_folio_nominees()

            if not folio_df.empty:

                folio_gold_df = transform_folio_nominees(
                    folio_df
                )

                if not folio_gold_df.empty:

                    results["folio_nominees"] = load_folio_nominees(
                        folio_gold_df
                    )

                    print(
                        "Folio Nominees loaded successfully"
                    )

                else:

                    results["folio_nominees"] = load_result("skipped", 0)

            else:

                print(
                    "No Folio Nominee data found"
                )
                results["folio_nominees"] = load_result("skipped", 0)

        except Exception as e:

            print(
                "Folio Nominees Gold Failed"
            )

            print(e)
            results["folio_nominees"] = load_result("error", 0, str(e))

    else:

        results["folio_nominees"] = load_result("skipped", 0)


    print("=" * 80)
    print("GOLD LAYER LOAD COMPLETED")
    print("=" * 80)

    return results


# =====================================================
# RUN
# =====================================================

if __name__ == "__main__":

    load_gold()
