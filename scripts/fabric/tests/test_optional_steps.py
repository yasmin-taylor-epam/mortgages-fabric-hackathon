"""Offline tests for the optional deploy steps. Every HTTP call goes to a fake session. Nothing reaches Fabric, Azure or Graph."""
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from conftest import REPO
from hbfabric import config as cfgmod
from hbfabric import deploy, extra_steps, ids, payloads, stage
from hbfabric.api import FabricClient, FabricError
from test_deploy import EXAMPLE, FakeCredential, FakeSession, Resp

WS = "aaaaaaaa-0000-1111-2222-bbbbbbbbbbbb"
GROUP = "11111111-2222-3333-4444-555555555555"
GROUP2 = "66666666-7777-8888-9999-000000000000"
SECRET_PAT = "gh" + "p_" + "FakeTokenForTests" + "0" * 12  # built at run time so that no secret-shaped string sits in the file
FAKE_CONNECTION = "Endpoint=sb://fake.servicebus.windows.net/;SharedAccessKeyName=k;" + "SharedAccessKey=fake-key-for-tests"


def settings(tmp_path, **sections):
    doc = {"environments": {"dev": {"workspace_id": WS, "capacity_name": "cap", **sections}}}
    p = tmp_path / "c.yml"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return cfgmod.load(p)


def make(tmp_path, routes, dry_run=False, steps=None, env=None, runner=None, repo=REPO, **sections):
    cfg = settings(tmp_path, **sections)
    session = FakeSession(routes)
    lines: list[str] = []
    d = deploy.Deployer(cfg, credential=FakeCredential(), client=FabricClient(FakeCredential(), session=session, sleep=lambda s: None),
                        dry_run=dry_run, steps=steps or ["roles"], log=lines.append, build_dir=tmp_path / "b", env=env or {},
                        runner=runner, graph_credential=FakeCredential(), repo=repo)
    d.workspace_id = WS
    return d, session, lines


def items(*pairs):
    return Resp(body={"value": [{"id": f"id-{n}", "type": t, "displayName": n} for n, t in pairs]})


# ---------------------------------------------------------------- settings

def test_optional_sections_are_off_in_the_example_and_in_the_defaults():
    cfg = cfgmod.load(EXAMPLE, overrides={"capacity_name": "cap"})
    assert not any(getattr(cfg, name).enabled for name in cfgmod.OPTIONAL_SECTIONS)
    assert deploy.default_steps(cfg) == ["workspace", "core", "upload", "notebooks", "pipeline", "model", "labels"]
    assert cfg.schedule.interval_minutes == 15 and cfg.retention.softdelete == "7d"


def test_enabled_sections_join_the_default_run_in_order(tmp_path):
    cfg = settings(tmp_path, retention={"enabled": True}, schedule={"enabled": True}, roles={"enabled": True, "assignments": []})
    steps = deploy.default_steps(cfg)
    assert steps.index("workspace") < steps.index("roles") < steps.index("core") < steps.index("retention")
    assert steps.index("pipeline") < steps.index("schedule") < steps.index("model")
    assert "eventstream" not in steps  # only with --only


@pytest.mark.parametrize("bad", [
    {"git": {"enabled": True, "owner": "o", "repository": "r", "branch": "b", "pat": "x"}},
    {"git": {"enabled": True, "owner": "o", "repository": "r", "branch": SECRET_PAT}},
    {"roles": {"enabled": True, "assignments": [{"principal_id": GROUP, "type": "User", "role": "Viewer", "email": "a@b.example"}]}},
    {"capacity_create": {"enabled": True, "subscription_id": "x", "resource_group": "someone@example.com", "location": "l"}},
    {"replay": {"connection_string": "x"}},
])
def test_config_refuses_credentials_tokens_and_user_names(tmp_path, bad):
    p = tmp_path / "c.yml"
    p.write_text(yaml.safe_dump({"environments": {"dev": {"workspace_name": "w", **bad}}}), encoding="utf-8")
    with pytest.raises(cfgmod.ConfigError, match="must not hold secrets"):
        cfgmod.load(p)


def test_section_problems_are_reported_only_for_enabled_sections(tmp_path):
    cfg = settings(tmp_path, schedule={"enabled": True, "interval_minutes": 0}, roles={"enabled": True, "assignments": [{"principal_id": "x", "type": "Team", "role": "Boss"}]},
                   rls={"enabled": True, "members": {"General viewer": ["nope"]}}, git={"enabled": False, "owner": ""},
                   sharepoint={"enabled": True, "site_url": "http://example"}, retention={"enabled": True, "softdelete": "forever"})
    text = " | ".join(cfg.problems())
    for needle in ("schedule.interval_minutes", "principal_id is not a GUID", "type must be one of", "role must be one of",
                   "not a GUID", "sharepoint.site_url", "retention.softdelete"):
        assert needle in text
    assert "git." not in text


def test_unknown_key_in_a_section_is_an_error(tmp_path):
    p = tmp_path / "c.yml"
    p.write_text(yaml.safe_dump({"environments": {"dev": {"workspace_name": "w", "schedule": {"minutes": 5}}}}), encoding="utf-8")
    with pytest.raises(cfgmod.ConfigError, match="Unknown settings in 'schedule'"):
        cfgmod.load(p)


