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
# OPTIONAL BROKERAGE SUMMARY (WBR36 / WBR36H / ...)
# =====================================================

try:

    from etl_gold_brokerage_summary import (
        extract_brokerage_summary,
        transform_brokerage_summary,
        load_brokerage_summary
    )

    BROKERAGE_AVAILABLE = True

except ImportError:

    BROKERAGE_AVAILABLE = False


# =====================================================
# OPTIONAL INVESTOR KYC STATUS (WBR56 / ...)
# =====================================================

try:

    from etl_gold_kyc_status import (
        extract_kyc_status,
        transform_kyc_status,
        load_kyc_status
    )

    KYC_STATUS_AVAILABLE = True

except ImportError:

    KYC_STATUS_AVAILABLE = False


# =====================================================
# OPTIONAL INVALID EUIN (WBR68 / ...)
# =====================================================

try:

    from etl_gold_invalid_euin import (
        extract_invalid_euin,
        transform_invalid_euin,
        load_invalid_euin
    )

    INVALID_EUIN_AVAILABLE = True

except ImportError:

    INVALID_EUIN_AVAILABLE = False


# =====================================================
# MAIN GOLD LOAD FUNCTION
# =====================================================

def load_gold():

    print("=" * 80)
    print("STARTING GOLD LAYER LOAD")
    print("=" * 80)


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

                load_amc(
                    amc_gold_df
                )

                print("AMC loaded successfully")

        else:

            print("No AMC data found")

    except Exception as e:

        print("AMC Gold Failed")
        print(e)


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

                load_scheme(
                    scheme_gold_df
                )

                print("Scheme loaded successfully")

        else:

            print("No Scheme data found")

    except Exception as e:

        print("Scheme Gold Failed")
        print(e)


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

                load_scheme_nav(
                    nav_gold_df
                )

                print("Scheme NAV loaded successfully")

        else:

            print("No Scheme NAV data found")

    except Exception as e:

        print("Scheme NAV Gold Failed")
        print(e)


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

                load_transactions(
                    transaction_gold_df
                )

                print(
                    "Transactions loaded successfully"
                )

        else:

            print("No Transaction data found")

    except Exception as e:

        print("Transaction Gold Failed")
        print(e)


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

                load_holdings(
                    holdings_gold_df
                )

                print(
                    "Holdings loaded successfully"
                )

        else:

            print("No Holdings data found")

    except Exception as e:

        print("Holdings Gold Failed")
        print(e)


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

                    load_sip(
                        sip_gold_df
                    )

                    print(
                        "SIP loaded successfully"
                    )

            else:

                print(
                    "No SIP data found"
                )

        except Exception as e:

            print("SIP Gold Failed")
            print(e)

    else:

        print(
            "\nGold SIP module not available"
        )


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

                    load_clients(
                        client_gold_df
                    )

                    print(
                        "Clients loaded successfully"
                    )

            else:

                print(
                    "No Client data found"
                )

        except Exception as e:

            print("Clients Gold Failed")
            print(e)


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

                    load_folio_nominees(
                        folio_gold_df
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


    # =====================================================
    # GOLD BROKERAGE SUMMARY
    # =====================================================

    if BROKERAGE_AVAILABLE:

        try:

            print("\nLoading Gold Brokerage Summary")

            brokerage_df = extract_brokerage_summary()

            if not brokerage_df.empty:

                brokerage_gold_df = (
                    transform_brokerage_summary(
                        brokerage_df
                    )
                )

                if not brokerage_gold_df.empty:

                    load_brokerage_summary(
                        brokerage_gold_df
                    )

                    print(
                        "Brokerage Summary "
                        "loaded successfully"
                    )

            else:

                print(
                    "No Brokerage Summary data found"
                )

        except Exception as e:

            print("Brokerage Summary Gold Failed")
            print(e)

    else:

        print(
            "\nGold Brokerage Summary module "
            "not available"
        )


    # =====================================================
    # GOLD INVESTOR KYC STATUS
    # =====================================================

    if KYC_STATUS_AVAILABLE:

        try:

            print("\nLoading Gold Investor KYC Status")

            kyc_df = extract_kyc_status()

            if not kyc_df.empty:

                kyc_gold_df = transform_kyc_status(
                    kyc_df
                )

                if not kyc_gold_df.empty:

                    load_kyc_status(
                        kyc_gold_df
                    )

                    print(
                        "Investor KYC Status "
                        "loaded successfully"
                    )

            else:

                print(
                    "No Investor KYC Status data found"
                )

        except Exception as e:

            print("Investor KYC Status Gold Failed")
            print(e)

    else:

        print(
            "\nGold Investor KYC Status module "
            "not available"
        )


    # =====================================================
    # GOLD INVALID EUIN
    # =====================================================

    if INVALID_EUIN_AVAILABLE:

        try:

            print("\nLoading Gold Invalid EUIN")

            euin_df = extract_invalid_euin()

            if not euin_df.empty:

                euin_gold_df = transform_invalid_euin(
                    euin_df
                )

                if not euin_gold_df.empty:

                    load_invalid_euin(
                        euin_gold_df
                    )

                    print(
                        "Invalid EUIN "
                        "loaded successfully"
                    )

            else:

                print(
                    "No Invalid EUIN data found"
                )

        except Exception as e:

            print("Invalid EUIN Gold Failed")
            print(e)

    else:

        print(
            "\nGold Invalid EUIN module "
            "not available"
        )


    print("=" * 80)
    print("GOLD LAYER LOAD COMPLETED")
    print("=" * 80)


# =====================================================
# RUN
# =====================================================

if __name__ == "__main__":

    load_gold()