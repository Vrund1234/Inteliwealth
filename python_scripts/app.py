import streamlit as st
import pandas as pd
import traceback

from utils.triggers import create_triggers
from raw_ingestion import extract_and_push
from transformations.transform import load_silver
from utils.db import read_table
from gold_loader import load_gold

# Scheme Mapping
from scheme_mapping import load_scheme_mapping
from map_unmatched_nav_name import (
    RULE_NAME,
    RULE_NAME_TIER2,
    RULE_NAME_TIER3,
)
from promote_approved_mappings import RULE_NAMES as REVIEW_RULES
from promote_approved_mappings import promote_approved

# WBR reports (brokerage summary)
from mapping_wbr import identify_brokerage_file
from utils.db import engine
from sqlalchemy import bindparam, text


st.set_page_config(
    page_title="Mutual Fund",
    page_icon="📊",
    layout="wide"
)


# =========================================================
# HEADER
# =========================================================

st.markdown(
    "<h1 style='text-align:center;'>📊 Mutual Funds Dashboard</h1>",
    unsafe_allow_html=True
)

st.divider()


# =========================================================
# SESSION STATE
# =========================================================

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

if "uploaded_types" not in st.session_state:
    st.session_state.uploaded_types = {
        "investor": False,
        "transaction": False,
        "sip": False,
        "brokerage": False
    }


# =========================================================
# HELPERS
# =========================================================

def is_valid(df):
    return (
        df is not None
        and isinstance(df, pd.DataFrame)
        and not df.empty
    )


# =========================================================
# FILE UPLOAD UI
# =========================================================

st.subheader("📂 Upload CAMS / KFintech Excel Files")

col1, col2 = st.columns(
    [10, 2],
    vertical_alignment="top"
)


# =========================================================
# FILE UPLOADER
# =========================================================

with col1:

    uploaded_files = st.file_uploader(
        "Upload Files",
        type=["xlsx", "xls", "csv", "txt", "dbf"],
        accept_multiple_files=True,
        key=f"uploader_{st.session_state.uploader_key}"
    )


# =========================================================
# CLEAR BUTTON
# =========================================================

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

        st.session_state.uploaded_types = {
            "investor": False,
            "transaction": False,
            "sip": False
        }

        st.rerun()


st.divider()


# =========================================================
# BUTTONS
# =========================================================

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


# =========================================================
# EXTRACT LOGIC
# =========================================================

