import os

import streamlit as st
import pandas as pd
import traceback
from utils.triggers import create_triggers
from raw_ingestion import extract_and_push
from transformations.transform import load_silver
from utils.db import read_table
from gold_loader import load_gold
from export_wbr import export_wbr_reports
from etl_gold_wbr import load_wbr_gold
from mapping import WBR_OUTPUT_LAYOUTS

st.set_page_config(
    page_title="Mutual Fund",
    page_icon="📊",
    layout="wide"
)

# ==============================
# HEADER
# ==============================

st.markdown(
    "<h1 style='text-align:center;'>📊 Mutual Funds Dashboard</h1>",
    unsafe_allow_html=True
)

st.divider()

# ==============================
# SESSION STATE
# ==============================

if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0

if "extracted" not in st.session_state:
    st.session_state.extracted = False

if "transformed" not in st.session_state:
    st.session_state.transformed = False

if "bronze_data" not in st.session_state:
    st.session_state.bronze_data = {}

if "silver_data" not in st.session_state:
    st.session_state.silver_data = {}

if "gold_data" not in st.session_state:
    st.session_state.gold_data = {}

if "current_layer" not in st.session_state:
    st.session_state.current_layer = "bronze"

if "wbr_exports" not in st.session_state:
    st.session_state.wbr_exports = []

if "uploaded_types" not in st.session_state:
    st.session_state.uploaded_types = {
        "investor": False,
        "transaction": False,
        "sip": False,
    }


# ==============================
# HELPERS
# ==============================

def is_valid(df):
    return (
        df is not None
        and isinstance(df, pd.DataFrame)
        and not df.empty
    )


# ==============================
# FILE UPLOAD UI
# ==============================

st.subheader("📂 Upload CAMS / KFintech Excel Files")

col1, col2 = st.columns([10, 2], vertical_alignment="top")

with col1:

    uploaded_files = st.file_uploader(
        "Upload Files",
        # xls is here for the CAMS WBR reports, which are delivered as legacy
        # BIFF .xls. Reading them needs xlrd >= 2.0 in the environment.
        type=["xlsx", "xls", "csv", "txt"],
        accept_multiple_files=True,
        key=f"uploader_{st.session_state.uploader_key}"
    )

with col2:

    st.markdown(
        "<div style='height:32px;'></div>",
        unsafe_allow_html=True
    )

    if st.button(
        "🗑 Clear",
        width="stretch"
    ):

        st.session_state.uploader_key += 1

        st.session_state.extracted = False
        st.session_state.transformed = False

        st.session_state.current_layer = "bronze"

        st.session_state.bronze_data = {}
        st.session_state.silver_data = {}
        st.session_state.gold_data = {}
        st.session_state.wbr_exports = []

        st.session_state.uploaded_types = {
            "investor": False,
            "transaction": False,
            "sip": False,
        }

        st.rerun()

st.divider()

# ==============================
# BUTTONS
# ==============================

col1, col2 = st.columns(2)

extract_btn = col1.button(
    "🟢 Extract Raw Data",
    use_container_width=True
)

transform_btn = col2.button(
    "🟡 Transform Data",
    use_container_width=True
)

st.divider()


# ==============================
# EXTRACT LOGIC
# ==============================

