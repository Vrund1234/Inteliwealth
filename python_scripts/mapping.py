# ==============================
# INVESTOR MASTER COLUMN MAPPING
# ==============================

INVESTOR_MASTER_MAPPING = {

    "source": ["source"],

    # ================= CORE IDENTIFIERS =================
    "folio_no": ["foliochk", "folio", "FOLIO", "FOLIO_NO"],
    "investor_name": ["inv_name", "INV_NAME", "investor_name"],
    "joint_name_1": ["jnt_name1", "jtname1", "JOINT_NAME_1"],
    "joint_name_2": ["jnt_name2", "jtname2", "JOINT_NAME_2"],

    # ================= ADDRESS =================
    "address1": ["address1", "add1"],
    "address2": ["address2", "add2"],
    "address3": ["address3", "add3"],
    "city": ["city"],
    "state": ["state"],
    "country": ["country"],
    "pincode": ["pincode", "pin"],

    # ================= PERSONAL =================
    "dob": ["dob", "inv_dob"],
    "mobile_no": ["mobile_no", "mobile"],
    "email": ["email"],
    "phone_res": ["phone_res", "rphone"],
    "phone_off": ["phone_off", "ophone"],
 
    # ================= TAX / PAN =================
    "tax_status": ["tax_status", "status"],
    "holding_nature": ["holding_nature"],
    "pan_no": ["pan_no", "pan"],
    "joint1_pan": ["joint1_pan"],
    "joint2_pan": ["joint2_pan"],
    "guardian_pan": ["guardian_pan", "guard_pan","pangno"],

    # ================= BANK =================
    "bank_name": ["bank_name", "bname"],
    "bank_account_no": ["bank_account_no", "bnkacno"],
    "account_type": ["account_type", "bnkactype"],
    "branch": ["branch"],
    "ifsc_code": ["ifsc_code"],

    "bank_address1": ["bank_address1", "badd1"],
    "bank_address2": ["bank_address2", "badd2"],
    "bank_address3": ["bank_address3", "badd3"],
    "bank_city": ["bank_city", "bcity"],
    "bank_state": ["bank_state"],
    "bank_country": ["bank_country"],

    # ================= NOMINEE 1 =================
    "nominee1_name": ["nominee1_name", "nom_name"],
    "nominee1_relation": ["nominee1_relation", "relation"],
    "nominee1_address1": ["nominee1_address1", "nom_addr1"],
    "nominee1_address2": ["nominee1_address2", "nom_addr2"],
    "nominee1_address3": ["nominee1_address3", "nom_addr3"],
    "nominee1_city": ["nominee1_city", "nom_city"],
    "nominee1_state": ["nominee1_state", "nom_state"],
    "nominee1_pincode": ["nominee1_pincode", "nom_pincode"],
    "nominee1_phone": ["nominee1_phone", "nom_ph_off", "nom_ph_res"],
    "nominee1_email": ["nominee1_email", "nom_email"],
    "nominee1_percentage": ["nominee1_percentage", "nom_percentage"],

    # ================= NOMINEE 2 =================
    "nominee2_name": ["nominee2_name", "nom2_name"],
    "nominee2_relation": ["nominee2_relation", "nom2_relation"],
    "nominee2_address1": ["nominee2_address1", "nom2_addr1"],
    "nominee2_address2": ["nominee2_address2", "nom2_addr2"],
    "nominee2_address3": ["nominee2_address3", "nom2_addr3"],
    "nominee2_city": ["nominee2_city", "nom2_city"],
    "nominee2_state": ["nominee2_state", "nom2_state"],
    "nominee2_pincode": ["nominee2_pincode", "nom2_pincode"],
    "nominee2_phone": ["nominee2_phone", "nom2_ph_off", "nom2_ph_res"],
    "nominee2_email": ["nominee2_email", "nom2_email"],
    "nominee2_percentage": ["nominee2_percentage", "nom2_percentage"],

    # ================= NOMINEE 3 =================
    "nominee3_name": ["nominee3_name", "nom3_name"],
    "nominee3_relation": ["nominee3_relation", "nom3_relation"],
    "nominee3_address1": ["nominee3_address1", "nom3_addr1"],
    "nominee3_address2": ["nominee3_address2", "nom3_addr2"],
    "nominee3_address3": ["nominee3_address3", "nom3_addr3"],
    "nominee3_city": ["nominee3_city", "nom3_city"],
    "nominee3_state": ["nominee3_state", "nom3_state"],
    "nominee3_pincode": ["nominee3_pincode", "nom3_pincode"],
    "nominee3_phone": ["nominee3_phone", "nom3_ph_off", "nom3_ph_res"],
    "nominee3_email": ["nominee3_email", "nom3_email"],
    "nominee3_percentage": ["nominee3_percentage", "nom3_percentage"],
        # ================= KYC / BROKER =================
    "broker_code": ["broker_code", "brokcode", "td_agent", "td_broker"],
    "dp_id": ["dp_id"],
    "demat_flag": ["demat_flag", "demat", "Demat Folio flag"],
    "ckyc_no": ["ckyc_no", "fh_ckyc_no", "CKYC NO"],
    "jh1_ckyc": ["jh1_ckyc"],
    "jh2_ckyc": ["jh2_ckyc"],
    "guardian_ckyc_no": ["guardian_ckyc_no", "g_ckyc_no"],
    "guardian_name": ["guardian_name", "guardian"],

    # ================= SYSTEM =================
    "report_date": ["report_date", "rep_date", "Report Date"],
    "report_time": ["report_time", "time1"],
    "folio_date": ["folio_date"],
    "occupation": ["occupation", "occpn", "occ_code"],
    "occupation_description": ["occupation_description"],

    # ================= PRODUCT / SCHEME =================
    "product_code": ["product_code", "product", "prod"],
    "scheme_name": ["scheme_name", "scheme", "sch_name"],
    "closing_balance": ["closing_balance", "clos_bal"],
    "rupee_balance": ["rupee_balance", "rupee_bal"],

    # ================= ADDITIONAL SOURCE COLUMNS =================
    # "foliochk": ["foliochk"],
    "product": ["product", "prod"],
    # "sch_name": ["sch_name", "scheme"],
    "rep_date": ["rep_date"],
    # "clos_bal": ["clos_bal"],
    "rupee_bal": ["rupee_bal"],
    "uin_no": ["uin_no"],
    "inv_iin": ["inv_iin"],
    "subbroker": ["subbroker", "subbrok"],
    "brokcode": ["brokcode", "broker_code"],
    "reinv_flag": ["reinv_flag", "reinvest_f"],
    "b_pincode": ["b_pincode", "bpin"],
    "nom_ph_off": ["nom_ph_off"],
    "nom2_ph_off": ["nom2_ph_off"],
    "nom3_ph_off": ["nom3_ph_off"],
    "tpa_linked": ["tpa_linked"],
    "g_ckyc_no": ["g_ckyc_no"],
    "jh1_dob": ["jh1_dob"],
    "jh2_dob": ["jh2_dob"],
    "guardian_dob": ["guardian_dob"],
    "amc_code": ["amc_code"],
    "gst_state_code": ["gst_state_code", "gst_state_"],
    "folio_old": ["folio_old", "old_folio"],
    "scheme_folio_number": ["scheme_folio_number", "scheme_fol"],
    "fund": ["fund", "td_fund"],
    # "folio": ["folio", "folio_no"],
    "fund_description": ["fund_description"],
        # ================= HOLDER DETAILS =================
    "tpin": ["tpin"],
    "f_name": ["f_name"],
    "m_name": ["m_name"],

    # ================= CONTACT DETAILS =================
    "phone_res1": ["phone_res1", "rphone1"],
    "phone_res2": ["phone_res2", "rphone2"],
    "phone_off1": ["phone_off1", "ophone1"],
    "phone_off2": ["phone_off2", "ophone2"],
    "fax_residence": ["fax_residence", "fax"],
    "fax_office": ["fax_office", "faxoff"],

    # ================= OCCUPATION / BANK =================
    "occ_code": ["occ_code", "occpn"],
    "bank_phone": ["bank_phone", "bphone"],

    # ================= INVESTOR DETAILS =================
    "investor_id": ["investor_id", "invid"],
    "client_id": ["client_id"],
    "dividend_option": ["dividend_option", "divopt"],
    "mode_of_holding_description": [
        "mode_of_holding_description",
        "holding_nature"
    ],
    "mapin_id": ["mapin_id"],
    "pan2": ["pan2"],
    "pan3": ["pan3"],

    # ================= CATEGORY =================
    "category": ["category"],
    "categorydesc": ["categorydesc"],
    "statusdesc": ["statusdesc"],

    # ================= KYC FLAGS =================
    "kyc1flag": ["kyc1flag"],
    "kyc2flag": ["kyc2flag"],
    "kyc3flag": ["kyc3flag"],
    "lastupdateddate": ["lastupdateddate"],

    # ================= COMMON ACCOUNT =================
    "commonaccno": ["commonaccno"],

    # ================= AADHAAR =================
    "holder_1_aadhaar_info": ["holder_1_aadhaar_info"],
    "holder_2_aadhaar_info": ["holder_2_aadhaar_info"],
    "holder_3_aadhaar_info": ["holder_3_aadhaar_info"],
    "guardian_aadhaar_info": ["guardian_aadhaar_info"],

    # ================= JOINT HOLDER CONTACT =================
    "joint_holder_1st_resi_phone_no": [
        "joint_holder_1st_resi_phone_no"
    ],
    "joint_holder_2nd_resi_phone_no": [
        "joint_holder_2nd_resi_phone_no"
    ],
    "joint_holder_1_contact_number": [
        "joint_holder_1_contact_number"
    ],
    "joint_holder_2_contact_number": [
        "joint_holder_2_contact_number"
    ],
    "joint_holder_1_email_id": [
        "joint_holder_1_email_id"
    ],
    "joint_holder_2_email_id": [
        "joint_holder_2_email_id"
    ],

    # ================= ADDITIONAL DETAILS =================
    "investors_resi_faxno": [
        "investors_resi_faxno",
        "fax"
    ],
    "kycgflag": ["kycgflag"],

    # ================= NOMINEE =================
    "nominee_opt_out_flag": ["nominee_opt_out_flag"],
    "nominee_dob": ["nominee_dob"],
    "nominee_guardian_name": ["nominee_guardian_name"],

    # ================= COMMUNICATION =================
    "emailconcern": ["emailconcern"],
    "emailrelationship": ["emailrelationship"],
    "mobilerelationship": ["mobilerelationship"],

    # ================= META =================
    "flag": [],
    "created_at": [],
    "updated_at": []
}