if extract_btn:

    st.session_state.transformed = False
    st.session_state.current_layer = "bronze"
    st.session_state.silver_data = {}
    st.session_state.gold_data = {}

    try:

        # =================================================
        # CHECK FILE UPLOAD
        # =================================================

        if not uploaded_files:

            st.warning(
                "⚠ Please upload one or more files."
            )

            st.stop()


        # =================================================
        # STEP 1 : DETECT UPLOADED FILE TYPES
        # =================================================

        st.info(
            "Reading uploaded files..."
        )

        uploaded_types = {
            "investor": False,
            "transaction": False,
            "sip": False,
            "brokerage": False
        }

        for file in uploaded_files:

            name = file.name.lower()

            # ---------- BROKERAGE (WBR36 / WBR36H) ----------
            #
            # Same registry raw_ingestion.py uses, so the
            # banner and the real routing cannot disagree.

            if identify_brokerage_file(name) is not None:

                uploaded_types["brokerage"] = True

            # ---------- CAMS ----------

            elif name.endswith("r9.csv"):

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


        # =================================================
        # STEP 2 : BRONZE EXTRACTION
        # =================================================

        st.info(
            "1️⃣ Loading raw data into Bronze..."
        )

        (
            transaction_count,
            investor_count,
            sip_count,
            sip_preview,
            brokerage_count
        ) = extract_and_push(
            uploaded_files
        )

        create_triggers()


        st.success(
            f"✔ Bronze Extraction Complete "
            f"(Transactions: {transaction_count}, "
            f"Investor: {investor_count}, "
            f"SIP: {sip_count}, "
            f"Brokerage: {brokerage_count})"
        )


        # =================================================
        # STEP 3 : SCHEME MAPPING
        # =================================================
        #
        # IMPORTANT:
        # scheme_mapping.py now reads directly from:
        #
        # bronze.transaction_master_new
        #
        # Therefore NO Silver refresh is required here.
        #
        # Flow:
        #
        # Uploaded File
        #       ↓
        # Bronze transaction_master_new
        #       ↓
        # scheme_mapping.py
        #       ↓
        # bronze.scheme_mapping
        #
        # =================================================

        if uploaded_types["transaction"]:

            st.info(
                "2️⃣ Running Scheme Mapping from Bronze..."
            )

            # load_scheme_mapping() now also applies any approvals made in
            # bronze.scheme_mapping_review since the last run, then re-queues
            # whatever is still unresolved. One call, nothing left stale.
            mapping_summary = load_scheme_mapping()

            st.success(
                "✔ Scheme Mapping Completed"
            )

            if mapping_summary["newly_mapped"]:
                st.success(
                    f"✔ Applied {mapping_summary['newly_mapped']} "
                    f"previously-approved scheme(s) from the review queue."
                )

            if mapping_summary["newly_queued"]:
                st.warning(
                    f"⚠ {mapping_summary['newly_queued']} scheme(s) matched "
                    f"and are awaiting approval "
                    f"(NAV+name: {mapping_summary['queued_tier1']}, "
                    f"name-only: {mapping_summary['queued_tier2']}). "
                    "They are NOT mapped yet — approve them, then run Extract "
                    "again to apply them."
                )

            if mapping_summary["ambiguous"]:
                st.warning(
                    f"⚠ {mapping_summary['ambiguous']} scheme(s) matched more "
                    "than one candidate and were deliberately left unmapped."
                )

            if mapping_summary["still_unmatched"]:
                st.info(
                    f"ℹ {mapping_summary['still_unmatched']} scheme(s) remain "
                    f"unmatched ({mapping_summary['no_candidate']} with no "
                    "candidate found)."
                )

        else:

            st.info(
                "ℹ No transaction file uploaded. "
                "Scheme Mapping skipped."
            )


        # =================================================
        # STEP 4 : BRONZE PREVIEW
        # =================================================

        st.info(
            "3️⃣ Preparing Bronze preview..."
        )

        bronze_data = {}


        # ---------- INVESTOR ----------

        if uploaded_types["investor"]:

            bronze_data["Investor Master"] = read_table(
                "bronze",
                "investor_master"
            )


        # ---------- TRANSACTION ----------

        if uploaded_types["transaction"]:

            bronze_data["Transactions"] = read_table(
                "bronze",
                "transaction_master_new"
            )


        # ---------- SIP ----------

        if uploaded_types["sip"]:

            bronze_data["SIP"] = read_table(
                "bronze",
                "sip_master_new"
            )


        # ---------- BROKERAGE SUMMARY ----------

        if uploaded_types["brokerage"]:

            bronze_data["Brokerage Summary"] = read_table(
                "bronze",
                "brokerage_summary"
            )


        # ---------- SCHEME MAPPING ----------

        try:

            scheme_mapping_data = read_table(
                "bronze",
                "scheme_mapping"
            )

            if is_valid(scheme_mapping_data):

                bronze_data["Scheme Mapping"] = (
                    scheme_mapping_data
                )

        except Exception:

            # Scheme mapping preview should not
            # fail the entire extraction.
            pass


        # =================================================
        # SAVE SESSION STATE
        # =================================================

        st.session_state.bronze_data = bronze_data

        st.session_state.extracted = True

        st.session_state.current_layer = "bronze"


        st.success(
            "✔ Extraction Completed + "
            "Bronze + Scheme Mapping Updated"
        )


    except Exception:

        st.error(
            "❌ Extraction Failed"
        )

        st.code(
            traceback.format_exc()
        )


# =========================================================
# SCHEME MAPPING REVIEW
# =========================================================
# The approval gate for fallback matches. Kept out of the ingestion run on
# purpose: a wrong scheme mapping silently misattributes every holding and
# transaction behind it, so mapping is a decision someone makes, not a side
# effect of uploading a file.