if extract_btn:

    st.session_state.transformed = False
    st.session_state.current_layer = "bronze"
    st.session_state.silver_data = {}

    try:

        if not uploaded_files:

            st.warning("⚠ Please upload one or more files.")
            st.stop()

        st.info("Reading uploaded files...")

        # Detect uploaded file types.
        #
        # One loop. The dict is built once, before it, so a flag set by an
        # earlier file survives the later ones.
        uploaded_types = {
            "investor": False,
            "transaction": False,
            "sip": False,
        }

        for file in uploaded_files:

            name = file.name.lower()

            # ---------- CAMS ----------
            if name.endswith("r9.csv"):
                uploaded_types["investor"] = True

            elif name.endswith("r2.csv"):
                uploaded_types["transaction"] = True

            elif name.endswith("r49.csv"):
                uploaded_types["sip"] = True

            # ---------- KFIN ----------
            elif "mfsd211" in name:
                uploaded_types["investor"] = True

            elif "mfsd201" in name:
                uploaded_types["transaction"] = True

            elif "mfsd243" in name:
                uploaded_types["sip"] = True

        st.session_state.uploaded_types = uploaded_types

        # Run Raw Ingestion
        (
            transaction_count,
            investor_count,
            sip_count,
            sip_preview
        ) = extract_and_push(
            uploaded_files
        )
        create_triggers()

        st.success(
            f"✔ Extraction Complete "
            f"(Transactions: {transaction_count}, "
            f"Investor: {investor_count}, "
            f"SIP: {sip_count})"
        )

        # Bronze Preview
        bronze_data = {}

        if uploaded_types["investor"]:
            bronze_data["Investor Master"] = read_table(
                "bronze",
                "investor_master"
            )

        if uploaded_types["transaction"]:
            bronze_data["Transactions"] = read_table(
                "bronze",
                "transaction_master_new"
            )

        if uploaded_types["sip"]:
            bronze_data["SIP"] = read_table(
                "bronze",
                "sip_master_new"
            )

        st.session_state.bronze_data = bronze_data
        st.session_state.extracted = True
        st.session_state.current_layer = "bronze"

        st.success("✔ Extraction Completed + DB Load Done")

    except Exception:

        st.error("Extraction Failed")
        st.code(traceback.format_exc())

# ==============================
# TRANSFORM LOGIC
# ==============================

if transform_btn:

    if not st.session_state.extracted:

        st.warning("⚠ Run Extract First")

    else:

        try:

            st.info("Running transformation layer...")

            # Bronze → Silver
            load_silver()
            create_triggers()

            load_gold()
            create_triggers()

            # Silver → Gold
            st.info("Loading Gold Layer...")

            uploaded = st.session_state.uploaded_types

            silver_data = {}

            if uploaded["investor"]:

                silver_data["Investor Master"] = read_table(
                    "silver",
                    "investor_master"
                )

            if uploaded["transaction"]:

                silver_data["Transactions"] = read_table(
                    "silver",
                    "transaction_master_new"
                )

            if uploaded["sip"]:

                silver_data["SIP"] = read_table(
                    "silver",
                    "sip_master_new"
                )

            gold_data = {}

            gold_data["AMC"] = read_table(
                "gold",
                "amc"
            )

            gold_data["Scheme"] = read_table(
                "gold",
                "scheme"
            )

            gold_data["Scheme NAV"] = read_table(
                "gold",
                "scheme_nav"
            )

            gold_data["Clients"] = read_table(
                "gold",
                "clients"
            )

            gold_data["Transactions"] = read_table(
                "gold",
                "transactions"
            )

            gold_data["Holdings"] = read_table(
                "gold",
                "holdings"
            )

            gold_data["Folio Nominees"] = read_table(
                "gold",
                "folio_nominees"
            )

            gold_data["SIP"] = read_table(
                "gold",
                "sip"
            )

            # =====================================================
            # CAMS WBR REPORTS
            # =====================================================
            #
            # Derived from silver, not uploaded. They are built whenever
            # transaction or investor data is present, because that is what they
            # are made of: WBR36 and WBR68 come from
            # silver.transaction_master_new, WBR56 from silver.investor_master.
            #
            # Neither the gold load nor the export may fail the transformation.
            # The entity gold tables are the primary deliverable and are already
            # committed by this point, and the reports can be rebuilt at any time
            # by running etl_gold_wbr.py and export_wbr.py.

            if uploaded.get("transaction") or uploaded.get("investor"):

                st.info("Building CAMS WBR reports from silver...")

                try:

                    load_wbr_gold()

                    gold_data["Brokerage By Scheme"] = read_table(
                        "gold",
                        "brokerage_by_scheme"
                    )

                    gold_data["Investor KYC Status"] = read_table(
                        "gold",
                        "investor_kyc_status"
                    )

                    gold_data["Invalid EUIN"] = read_table(
                        "gold",
                        "invalid_euin"
                    )

                    st.session_state.wbr_exports = export_wbr_reports()

                except Exception:

                    st.session_state.wbr_exports = []

                    st.warning(
                        "⚠ WBR report build failed. The entity gold tables are "
                        "loaded; run etl_gold_wbr.py then export_wbr.py to retry."
                    )

                    st.code(traceback.format_exc())

            st.session_state.gold_data = gold_data

            st.session_state.silver_data = silver_data
            st.session_state.transformed = True
            st.session_state.current_layer = "silver_gold"

            st.success("✔ Transformation Completed + Silver Loaded to DB")

        except Exception:

            st.error("Transformation Failed")
            st.code(traceback.format_exc())

