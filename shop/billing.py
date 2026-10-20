"""Billing data checks: what the payment provider (iyzico) needs about the buyer before a charge.

    tckn_valid("10000000146") -> bool      TC kimlik no (11 digits, official checksum)
    vkn_valid("1234567890")   -> bool      vergi kimlik no (10 digits, official checksum)
    billing_errors(profile)   -> {field: message}    empty = complete
"""
REQUIRED_ALWAYS = ("first_name", "last_name", "email", "address", "district", "city", "country")
REQUIRED_COMPANY = ("company_name", "tax_office", "tax_number")


def tckn_valid(value: str) -> bool:
    if len(value) != 11 or not value.isdigit() or value[0] == "0":
        return False
    d = [int(c) for c in value]
    if (sum(d[0:9:2]) * 7 - sum(d[1:8:2])) % 10 != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


def vkn_valid(value: str) -> bool:
    if len(value) != 10 or not value.isdigit():
        return False
    total = 0
    for i in range(9):
        tmp = (int(value[i]) + (9 - i)) % 10
        v = (tmp * 2 ** (9 - i)) % 9
        if tmp != 0 and v == 0:
            v = 9
        total += v
    return (10 - total % 10) % 10 == int(value[9])


def billing_errors(p) -> dict[str, str]:
    """Missing or invalid fields of a BillingProfile; keys are model field names."""
    errors = {f: "required" for f in REQUIRED_ALWAYS if not getattr(p, f, "").strip()}
    if p.type == "individual":
        if not p.identity_number:
            errors["identity_number"] = "required"
        elif not tckn_valid(p.identity_number):
            errors["identity_number"] = "invalid TC kimlik no"
    else:
        errors.update({f: "required" for f in REQUIRED_COMPANY if not getattr(p, f, "").strip()})
        if p.tax_number and not vkn_valid(p.tax_number):
            errors["tax_number"] = "invalid vergi kimlik no"
        if p.identity_number and not tckn_valid(p.identity_number):
            errors["identity_number"] = "invalid TC kimlik no"
    if p.email and ("@" not in p.email or "." not in p.email.split("@")[-1]):
        errors["email"] = "invalid email"
    return errors
