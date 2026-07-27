import csv
import io
import pandas as pd
from utils.db import engine
from etl_investor_master import process_investor_master
from etl_trans import process_transactions
from etl_sip import process_sip

# =====================================================
# READ FILE
# =====================================================

def read_file(file):

    name = file.name.lower()

    # =================================================
    # CSV / TXT
    # =================================================
    if name.endswith((".csv", ".txt")):

        file.seek(0)

        try:
            text = file.read().decode("utf-8")
        except UnicodeDecodeError:
            file.seek(0)
            text = file.read().decode("latin1")


        # FIX WINDOWS NEWLINE ISSUE
        text = text.replace("\r\n", "\n").replace("\r", "\n")


        # Detect delimiter
        first_line = text.split("\n")[0]

        if "\t" in first_line:
            delimiter = "\t"

        elif "," in first_line:
            delimiter = ","

        elif ";" in first_line:
            delimiter = ";"

        else:
            delimiter = "\t"


        reader = csv.reader(
            io.StringIO(text),
            delimiter=delimiter,
            quotechar="'",
            skipinitialspace=True
        )


        rows = []

        for row in reader:
            rows.append(row)


        header = [
            h.strip()
            .strip("'")
            .strip('"')
            for h in rows[0]
        ]


        clean_rows = []


        for row in rows[1:]:

            row = [
                x.strip()
                .strip("'")
                .strip('"')
                for x in row
            ]

            if len(row) == len(header):

                clean_rows.append(row)


            elif len(row) < len(header):

                row.extend(
                    [""] * (len(header) - len(row))
                )

                clean_rows.append(row)


            else:

                print(
                    "Skipping bad row. Expected:",
                    len(header),
                    "Found:",
                    len(row)
                )

                clean_rows.append(
                    row[:len(header)]
                )


        df = pd.DataFrame(
            clean_rows,
            columns=header
        )

    # =================================================
    # EXCEL
    # =================================================
    else:

        file.seek(0)

        df = pd.read_excel(
            file,
            dtype=str,
            keep_default_na=False
        )

    # =====================================================
    # CLEAN COLUMN NAMES
    # =====================================================

    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.strip("'")
        .str.strip('"')
    )

     # Remove blank header columns
    df = df.loc[:, df.columns != ""]

    # Remove duplicate columns
    df = df.loc[:, ~df.columns.duplicated()]

    print(f"\nProcessing file: {file.name}")

    print("Shape:", df.shape)
    print("Columns:", len(df.columns))
    print("Unique columns:", len(df.columns.unique()))

    duplicates = df.columns[df.columns.duplicated()]
    print("Duplicate columns:", duplicates.tolist())

   

    # =====================================================
    # CLEAN VALUES
    # =====================================================

    # object_cols = df.select_dtypes(include="object").columns

    # if len(object_cols):

    #     df[object_cols] = (
    #         df[object_cols]
    #         .astype(str)
    #         .apply(
    #             lambda s: s.str.replace("'", "", regex=False)
    #                     .str.replace('"', "", regex=False)
    #                     .str.strip()
    #         )
    #         .replace({
    #             "nan": "",
    #             "None": "",
    #             "<NA>": ""
    #         })
    #     )
    
    object_cols = df.select_dtypes(include="object").columns.unique()

    for col in object_cols:
        df[col] = (
            df[col]
            .astype(str)
            .str.replace("'", "", regex=False)
            .str.replace('"', "", regex=False)
            .str.strip()
            .replace({
                "nan": "",
                "None": "",
                "<NA>": ""
            })
        )

    # =====================================================
    # DEBUG
    # =====================================================

    print("\n" + "=" * 80)
    print("FILE :", file.name)
    print("ROWS READ :", len(df))
    print("TOTAL COLUMNS :", len(df.columns))
    print("COLUMN NAMES :")
    print(df.columns.tolist())
    print("=" * 80 + "\n")

    return df


def extract_and_push(uploaded_files):

    cams_transaction = []
    kfin_transaction = []
    cams_investor = []
    kfin_investor = []
    cams_sip = []
    kfin_sip = []

    for file in uploaded_files:

        name = file.name.lower()
        df = read_file(file)

        if "trans" in name:
            if "cams" in name:
                cams_transaction.append(df)

            elif "kfin" in name or "karvy" in name:
                kfin_transaction.append(df)

            else:
                cams_transaction.append(df)

        elif "inv" in name:

            if "cams" in name:
                cams_investor.append(df)

            elif "kfin" in name or "karvy" in name:
                kfin_investor.append(df)

            else:
                cams_investor.append(df)

        elif "sip" in name:

            if "cams" in name:
                cams_sip.append(df)

            elif "kfin" in name or "karvy" in name:
                kfin_sip.append(df)

    # =====================================================
    # Transactions
    # =====================================================

    cams_df = (
        pd.concat(cams_transaction, ignore_index=True)
        if cams_transaction else None
    )

    kfin_df = (
        pd.concat(kfin_transaction, ignore_index=True)
        if kfin_transaction else None
    )

    if cams_df is not None or kfin_df is not None:

        process_transactions(
            cams=cams_df,
            kfin=kfin_df
        )
        print("=" * 80)
        print("TRANSACTION DATA BEFORE ETL")
        
        if cams_df is not None:
            print("CAMS")
            print(cams_df.shape)
            print(cams_df.columns.tolist())

        if kfin_df is not None:
            print("KFIN")
            print(kfin_df.shape)
            print(kfin_df.columns.tolist())
        print("=" * 80)

    # Investors
    cams_df = (
        pd.concat(cams_investor, ignore_index=True)
        if cams_investor else None
    )

    kfin_df = (
        pd.concat(kfin_investor, ignore_index=True)
        if kfin_investor else None
    )

    if cams_df is not None or kfin_df is not None:
        process_investor_master(
            cams=cams_df,
            kfin=kfin_df
        )

    # SIP

    cams_df = (
        pd.concat(cams_sip, ignore_index=True)
        if cams_sip else None
    )

    kfin_df = (
        pd.concat(kfin_sip, ignore_index=True)
        if kfin_sip else None
    )

    sip_preview = None

    if cams_df is not None or kfin_df is not None:

        process_sip(
            cams=cams_df,
            kfin=kfin_df
        )

        sip_preview = pd.read_sql(
            "SELECT * FROM bronze.sip_master_new",
            con=engine
        )

    return (
        len(cams_transaction) + len(kfin_transaction),
        len(cams_investor) + len(kfin_investor),
        len(cams_sip) + len(kfin_sip),
        sip_preview
    )