# =========================================================
# TRANSACTION MASTER MAPPING (CAMS + KFINTECH UNION)
# Target: bronze.transaction_master_new
# =========================================================

TRANSACTION_MASTER_MAPPING = {

    # ================= SYSTEM =================
    "source": ["source"],

    # ================= CORE =================
    "prodcode": ["prodcode", "fmcode"],                 # Product Code
    "amc_code": ["amc_code", "td_fund"],                # Fund
    "folio_no": ["folio_no", "td_acno"],                # Folio Number
    "divopt": ["divopt"],                               # Dividend Option
    "scheme": ["scheme", "funddesc"],                   # Fund Description
    "trxnno": ["trxnno", "td_trno"],                    # Transaction Number
    "inv_name": ["inv_name", "invname"],                # Investor Name

    # ================= TRANSACTION =================
    "trxnmode": ["trxnmode", "trnmode"],                # Transaction Mode
    "trxnstat": ["trxnstat", "trnstat"],                # Transaction Status
    "trxntype": ["trxntype", "td_trtype"],              # Transaction Type
    "trxnsubtyp": ["trxnsubtype", "subtrtype"],         # SubTranType
    "trxn_nature": ["trxn_nature", "trdesc"],           # Transaction Description
    "trflag": ["trflag"],

    # ================= DATES =================
    "traddate": ["traddate", "navdate"],                # Nav Date
    "postdate": ["postdate", "td_prdt"],                # Process Date
    "rep_date": ["rep_date", "crdate"],                 # Report Date
    "sys_regn_date": ["sys_regn_date", "sipregdt"],

    # ================= AMOUNT =================
    "units": ["units", "td_units"],
    "amount": ["amount", "td_amt"],
    "purprice": ["nav", "td_nav"],                      # NAV
    "load": ["load", "load1"],
    "stt": ["stt"],
    "stamp_duty": ["stamp_duty"],
    "trxn_charges": ["trxn_charge", "trcharges"],
    "total_tax": ["total_tds", "tdsamount"],

    # ================= DISTRIBUTOR =================
    "brokcode": ["brokcode", "td_agent"],               # Agent Code
    "subbrok": ["subbrok", "td_broker"],                # Sub Broker Code
    "usercode": ["usercode", "branchcode"],             # Branch Code
    "usrtrxno": ["usrtrxno", "ihno"],                   # IHNO

    # ================= INVESTOR =================
    "pan": ["pan", "pan1"],
    "client_id": ["client_id", "clientid"],
    "dp_id": ["dp_id", "dpid"],
    "tax_status": ["tax_status", "status"],

    # ================= BANK =================
    "chqno": ["chqno"],

    # ================= SIP =================
    "siptrxnno": ["siptrxnno", "sipregslno"],

    # ================= SWITCH =================
    "targ_src_scheme": ["targ_src_s", "prcode1"],

    # ================= OTHER =================
    "scheme_type": ["scheme_type", "assettype"],
    "ter_location": ["ter_location", "citycateg5"],
    "euin": ["euin"],
    "euin_valid": ["euin_valid", "evalid"],
    "euin_opted": ["euin_opted", "edeclflag"],
    "sub_brk_arn": ["sub_brk_arn", "subarncode"],
    "exchange_flag": ["exchange_f", "td_trxnmod"],
    "remarks": ["remarks"],
    "altfolio": ["altfolio"],

    # ================= NEW COLUMNS =================
    "common_account_number": ["can"],
    "ft_accno": ["ft_accno", "ftaccno"],
    "rejtrnoor2": ["rejtrnoor2"],
    "to_product_code": ["targ_src_s", "prcode1"],
    "reversal_c": ["reversal_c"],

    # ================= LEGACY COLUMNS (KEEP) =================
    "td_fund": ["td_fund"],
    "funddesc": ["funddesc"],
    "td_purred": ["td_purred"],
    "folio_old": ["folio_old"],
    "old_folio": ["old_folio"],
    "scheme_folio_number": ["scheme_folio_number"],
    "time1": ["time1"],
    "crdate": ["crdate"],
    "crtime": ["crtime"],
    "purdate": ["purdate"],
    "puramt": ["puramt"],
    "purunits": ["purunits"],
    "brokperc": ["brokperc"],
    "brokcomm": ["brokcomm"],
    "application_no": ["application_no", "td_appno"],
    "tax": ["tax"],
    "te_15h": ["te_15h"],
    "bank_name": ["bank_name"],
    "ac_no": ["ac_no"],
    "micr_no": ["micr_no"],
    "inv_iin": ["inv_iin"],
    "invid": ["invid"],
    "guardpanno": ["guardpanno"],
    "scanrefno": ["scanrefno"],
    "trxn_type_flag": ["trxn_type_flag"],
    "ticob_trtype": ["ticob_trtype"],
    "ticob_trno": ["ticob_trno"],
    "ticob_posted_date": ["ticob_posted_date"],
    "eligib_amt": ["eligib_amt"],
    "src_of_txn": ["src_of_txn"],
    "trxn_suffix": ["trxn_suffix"],
    "exch_dc_flag": ["exch_dc_flag"],
    "src_brk_code": ["src_brk_code"],
    "ca_initiated_date": ["ca_initiated_date"],
    "gst_state_code": ["gst_state_code"],
    "igst_amount": ["igst_amount"],
    "cgst_amount": ["cgst_amount"],
    "sgst_amount": ["sgst_amount"],
    "rev_remark": ["rev_remark"],
    "original_trxnno": ["original_trxnno"],
    "amc_ref_no": ["amc_ref_no"],
    "request_ref_no": ["request_ref_no"],
    "transmission_flag": ["transmission_flag"],
    "swflag": ["swflag"],
    "seq_no": ["seq_no"],
    "reinvest_flag": ["reinvest_flag"],
    "mult_brok": ["mult_brok"],
    "location": ["location"],
    "divper": ["divper"],
    "loadper": ["loadper"],
    "ihno": ["ihno"],
    "branchcode": ["branchcode"],
    "inwardno": ["inwardno"],
    "sipregslno": ["sipregslno"],
    "cleared": ["cleared"],
    "invstate": ["invstate"],
    "isctrno": ["isctrno"],
    "td_pop": ["td_pop"],
    "td_ptrno": ["td_ptrno"],
    "chqdate": ["chqdate"],
    "exchorgtrtype": ["exchorgtrtype"],

    # ================= SYSTEM =================
    "flag": [],
    "created_at": [],
    "updated_at": []
}

