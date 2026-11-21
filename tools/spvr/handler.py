"""The SPVR supplier register.

A separate system from S/4HANA, so a separate Gateway target. One operation,
the yellow band of the flow, which runs only when an invoice is overdue and
unpaid:

    spvr_get_supplier_block   API 3: is the supplier registered, and is it blocked

The contract takes a best code, a company code and a purchasing organisation.
The best code is derived here from the vendor code S/4HANA returned, so the
agent passes the S/4 values straight through.

The API does not exist yet, so the register is held here and every response
carries source: "mock". Replacing it means rewriting _call_api3 only.
"""

from toolkit import dispatch

# The block flags API 3 returns, in the order the contract sheet lists them.
BLOCK_FLAGS = (
    "companyCodePostingBlock",
    "companyCodePaymentBlock",
    "purchaseOrganisationPurchasingBlock",
    "bankruptcyBlock",
    "buyerFurnishedEquipmentBlock",
    "courtOrderBlock",
    "debitBalanceBlock",
    "profileInactive",
    "lawsuitPendingBlock",
    "paymentRestrictedBlock",
    "waitingOnW8W9Block",
    "thirdPartyRiskVisibilityBlock",
)


def _entry(*active: str) -> dict:
    """An API 3 response: every flag false except those named, and block set when any is."""
    flags = {name: name in active for name in BLOCK_FLAGS}
    return {"block": any(flags.values()), **flags}


# Keyed by (best code, company code, purchasing organisation), the contract's inputs.
REGISTER = {
    ("BE1234", "1000", "2000"): _entry("waitingOnW8W9Block"),
    ("BE1235", "1000", "2000"): _entry(),
    ("BE1236", "1000", "2000"): _entry(),
}


def best_code_from_vendor(vendor: str) -> str:
    """Vendor code to best code, for example 001234 to BE1234.

    The rule comes from one example in the contract sheet and is not confirmed:
    "BE" plus the last four digits. If it turns out to be a lookup, this is the
    only place that changes.
    """
    return f"BE{vendor.strip()[-4:]}"


def _call_api3(best_code: str, company_code: str, purchase_organisation: str) -> dict:
    """API 3, supplier and SPVR investigation. The single seam to replace."""
    entry = REGISTER.get((best_code, company_code, purchase_organisation))
    if entry is None:
        return {"error": {"code": "NotFound",
                          "message": f"Best code {best_code} is not in SPVR for company code "
                                     f"{company_code} and purchasing organisation {purchase_organisation}."}}
    return entry


def spvr_get_supplier_block(vendor: str, company_code: str, purchase_organisation: str) -> dict:
    """Is the supplier in SPVR, and is it blocked?"""
    best_code = best_code_from_vendor(vendor)
    raw = _call_api3(best_code, company_code.strip(), purchase_organisation.strip())
    if "error" in raw:
        return {"vendor": vendor, "best_code": best_code, "found": False, "in_spvr": False,
                "source": "mock", "detail": raw["error"].get("message", "Supplier not found in SPVR.")}
    flags = {name: bool(raw.get(name)) for name in BLOCK_FLAGS}
    return {
        "vendor": vendor,
        "best_code": best_code,
        "found": True,
        "in_spvr": True,
        "blocked": bool(raw.get("block")),
        "active_blocks": [name for name, on in flags.items() if on],
        "flags": flags,
        "source": "mock",
    }


_TOOLS = {
    "spvr_get_supplier_block": (spvr_get_supplier_block, ("vendor", "company_code", "purchase_organisation")),
}


def lambda_handler(event, context):
    return dispatch(_TOOLS, event, context)