# ==============================
# PREVIEW
# ==============================

pretty_names = {
    "Investor Master": "📘 Master Table (Investor)",
    "Transactions": "📊 Transaction Table",
    "SIP": "📈 SIP Table",
    "Brokerage By Scheme": "💰 WBR36 / WBR36H — Brokerage By Scheme",
    "Investor KYC Status": "🪪 WBR56 — Investor KYC Status",
    "Invalid EUIN": "⚠️ WBR68 — Invalid EUIN"
}


if st.session_state.current_layer == "bronze":

    st.markdown("## 📄 Bronze Layer Preview")

    for name, df in st.session_state.bronze_data.items():

        if is_valid(df):

            with st.container(border=True):

                st.markdown(
                    f"### {pretty_names.get(name, name)}"
                )

                c1, c2 = st.columns(2)

                c1.metric("Rows", len(df))
                c2.metric("Columns", len(df.columns))

                st.dataframe(
                    df,
                    width="stretch",
                    height=300
                )

                st.divider()


elif st.session_state.current_layer == "silver_gold":

    # ==============================
    # SILVER PREVIEW
    # ==============================

    st.markdown("## ✨ Silver Layer Preview")

    for name, df in st.session_state.silver_data.items():

        if is_valid(df):

            with st.container(border=True):

                st.markdown(
                    f"### {pretty_names.get(name, name)}"
                )

                c1, c2 = st.columns(2)

                c1.metric("Rows", len(df))
                c2.metric("Columns", len(df.columns))

                st.dataframe(
                    df,
                    width="stretch",
                    height=300
                )

                st.divider()


    # ==============================
    # GOLD PREVIEW
    # ==============================

    st.markdown("## ⭐ Gold Layer Preview")

    for name, df in st.session_state.gold_data.items():

        if is_valid(df):

            with st.container(border=True):

                st.markdown(
                    f"### {name}"
                )

                c1, c2 = st.columns(2)

                c1.metric("Rows", len(df))
                c2.metric("Columns", len(df.columns))

                st.dataframe(
                    df,
                    width="stretch",
                    height=300
                )

                st.divider()


    # ==============================
    # WBR REPORT DOWNLOADS
    # ==============================
    #
    # One block per report, four in total, each offering every format that was
    # written. A report that produced no file is shown with its error rather
    # than hidden, so a failed export is visible.

    if st.session_state.wbr_exports:

        st.markdown("## 📥 CAMS WBR Reports")

        st.caption(
            "Regenerated from the gold tables. Column order, column names and "
            "date formats reproduce the provider's own layout."
        )

        for result in st.session_state.wbr_exports:

            code = result["report_code"]

            layout = WBR_OUTPUT_LAYOUTS.get(code, {})

            with st.container(border=True):

                st.markdown(
                    f"### {code} — {layout.get('file_stem', code)}"
                )

                c1, c2 = st.columns(2)

                c1.metric("Rows", result["rows"])
                c2.metric("Columns", result["columns"])

                if result.get("error"):

                    st.error(f"Export failed : {result['error']}")

                    continue

                buttons = st.columns(
                    max(len(result["files"]), 1)
                )

                for column, path in zip(buttons, result["files"]):

                    filename = os.path.basename(path)

                    extension = os.path.splitext(filename)[1].lstrip(".")

                    try:

                        with open(path, "rb") as handle:
                            payload = handle.read()

                    except OSError as e:

                        column.error(f"{filename} unreadable : {e}")

                        continue

                    column.download_button(
                        f"⬇ {extension.upper()}",
                        data=payload,
                        file_name=filename,
                        key=f"download_{code}_{extension}",
                        width="stretch"
                    )

                st.divider()