with st.expander("🔍 Scheme Mapping Review", expanded=False):

    # Queried directly rather than through read_table(), which applies a
    # LIMIT: the approve button updates every pending row in the table, so a
    # truncated preview would have approved more than it displayed.
    # All four rules that queue rows here, not just the fallback pair. Tier 3
    # and the engine's own STRUCT_EXACT ambiguities were invisible in this
    # panel and therefore unreviewable, while promote_approved_mappings was
    # perfectly willing to apply them.
    awaiting = pd.read_sql(
        text(
            """
            SELECT rta, rta_scheme_code, rta_scheme_name,
                   candidate_rank, candidate_amfi_code, candidate_amfi_name,
                   candidate_score, rule_name
            FROM bronze.scheme_mapping_review
            WHERE reviewer_decision IS NULL
              AND rule_name = ANY(:rules)
            ORDER BY rta, rta_scheme_code, candidate_rank
            """
        ),
        engine,
        params={"rules": list(REVIEW_RULES)},
    )

    if awaiting.empty:
        st.success("✔ Nothing pending. Every queued scheme has a decision.")

    else:
        # A scheme offering several candidates is an ambiguity, and approving
        # all of them restates it rather than resolving it -- promotion refuses
        # such a scheme outright. So the two cases are separated here: only
        # single-candidate rows can be approved in bulk.
        counts = awaiting.groupby(["rta", "rta_scheme_code"])["candidate_amfi_code"].transform("nunique")
        single = awaiting[counts == 1]
        contested = awaiting[counts > 1]

        reviewer = st.text_input(
            "Your name (recorded against every decision)",
            key="reviewer_name",
        )

        # ---------------- single-candidate rows ----------------
        if not single.empty:
            st.markdown(f"#### {len(single)} scheme(s) with one candidate")
            st.caption(
                f"`{RULE_NAME}` carries two independent signals (NAV and name). "
                f"`{RULE_NAME_TIER2}` has only the name. "
                f"`{RULE_NAME_TIER3}` is a close-but-inexact name match — "
                "check those most carefully."
            )
            st.dataframe(
                single.drop(columns=["candidate_rank"]),
                use_container_width=True,
            )

            if st.button("✅ Approve all single-candidate schemes",
                         use_container_width=True):
                if not reviewer.strip():
                    st.error("Enter your name before approving.")
                else:
                    pairs = list(
                        single[["rta", "rta_scheme_code"]]
                        .drop_duplicates().itertuples(index=False, name=None)
                    )
                    with engine.begin() as conn:
                        updated = conn.execute(
                            text(
                                "UPDATE bronze.scheme_mapping_review "
                                "   SET reviewer_decision = 'APPROVED', "
                                "       reviewed_by = :who, reviewed_at = now() "
                                " WHERE reviewer_decision IS NULL "
                                "   AND (rta, rta_scheme_code) IN :pairs"
                            ).bindparams(bindparam("pairs", expanding=True)),
                            {"who": reviewer.strip(), "pairs": pairs},
                        ).rowcount
                    st.success(f"✔ Approved {updated} row(s).")
                    st.rerun()

        # ---------------- ambiguous schemes ----------------
        if not contested.empty:
            schemes = contested[["rta", "rta_scheme_code"]].drop_duplicates()
            st.markdown(
                f"#### {len(schemes)} scheme(s) with several candidates"
            )
            st.warning(
                "These matched more than one AMFI scheme. Pick one per scheme —"
                " approving several restates the ambiguity, and promotion "
                "refuses any scheme whose candidates disagree."
            )

            for row in schemes.itertuples(index=False):
                options = contested[
                    (contested["rta"] == row.rta)
                    & (contested["rta_scheme_code"] == row.rta_scheme_code)
                ]
                st.markdown(
                    f"**{row.rta} / {row.rta_scheme_code}** — "
                    f"{options.iloc[0]['rta_scheme_name']}"
                )
                labels = {
                    f"{o.candidate_amfi_code} — {o.candidate_amfi_name}":
                        o.candidate_amfi_code
                    for o in options.itertuples()
                }
                chosen = st.radio(
                    "Correct AMFI scheme",
                    options=["(leave undecided)"] + list(labels),
                    key=f"pick_{row.rta}_{row.rta_scheme_code}",
                )
                if st.button(
                    "Save decision",
                    key=f"save_{row.rta}_{row.rta_scheme_code}",
                ):
                    if not reviewer.strip():
                        st.error("Enter your name before deciding.")
                    elif chosen == "(leave undecided)":
                        st.error("Pick a candidate first.")
                    else:
                        # The rejected candidates are recorded as REJECTED
                        # rather than left NULL, so the decision is durable and
                        # the scheme does not reappear as unreviewed.
                        with engine.begin() as conn:
                            conn.execute(
                                text(
                                    "UPDATE bronze.scheme_mapping_review "
                                    "   SET reviewer_decision = CASE "
                                    "         WHEN candidate_amfi_code = :pick "
                                    "         THEN 'APPROVED' ELSE 'REJECTED' END, "
                                    "       reviewed_by = :who, reviewed_at = now() "
                                    " WHERE reviewer_decision IS NULL "
                                    "   AND rta = :rta AND rta_scheme_code = :code"
                                ),
                                {"pick": labels[chosen], "who": reviewer.strip(),
                                 "rta": row.rta, "code": row.rta_scheme_code},
                            )
                        st.success(f"✔ {row.rta_scheme_code} -> {labels[chosen]}")
                        st.rerun()

        # ---------------- apply ----------------
        st.divider()
        if st.button("⬆ Promote approved to scheme_mapping",
                     use_container_width=True):
            outcome = promote_approved(engine)
            st.success(f"✔ Promoted {outcome['promoted']} mapping(s).")
            if outcome["skipped_already_mapped"]:
                st.info(
                    f"ℹ {outcome['skipped_already_mapped']} approval(s) skipped "
                    "— another rule had already mapped those schemes at higher "
                    "confidence."
                )
            if outcome["skipped_ambiguous"]:
                st.warning(
                    f"⚠ {outcome['skipped_ambiguous']} scheme(s) skipped — "
                    "more than one candidate was approved, so the ambiguity "
                    "is unresolved."
                )