def test_only_switches_an_optional_step_on_and_validate_checks_it(tmp_path, capsys):
    p = tmp_path / "c.yml"
    p.write_text(yaml.safe_dump({"environments": {"dev": {"workspace_name": "w", "capacity_name": "c"}}}), encoding="utf-8")
    assert deploy.main(["--validate", "--config", str(p), "--only", "rls"]) == 1
    assert "rls.members is empty" in capsys.readouterr().err
    assert deploy.main(["--validate", "--config", str(p), "--only", "schedule"]) == 0
    assert "schedule" in capsys.readouterr().out
    p.write_text(yaml.safe_dump({"environments": {"dev": {"workspace_name": "w", "capacity_name": "c",
                                                         "rls": {"enabled": True, "members": {"No such role": [GROUP]}}}}}), encoding="utf-8")
    assert deploy.main(["--validate", "--config", str(p)]) == 1
    assert "not in the semantic model" in capsys.readouterr().err


def test_the_real_sharepoint_schema_converts_for_graph():
    for lst in payloads.sharepoint_lists(REPO / "action" / "sharepoint" / "review-cases.list.json"):
        body = payloads.list_body(lst)
        assert body["list"] == {"template": "genericList"} and len(body["columns"]) == len(lst["columns"])
    case_key = next(c for c in payloads.list_body(payloads.sharepoint_lists(REPO / "action/sharepoint/review-cases.list.json")[0])["columns"] if c["name"] == "case_key")
    assert case_key["enforceUniqueValues"] is True and case_key["indexed"] is True and case_key["required"] is True
    assert payloads.validate_offline(cfgmod.EnvSettings(environment="dev", workspace_name="w"), REPO) == []


# ---------------------------------------------------------------- pure builders

def test_retention_command_and_comparison():
    assert payloads.retention_command("RawEventsLanding", "7d") == ".alter-merge table RawEventsLanding policy retention softdelete = 7d"
    row = ["policy_retention", "RawEventsLanding", json.dumps({"SoftDeletePeriod": "7.00:00:00", "Recoverability": "Enabled"}), "", ""]
    assert payloads.retention_matches([row], "7d") and not payloads.retention_matches([row], "8d") and not payloads.retention_matches([], "7d")
    assert payloads.timespan_seconds("36h") == 129600 and payloads.timespan_seconds("365000.00:00:00") == 365000 * 86400


def test_schedule_body_uses_the_documented_cron_shape():
    s = cfgmod.ScheduleSettings(enabled=True)
    body = payloads.schedule_body(s, datetime(2026, 11, 5, 8, 0, tzinfo=timezone.utc))
    assert body == {"enabled": True, "configuration": {"type": "Cron", "interval": 15, "startDateTime": "2026-11-05T08:00:00Z", "localTimeZoneId": "UTC"}}
    assert payloads.schedule_matches({"enabled": True, "configuration": {"type": "Cron", "interval": 15}}, s)
    assert not payloads.schedule_matches({"enabled": False, "configuration": {"type": "Cron", "interval": 15}}, s)


def test_capacity_and_git_bodies_hold_no_stored_secret():
    cap = payloads.capacity_body(cfgmod.CapacitySettings(enabled=True, location="westeurope", sku="F2"), ["admin-from-env"])
    assert cap == {"location": "westeurope", "sku": {"name": "F2", "tier": "Fabric"}, "properties": {"administration": {"members": ["admin-from-env"]}}}
    g = cfgmod.GitSettings(enabled=True, owner="o", repository="r", branch="main")
    conn = payloads.github_connection_body(g, "run-time-pat")
    assert conn["credentialDetails"]["credentials"] == {"credentialType": "Key", "key": "run-time-pat"}
    assert payloads.git_connect_body(g, "cid")["gitProviderDetails"]["gitProviderType"] == "GitHub"


# ---------------------------------------------------------------- roles

ROLES = {"enabled": True, "assignments": [{"principal_id": GROUP, "type": "Group", "role": "Member"}, {"principal_id": GROUP2, "type": "Group", "role": "Viewer"}]}


def test_roles_add_update_and_keep_without_duplicates(tmp_path):
    routes = [
        (("GET", "/roleAssignments"), Resp(body={"value": [{"id": GROUP2, "principal": {"id": GROUP2, "type": "Group"}, "role": "Contributor"}]})),
        (("POST", "/roleAssignments"), Resp(201, {})),
        (("PATCH", f"/roleAssignments/{GROUP2}"), Resp(200, {})),
    ]
    d, s, _ = make(tmp_path, routes, roles=ROLES)
    d.roles()
    sent = [(m, u.split(WS)[1], b) for m, u, b in s.calls if m != "GET"]
    assert sent == [("POST", "/roleAssignments", {"principal": {"id": GROUP, "type": "Group"}, "role": "Member"}),
                    ("PATCH", f"/roleAssignments/{GROUP2}", {"role": "Viewer"})]
    again = [(("GET", "/roleAssignments"), Resp(body={"value": [{"id": GROUP, "principal": {"id": GROUP}, "role": "Member"}, {"id": GROUP2, "principal": {"id": GROUP2}, "role": "Viewer"}]}))]
    d2, s2, lines = make(tmp_path, again, roles=ROLES)
    d2.roles()
    assert {m for m, _, _ in s2.calls} == {"GET"} and sum("role kept" in x for x in lines) == 2


