# =====================================================
# GOLD LAYER LOADER
# =====================================================


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

from utils.db import collect_upserts


# =====================================================
# OPTIONAL GOLD SIP
# =====================================================

try:

    from etl_gold_sip import (
        extract_sip,
        transform_sip,
        load_sip
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
# OPTIONAL GOLD CLIENT BANK
# =====================================================

try:

    from etl_gold_client_bank import (
        extract_client_bank,
        transform_client_bank,
        load_client_bank
    )

    CLIENT_BANK_AVAILABLE = True

except ImportError:

    CLIENT_BANK_AVAILABLE = False


# =====================================================
# OPTIONAL CLIENT ADDRESS REVIEW DETECTION
# =====================================================

try:

    from detect_client_address_duplicates import detect as detect_address_duplicates

    ADDRESS_REVIEW_AVAILABLE = True

except ImportError:

    ADDRESS_REVIEW_AVAILABLE = False


# =====================================================
# OPTIONAL GOLD CLIENT ADDRESS
# =====================================================

try:

    from etl_gold_client_address import (
        extract_client_address,
        transform_client_address,
        load_client_address
    )

    CLIENT_ADDRESS_AVAILABLE = True

except ImportError:

    CLIENT_ADDRESS_AVAILABLE = False


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
# PER-ENTITY RESULTS
# =====================================================

# Purely additive. app.py:665 discards load_gold()'s return value, so none of
# this changes what the Streamlit Transform button does. The etl_pipeline
# runner reads it to decide which reserved files to report FAILED (a gold
# entity failing fails only the report types that feed it -- see
# etl_pipeline/dispatch.py) and to write the GOLD rows of
# pipeline.etl_pipeline_log.

# The etl_gold_*.py modules are deliberately NOT modified: their load_*()
# functions already return True/False, and load_gold() already calls
# them one at a time, so a collect_upserts() block around each call is enough
# to attribute an insert/update split to one entity.


GOLD_ENTITIES = (
    "amc",
    "scheme",
    "scheme_nav",
    "transactions",
    "holdings",
    "sip",
    "clients",
    "client_bank",
    "client_address",
    "client_address_review",
    "folio_nominees",
)


def _gold_result(entity, status, total=0, upserts=(), error=None):

    return {
        "entity": entity,
        "status": status,
        "total": total,
        "inserted": sum(u["inserted"] for u in upserts),
        "updated": sum(u["updated"] for u in upserts),
        "error": error,
    }


def _record_gold(results, entity, gold_df, upserts, loaded):

    """
    Turn one entity's load into a result row.

    `loaded` is what the entity's load_*() returned. Several of them catch
    their own insert failure and return False without re-raising, so
    load_gold()'s try/except never sees it -- that False is the only signal.

    A loader that falls off the end returning None counts as COMPLETED:
    the conservative direction, because a spurious FAILED would fail every
    reserved file that entity depends on.
    """

    if loaded is False:

        results[entity] = _gold_result(
            entity,
            "FAILED",
            total=len(gold_df),
            upserts=upserts,
            error=f"{entity}: loader reported failure",
        )

    else:

        results[entity] = _gold_result(
            entity,
            "COMPLETED",
            total=len(gold_df),
            upserts=upserts,
        )


# =====================================================
# MAIN GOLD LOAD FUNCTION
# =====================================================

def load_gold():

    print("=" * 80)
    print("STARTING GOLD LAYER LOAD")
    print("=" * 80)

    results = {
        entity: _gold_result(entity, "SKIPPED")
        for entity in GOLD_ENTITIES
    }


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

                with collect_upserts() as upserts:

                    loaded = load_amc(
                        amc_gold_df
                    )

                _record_gold(
                    results,
                    "amc",
                    amc_gold_df,
                    upserts,
                    loaded
                )

                print("AMC loaded successfully")

        else:

            print("No AMC data found")

    except Exception as e:

        print("AMC Gold Failed")
        print(e)

        results["amc"] = _gold_result(
            "amc",
            "FAILED",
            error=f"{type(e).__name__}: {e}"
        )


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

                with collect_upserts() as upserts:

                    loaded = load_scheme(
                        scheme_gold_df
                    )

                _record_gold(
                    results,
                    "scheme",
                    scheme_gold_df,
                    upserts,
                    loaded
                )

                print("Scheme loaded successfully")

        else:

            print("No Scheme data found")

    except Exception as e:

        print("Scheme Gold Failed")
        print(e)

        results["scheme"] = _gold_result(
            "scheme",
            "FAILED",
            error=f"{type(e).__name__}: {e}"
        )


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

                with collect_upserts() as upserts:

                    loaded = load_scheme_nav(
                        nav_gold_df
                    )

                _record_gold(
                    results,
                    "scheme_nav",
                    nav_gold_df,
                    upserts,
                    loaded
                )

                print("Scheme NAV loaded successfully")

        else:

            print("No Scheme NAV data found")

    except Exception as e:

        print("Scheme NAV Gold Failed")
        print(e)

        results["scheme_nav"] = _gold_result(
            "scheme_nav",
            "FAILED",
            error=f"{type(e).__name__}: {e}"
        )


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

                with collect_upserts() as upserts:

                    loaded = load_transactions(
                        transaction_gold_df
                    )

                _record_gold(
                    results,
                    "transactions",
                    transaction_gold_df,
                    upserts,
                    loaded
                )

                print(
                    "Transactions loaded successfully"
                )

        else:

            print("No Transaction data found")

    except Exception as e:

        print("Transaction Gold Failed")
        print(e)

        results["transactions"] = _gold_result(
            "transactions",
            "FAILED",
            error=f"{type(e).__name__}: {e}"
        )


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

                with collect_upserts() as upserts:

                    loaded = load_holdings(
                        holdings_gold_df
                    )

                _record_gold(
                    results,
                    "holdings",
                    holdings_gold_df,
                    upserts,
                    loaded
                )

                print(
                    "Holdings loaded successfully"
                )

        else:

            print("No Holdings data found")

    except Exception as e:

        print("Holdings Gold Failed")
        print(e)

        results["holdings"] = _gold_result(
            "holdings",
            "FAILED",
            error=f"{type(e).__name__}: {e}"
        )


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

                    with collect_upserts() as upserts:

                        loaded = load_sip(
                            sip_gold_df
                        )

                    _record_gold(
                        results,
                        "sip",
                        sip_gold_df,
                        upserts,
                        loaded
                    )

                    print(
                        "SIP loaded successfully"
                    )

            else:

                print("No SIP data found")

        except Exception as e:

            print("SIP Gold Failed")
            print(e)

            results["sip"] = _gold_result(
                "sip",
                "FAILED",
                error=f"{type(e).__name__}: {e}"
            )

    else:

        print(
            "\nGold SIP module not available"
        )


    # =====================================================
    # CLIENT IDENTITY MAPPING
    #
    # Runs before the clients load, so bronze.client_mapping_review
    # reflects the same silver data the gold load is about to use.
    # A failure here is reported but never blocks the load: the
    # mapping is a review artefact, not a dependency.
    # =====================================================

    try:

        print("\nMapping client identities")

        import client_mapping

        mapping_df = client_mapping.extract_folios()

        if not mapping_df.empty:

            mapped = client_mapping.map_clients(mapping_df)

            mapped = client_mapping.collapse_to_clients(mapped)

            client_mapping.load_review(mapped)

    except Exception as e:

        print("Client identity mapping failed (continuing)")
        print(e)

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

                    with collect_upserts() as upserts:

                        loaded = load_clients(
                            client_gold_df
                        )

                    _record_gold(
                        results,
                        "clients",
                        client_gold_df,
                        upserts,
                        loaded
                    )

                    # load_clients returns False on failure --
                    # printing success regardless reported a
                    # successful load while nothing was inserted.
                    if loaded:

                        print(
                            "Clients loaded successfully"
                        )

                    else:

                        print(
                            "Clients Gold FAILED -- nothing was "
                            "inserted, see the error above"
                        )

            else:

                print(
                    "No Client data found"
                )

        except Exception as e:

            print("Clients Gold Failed")
            print(e)

            results["clients"] = _gold_result(
                "clients",
                "FAILED",
                error=f"{type(e).__name__}: {e}"
            )

    else:

        print(
            "\nGold Clients module not available"
        )


    # =====================================================
    # GOLD CLIENT BANK
    # =====================================================

    if CLIENT_BANK_AVAILABLE:

        try:

            print("\nLoading Gold Client Bank")

            client_bank_df = extract_client_bank()

            if not client_bank_df.empty:

                client_bank_gold_df = transform_client_bank(
                    client_bank_df
                )

                if not client_bank_gold_df.empty:

                    with collect_upserts() as upserts:

                        loaded = load_client_bank(
                            client_bank_gold_df
                        )

                    _record_gold(
                        results,
                        "client_bank",
                        client_bank_gold_df,
                        upserts,
                        loaded
                    )

                    print(
                        "Client Bank loaded successfully"
                    )

                else:

                    print(
                        "No Client Bank data after transformation"
                    )

            else:

                print(
                    "No Client Bank data found"
                )

        except Exception as e:

            print("Client Bank Gold Failed")
            print(e)

            results["client_bank"] = _gold_result(
                "client_bank",
                "FAILED",
                error=f"{type(e).__name__}: {e}"
            )

    else:

        print(
            "\nGold Client Bank module not available"
        )


    # =====================================================
    # GOLD CLIENT ADDRESS
    # =====================================================

    if CLIENT_ADDRESS_AVAILABLE:

        try:

            print("\nLoading Gold Client Address")

            client_address_df = extract_client_address()

            if not client_address_df.empty:

                client_address_gold_df = transform_client_address(
                    client_address_df
                )

                if not client_address_gold_df.empty:

                    with collect_upserts() as upserts:

                        loaded = load_client_address(
                            client_address_gold_df
                        )

                    _record_gold(
                        results,
                        "client_address",
                        client_address_gold_df,
                        upserts,
                        loaded
                    )

                    print(
                        "Client Address loaded successfully"
                    )

                else:

                    print(
                        "No Client Address data after transformation"
                    )

            else:

                print(
                    "No Client Address data found"
                )

        except Exception as e:

            print("Client Address Gold Failed")
            print(e)

            results["client_address"] = _gold_result(
                "client_address",
                "FAILED",
                error=f"{type(e).__name__}: {e}"
            )

    else:

        print(
            "\nGold Client Address module not available"
        )


    # =====================================================
    # CLIENT ADDRESS REVIEW QUEUE
    # =====================================================
    #
    # Detection only -- it queues pairs that are the same address but cannot
    # be proven so by exact matching, and MERGES NOTHING. A fuzzy rule that
    # merged on its own would eventually collapse two addresses that genuinely
    # differ, so the decision stays with a person, exactly as scheme mapping
    # keeps fuzzy matches out of promote_approved()
    # (etl_pipeline/runner.py:157).
    #
    # A non-empty queue is NOT a failure: reporting one would blame source
    # files for a human's inbox and march them to ABANDONED after three
    # attempts. The count goes in the log line instead.
    #
    # The queue is a published detection, not a decision log -- the app owns
    # the decisions -- so the same pairs are re-detected every run and the
    # size holds steady. A stable count is the healthy reading.
    # =====================================================

    if ADDRESS_REVIEW_AVAILABLE:

        try:

            print("\nDetecting similar client addresses")

            outcome = detect_address_duplicates()

            if outcome is None:

                results["client_address_review"] = _gold_result(
                    "client_address_review",
                    "FAILED",
                    error="client_address_review: detection reported failure"
                )

            else:

                results["client_address_review"] = _gold_result(
                    "client_address_review",
                    "COMPLETED",
                    total=outcome["detected"],
                    upserts=[{
                        "inserted": outcome["newly_queued"],
                        "updated": 0
                    }],
                )

                print(
                    "Client address review queue:",
                    outcome["queued"],
                    "pair(s) published for review"
                )

        except Exception as e:

            print("Client Address Review Detection Failed")
            print(e)

            results["client_address_review"] = _gold_result(
                "client_address_review",
                "FAILED",
                error=f"{type(e).__name__}: {e}"
            )

    else:

        print(
            "\nClient address review detection not available"
        )


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

                    with collect_upserts() as upserts:

                        loaded = load_folio_nominees(
                            folio_gold_df
                        )

                    _record_gold(
                        results,
                        "folio_nominees",
                        folio_gold_df,
                        upserts,
                        loaded
                    )

                    print(
                        "Folio Nominees loaded successfully"
                    )

            else:

                print(
                    "No Folio Nominee data found"
                )

        except Exception as e:

            print(
                "Folio Nominees Gold Failed"
            )

            print(e)

            results["folio_nominees"] = _gold_result(
                "folio_nominees",
                "FAILED",
                error=f"{type(e).__name__}: {e}"
            )

    else:

        print(
            "\nGold Folio Nominees module not available"
        )


    # =====================================================
    # GOLD LAYER COMPLETED
    # =====================================================

    print("=" * 80)
    print("GOLD LAYER LOAD COMPLETED")
    print("=" * 80)

    return results


# =====================================================
# RUN
# =====================================================

if __name__ == "__main__":

    load_gold()