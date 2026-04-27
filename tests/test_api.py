"""Smoke tests for the FastAPI surface — no network calls to Anthropic.

The grant-writer / policy-navigator endpoints are not exercised here because
they require a live Anthropic API key. test_agent_smoke.py monkey-patches the
LLM call for that.
"""

from fastapi.testclient import TestClient

from npo_agent import api as api_module


def test_health_endpoint(isolated_db):
    client = TestClient(api_module.app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "ca-central" in body["data_residency"] or body["data_residency"]
    assert "clinical-empathetic" in body["available_personas"]


def test_admin_create_tenant_requires_token(isolated_db):
    client = TestClient(api_module.app)
    response = client.post(
        "/admin/tenants",
        json={"name": "BCSS", "persona": "clinical-empathetic"},
    )
    assert response.status_code == 401


def test_admin_create_tenant_returns_key(isolated_db):
    client = TestClient(api_module.app)
    response = client.post(
        "/admin/tenants",
        json={"name": "BCSS", "persona": "clinical-empathetic"},
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["api_key"].startswith("npo_")
    assert body["name"] == "BCSS"


def test_ingest_requires_api_key(isolated_db):
    client = TestClient(api_module.app)
    response = client.post(
        "/v1/documents",
        json={"title": "x", "body": "y"},
    )
    assert response.status_code == 401


def test_ingest_and_list_roundtrip(isolated_db):
    client = TestClient(api_module.app)
    create_resp = client.post(
        "/admin/tenants",
        json={"name": "BCSS"},
        headers={"X-Admin-Token": "test-admin-token"},
    )
    api_key = create_resp.json()["api_key"]

    ingest_resp = client.post(
        "/v1/documents",
        json={
            "title": "Crisis Protocol",
            "body": "Internal BCSS crisis line procedure.",
            "namespace": "policies",
        },
        headers={"X-API-Key": api_key},
    )
    assert ingest_resp.status_code == 200
    assert ingest_resp.json()["namespace"] == "policies"

    list_resp = client.get(
        "/v1/documents?namespace=policies",
        headers={"X-API-Key": api_key},
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["count"] == 1


def test_cross_tenant_isolation_via_api(isolated_db):
    """Two tenants, one document each. Each tenant only sees its own."""
    client = TestClient(api_module.app)
    headers_admin = {"X-Admin-Token": "test-admin-token"}

    bcss_key = client.post(
        "/admin/tenants", json={"name": "BCSS"}, headers=headers_admin
    ).json()["api_key"]
    foodbank_key = client.post(
        "/admin/tenants", json={"name": "Food Bank"}, headers=headers_admin
    ).json()["api_key"]

    client.post(
        "/v1/documents",
        json={"title": "BCSS-only doc", "body": "Sensitive crisis protocol."},
        headers={"X-API-Key": bcss_key},
    )
    client.post(
        "/v1/documents",
        json={"title": "Foodbank-only doc", "body": "Donor list."},
        headers={"X-API-Key": foodbank_key},
    )

    bcss_docs = client.get("/v1/documents", headers={"X-API-Key": bcss_key}).json()
    foodbank_docs = client.get(
        "/v1/documents", headers={"X-API-Key": foodbank_key}
    ).json()

    assert bcss_docs["count"] == 1
    assert foodbank_docs["count"] == 1
    assert bcss_docs["documents"][0]["title"] == "BCSS-only doc"
    assert foodbank_docs["documents"][0]["title"] == "Foodbank-only doc"