def test_roles_dry_run_changes_nothing(tmp_path):
    d, s, lines = make(tmp_path, [(("GET", "/roleAssignments"), Resp(body={"value": []}))], dry_run=True, roles=ROLES)
    d.roles()
    assert {m for m, _, _ in s.calls} == {"GET"} and sum("would add" in x for x in lines) == 2


# ---------------------------------------------------------------- retention

KQL_ROUTES = lambda *mgmt: [  # noqa: E731
    (("GET", "/items"), items((ids.KQL_DATABASE, "KQLDatabase"))),
    (("GET", "/kqlDatabases/"), Resp(body={"displayName": ids.KQL_DATABASE, "properties": {"queryServiceUri": "https://q.kusto.fabric.microsoft.com"}})),
    (("POST", "/v1/rest/mgmt"), list(mgmt)),
]


def test_retention_runs_the_alter_command_only_when_the_value_differs(tmp_path):
    show_other = Resp(body={"Tables": [{"Rows": [["p", "t", json.dumps({"SoftDeletePeriod": "30.00:00:00"}), "", ""]]}]})
    d, s, _ = make(tmp_path, KQL_ROUTES(show_other, Resp(body={"Tables": []})), steps=["retention"], retention={"enabled": True})
    d.retention()
    posts = [b for m, u, b in s.calls if m == "POST"]
    assert [p["csl"] for p in posts] == [".show table RawEventsLanding policy retention", ".alter-merge table RawEventsLanding policy retention softdelete = 7d"]
    assert all(p["db"] == f"id-{ids.KQL_DATABASE}" for p in posts)  # the Kusto database name is the item ID
    show_same = Resp(body={"Tables": [{"Rows": [["p", "t", json.dumps({"SoftDeletePeriod": "7.00:00:00"}), "", ""]]}]})
    d2, s2, lines = make(tmp_path, KQL_ROUTES(show_same), steps=["retention"], retention={"enabled": True})
    d2.retention()
    assert len([1 for m, _, _ in s2.calls if m == "POST"]) == 1 and "already 7d" in " ".join(lines)


def test_retention_dry_run_never_sends_the_alter_command(tmp_path):
    show_other = Resp(body={"Tables": [{"Rows": [["p", "t", json.dumps({"SoftDeletePeriod": "30.00:00:00"}), "", ""]]}]})
    d, s, lines = make(tmp_path, KQL_ROUTES(show_other), dry_run=True, retention={"enabled": True})
    d.retention()
    assert all("alter" not in b["csl"] for m, _, b in s.calls if m == "POST") and any("Would run" in x for x in lines)


def test_kusto_calls_use_the_kusto_token_scope():
    scopes = []

    class Cred:
        def get_token(self, scope):
            scopes.append(scope)
            return type("T", (), {"token": "t"})()
    s = FakeSession([(("POST", "/v1/rest/mgmt"), Resp(body={"Tables": []}))])
    FabricClient(Cred(), session=s).kusto_mgmt("https://q.kusto.fabric.microsoft.com/", "db", ".show version")
    assert scopes == ["https://api.kusto.windows.net/.default"] and s.calls[0][1].endswith("/v1/rest/mgmt")


# ---------------------------------------------------------------- schedule

PIPELINE = (("GET", "/items"), items((ids.PIPELINE_SCORING, "DataPipeline")))


def test_schedule_is_created_with_a_15_minute_cron_interval(tmp_path):
    routes = [(("GET", "/schedules"), Resp(body={"value": []})), PIPELINE, (("POST", "/schedules"), Resp(201, {"id": "s1"}))]
    d, s, _ = make(tmp_path, routes, steps=["schedule"], schedule={"enabled": True, "start": "2026-11-05T00:00:00Z"})
    d.schedule()
    method, url, body = s.calls[-1]
    assert method == "POST" and url.endswith(f"/items/id-{ids.PIPELINE_SCORING}/jobs/Pipeline/schedules")
    assert body == {"enabled": True, "configuration": {"type": "Cron", "interval": 15, "startDateTime": "2026-11-05T00:00:00Z", "localTimeZoneId": "UTC"}}


def test_schedule_is_kept_when_equal_and_patched_when_different(tmp_path):
    same = [(("GET", "/schedules"), Resp(body={"value": [{"id": "s1", "enabled": True, "configuration": {"type": "Cron", "interval": 15}}]})), PIPELINE]
    d, s, lines = make(tmp_path, same, steps=["schedule"], schedule={"enabled": True})
    d.schedule()
    assert {m for m, _, _ in s.calls} == {"GET"} and "already runs every 15" in " ".join(lines)
    other = [(("GET", "/schedules"), Resp(body={"value": [{"id": "s1", "enabled": True, "configuration": {"type": "Cron", "interval": 60}}]})), PIPELINE,
             (("PATCH", "/schedules/s1"), Resp(200, {}))]
    d2, s2, _ = make(tmp_path, other, steps=["schedule"], schedule={"enabled": True})
    d2.schedule()
    assert s2.calls[-1][0] == "PATCH" and s2.calls[-1][2]["configuration"]["interval"] == 15


