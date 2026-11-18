"""Model Economics: the price table starts unset and costs are never
fabricated; estimates are computed from set rates; estimated and
actual stay separate; budgets alert from recorded spend."""

from conftest import OPERATOR


def _agent(client, name):
    res = client.post("/api/agents", json={"display_name": name},
                      headers=OPERATOR)
    assert res.status_code == 201, res.text
    return res.json()


def _model(client, name):
    res = client.post(
        "/api/model-configs",
        json={"provider": "example", "model_name": name,
              "display_name": f"Model {name}"},
        headers=OPERATOR)
    assert res.status_code == 201, res.text
    return res.json()


def test_rate_not_set_means_no_estimate(client):
    agent = _agent(client, "Econ Unrated")
    model = _model(client, "unrated-1")
    res = client.post(
        "/api/usage-records",
        json={"agent_id": agent["id"], "model_config_id": model["id"],
              "input_tokens": 1000, "output_tokens": 500},
        headers=OPERATOR)
    assert res.status_code == 201, res.text
    usage = res.json()
    assert usage["estimated_cost_minor"] is None
    assert usage["actual_cost_minor"] is None
    assert usage["rate_set"] is False
    assert "rate not set" in usage["cost_note"]
    summary = client.get(
        f"/api/economics/summary?agent_id={agent['id']}").json()
    block = summary["by_source"]["live"]
    assert block["records"] == 1
    assert block["estimated_cost_minor"] is None
    assert block["unrated_records"] == 1
    prices = client.get("/api/model-prices").json()
    entry = [p for p in prices
             if p["model_config_id"] == model["id"]][0]
    assert entry["rate_set"] is False


def test_estimate_computed_from_set_rate(client):
    agent = _agent(client, "Econ Rated")
    model = _model(client, "rated-1")
    res = client.post(
        "/api/model-prices",
        json={"model_config_id": model["id"],
              "input_per_1m_minor": 2000, "output_per_1m_minor": 4000},
        headers=OPERATOR)
    assert res.status_code == 201, res.text
    assert res.json()["rate_set"] is True
    usage = client.post(
        "/api/usage-records",
        json={"agent_id": agent["id"], "model_config_id": model["id"],
              "input_tokens": 500_000, "output_tokens": 250_000},
        headers=OPERATOR).json()
    # (500000*2000 + 250000*4000) / 1e6 = 2000 minor units exactly.
    assert usage["estimated_cost_minor"] == 2000
    assert usage["rate_set"] is True


def test_actual_and_estimated_kept_separate(client):
    agent = _agent(client, "Econ Separated")
    model = _model(client, "separated-1")
    client.post("/api/model-prices",
                json={"model_config_id": model["id"],
                      "input_per_1m_minor": 1000,
                      "output_per_1m_minor": 1000},
                headers=OPERATOR)
    usage = client.post(
        "/api/usage-records",
        json={"agent_id": agent["id"], "model_config_id": model["id"],
              "input_tokens": 100_000, "output_tokens": 0,
              "actual_cost_minor": 777},
        headers=OPERATOR).json()
    assert usage["estimated_cost_minor"] == 100
    assert usage["actual_cost_minor"] == 777
    summary = client.get(
        f"/api/economics/summary?agent_id={agent['id']}").json()
    block = summary["by_source"]["live"]
    assert block["estimated_cost_minor"] == 100
    assert block["actual_cost_minor"] == 777
    # The two are never collapsed into one number.
    assert block["estimated_cost_minor"] != block["actual_cost_minor"]


def test_budget_threshold_alerts_from_recorded_spend(client):
    agent = _agent(client, "Econ Budgeted")
    budget = client.post(
        "/api/budgets",
        json={"scope_type": "agent", "scope_id": agent["id"],
              "limit_minor": 1000, "alert_threshold_pct": 80},
        headers=OPERATOR).json()
    assert budget["status"] == "ok"
    assert budget["spent_minor"] == 0
    assert budget["spent_basis"] == "none"
    client.post("/api/usage-records",
                json={"agent_id": agent["id"], "input_tokens": 10,
                      "actual_cost_minor": 850},
                headers=OPERATOR)
    budget = client.get(f"/api/budgets/{budget['id']}").json()
    assert budget["spent_minor"] == 850
    assert budget["spent_basis"] == "actual"
    assert budget["status"] == "warning"
    assert budget["alert"] is True
    client.post("/api/usage-records",
                json={"agent_id": agent["id"], "input_tokens": 10,
                      "actual_cost_minor": 200},
                headers=OPERATOR)
    budget = client.get(f"/api/budgets/{budget['id']}").json()
    assert budget["spent_minor"] == 1050
    assert budget["status"] == "exceeded"
    assert budget["remaining_minor"] == -50


def test_price_and_budget_validation(client):
    model = _model(client, "validated-1")
    bad_price = client.post(
        "/api/model-prices",
        json={"model_config_id": model["id"],
              "input_per_1m_minor": -5},
        headers=OPERATOR)
    assert bad_price.status_code == 422
    bad_budget = client.post(
        "/api/budgets",
        json={"scope_type": "agent", "limit_minor": 0},
        headers=OPERATOR)
    assert bad_budget.status_code == 422
    bad_scope = client.post(
        "/api/budgets",
        json={"scope_type": "galaxy", "limit_minor": 100},
        headers=OPERATOR)
    assert bad_scope.status_code == 422
