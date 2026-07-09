"""Tests for phone + Client Job Code pairing against RAW DATA (pure logic)."""

from app.fms_v2.auth_sheet import (
    RAW_DATA_CODE_COL,
    RAW_DATA_PHONE_COL,
    normalize_phone,
    verify_phone_code_pairing,
)


def _row(code="", phone="", name="Acme"):
    row = [""] * 20
    row[1] = name
    row[RAW_DATA_CODE_COL] = code
    row[RAW_DATA_PHONE_COL] = phone
    return row


RAW = [
    ["Timestamp", "Client Name"],  # header-ish
    _row("HOACPL-F25F-TL01", "9993866117", "Hindustan Oil"),
    _row("SIPL-F25F-TL04", "9300000722", "Sarthak Ispat"),
    _row("NOPHONE-F25F-TL99", "", "No Phone Co"),  # code with no phone
]


def test_unicode_dash_in_code_still_matches():
    """A non-breaking hyphen (U+2011) or en-dash in the code must fold to '-'
    and still match the ASCII-hyphen code in RAW DATA."""
    assert verify_phone_code_pairing("9993866117", "HOACPL‑F25F‑TL01", RAW) is not None
    assert verify_phone_code_pairing("9993866117", "HOACPL–F25F–TL01", RAW) is not None


def test_normalize_phone_strips_country_code_and_formatting():
    assert normalize_phone("+91 99938 66117") == "9993866117"
    assert normalize_phone("919993866117") == "9993866117"
    assert normalize_phone("09993866117") == "9993866117"
    assert normalize_phone("9993866117") == "9993866117"


def test_correct_pairing_matches():
    r = verify_phone_code_pairing("9993866117", "HOACPL-F25F-TL01", RAW)
    assert r is not None
    assert r["client_job_code"] == "HOACPL-F25F-TL01"
    assert r["client_name"] == "Hindustan Oil"


def test_formatted_phone_matches():
    assert verify_phone_code_pairing("+91 99938 66117", "hoacpl-f25f-tl01", RAW) is not None


def test_wrong_phone_rejected():
    assert verify_phone_code_pairing("9999999999", "HOACPL-F25F-TL01", RAW) is None


def test_phone_code_mismatch_rejected():
    # Right phone, but paired with a different client's code.
    assert verify_phone_code_pairing("9993866117", "SIPL-F25F-TL04", RAW) is None


def test_code_without_phone_cannot_login():
    # Strict pairing: a code that has no phone on file is never authenticated.
    assert verify_phone_code_pairing("", "NOPHONE-F25F-TL99", RAW) is None
    assert verify_phone_code_pairing("9993866117", "NOPHONE-F25F-TL99", RAW) is None


def test_missing_inputs_return_none():
    assert verify_phone_code_pairing("", "HOACPL-F25F-TL01", RAW) is None
    assert verify_phone_code_pairing("9993866117", "", RAW) is None