def test_schedule_interval_comes_from_the_settings(tmp_path):
    routes = [(("GET", "/schedules"), Resp(body={"value": []})), PIPELINE, (("POST", "/schedules"), Resp(201, {}))]
    d, s, _ = make(tmp_path, routes, steps=["schedule"], schedule={"enabled": True, "interval_minutes": 30})
    d.schedule()
    assert s.calls[-1][2]["configuration"]["interval"] == 30


# ---------------------------------------------------------------- git

GIT = {"enabled": True, "owner": "acme", "repository": "repo", "branch": "main", "directory": "/fabric"}


def test_git_connects_with_a_pat_from_the_environment_and_never_logs_it(tmp_path):
    routes = [
        (("GET", "/git/connection"), Resp(body={"gitConnectionState": "NotConnected"})),
        (("GET", "/v1/connections"), Resp(body={"value": []})),
        (("POST", "/v1/connections"), Resp(201, {"id": "cccccccc-0000-1111-2222-bbbbbbbbbbbb"})),
        (("POST", "/git/connect"), Resp(200, {})),
        (("POST", "/git/initializeConnection"), Resp(200, {"requiredAction": "UpdateFromGit", "workspaceHead": "h1", "remoteCommitHash": "r1"})),
        (("POST", "/git/updateFromGit"), Resp(200, {})),
    ]
    d, s, lines = make(tmp_path, routes, steps=["git"], env={extra_steps.ENV_GITHUB_PAT: SECRET_PAT}, git=GIT)
    d.git()
    posts = {u.rsplit("/", 1)[-1]: b for m, u, b in s.calls if m == "POST"}
    assert posts["connections"]["credentialDetails"]["credentials"]["key"] == SECRET_PAT
    assert posts["connect"]["myGitCredentials"]["connectionId"] == "cccccccc-0000-1111-2222-bbbbbbbbbbbb"
    assert posts["initializeConnection"] == {"initializationStrategy": "PreferWorkspace"}
    assert posts["updateFromGit"]["remoteCommitHash"] == "r1" and posts["updateFromGit"]["workspaceHead"] == "h1"
    assert SECRET_PAT not in "\n".join(lines)


def test_git_is_a_no_op_when_already_connected_and_needs_a_pat_otherwise(tmp_path):
    d, s, lines = make(tmp_path, [(("GET", "/git/connection"), Resp(body={"gitConnectionState": "ConnectedAndInitialized"}))], steps=["git"], git=GIT)
    d.git()
    assert {m for m, _, _ in s.calls} == {"GET"} and "already connected" in " ".join(lines)
    routes = [(("GET", "/git/connection"), Resp(body={"gitConnectionState": "NotConnected"})), (("GET", "/v1/connections"), Resp(body={"value": []}))]
    d2, _, _ = make(tmp_path, routes, steps=["git"], env={}, git=GIT)
    with pytest.raises(FabricError, match=extra_steps.ENV_GITHUB_PAT):
        d2.git()


def test_git_re_uses_an_existing_connection_without_a_pat(tmp_path):
    routes = [
        (("GET", "/git/connection"), Resp(body={"gitConnectionState": "NotConnected"})),
        (("GET", "/v1/connections"), Resp(body={"value": [{"id": "cccccccc-0000-1111-2222-bbbbbbbbbbbb", "displayName": "HomeBridge-GitHub"}]})),
        (("POST", "/git/connect"), Resp(200, {})),
        (("POST", "/git/initializeConnection"), Resp(200, {"requiredAction": "None"})),
    ]
    d, s, _ = make(tmp_path, routes, steps=["git"], env={}, git=GIT)
    d.git()
    assert not any(u.endswith("/v1/connections") and m == "POST" for m, u, _ in s.calls)


# ---------------------------------------------------------------- eventstream and replay

def es_routes():
    return [
        (("GET", "/items"), items((ids.EVENTSTREAM, "Eventstream"))),
        (("GET", "/topology"), Resp(body={"sources": [{"id": "src1", "name": "HomeBridgeLiveEvents", "type": "CustomEndpoint"}]})),
        (("GET", "/sources/src1/connection"), Resp(body={"type": "CustomEndpoint", "fullyQualifiedNamespace": "ns.servicebus.windows.net", "eventHubName": "es_hub",
                                                           "accessKeys": {"primaryConnectionString": FAKE_CONNECTION}})),
    ]


def test_eventstream_step_prints_names_and_never_the_key(tmp_path):
    d, _, lines = make(tmp_path, es_routes(), steps=["eventstream"])
    d.eventstream()
    text = "\n".join(lines)
    assert "ns.servicebus.windows.net" in text and "es_hub" in text and "fake-key-for-tests" not in text
    assert d.eventstream_connection["connection"] == FAKE_CONNECTION


