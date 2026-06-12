"""One-link FitCoach demo bootstrap: idempotent seeding + the demo-key endpoint."""

from fastapi.testclient import TestClient

from npo_agent import config


def _enable_fitness_demo(monkeypatch):
    monkeypatch.setenv("NPO_FITNESS_DEMO_MODE", "1")
    config.get_settings.cache_clear()


def test_demo_key_hidden_when_not_in_demo_mode(isolated_db):
    from npo_agent import api as api_module

    client = TestClient(api_module.app)
    assert client.get("/fit-demo/key").status_code == 404


def test_demo_bootstraps_and_serves_a_working_key(isolated_db, monkeypatch):
    _enable_fitness_demo(monkeypatch)
    from npo_agent import api as api_module

    # Context-manager use triggers startup → ensure_fitness_demo().
    with TestClient(api_module.app) as client:
        r = client.get("/fit-demo/key")
        assert r.status_code == 200
        key = r.json()["api_key"]
        assert key == "npo_fitcoach_demo"

        # The seeded roster is immediately visible on the dashboard.
        d = client.get("/v1/dashboard", headers={"X-API-Key": key}).json()
        assert d["member_count"] == 6
        assert d["risk_bands"]["high"] >= 1
        assert d["risk_bands"]["medium"] >= 1
        # Root redirects to the owner dashboard in demo mode.
        assert client.get("/", follow_redirects=False).headers["location"] == "/dashboard"


def test_ensure_fitness_demo_is_idempotent(isolated_db, monkeypatch):
    _enable_fitness_demo(monkeypatch)
    from npo_agent import fitness, fitness_demo
    from npo_agent.tenancy import get_tenant

    first = fitness_demo.ensure_fitness_demo()
    second = fitness_demo.ensure_fitness_demo()
    assert first["tenant_id"] == second["tenant_id"]
    assert first["api_key"] == second["api_key"] == "npo_fitcoach_demo"

    tenant = get_tenant(first["tenant_id"])
    assert len(fitness.list_members(tenant)) == 6  # not duplicated