SIP_MASTER_MAPPING = {

    # =====================================================
    # SYSTEM
    # =====================================================
    "source": ["source"],

    # =====================================================
    # PRODUCT DETAILS
    # =====================================================

    # CAMS: PRODUCT (B331G, B92...)
    # KFIN: Product Code (RMFLPIG, 117EBRG...)
    "product_code": [
        "PRODUCT",
        "Product Code"
    ],

    # CAMS: SCHEME_CODE (331G, 92...)
    # KFIN: Scheme (LP, EB, IO, TS...)
    "scheme_code": [
        "SCHEME_CODE",
        "Scheme_code"
    ],

    # Full scheme name
    "scheme_name": [
        "SCHEME_NAME",
        "Scheme Name"
    ],

    # KFIN only
    "plan": [
        "Plan"
    ],

    # =====================================================
    # INVESTOR
    # =====================================================

    "folio_no": [
        "FOLIO_NO",
        "Folio"
    ],

    "folio_old": [
        "FOLIO_OLD"
    ],

    "inv_name": [
        "INV_NAME",
        "Investor Name"
    ],

    "pan": [
        "PAN"
    ],

    # CAMS INV_IIN = KFIN Ihno
    "inv_iin": [
        "INV_IIN",
        "Ihno"
    ],

    "inv_dp_id": [
        "InvDpId"
    ],

    "inv_client_id": [
        "InvClientId"
    ],

    "dp_inv_name": [
        "DP_InvName"
    ],

    # =====================================================
    # SIP DETAILS
    # =====================================================

    "aut_trntyp": [
        "AUT_TRNTYP",
        "SipType"
    ],

    "auto_trno": [
        "AUTO_TRNO",
        "RegSlno"
    ],

    "ft_sip_regno": [
        "FT_SIP_REGNO"
    ],

    "auto_amount": [
        "AUTO_AMOUNT",
        "Amount"
    ],

    "no_of_installments": [
        "No Of Installments"
    ],

    "periodicity": [
        "PERIODICITY",
        "Frequency"
    ],

    "period_day": [
        "PERIOD_DAY"
    ],

    "payment_mode": [
        "PAYMENT_MODE",
        "SIP Mode"
    ],

    # =====================================================
    # DATES
    # =====================================================

    "reg_date": [
        "REG_DATE",
        "RegistrationDate"
    ],

    "from_date": [
        "FROM_DATE",
        "Start Date"
    ],

    "to_date": [
        "TO_DATE",
        "End Date"
    ],

    "cease_date": [
        "CEASE_DATE",
        "TerminateDate"
    ],

    "pause_from_date": [
        "PAUSE_FROM_DATE"
    ],

    "pause_to_date": [
        "PAUSE_TO_DATE"
    ],

    # =====================================================
    # TARGET / SWITCH
    # =====================================================

    # CAMS: TARGET_SCHEME (Scheme Name)
    # KFIN: To Scheme (Short Code)
    "target_scheme": [
        "TARGET_SCHEME",
        "To Scheme"
    ],

    # CAMS: TARGET_SCHEME_CODE
    # KFIN: ToProductCode
    "target_scheme_code": [
        "TARGET_SCHEME_CODE",
        "ToProductCode"
    ],

    # KFIN only (Full Scheme Name)
    "target_scheme_name": [
        "ToSchemeName"
    ],

    # KFIN only
    "target_plan": [
        "To Plan"
    ],

    # =====================================================
    # DISTRIBUTOR
    # =====================================================

    "sub_arn_code": [
        "SUB_ARN_CODE",
        "AgentCode"
    ],

    "agent_name": [
        "AgentName"
    ],

    "subbroker": [
        "SUBBROKER",
        "Subbroker"
    ],

    "euin": [
        "EUIN"
    ],

    "zone": [
        "Zone"
    ],

    # IMPORTANT:
    # DO NOT MAP CAMS BRANCH with KFIN Branch.
    # They are different business fields.
    "branch": [
        "Branch",
        "BRANCH"
    ],

    # # CAMS Branch kept separately
    # "cams_branch": [
    #     "BRANCH"
    # ],

    "ter_location": [
        "TER_LOCATION",
        "Location"
    ],

    # =====================================================
    # BANK
    # =====================================================

    "bank": [
        "BANK",
        "ECSBankName"
    ],

    "ac_type": [
        "AC_TYPE"
    ],

    "instrm_no": [
        "INSTRM_NO",
        "ECSNO"
    ],

    "cheq_micr_no": [
        "CHEQ_MICR_NO"
    ],

    "ecs_account_no": [
        "ECSAcno"
    ],

    "ac_holder_name": [
        "AC_HOLDER_NAME",
        "ECSHolderName"
    ],

    # =====================================================
    # AMC
    # =====================================================

    "amc_code": [
        "AMC_CODE"
    ],

    "user_code": [
        "USER_CODE"
    ],

    "package_name": [
        "PACKAGE_NAME"
    ],

    "special_product": [
        "SPECIAL_PRODUCT"
    ],

    # CAMS SUBTRXNDESC = KFIN Trtype
    "subtrxndesc": [
        "SUBTRXNDESC",
        "Trtype"
    ],

    # =====================================================
    # EXTRA
    # =====================================================

    "remarks": [
        "REMARKS"
    ],

    "top_up_frq": [
        "TOP_UP_FRQ"
    ],

    "top_up_amt": [
        "TOP_UP_AMT"
    ],

    "top_up_perc": [
        "TOP_UP_PERC"
    ],

    "status": [
        "Status"
    ],

    "modify_flag": [
        "ModifyFlag"
    ],

    "umrn_code": [
        "umrncode"
    ],

    "scheme_folio_number": [
        "SCHEME_FOLIO_NUMBER"
    ],

    "request_ref_no": [
        "REQUEST_REF_NO"
    ],

    # =====================================================
    # SYSTEM
    # =====================================================

    "flag": [],
    "created_at": [],
    "updated_at": []
}


# #Gold Layer Mapping

# HOLDINGS_MAPPING = {
#     "rta": "source",
#     "pan": "pan_no",
#     "folio_number": "scheme_folio_number",   # As per requirement
#     "units": "closing_balance",
#     "market_value": "rupee_balance",
#     "as_on_date": "report_date",
#     "folio_date": "folio_date",
#     "arn": "broker_code",
#     "holding_nature": "holding_nature",
#     "nominee_name": "nominee1_name",
#     "nominee_relation": "nominee1_relation",
#     "nominee_pct": "nominee1_percentage",
#     "bank_name": "bank_name",
#     "bank_ac_last4": "bank_account_no",
#     "demat_flag": "demat_flag",
#     "kyc_status": "ckyc_no"
# }