def test_replay_passes_the_connection_to_the_child_process_only(tmp_path):
    repo = tmp_path / "repo"
    (repo / "data" / "output" / "eventhouse").mkdir(parents=True)
    (repo / "data" / "output" / "eventhouse" / "account_context.parquet").write_bytes(b"x")
    calls = []

    def runner(cmd, env, cwd=None):
        calls.append((cmd, env, cwd))
        return 0, '{"sent": 3}'
    d, _, lines = make(tmp_path, es_routes(), steps=["replay"], runner=runner, repo=repo, replay={"enabled": True}, env={"PATH": "x"})
    d.replay()
    cmd, env, cwd = calls[0]
    assert cmd[1:] == ["-m", "hbdata", "replay", "--target", "eventstream", "--backfill"] and cwd == str(repo / "data")
    assert env["HB_EVENTSTREAM_CONNECTION"] == FAKE_CONNECTION and env["HB_EVENTSTREAM_NAME"] == "es_hub"
    assert "HB_EVENTSTREAM_CONNECTION" not in d.env and FAKE_CONNECTION not in "\n".join(lines)


def test_replay_needs_local_data_first(tmp_path):
    d, _, _ = make(tmp_path, es_routes(), steps=["replay"], repo=tmp_path, replay={"enabled": True})
    with pytest.raises(FabricError, match="local data"):
        d.replay()


def test_replay_dry_run_reads_nothing(tmp_path):
    d, s, lines = make(tmp_path, [], dry_run=True, steps=["replay"], replay={"enabled": True})
    d.replay()
    assert s.calls == [] and "Would read the Eventstream connection" in " ".join(lines)


# ---------------------------------------------------------------- capacity

CAP = {"enabled": True, "subscription_id": "12345678-1234-1234-1234-123456789abc", "resource_group": "rg", "location": "westeurope", "sku": "F2"}


def test_capacity_is_created_once_with_admins_from_the_environment(tmp_path):
    cfg_routes = [
        (("GET", "management.azure.com"), [Resp(404, {}), Resp(body={"properties": {"state": "Provisioning"}}), Resp(body={"properties": {"state": "Active"}})]),
        (("PUT", "management.azure.com"), Resp(201, {"properties": {"state": "Provisioning"}})),
        (("GET", "/v1/capacities"), Resp(body={"value": [{"displayName": "hbcap", "id": "cccccccc-0000-1111-2222-bbbbbbbbbbbb"}]})),
    ]
    d, s, _ = make(tmp_path, cfg_routes, steps=["capacity"], env={extra_steps.ENV_CAPACITY_ADMINS: "admin-one, admin-two"}, capacity_create=CAP)
    d.cfg.capacity_name = "hbcap"
    d.capacity()
    put = next((u, b) for m, u, b in s.calls if m == "PUT")
    assert "/resourceGroups/rg/providers/Microsoft.Fabric/capacities/hbcap?api-version=2023-11-01" in put[0]
    assert put[1]["properties"]["administration"]["members"] == ["admin-one", "admin-two"]
    again = [(("GET", "management.azure.com"), Resp(body={"properties": {"state": "Active"}}))]
    d2, s2, lines = make(tmp_path, again, steps=["capacity"], capacity_create=CAP)
    d2.cfg.capacity_name = "hbcap"
    d2.capacity()
    assert {m for m, _, _ in s2.calls} == {"GET"} and "already exists" in " ".join(lines)


def test_capacity_without_admins_stops_before_creating(tmp_path):
    d, s, _ = make(tmp_path, [(("GET", "management.azure.com"), Resp(404, {}))], steps=["capacity"], env={}, capacity_create=CAP)
    d.cfg.capacity_name = "hbcap"
    with pytest.raises(FabricError, match=extra_steps.ENV_CAPACITY_ADMINS):
        d.capacity()
    assert {m for m, _, _ in s.calls} == {"GET"}


def test_capacity_dry_run_does_not_create(tmp_path):
    d, s, lines = make(tmp_path, [(("GET", "management.azure.com"), Resp(404, {}))], dry_run=True, steps=["capacity"], capacity_create=CAP)
    d.cfg.capacity_name = "hbcap"
    d.capacity()
    assert {m for m, _, _ in s.calls} == {"GET"} and "Would create capacity" in " ".join(lines)


def test_capacity_uses_the_arm_token_scope():
    scopes = []

    class Cred:
        def get_token(self, scope):
            scopes.append(scope)
            return type("T", (), {"token": "t"})()
    s = FakeSession([(("GET", "management.azure.com"), Resp(404, {}))])
    assert FabricClient(Cred(), session=s).arm_get_capacity("https://management.azure.com/x") is None
    assert scopes == ["https://management.azure.com/.default"]


# ---------------------------------------------------------------- SharePoint

SP = {"enabled": True, "site_url": "https://contoso.sharepoint.com/sites/homebridge-review"}


def test_sharepoint_creates_missing_lists_and_adds_only_missing_columns(tmp_path):
    spec = payloads.sharepoint_lists(REPO / "action" / "sharepoint" / "review-cases.list.json")
    first = spec[0]
    routes = [
        (("GET", "graph.microsoft.com/v1.0/sites/contoso.sharepoint.com:/sites/homebridge-review"), Resp(body={"id": "site-1"})),
        (("GET", "/sites/site-1/lists/list-1/columns"), Resp(body={"value": [{"name": c["name"]} for c in first["columns"][:-1]]})),
        (("GET", "/sites/site-1/lists?"), Resp(body={"value": [{"id": "list-1", "displayName": first["title"]}]})),
        (("POST", "/sites/site-1/lists/list-1/columns"), Resp(201, {})),
        (("POST", "/sites/site-1/lists"), Resp(201, {"id": "new"})),
    ]
    d, s, _ = make(tmp_path, routes, steps=["sharepoint"], sharepoint=SP)
    d.sharepoint()
    posts = [(u, b) for m, u, b in s.calls if m == "POST"]
    added = [b for u, b in posts if u.endswith("/columns")]
    created = [b for u, b in posts if u.endswith("/lists")]
    assert [b["name"] for b in added] == [first["columns"][-1]["name"]]
    assert [b["displayName"] for b in created] == [x["title"] for x in spec[1:]]