st.divider()


# =========================================================
# TRANSFORM LOGIC
# =========================================================

if transform_btn:

    if not st.session_state.extracted:

        st.warning(
            "⚠ Run Extract First"
        )

    else:

        try:

            st.info(
                "Running transformation layer..."
            )


            # =================================================
            # STEP 1 : BRONZE → SILVER
            # =================================================

            st.info(
                "1️⃣ Loading Silver Layer..."
            )

            load_silver()

            create_triggers()


            st.success(
                "✔ Silver Layer Loaded"
            )


            # =================================================
            # STEP 2 : SILVER → GOLD
            # =================================================

            st.info(
                "2️⃣ Loading Gold Layer..."
            )

            load_gold()

            create_triggers()


            st.success(
                "✔ Gold Layer Loaded"
            )


            uploaded = (
                st.session_state.uploaded_types
            )


            # =================================================
            # SILVER PREVIEW
            # =================================================

            silver_data = {}


            # ---------- INVESTOR ----------

            if uploaded["investor"]:

                silver_data["Investor Master"] = (
                    read_table(
                        "silver",
                        "investor_master"
                    )
                )


            # ---------- TRANSACTION ----------

            if uploaded["transaction"]:

                silver_data["Transactions"] = (
                    read_table(
                        "silver",
                        "transaction_master_new"
                    )
                )


            # ---------- SIP ----------

            if uploaded["sip"]:

                silver_data["SIP"] = (
                    read_table(
                        "silver",
                        "sip_master_new"
                    )
                )


            # ---------- BROKERAGE SUMMARY ----------

            if uploaded.get("brokerage"):

                silver_data["Brokerage Summary"] = (
                    read_table(
                        "silver",
                        "brokerage_summary"
                    )
                )


            # =================================================
            # GOLD PREVIEW
            # =================================================

            gold_data = {}


            # ---------- AMC ----------

            gold_data["AMC"] = read_table(
                "gold",
                "amc"
            )


            # ---------- SCHEME ----------

            gold_data["Scheme"] = read_table(
                "gold",
                "scheme"
            )


            # ---------- SCHEME NAV ----------

            gold_data["Scheme NAV"] = read_table(
                "gold",
                "scheme_nav"
            )


            # ---------- CLIENTS ----------

            gold_data["Clients"] = read_table(
                "gold",
                "clients"
            )


            # ---------- TRANSACTIONS ----------

            gold_data["Transactions"] = read_table(
                "gold",
                "transactions"
            )


            # ---------- HOLDINGS ----------

            gold_data["Holdings"] = read_table(
                "gold",
                "holdings"
            )


            # ---------- FOLIO NOMINEES ----------

            gold_data["Folio Nominees"] = read_table(
                "gold",
                "folio_nominees"
            )


            # ---------- SIP ----------

            gold_data["SIP"] = read_table(
                "gold",
                "sip"
            )


            # ---------- BROKERAGE SUMMARY ----------

            gold_data["Brokerage Summary"] = read_table(
                "gold",
                "brokerage_summary"
            )


            # =================================================
            # SAVE SESSION STATE
            # =================================================

            st.session_state.silver_data = silver_data

            st.session_state.gold_data = gold_data

            st.session_state.transformed = True

            st.session_state.current_layer = (
                "silver_gold"
            )


            st.success(
                "✔ Transformation Completed + "
                "Silver + Gold Loaded to DB"
            )


        except Exception:

            st.error(
                "❌ Transformation Failed"
            )

            st.code(
                traceback.format_exc()
            )


