# =====================================================
# GOLD LAYER LOADER
# =====================================================

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
    from etl_sip import (
        extract_sip,
        transform_sip,
        load_sip
    )
    SIP_AVAILABLE = True

except ImportError:
    SIP_AVAILABLE = False



try:
    from etl_investor_master import (
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

        print(
            f"Silver Transactions Rows : {len(transaction_df)}"
        )


        transaction_gold_df = transform_transactions(
            transaction_df
        )


        print(
            f"Gold Transactions Rows : {len(transaction_gold_df)}"
        )


        if not transaction_gold_df.empty:

            load_transactions(
                transaction_gold_df
            )

            print(
                "Transactions loaded successfully"
            )

        else:

            print(
                "No transaction data to load"
            )


    except Exception as e:

        print(
            "Transaction Gold Failed"
        )

        print(e)



    # =================================================
    # GOLD HOLDINGS
    # =================================================

    try:

        print("\nLoading Gold Holdings")


        holdings_df = extract_holdings()


        print(
            f"Silver Holdings Rows : {len(holdings_df)}"
        )


        holdings_gold_df = transform_holdings(
            holdings_df
        )


        print(
            f"Gold Holdings Rows : {len(holdings_gold_df)}"
        )


        if not holdings_gold_df.empty:

            load_holdings(
                holdings_gold_df
            )

            print(
                "Holdings loaded successfully"
            )

        else:

            print(
                "No holdings data to load"
            )


    except Exception as e:

        print(
            "Holdings Gold Failed"
        )

        print(e)



    # =================================================
    # GOLD SIP
    # =================================================

    if SIP_AVAILABLE:

        try:

            print("\nLoading Gold SIP")


            sip_df = extract_sip()


            print(
                f"Silver SIP Rows : {len(sip_df)}"
            )


            sip_gold_df = transform_sip(
                sip_df
            )


            print(
                f"Gold SIP Rows : {len(sip_gold_df)}"
            )


            if not sip_gold_df.empty:

                load_sip(
                    sip_gold_df
                )

                print(
                    "SIP loaded successfully"
                )


        except Exception as e:

            print(
                "SIP Gold Failed"
            )

            print(e)

    else:

        print(
            "\nSIP ETL not available"
        )



    # =================================================
    # GOLD CLIENTS
    # =================================================

    if CLIENT_AVAILABLE:

        try:

            print("\nLoading Gold Clients")


            client_df = extract_clients()


            print(
                f"Silver Clients Rows : {len(client_df)}"
            )


            client_gold_df = transform_clients(
                client_df
            )


            print(
                f"Gold Clients Rows : {len(client_gold_df)}"
            )


            if not client_gold_df.empty:

                load_clients(
                    client_gold_df
                )

                print(
                    "Clients loaded successfully"
                )


        except Exception as e:

            print(
                "Clients Gold Failed"
            )

            print(e)

    else:

        print(
            "\nClient ETL not available"
        )


    print("=" * 80)
    print("GOLD LAYER COMPLETED")
    print("=" * 80)

    transaction_df = extract_transactions()
    print("Extracted:", len(transaction_df))

    transaction_gold_df = transform_transactions(transaction_df)
    print("Transformed:", len(transaction_gold_df))

    result = load_transactions(transaction_gold_df)
    print("Load result:", result)

def load_gold():

    print("=" * 80)
    print("STARTING GOLD LAYER")
    print("=" * 80)

    print("Loading Gold Transactions...")

    transaction_df = extract_transactions()
    print("Silver rows:", len(transaction_df))

    transaction_gold_df = transform_transactions(transaction_df)
    print("Gold rows:", len(transaction_gold_df))

    result = load_transactions(transaction_gold_df)
    print("Insert Result:", result)

    print("=" * 80)