def test_sharepoint_dry_run_creates_nothing(tmp_path):
    routes = [(("GET", "contoso.sharepoint.com:/sites"), Resp(body={"id": "site-1"})), (("GET", "/lists?"), Resp(body={"value": []}))]
    d, s, lines = make(tmp_path, routes, dry_run=True, steps=["sharepoint"], sharepoint=SP)
    d.sharepoint()
    assert {m for m, _, _ in s.calls} == {"GET"} and sum("would create list" in x for x in lines) == 3


def test_graph_uses_app_only_sign_in_only_when_all_three_variables_are_set(monkeypatch):
    default = object()
    assert extra_steps.make_graph_credential({}, default) is default
    assert extra_steps.make_graph_credential({"HB_GRAPH_CLIENT_ID": "x"}, default) is default
    made = []
    monkeypatch.setattr("azure.identity.ClientSecretCredential", lambda *a: made.append(a) or "app")
    env = {"HB_GRAPH_TENANT_ID": "t", "HB_GRAPH_CLIENT_ID": "c", "HB_GRAPH_CLIENT_SECRET": "s"}
    assert extra_steps.make_graph_credential(env, default) == "app" and made == [("t", "c", "s")]


# ---------------------------------------------------------------- RLS

RLS = {"enabled": True, "members": {"General viewer": [GROUP], "Authorised adviser": [GROUP2]}}


def test_rls_runs_the_xmla_script_with_a_plan_of_object_ids_and_a_token_only_in_the_child_env(tmp_path, monkeypatch):
    monkeypatch.setattr(extra_steps.shutil, "which", lambda name: "pwsh" if name == "pwsh" else None)
    calls = []
    d, _, lines = make(tmp_path, [], steps=["rls"], runner=lambda cmd, env, cwd=None: calls.append((cmd, env)) or (0, "added  General viewer x"), rls=RLS)
    d.cfg.workspace_name = "HomeBridge-Dev"
    d.rls()
    cmd, env = calls[0]
    assert cmd[0] == "pwsh" and cmd[-2] == "-PlanPath" and cmd[cmd.index("-File") + 1].endswith("set_rls_members.ps1")
    assert env["HB_XMLA_TOKEN"] == "fake-token-not-real" and "HB_XMLA_TOKEN" not in d.env
    plan = json.loads((tmp_path / "b" / "rls.members.json").read_text(encoding="utf-8"))
    assert plan == {"workspace": "HomeBridge-Dev", "model": ids.SEMANTIC_MODEL, "memberType": "Group",
                    "members": {"General viewer": [GROUP], "Authorised adviser": [GROUP2]}}
    assert "fake-token-not-real" not in (tmp_path / "b" / "rls.members.json").read_text(encoding="utf-8") + "\n".join(lines)


def test_rls_failure_points_to_the_manual_step(tmp_path, monkeypatch):
    monkeypatch.setattr(extra_steps.shutil, "which", lambda name: "pwsh")
    d, _, _ = make(tmp_path, [], steps=["rls"], runner=lambda cmd, env, cwd=None: (1, "invalid rolememberships"), rls=RLS)
    d.cfg.workspace_name = "w"
    with pytest.raises(FabricError, match="manual step"):
        d.rls()


def test_rls_dry_run_runs_nothing(tmp_path):
    ran = []
    d, _, lines = make(tmp_path, [], dry_run=True, steps=["rls"], runner=lambda *a, **k: ran.append(a), rls=RLS)
    d.rls()
    assert ran == [] and "XMLA" in " ".join(lines)


def test_the_rls_script_has_no_syntax_errors_and_holds_no_secret():
    script = REPO / "scripts" / "fabric" / "set_rls_members.ps1"
    text = script.read_text(encoding="utf-8")
    assert "HB_XMLA_TOKEN" in text and "Write-Output $token" not in text and "ghp_" not in text
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell is not installed")
    cmd = f"$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('{script}',[ref]$null,[ref]$e); exit $e.Count"
    assert subprocess.run([shell, "-NoProfile", "-Command", cmd], capture_output=True).returncode == 0


# ---------------------------------------------------------------- the whole run

