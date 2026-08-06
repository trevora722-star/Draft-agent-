"""API smoke tests for the FitCoach surface — non-LLM endpoints only.

(Coach/accountability LLM endpoints are exercised in test_coach.py /
test_accountability.py with the model stubbed.)
"""

from fastapi.testclient import TestClient

from npo_agent import api as api_module


def _tenant_key(client) -> str:
    return client.post(
        "/admin/tenants",
        json={"name": "Anytime Group", "persona": "coach-hype"},
        headers={"X-Admin-Token": "test-admin-token"},
    ).json()["api_key"]


def test_location_and_member_lifecycle(isolated_db):
    client = TestClient(api_module.app)
    key = _tenant_key(client)
    h = {"X-API-Key": key}

    loc = client.post("/v1/locations", json={"name": "Rutland"}, headers=h).json()
    assert loc["name"] == "Rutland"

    # Ingest equipment into the location namespace.
    doc = client.post(
        f"/v1/locations/{loc['id']}/documents",
        json={"title": "Equipment", "body": "dumbbells, squat rack", "kind": "equipment"},
        headers=h,
    ).json()
    assert doc["namespace"] == f"loc:{loc['id']}:equipment"

    member = client.post(
        "/v1/members",
        json={"name": "Alex Kim", "home_location_id": loc["id"], "target_visits_per_week": 3},
        headers=h,
    ).json()
    assert member["name"] == "Alex Kim"

    imp = client.post(
        f"/v1/members/{member['id']}/checkins",
        json={"location_id": loc["id"], "timestamps": ["2026-06-01", "2026-06-03"]},
        headers=h,
    ).json()
    assert imp["imported"] == 2


def test_dashboard_reports_risk(isolated_db):
    client = TestClient(api_module.app)
    key = _tenant_key(client)
    h = {"X-API-Key": key}

    # A member with no check-ins is high risk.
    client.post("/v1/members", json={"name": "Ghost"}, headers=h)
    d = client.get("/v1/dashboard", headers=h).json()
    assert d["member_count"] == 1
    assert d["risk_bands"]["high"] == 1
    assert d["roster"][0]["band"] == "high"
    assert d["roster"][0]["days_since_last"] is None


def test_assess_endpoint(isolated_db):
    client = TestClient(api_module.app)
    key = _tenant_key(client)
    h = {"X-API-Key": key}
    member = client.post("/v1/members", json={"name": "Ghost"}, headers=h).json()
    a = client.get(f"/v1/agents/accountability/assess/{member['id']}", headers=h).json()
    assert a["band"] == "high"
    assert a["visits_last_28"] == 0


def test_fitness_endpoints_require_api_key(isolated_db):
    client = TestClient(api_module.app)
    assert client.get("/v1/dashboard").status_code == 401
    assert client.post("/v1/locations", json={"name": "x"}).status_code == 401


def test_member_isolation_across_tenants(isolated_db):
    client = TestClient(api_module.app)
    a_key = _tenant_key(client)
    b_key = _tenant_key(client)
    client.post("/v1/members", json={"name": "AliceA"}, headers={"X-API-Key": a_key})

    b_members = client.get("/v1/members", headers={"X-API-Key": b_key}).json()
    assert b_members["members"] == []


def test_ui_routes_serve_html(isolated_db):
    client = TestClient(api_module.app)
    for path in ("/coach", "/dashboard"):
        r = client.get(path)
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]


def test_program_endpoint_returns_null_not_404_when_none(isolated_db):
    """Regression (browser bug hunt): a member with no program is a normal
    state — 200 {program: null}, not a 404 that spams the console."""
    client = TestClient(api_module.app)
    key = _tenant_key(client)
    h = {"X-API-Key": key}
    member = client.post("/v1/members", json={"name": "Alex"}, headers=h).json()

    r = client.get(f"/v1/members/{member['id']}/program", headers=h)
    assert r.status_code == 200
    assert r.json()["program"] is None

    # Once a program exists it comes back nested under "program".
    from npo_agent import fitness
    from npo_agent.tenancy import authenticate

    fitness.save_program(authenticate(key), member["id"], "# Plan\nDay 1: squats")
    r2 = client.get(f"/v1/members/{member['id']}/program", headers=h)
    assert r2.status_code == 200
    assert "Day 1" in r2.json()["program"]["body"]


def test_agent_endpoint_returns_503_when_llm_unconfigured(isolated_db, monkeypatch):
    """Regression (browser bug hunt): a missing/broken model config must
    degrade to a friendly 503, never a bare 500 stack trace."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from npo_agent import config

    config.get_settings.cache_clear()

    client = TestClient(api_module.app, raise_server_exceptions=False)
    key = _tenant_key(client)
    h = {"X-API-Key": key}
    loc = client.post("/v1/locations", json={"name": "Rutland"}, headers=h).json()
    member = client.post(
        "/v1/members", json={"name": "Alex", "home_location_id": loc["id"]}, headers=h
    ).json()

    r = client.post("/v1/agents/coach/program", json={"member_id": member["id"]}, headers=h)
    assert r.status_code == 503
    assert "coaching service" in r.json()["detail"].lower()
