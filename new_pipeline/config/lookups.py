"""Value standardisation lookups.

One dictionary per business concept, shared by every entity. The existing pipeline
duplicates these per transform function, which is how tax status `N` became
"N" in transform_investor_master and "NRI" in transform_transaction — the same input
producing two different silver values.

Unmapped values are RECORDED, not silently passed through. An unmapped value is
either a new legitimate code that belongs here or bad data; both need a human.
The existing code does `.map(dict).fillna(original)`, which hides both cases.
"""

from __future__ import annotations

# Keys are compared after uppercasing and trimming.

KYC_FLAG = {
    "KYC OK": "OK",
    # Found only by reading the .xls directly. The LibreOffice-converted CSV used for
    # the first profile pass showed just "KYC OK", because the sample rows inspected
    # happened not to include this value.
    "KYC NOT VERIFIED": "NOT_VERIFIED",
    "KYC NOT AVAILABLE": "NOT_AVAILABLE",
    "": None,
}

KYC_STATUS = {
    "KYC VALIDATED": "VALIDATED",
    "KYC REGISTERED - NEW KYC": "REGISTERED_NEW",
    "KYC REGISTERED": "REGISTERED",
    "KYC ON HOLD": "ON_HOLD",
    "KYC REJECTED": "REJECTED",
    "": None,
}

AADHAAR_LINK = {
    "AADHAR LINKED": "LINKED",
    "AADHAAR LINKED": "LINKED",   # provider spells it "Aadhar"; accept both
    "NOT LINKED": "NOT_LINKED",
    "NOT APPLICABLE": "NOT_APPLICABLE",   # present in the .xls, absent from the CSV
    "": None,
}

# Only 'Y' is valid. The WBR68 sample carries both 'N' and 'F' as invalid, which is
# why the report filter is `euin_valid <> 'Y'` and not `= 'N'`.
EUIN_VALID = {
    "Y": "VALID",
    "N": "INVALID",
    "F": "INVALID_FORMAT",
    "": None,
}

REGISTRY = {
    "kyc_flag": KYC_FLAG,
    "kyc_status": KYC_STATUS,
    "aadhaar_link": AADHAAR_LINK,
    "euin_valid": EUIN_VALID,
}


def resolve(lookup_name: str, raw: str | None) -> tuple[str | None, bool]:
    """Return (standardised_value, was_recognised).

    A caller that gets was_recognised=False must record it. Never silently keep the
    raw value.
    """
    table = REGISTRY.get(lookup_name)
    if table is None:
        raise KeyError(f"unknown lookup {lookup_name!r}; known: {sorted(REGISTRY)}")

    if raw is None:
        return None, True
    key = str(raw).strip().upper()
    if key in table:
        return table[key], True
    return None, False