def test_a_dry_run_with_every_optional_step_sends_no_change_request(tmp_path):
    cfg = settings(tmp_path, capacity_create=CAP, roles=ROLES, retention={"enabled": True}, schedule={"enabled": True},
                   rls=RLS, git=GIT, sharepoint=SP, replay={"enabled": True})
    cfg.capacity_name = "hbcap"
    routes = [
        (("GET", "management.azure.com"), Resp(404, {})),
        (("GET", "/v1/capacities"), Resp(body={"value": []})),
        (("GET", f"/v1/workspaces/{WS}/roleAssignments"), Resp(body={"value": []})),
        (("GET", f"/v1/workspaces/{WS}/git/connection"), Resp(body={"gitConnectionState": "ConnectedAndInitialized"})),
        (("GET", "/schedules"), Resp(body={"value": []})),
        (("GET", "/kqlDatabases/"), Resp(body={"displayName": ids.KQL_DATABASE, "properties": {"queryServiceUri": "https://q"}})),
        (("POST", "/v1/rest/mgmt"), Resp(body={"Tables": []})),
        (("GET", "contoso.sharepoint.com:/"), Resp(body={"id": "site-1"})),
        (("GET", "/lists?"), Resp(body={"value": []})),
        (("GET", f"/v1/workspaces/{WS}"), Resp(body={"id": WS, "displayName": "W", "capacityId": "x"})),
        (("GET", "/items"), items((ids.KQL_DATABASE, "KQLDatabase"), (ids.PIPELINE_SCORING, "DataPipeline"))),
    ]
    session = FakeSession(routes)
    lines: list[str] = []
    d = deploy.Deployer(cfg, credential=FakeCredential(), client=FabricClient(FakeCredential(), session=session, sleep=lambda s: None),
                        dry_run=True, log=lines.append, build_dir=tmp_path / "b", env={}, graph_credential=FakeCredential())
    assert d.steps == deploy.default_steps(cfg) and "eventstream" not in d.steps
    d.steps = ["capacity", "workspace", "roles", "retention", "schedule", "rls", "sharepoint", "replay", "git"]
    d.run()
    changes = [(m, u) for m, u, b in session.calls if m != "GET" and not (m == "POST" and u.endswith("/v1/rest/mgmt"))]
    assert changes == []
    assert all("alter" not in b["csl"] for m, u, b in session.calls if m == "POST")
    assert "Dry run finished" in lines[-1]


def test_step_order_puts_capacity_first_and_the_model_before_rls():
    s = deploy.STEPS
    assert s.index("capacity") < s.index("workspace") < s.index("roles") < s.index("core") < s.index("retention")
    assert s.index("core") < s.index("streaming") < s.index("retention")
    assert s.index("model") < s.index("rls") and s.index("pipeline") < s.index("schedule") < s.index("model")
    assert s.index("eventstream") < s.index("replay")


# ---------------------------------------------------------------- streaming ingestion

STREAM_CONFLICT = ("Operation failed. Error Code: ScriptExecutionFailed. Referencing additional tables from update policy is not allowed "
                   "when streaming ingestion is enabled on the source table. Disable streaming ingestion on the following table: 'RawEventsLanding'.")


class CicdFailure(Exception):
    def __init__(self, text):
        super().__init__("Failed to publish 1 item(s): ['HomeBridgeEvents']")
        self.errors = [("HomeBridgeEvents", Exception(text))]


def test_streaming_off_command_follows_the_documented_form():
    assert payloads.streaming_off_command("RawEventsLanding") == ".alter-merge table ['RawEventsLanding'] policy streamingingestion '{\"IsEnabled\": false}'"
    assert payloads.streaming_is_enabled([]) and payloads.streaming_is_enabled([["p", json.dumps({"IsEnabled": True})]])
    assert not payloads.streaming_is_enabled([["p", json.dumps({"IsEnabled": False})]])
    assert payloads.is_streaming_conflict(CicdFailure(STREAM_CONFLICT)) and not payloads.is_streaming_conflict(CicdFailure("some other error"))


def test_streaming_step_turns_it_off_only_when_on(tmp_path):
    on = Resp(body={"Tables": [{"Rows": [["p", json.dumps({"IsEnabled": True})]]}]})
    d, s, _ = make(tmp_path, KQL_ROUTES(on, Resp(body={"Tables": []})), steps=["streaming"])
    d.streaming()
    csl = [b["csl"] for m, _, b in s.calls if m == "POST"]
    assert csl == [".show table RawEventsLanding policy streamingingestion", payloads.streaming_off_command("RawEventsLanding")]
    off = Resp(body={"Tables": [{"Rows": [["p", json.dumps({"IsEnabled": False})]]}]})
    d2, s2, lines = make(tmp_path, KQL_ROUTES(off), steps=["streaming"])
    d2.streaming()
    assert len([1 for m, _, _ in s2.calls if m == "POST"]) == 1 and "already off" in " ".join(lines)
    d3, s3, _ = make(tmp_path, KQL_ROUTES(on), dry_run=True, steps=["streaming"])
    d3.streaming()
    assert all("alter" not in b["csl"] for m, _, b in s3.calls if m == "POST")


def kql_routes_after_first_pass(*mgmt):
    """/items answers 'no database' twice (the Eventstream level check reads it first), then 'database exists'."""
    return [
        (("GET", "/items"), [items(), items(), items((ids.KQL_DATABASE, "KQLDatabase")), items((ids.KQL_DATABASE, "KQLDatabase"))]),
        (("GET", "/kqlDatabases/"), Resp(body={"displayName": ids.KQL_DATABASE, "properties": {"queryServiceUri": "https://q.kusto.fabric.microsoft.com"}})),
        (("POST", "/v1/rest/mgmt"), list(mgmt)),
    ]


ON = Resp(body={"Tables": [{"Rows": [["p", json.dumps({"IsEnabled": True})]]}]})


