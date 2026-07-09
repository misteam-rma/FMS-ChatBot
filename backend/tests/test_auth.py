"""
Auth tests for JWT round-trip and active FMS v2 auth endpoints.

The FMS sheet fetch function is stubbed so these run offline.
"""

import pytest

from app.utils.auth import create_access_token, verify_token


# ── JWT round-trip ────────────────────────────────────────

def test_jwt_round_trip():
    token = create_access_token({
        "employee_id": "HOACPL-F25F-TL01",
        "employee_name": "Test Client",
        "mobile_number": "9876543210",
        "user_type": "client",
        "client_job_code": "HOACPL-F25F-TL01",
    })
    payload = verify_token(token)
    assert payload.employee_id == "HOACPL-F25F-TL01"
    assert payload.user_type == "client"
    assert payload.client_job_code == "HOACPL-F25F-TL01"


def test_verify_token_rejects_garbage():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        verify_token("not-a-real-jwt")
    assert exc.value.status_code == 401


# ── FMS v2 auth endpoints ─────────────────────────────────

@pytest.fixture
def stub_fms_auth(monkeypatch):
    """Stub FMS sheet lookup so verify-client-code runs offline."""
    from app.fms_v2.models import FetchFmsRecordsOutput, FmsRecord
    from app.routers import fms_v2_auth_router

    async def _fake_fetch(data):
        if data.client_job_code == "MISSING-F25F-TL01":
            return FetchFmsRecordsOutput(
                ok=True,
                client_job_code=data.client_job_code,
                records=[],
                errors=[],
                latency_ms=1,
            )
        return FetchFmsRecordsOutput(
            ok=True,
            client_job_code=data.client_job_code,
            records=[
                FmsRecord(
                    sheet_name="FMS1",
                    row_number=7,
                    client_job_code=data.client_job_code,
                    client_name="Hindustan Oil",
                    base_fields={"Client Job Code": data.client_job_code},
                    step_fields={},
                    source_columns={},
                ),
                FmsRecord(
                    sheet_name="FMS2",
                    row_number=8,
                    client_job_code=data.client_job_code,
                    client_name="Hindustan Oil",
                    base_fields={"Client Job Code": data.client_job_code},
                    step_fields={},
                    source_columns={},
                ),
            ],
            errors=[],
            latency_ms=1,
        )

    monkeypatch.setattr(fms_v2_auth_router, "fetch_fms_records_by_client_code", _fake_fetch)


@pytest.fixture
def reset_limiter():
    """Clear the in-memory rate-limit storage so counts start fresh.

    The limiter is process-global and keys on the (testclient) client IP, so
    requests from earlier tests would otherwise consume this test's budget.
    """
    from app.utils.limiter import limiter

    limiter.reset()
    yield
    limiter.reset()


def test_verify_client_code_happy_path(client, stub_fms_auth, reset_limiter):
    resp = client.post(
        "/api/auth/verify-client-code",
        json={"client_job_code": " hoacpl-f25f-tl01 "},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["employee_id"] == "HOACPL-F25F-TL01"
    assert body["client_job_code"] == "HOACPL-F25F-TL01"
    assert body["employee_name"] == "Hindustan Oil"
    assert body["user_type"] == "client"
    assert body["access_token"]


def test_verify_client_code_unknown_code(client, stub_fms_auth, reset_limiter):
    resp = client.post(
        "/api/auth/verify-client-code",
        json={"client_job_code": "MISSING-F25F-TL01"},
    )
    assert resp.status_code == 401


def test_verify_client_code_rate_limited(client, stub_fms_auth, reset_limiter):
    """6th request within the window must be rejected with 429."""
    codes = [
        client.post(
            "/api/auth/verify-client-code",
            json={"client_job_code": "MISSING-F25F-TL01"},
        ).status_code
        for _ in range(6)
    ]
    assert codes[-1] == 429
    assert codes[:5] == [401, 401, 401, 401, 401]


def test_verify_admin_hardcoded_success(client, reset_limiter):
    resp = client.post(
        "/api/auth/verify-admin",
        json={"username": "admin", "password": "admin123"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["employee_id"] == "admin"
    assert body["user_type"] == "admin"
    assert body["access_token"]


def test_verify_admin_hardcoded_failure(client, reset_limiter):
    resp = client.post(
        "/api/auth/verify-admin",
        json={"username": "admin", "password": "wrong"},
    )
    assert resp.status_code == 401
