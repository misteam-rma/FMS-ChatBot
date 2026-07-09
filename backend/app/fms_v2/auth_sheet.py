"""Phone + Client Job Code authentication against the RAW DATA tab.

A client is authenticated only if the submitted phone number and Client Job Code
appear in the SAME RAW DATA row:
  - col 15 (P) = Client Job Code
  - col 17 (R) = Mobile Number

This is the phone-owns-this-code check. Read-only; nothing is written back.
Codes without a phone on file cannot log in (strict pairing).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from app.fms_v2.sheets import fetch_worksheet_values, normalize_client_job_code


logger = logging.getLogger("botivate_api.fms_v2.auth_sheet")

RAW_DATA_SHEET = "RAW DATA"
RAW_DATA_CODE_COL = 15   # column P: Client Job Code
RAW_DATA_PHONE_COL = 17  # column R: Mobile Number
RAW_DATA_NAME_COL = 1    # column B: Client Name

_JOB_CODE_RE = re.compile(r"-F\d{2}[A-Z]-", re.IGNORECASE)


def normalize_phone(value: str) -> str:
    """Reduce a phone to comparable digits, dropping a leading 91 country code.

    Mirrors the Apps Script intent: compare on the core 10-digit number so
    '9993866117', '+91 99938 66117', and '919993866117' all match.
    """

    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def _cell(row: list, idx: int) -> str:
    return str(row[idx]).strip() if idx < len(row) and row[idx] is not None else ""


def verify_phone_code_pairing(
    phone: str, client_job_code: str, raw_values: list[list[str]]
) -> dict | None:
    """Return the matched client info if phone+code share a RAW DATA row.

    Pure function over already-fetched values so it is easy to unit test.
    Returns None when there is no matching pairing.
    """

    target_phone = normalize_phone(phone)
    target_code = normalize_client_job_code(client_job_code)
    if not target_phone or not target_code:
        return None

    for row in raw_values:
        code = _cell(row, RAW_DATA_CODE_COL)
        if normalize_client_job_code(code) != target_code:
            continue
        if normalize_phone(_cell(row, RAW_DATA_PHONE_COL)) == target_phone:
            return {
                "client_job_code": normalize_client_job_code(code),
                "client_name": _cell(row, RAW_DATA_NAME_COL) or target_code,
                "phone": target_phone,
            }
    return None


async def authenticate_phone_code(phone: str, client_job_code: str) -> dict | None:
    """Fetch RAW DATA and verify the phone+code pairing. Read-only."""

    started = time.perf_counter()
    values = await asyncio.to_thread(fetch_worksheet_values, RAW_DATA_SHEET)
    result = verify_phone_code_pairing(phone, client_job_code, values)
    logger.info(
        "FMS v2 phone+code auth code=%s matched=%s latency_ms=%s",
        normalize_client_job_code(client_job_code),
        bool(result),
        int((time.perf_counter() - started) * 1000),
    )
    return result
