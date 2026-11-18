"""Registry CRUD: agents, teams, capabilities, model configs."""

from conftest import OPERATOR, OWNER


def test_create_and_get_agent(client, make_agent):
    agent = make_agent(display_name="Registry Agent", role="builder")
    assert agent["slug"] == "registry-agent"
    assert agent["connection_status"] == "demo_simulated"
    assert agent["lifecycle_status"] == "active"
    assert agent["data_source"] == "live"
    got = client.get(f"/api/agents/{agent['id']}")
    assert got.status_code == 200
    assert got.json()["display_name"] == "Registry Agent"


def test_agent_list_and_update(client, make_agent):
    agent = make_agent(display_name="Editable Agent")
    res = client.patch(
        f"/api/agents/{agent['id']}",
        json={"role": "reviewer", "notes": "updated"},
        headers=OPERATOR,
    )
    assert res.status_code == 200
    assert res.json()["role"] == "reviewer"
    assert res.json()["notes"] == "updated"
    listing = client.get("/api/agents").json()
    assert any(a["id"] == agent["id"] for a in listing)


def test_duplicate_slug_refused(client, make_agent):
    make_agent(display_name="Dup Agent", slug="dup-agent")
    res = client.post(
        "/api/agents",
        json={"display_name": "Other", "slug": "dup-agent"},
        headers=OPERATOR,
    )
    assert res.status_code == 409


def test_team_crud_and_membership(client, make_agent):
    team = client.post(
        "/api/teams", json={"display_name": "Alpha Team"}, headers=OWNER
    )
    assert team.status_code == 201
    team = team.json()
    agent = make_agent(display_name="Member Agent")
    res = client.post(
        f"/api/teams/{team['id']}/members",
        json={"agent_id": agent["id"], "assignment_role": "lead"},
        headers=OPERATOR,
    )
    assert res.status_code == 200
    members = res.json()["members"]
    assert len(members) == 1
    assert members[0]["agent_id"] == agent["id"]
    detail = client.get(f"/api/agents/{agent['id']}").json()
    assert detail["teams"][0]["slug"] == "alpha-team"


def test_capability_and_agent_assignment(client, make_agent):
    cap = client.post(
        "/api/capabilities",
        json={"display_name": "Code Review", "slug": "code-review"},
        headers=OPERATOR,
    )
    assert cap.status_code == 201
    agent = make_agent(display_name="Capable Agent",
                       capabilities=["code-review"])
    assert agent["capabilities"][0]["slug"] == "code-review"
    res = client.post(
        "/api/agents",
        json={"display_name": "Bad Cap", "capabilities": ["nope"]},
        headers=OPERATOR,
    )
    assert res.status_code == 422


def test_model_config_and_assignment(client, make_agent):
    cfg = client.post(
        "/api/model-configs",
        json={"provider": "local", "model_name": "m1",
              "display_name": "Local M1"},
        headers=OPERATOR,
    )
    assert cfg.status_code == 201
    cfg = cfg.json()
    assert cfg["config_status"] == "demo_simulated"
    agent = make_agent(display_name="Model Agent",
                       model_config_id=cfg["id"])
    assert agent["model_assignments"][0]["model_config_id"] == cfg["id"]
    res = client.post(
        f"/api/agents/{agent['id']}/model-assignments",
        json={"model_config_id": cfg["id"], "purpose": "fallback"},
        headers=OPERATOR,
    )
    assert res.status_code == 200
    assert len(res.json()["model_assignments"]) == 2


def test_unknown_agent_404(client):
    assert client.get("/api/agents/999999").status_code == 404
