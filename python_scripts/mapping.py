# ==============================
# INVESTOR MASTER COLUMN MAPPING
# ==============================

INVESTOR_MASTER_MAPPING = {

    "source": ["source"],

    # ================= CORE IDENTIFIERS =================
    "folio_no": ["foliochk", "folio", "folio_no"],
    "investor_name": ["inv_name", "investor_name"],
    "joint_name_1": ["jnt_name1", "jtname1", "joint_name_1"],
    "joint_name_2": ["jnt_name2", "jtname2", "joint_name_2"],

    # ================= ADDRESS =================
    "address1": ["address1", "address_1", "add1", "Address #1"],
    "address2": ["address2", "address_2", "add2", "Address #2"],
    "address3": ["address3", "address_3", "add3", "Address #3"],
    "city": ["city", "City"],
    "state": ["state", "State"],
    "country": ["country", "Country"],
    "pincode": ["pincode", "pin", "Pincode"],

    # ================= PERSONAL =================
    "dob": ["dob", "inv_dob", "date_of_birth"],
    "mobile_no": ["mobile_no", "mobile", "mobile_number"],
    "email": ["email"],
    "phone_res": ["phone_res", "rphone", "phone_residence"],
    "phone_off": ["phone_off", "ophone", "phone_office"],
 
    # ================= TAX / PAN =================
    "tax_status": ["tax_status", "status"],
    "holding_nature": ["holding_nature", "holding_na"],
    "pan_no": ["pan_no", "pan", "pan_number"],
    "joint1_pan": ["joint1_pan"],
    "joint2_pan": ["joint2_pan"],
    "guardian_pan": ["guardian_pan", "guard_pan", "pangno", "guardpanno"],

    # ================= BANK =================
    "bank_name": ["bank_name", "bname"],
    "bank_account_no": ["bank_account_no", "bnkacno", "ac_no", "bankaccno"],
    "account_type": ["account_type", "bnkactype", "ac_type"],
    "branch": ["branch"],
    "ifsc_code": ["ifsc_code"],

    "bank_address1": ["bank_address1", "bank_address_1", "badd1", "b_address1"],
    "bank_address2": ["bank_address2", "bank_address_2", "badd2", "b_address2"],
    "bank_address3": ["bank_address3", "bank_address_3", "badd3", "b_address3"],

    "bank_city": ["bank_city", "bcity", "b_city"],
    "bank_state": ["bank_state"],
    "bank_country": ["bank_country"],

    # ================= NOMINEE 1 =================
    "nominee1_name": ["nominee1_name", "nom_name", "nominee"],
    "nominee1_relation": ["nominee1_relation", "relation", "nominee_relation"],
    "nominee1_address1": ["nominee1_address1", "nom_addr1", "nominee_address1"],
    "nominee1_address2": ["nominee1_address2", "nom_addr2", "nominee_address2"],
    "nominee1_address3": ["nominee1_address3", "nom_addr3", "nominee_address3"],
    "nominee1_city": ["nominee1_city", "nom_city", "nominee_city"],
    "nominee1_state": ["nominee1_state", "nom_state", "nominee_state"],
    "nominee1_pincode": ["nominee1_pincode", "nom_pincode", "nominee_pin_code", "nom_pincod"],
    "nominee1_phone": ["nominee1_phone", "nom_ph_off", "nom_ph_res", "nominee_phone_residence"],
    "nominee1_email": ["nominee1_email", "nom_email", "nominee_email"],
    "nominee1_percentage": ["nominee1_percentage", "nom_percentage", "nominee_ratio", "nom_percen"],

    # ================= NOMINEE 2 =================
    "nominee2_name": ["nominee2_name", "nom2_name", "nominee2"],

    "nominee2_relation": ["nominee2_relation", "nom2_relation", "nom2_relat"],
    "nominee2_address1": ["nominee2_address1", "nom2_addr1"],
    "nominee2_address2": ["nominee2_address2", "nom2_addr2"],
    "nominee2_address3": ["nominee2_address3", "nom2_addr3"],

    "nominee2_city": ["nominee2_city", "nom2_city"],

    "nominee2_state": ["nominee2_state", "nom2_state"],

    "nominee2_pincode": ["nominee2_pincode", "nom2_pincode", "nominee2_pin_code", "nom2_pinco"],

    "nominee2_phone": ["nominee2_phone", "nom2_ph_off", "nom2_ph_res", "nominee2_phone_residence", "nom2_ph_re"],

    "nominee2_email": ["nominee2_email", "nom2_email"],

    "nominee2_percentage": ["nominee2_percentage", "nom2_percentage", "nominee2_ratio", "nom2_perce"],

    # ================= NOMINEE 3 =================
    "nominee3_name": ["nominee3_name", "nom3_name", "nominee3"],

    "nominee3_relation": ["nominee3_relation", "nom3_relation", "nom3_relat"],

    "nominee3_address1": ["nominee3_address1", "nom3_addr1"],
    "nominee3_address2": ["nominee3_address2", "nom3_addr2"],
    "nominee3_address3": ["nominee3_address3", "nom3_addr3"],
    "nominee3_city": ["nominee3_city", "nom3_city"],
    "nominee3_state": ["nominee3_state", "nom3_state"],
    "nominee3_pincode": ["nominee3_pincode", "nom3_pincode", "nom3_pinco"],
    "nominee3_phone": ["nominee3_phone", "nom3_ph_off", "nom3_ph_res", "nominee3_phone_residence", "nom3_ph_re"],
    "nominee3_email": ["nominee3_email", "nom3_email"],
    "nominee3_percentage": ["nominee3_percentage", "nom3_percentage", "nominee3_ratio", "nom3_perce"],

        # ================= KYC / BROKER =================
    "broker_code": ["broker_code", "brokcode", "td_agent", "td_broker", "broker_cod"],
    "dp_id": ["dp_id", "dpid"],
    "demat_flag": ["demat_flag", "demat", "demat_folio_flag"],
    "ckyc_no": ["ckyc_no", "fh_ckyc_no", "fh_ckyc", "fh_ckyc_n"],
    "jh1_ckyc": ["jh1_ckyc", "jh1_ckyc_no", "jh1_ckyc_n"],
    "jh2_ckyc": ["jh2_ckyc", "jh2_ckyc_no", "jh2_ckyc_n"],
    "guardian_ckyc_no": ["guardian_ckyc_no", "g_ckyc_no", "g_ckyc_n"],
    "guardian_name": ["guardian_name", "guardian", "guardianname", "guard_name", "GUARD_NAME"],

    # ================= SYSTEM =================
    "report_date": ["report_date", "rep_date"],
    "report_time": ["report_time", "time1"],
    "folio_date": ["folio_date", "folio_dat", "folio_dt", "foliodate"],
    "occupation": [
        "occ_code"
    ],

    "occupation_description": ["occupation_description", "Occupation Description"],

    # ================= PRODUCT / SCHEME =================
    "occupation": ["occupation", "occpn", "occ_code"],

    # ================= ADDITIONAL SOURCE COLUMNS =================
    #"product": ["product", "prod"],
    "product_code": ["product", "product_code"],
    "scheme_name": ["scheme_name", "SCH_NAME", "SCHEME", "Scheme Name",
    "fund_description", "Fund Description", "funddesc"],

    "rep_date": ["rep_date"],

    "rupee_bal": ["rupee_bal"],

    "uin_no": ["uin_no"],

    "inv_iin": ["inv_iin"],

    "subbroker": ["subbroker", "subbrok"],

    "brokcode": ["brokcode", "broker_code"],

    "reinv_flag": ["reinv_flag", "reinvest_f"],

    "b_pincode": ["b_pincode", "bpin"],

    "nom_ph_off": ["nom_ph_off"],
    "nom2_ph_off": ["nom2_ph_off", "nom2_ph_of"],
    "nom3_ph_off": ["nom3_ph_off", "nom3_ph_of"],

    "tpa_linked": ["tpa_linked", "tpa_link"],

    "g_ckyc_no": ["g_ckyc_no", "g_ckyc_n", "guardian_ckyc_no"],

    "jh1_dob": ["jh1_dob"],
    "jh2_dob": ["jh2_dob"],
    "guardian_dob": ["guardian_dob", "guardian_d"],

    "amc_code": ["amc_code", "Fund"],
    "gst_state_code": ["gst_state_code", "gst_state_"],
    "folio_old": ["folio_old", "old_folio"],
    "scheme_folio_number": ["scheme_folio_number", "scheme_fol"],
    "fund": ["fund", "td_fund"],
    "fund_description": ["fund_description", "scheme_name"],
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
    "mode_of_holding_description": ["holding_nature", "mode_of_holding_description"],
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
    "holder_1_aadhaar_info": ["holder_1_aadhaar_info", "aadhaar"],
    "holder_2_aadhaar_info": ["holder_2_aadhaar_info"],
    "holder_3_aadhaar_info": ["holder_3_aadhaar_info"],
    "guardian_aadhaar_info": ["guardian_aadhaar_info"],

    # ================= JOINT HOLDER CONTACT =================
    "joint_holder_1st_resi_phone_no": [
        "joint_holder_1st_resi_phone_no"
    ],
    "joint_holder_2nd_resi_phone_no": ["joint_holder_2nd_resi_phone_no"],
    "joint_holder_1_contact_number": ["joint_holder_1_contact_number", "jh1_mobile_no", "jh1_mobile"],
    "joint_holder_2_contact_number": ["joint_holder_2_contact_number", "jh2_mobile_no", "jh2_mobile"],
    "joint_holder_1_email_id": ["joint_holder_1_email_id", "jh1_email"],
    "joint_holder_2_email_id": ["joint_holder_2_email_id", "jh2_email"],

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
    "trxnsubtyp": ["trxnsubtype", "subtrtype", "trxnsubtyp", "trnsub"],         # SubTranType
    "trxn_nature": ["trxn_nature", "trdesc", "trxn_natur"],           # Transaction Description
    "trflag": ["trflag"],

    # ================= DATES =================
    "traddate": ["traddate", "navdate", "td_trdt"],                # Nav Date
    "postdate": ["postdate", "td_prdt"],                # Process Date
    "rep_date": ["rep_date", "crdate"],                 # Report Date
    "sys_regn_date": ["sys_regn_date", "sipregdt", "sys_regn_d"],

    # ================= AMOUNT =================
    "units": ["units", "td_units"],
    "amount": ["amount", "td_amt"],
    "purprice": ["purprice", "td_pop"],                     # NAV
    "load": ["load", "load1"],
    "stt": ["stt"],
    "stamp_duty": ["stamp_duty"],
    "trxn_charges": ["trxn_charge", "trcharges", "trxn_charges", "trxn_charg"],
    "total_tax": ["total_tds", "tdsamount", "total_tax"],

    # ================= DISTRIBUTOR =================
    "brokcode": ["brokcode", "td_agent"],               # Agent Code
    "subbrok": ["subbrok", "td_broker"],                # Sub Broker Code
    "usercode": ["usercode", "branchcode"],             # Branch Code
    "usrtrxno": ["usrtrxno", "ihno", "inwardnum1"],                   # IHNO

    # ================= INVESTOR =================
    "pan": ["pan", "pan1", "Pan Number"],
    "client_id": ["client_id", "clientid"],
    "dp_id": ["dp_id", "dpid"],
    "tax_status": ["tax_status", "status"],

    # ================= BANK =================
    "chqno": ["chqno"],

    # ================= SIP =================
    "siptrxnno": ["siptrxnno", "sipregslno"],

    # ================= SWITCH =================
    "targ_src_scheme": ["targ_src_s", "prcode1", "targ_src_scheme"],

    # ================= OTHER =================
    "scheme_type": ["scheme_type", "assettype", "scheme_typ"],
    "ter_location": ["ter_location", "citycateg5"],
    "euin": ["euin"],
    "euin_valid": ["euin_valid", "evalid"],
    "euin_opted": ["euin_opted", "edeclflag"],
    "sub_brk_arn": ["sub_brk_arn", "subarncode", "sub_brk_ar"],
    "exchange_flag": ["exchange_f", "td_trxnmod", "exchange_flag", "electrxnflag"],
    "remarks": ["remarks"],
    "altfolio": ["altfolio"],

    # ================= NEW COLUMNS =================
    "common_account_number": ["can"],
    "ft_accno": ["ft_accno", "ftaccno"],
    "rejtrnoor2": ["rejtrnoor2"],
    "to_product_code": ["targ_src_s", "prcode1"],
    "reversal_c": ["reversal_c", "reversal_code"],

    # ================= LEGACY COLUMNS (KEEP) =================
    "td_fund": ["td_fund"],
    "funddesc": ["funddesc"],
    "td_purred": ["td_purred"],
    "folio_old": ["folio_old"],
    "old_folio": ["old_folio"],
    "scheme_folio_number": ["scheme_folio_number", "scheme_fol"],
    "time1": ["time1"],
    "crdate": ["crdate"],
    "crtime": ["crtime"],
    "purdate": ["purdate"],
    "puramt": ["puramt"],
    "purunits": ["purunits"],
    "brokperc": ["brokperc"],
    "brokcomm": ["brokcomm"],
    "application_no": ["application_no", "td_appno", "applicatio"],
    "tax": ["tax"],
    "te_15h": ["te_15h"],
    "bank_name": ["bank_name"],
    "ac_no": ["ac_no", "BankAccno"],
    "micr_no": ["micr_no"],
    "inv_iin": ["inv_iin"],
    "invid": ["invid"],
    "guardpanno": ["guardpanno"],
    "scanrefno": ["scanrefno"],
    "trxn_type_flag": ["trxn_type_flag", "trxn_type_"],
    "ticob_trtype": ["ticob_trtype", "ticob_trty"],
    "ticob_trno": ["ticob_trno"],
    "ticob_posted_date": ["ticob_posted_date", "ticob_post"],
    "eligib_amt": ["eligib_amt"],
    "src_of_txn": ["src_of_txn"],
    "trxn_suffix": ["trxn_suffix", "trxn_suffi"],
    "exch_dc_flag": ["exch_dc_flag", "exch_dc_fl"],
    "src_brk_code": ["src_brk_code", "src_brk_co"],
    "ca_initiated_date": ["ca_initiated_date", "ca_initiat"],
    "gst_state_code": ["gst_state_code", "gst_state_"],
    "igst_amount": ["igst_amount", "igst_amoun"],
    "cgst_amount": ["cgst_amount", "cgst_amoun"],
    "sgst_amount": ["sgst_amount", "sgst_amoun"],
    "rev_remark": ["rev_remark"],
    "original_trxnno": ["original_trxnno","original_t"],
    "amc_ref_no": ["amc_ref_no"],
    "request_ref_no": ["request_ref_no", "request_re"],
    "transmission_flag": ["transmission_flag", "transmissi"],
    "swflag": ["swflag"],
    "seq_no": ["seq_no"],
    "reinvest_flag": ["reinvest_flag", "reinvest_f"],
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
    "sfunddt": ["sfunddt"],   

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
    "product_code": ["PRODUCT", "Product Code"],

    "scheme_code": ["SCHEME_CODE", "Scheme", "scheme_cod", "SCHEME_COD"],

    "scheme_name": ["SCHEME", "Scheme Name"],
    "plan": ["Plan"],

    # =====================================================
    # INVESTOR
    # =====================================================

    "folio_no": ["FOLIO_NO", "Folio"],
    "folio_old": ["FOLIO_OLD"],
    "inv_name": ["INV_NAME", "Investor Name"],
    "pan": ["PAN"],

    "inv_iin": ["INV_IIN", "Ihno"],

    "inv_dp_id": ["InvDpId"],
    "inv_client_id": ["InvClientId"],

    "dp_inv_name": ["DP_InvName"],

    # =====================================================
    # SIP DETAILS
    # =====================================================

    "aut_trntyp": ["AUT_TRNTYP", "SipType"],
    "auto_trno": ["AUTO_TRNO"],
    "ft_sip_regno": ["FT_SIP_REGNO", "RegSlno", "FT_SIP_REG"],
    "auto_amount": ["AUTO_AMOUNT", "Amount", "AUTO_AMOUN"],
    "no_of_installments": ["No Of Installments"],
    "periodicity": ["PERIODICITY", "Frequency", "PERIODICIT"],
    "period_day": ["PERIOD_DAY"],
    "payment_mode": ["PAYMENT_MODE", "SIP Mode", "PAYMENT_MO"],

    # =====================================================
    # DATES
    # =====================================================

    "reg_date": ["REG_DATE", "RegistrationDate"],
    "from_date": [ "FROM_DATE", "Start Date"],
    "to_date": ["TO_DATE", "End Date"],
    "cease_date": ["CEASE_DATE", "TerminateDate"],
    "pause_from_date": ["PAUSE_FROM_DATE", "PAUSE_FROM"],
    "pause_to_date": ["PAUSE_TO_DATE", "PAUSE_TO_D"],

    "target_scheme": ["TARGET_SCHEME", "To Scheme"],

    # CAMS: TARGET_SCHEME_CODE
    # KFIN: ToProductCode
    "target_scheme_code": ["TARGET_SCHEME_CODE", "ToProductCode"],

    "target_scheme_name": ["ToSchemeName"],

    "target_plan": ["To Plan"],

    # =====================================================
    # DISTRIBUTOR
    # =====================================================
    "sub_arn_code": ["SUB_ARN_CODE", "AgentCode", "SUB_ARN_CO"],
    "agent_name": ["AgentName"],
    "subbroker": ["SUBBROKER", "Subbroker"],
    "euin": ["EUIN"],
    "zone": ["Zone"],

    "branch": ["Branch", "BRANCH"],

    "ter_location": ["TER_LOCATION", "Location", "ter_locati"],

    "bank": ["BANK", "ECSBankName"],
    "ac_type": ["AC_TYPE"],
    "instrm_no": ["INSTRM_NO", "ECSNO"],

    "cheq_micr_no": ["CHEQ_MICR_NO", "CHEQ_MICR_"],
    "ecs_account_no": ["ECSAcno"],
    "ac_holder_name": ["AC_HOLDER_NAME", "ECSHolderName", "AC_HOLDER_"],

    # =====================================================
    # AMC
    # =====================================================

    "amc_code": ["AMC_CODE", "Fund Code"],
    "user_code": ["USER_CODE"],
    "package_name": ["PACKAGE_NAME", "PACKAGE_NA"],
    "special_product": ["SPECIAL_PRODUCT", "SPECIAL_PR"],
    "subtrxndesc": ["SUBTRXNDESC", "Trtype", "SUBTRXNDES"],

    # =====================================================
    # EXTRA
    # =====================================================

    "remarks": ["REMARKS"],
    "top_up_frq": ["TOP_UP_FRQ"],
    "top_up_amt": ["TOP_UP_AMT"],
    "top_up_perc": ["TOP_UP_PERC", "TOP_UP_PER"],
    "status": ["Status"],
    "modify_flag": ["ModifyFlag"],
    "umrn_code": ["umrncode"],
    "scheme_folio_number": ["SCHEME_FOLIO_NUMBER", "SCHEME_FOL"],
    "request_ref_no": ["REQUEST_REF_NO", "REQUEST_RE"],

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