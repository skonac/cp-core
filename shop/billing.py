"""Billing data checks: what the payment provider (iyzico) needs about the buyer before a charge.

    tckn_valid("10000000146") -> bool      TC kimlik no (11 digits, official checksum)
    vkn_valid("1234567890")   -> bool      vergi kimlik no (10 digits, official checksum)
    billing_errors(profile)   -> {field: message}    empty = complete
    country_name("TR")        -> "Türkiye"           for iyzico and the documents

Country is an ISO 3166-1 alpha-2 code. Turkish buyers (TR): individuals need a valid TC kimlik no,
companies need unvan + vergi dairesi + a valid VKN, and the address needs an ilçe. Foreign buyers:
identity number optional (unchecked), companies need a company name and a tax/VAT number of any
shape, no tax office, district (state/region) optional.
"""
import re

REQUIRED_ALWAYS = ("first_name", "last_name", "email", "address", "city", "country")
REQUIRED_TR = ("district",)
REQUIRED_COMPANY = ("company_name", "tax_number")
REQUIRED_COMPANY_TR = ("tax_office",)
COUNTRY_NAMES = {"TR": "Türkiye"}   # names for the payment provider and documents; other codes are sent as-is


def country_name(code: str) -> str:
    return COUNTRY_NAMES.get(code, code)


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
    country = (p.country or "").strip().upper()
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        errors["country"] = "invalid country code"
    turkish = country == "TR"
    if turkish:
        errors.update({f: "required" for f in REQUIRED_TR if not getattr(p, f, "").strip()})
    if p.type == "individual":
        if turkish and not p.identity_number:
            errors["identity_number"] = "required"
        elif turkish and not tckn_valid(p.identity_number):
            errors["identity_number"] = "invalid TC kimlik no"
    else:
        errors.update({f: "required" for f in REQUIRED_COMPANY if not getattr(p, f, "").strip()})
        if turkish:
            errors.update({f: "required" for f in REQUIRED_COMPANY_TR if not getattr(p, f, "").strip()})
            if p.tax_number and not vkn_valid(p.tax_number):
                errors["tax_number"] = "invalid vergi kimlik no"
            if p.identity_number and not tckn_valid(p.identity_number):
                errors["identity_number"] = "invalid TC kimlik no"
    if p.email and ("@" not in p.email or "." not in p.email.split("@")[-1]):
        errors["email"] = "invalid email"
    return errors
