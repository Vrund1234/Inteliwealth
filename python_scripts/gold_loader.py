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

from etl_gold_folio_nominees import (
    extract_folio_nominees,
    transform_folio_nominees,
    load_folio_nominees
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


# Import only if functions exist
try:
    from etl_gold_sip import (
        extract_sip,
        transform_sip,
        load_sip
    )

    SIP_AVAILABLE = True

except ImportError:
    SIP_AVAILABLE = False



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
# MAIN GOLD LOAD FUNCTION
# =====================================================

def load_gold():

    print("=" * 80)
    print("STARTING GOLD LAYER LOAD")
    print("=" * 80)

    # =================================================
    # GOLD TRANSACTIONS
    # =================================================
    try:
        print("\nLoading Gold Transactions")
        transaction_df = extract_transactions()
        transaction_gold_df = transform_transactions(transaction_df)

        if not transaction_gold_df.empty:
            load_transactions(transaction_gold_df)
            print("Transactions loaded successfully")

    except Exception as e:
        print("Transaction Gold Failed")
        print(e)

    # =================================================
    # GOLD HOLDINGS
    # =================================================
    try:
        print("\nLoading Gold Holdings")
        holdings_df = extract_holdings()
        holdings_gold_df = transform_holdings(holdings_df)

        if not holdings_gold_df.empty:
            load_holdings(holdings_gold_df)
            print("Holdings loaded successfully")

    except Exception as e:
        print("Holdings Gold Failed")
        print(e)

    # =================================================
    # GOLD SIP
    # =================================================
    if SIP_AVAILABLE:
        try:
            print("\nLoading Gold SIP")
            sip_df = extract_sip()
            sip_gold_df = transform_sip(sip_df)

            if not sip_gold_df.empty:
                load_sip(sip_gold_df)
                print("SIP loaded successfully")

        except Exception as e:
            print("SIP Gold Failed")
            print(e)

    # =================================================
    # GOLD CLIENTS
    # =================================================
    if CLIENT_AVAILABLE:
        try:
            print("\nLoading Gold Clients")
            client_df = extract_clients()
            client_gold_df = transform_clients(client_df)

            if not client_gold_df.empty:
                load_clients(client_gold_df)
                print("Clients loaded successfully")

        except Exception as e:
            print("Clients Gold Failed")
            print(e)

    # =================================================
    # GOLD AMC
    # =================================================
    try:
        print("\nLoading Gold AMC")
        amc_df = extract_amc()
        amc_gold_df = transform_amc(amc_df)

        if not amc_gold_df.empty:
            load_amc(amc_gold_df)
            print("AMC loaded successfully")

    except Exception as e:
        print("AMC Gold Failed")
        print(e)

    # =================================================
    # GOLD SCHEME
    # =================================================
    try:

        print("\nLoading Gold Scheme")

        transaction_df, investor_df = extract_scheme()

        scheme_gold_df = transform_scheme(
            transaction_df,
            investor_df
        )

        if not scheme_gold_df.empty:

            load_scheme(
                scheme_gold_df
            )

            print("Scheme loaded successfully")

    except Exception as e:

        print("Scheme Gold Failed")
        print(e)

    # =================================================
    # GOLD SCHEME NAV
    # =================================================
    try:
        print("\nLoading Gold Scheme NAV")
        nav_df = extract_scheme_nav()
        nav_gold_df = transform_scheme_nav(nav_df)

        if not nav_gold_df.empty:
            load_scheme_nav(nav_gold_df)
            print("Scheme NAV loaded successfully")

    except Exception as e:
        print("Scheme NAV Gold Failed")
        print(e)

    # =================================================
    # GOLD FOLIO NOMINEES
    # =================================================
    try:
        print("\nLoading Gold Folio Nominees")
        folio_df = extract_folio_nominees()
        folio_gold_df = transform_folio_nominees(folio_df)

        if not folio_gold_df.empty:
            load_folio_nominees(folio_gold_df)
            print("Folio Nominees loaded successfully")

    except Exception as e:
        print("Folio Nominees Gold Failed")
        print(e)

    print("=" * 80)
    print("GOLD LAYER COMPLETED")
    print("=" * 80)