def recording_cicd(attempts, fail_on=()):
    def cicd(path):
        attempts.append(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
        if len(attempts) in fail_on:
            raise CicdFailure(STREAM_CONFLICT)
    return cicd


def test_core_without_a_database_publishes_it_without_policies_turns_streaming_off_then_deploys_everything(tmp_path):
    d, s, lines = make(tmp_path, kql_routes_after_first_pass(ON, Resp(body={"Tables": []})), steps=["core"])
    attempts = []
    d.cicd = recording_cicd(attempts)
    d.prepare()
    d.core()
    assert [a["core"]["item_types_in_scope"] for a in attempts] == [["Eventhouse", "KQLDatabase"], stage.CORE_TYPES]
    schema = next(Path(attempts[0]["core"]["repository_directory"]).rglob("DatabaseSchema.kql")).read_text(encoding="utf-8")
    assert "policy update" not in schema and ".create-merge table RawEventsLanding" in schema
    assert "policy update" in next(Path(attempts[1]["core"]["repository_directory"]).rglob("DatabaseSchema.kql")).read_text(encoding="utf-8")
    assert any(b["csl"] == payloads.streaming_off_command("RawEventsLanding") for m, _, b in s.calls if m == "POST")
    assert any("Turned streaming ingestion off" in x for x in lines)


def test_core_with_a_database_deploys_once_when_nothing_conflicts(tmp_path):
    d, s, _ = make(tmp_path, [(("GET", "/items"), items((ids.KQL_DATABASE, "KQLDatabase")))], steps=["core"])
    attempts = []
    d.cicd = recording_cicd(attempts)
    d.prepare()
    d.core()
    assert len(attempts) == 1 and all(m == "GET" for m, _, _ in s.calls)


def test_core_with_a_database_turns_streaming_off_after_a_conflict_and_retries_once(tmp_path):
    routes = [(("GET", "/items"), items((ids.KQL_DATABASE, "KQLDatabase"))),
              (("GET", "/kqlDatabases/"), Resp(body={"displayName": ids.KQL_DATABASE, "properties": {"queryServiceUri": "https://q"}})),
              (("POST", "/v1/rest/mgmt"), [ON, Resp(body={"Tables": []}), ON, Resp(body={"Tables": []})])]
    d, s, _ = make(tmp_path, routes, steps=["core"])
    attempts = []
    d.cicd = recording_cicd(attempts, fail_on=(1,))
    d.prepare()
    d.core()
    assert len(attempts) == 2
    d2, _, _ = make(tmp_path, routes, steps=["core"])
    n = []
    d2.cicd = recording_cicd(n, fail_on=(1, 2))
    d2.prepare()
    with pytest.raises(CicdFailure):
        d2.core()
    assert len(n) == 2  # one retry only


def test_core_does_not_retry_other_failures(tmp_path):
    d, s, _ = make(tmp_path, [(("GET", "/items"), items((ids.KQL_DATABASE, "KQLDatabase")))], steps=["core"])
    d.cicd = lambda p: (_ for _ in ()).throw(CicdFailure("quota exceeded"))
    d.prepare()
    with pytest.raises(CicdFailure):
        d.core()
    assert all(m == "GET" for m, _, _ in s.calls)


def test_first_pass_waits_while_fabric_still_holds_the_old_database_name(tmp_path):
    d, _, lines = make(tmp_path, kql_routes_after_first_pass(ON, Resp(body={"Tables": []})), steps=["core"])
    waits, calls = [], []
    d.sleep = waits.append

    def cicd(path):
        calls.append(path)
        if len(calls) <= 2:
            raise CicdFailure("Requested 'HomeBridgeEvents' is not available yet and is expected to become available in the upcoming minutes.")
    d.cicd = cicd
    d.prepare()
    d.core()
    assert waits == [30, 30] and len(calls) == 4 and any("Waiting 30 seconds" in x for x in lines)
    d2, _, _ = make(tmp_path, kql_routes_after_first_pass(), steps=["core"])
    d2.sleep = lambda s: None
    d2.cicd = lambda p: (_ for _ in ()).throw(CicdFailure("not available yet"))
    d2.prepare()
    with pytest.raises(CicdFailure):
        d2.core()


def test_without_update_policies_keeps_every_other_command():
    text = ".create-merge table T (a:string)\n.alter table RawEvents policy update @'[]'\n.create-or-alter function F() { 1 }\n"
    out = payloads.without_update_policies(text)
    assert "policy update" not in out and ".create-merge table T" in out and ".create-or-alter function F" in out


def test_kusto_falls_back_to_the_database_uri_audience_after_a_403():
    scopes = []

    class Cred:
        def get_token(self, scope):
            scopes.append(scope)
            return type("T", (), {"token": "t"})()
    s = FakeSession([(("POST", "/v1/rest/mgmt"), [Resp(403, {}), Resp(body={"Tables": [{"Rows": [["ok"]]}]})])])
    rows = FabricClient(Cred(), session=s).kusto_mgmt("https://q.kusto.fabric.microsoft.com", "db", ".show version")
    assert rows == [["ok"]] and scopes == ["https://api.kusto.windows.net/.default", "https://q.kusto.fabric.microsoft.com/.default"]