# =========================================================
# PRETTY NAMES
# =========================================================

pretty_names = {

    "Investor Master":
        "📘 Master Table (Investor)",

    "Transactions":
        "📊 Transaction Table",

    "SIP":
        "📈 SIP Table",

    "Scheme Mapping":
        "🔗 Scheme Mapping Table"
}


# =========================================================
# BRONZE PREVIEW
# =========================================================

if st.session_state.current_layer == "bronze":

    st.markdown(
        "## 📄 Bronze Layer Preview"
    )


    for name, df in (
        st.session_state.bronze_data.items()
    ):

        if is_valid(df):

            with st.container(
                border=True
            ):

                st.markdown(
                    f"### {pretty_names.get(name, name)}"
                )


                c1, c2 = st.columns(2)


                c1.metric(
                    "Rows",
                    len(df)
                )


                c2.metric(
                    "Columns",
                    len(df.columns)
                )


                st.dataframe(
                    df,
                    width="stretch",
                    height=300
                )


                st.divider()


# =========================================================
# SILVER + GOLD PREVIEW
# =========================================================

elif st.session_state.current_layer == "silver_gold":


    # =====================================================
    # SILVER PREVIEW
    # =====================================================

    st.markdown(
        "## ✨ Silver Layer Preview"
    )


    for name, df in (
        st.session_state.silver_data.items()
    ):

        if is_valid(df):

            with st.container(
                border=True
            ):

                st.markdown(
                    f"### {pretty_names.get(name, name)}"
                )


                c1, c2 = st.columns(2)


                c1.metric(
                    "Rows",
                    len(df)
                )


                c2.metric(
                    "Columns",
                    len(df.columns)
                )


                st.dataframe(
                    df,
                    width="stretch",
                    height=300
                )


                st.divider()


    # =====================================================
    # GOLD PREVIEW
    # =====================================================

    st.markdown(
        "## ⭐ Gold Layer Preview"
    )


    for name, df in (
        st.session_state.gold_data.items()
    ):

        if is_valid(df):

            with st.container(
                border=True
            ):

                st.markdown(
                    f"### {name}"
                )


                c1, c2 = st.columns(2)


                c1.metric(
                    "Rows",
                    len(df)
                )


                c2.metric(
                    "Columns",
                    len(df.columns)
                )


                st.dataframe(
                    df,
                    width="stretch",
                    height=300
                )


                st.divider()
