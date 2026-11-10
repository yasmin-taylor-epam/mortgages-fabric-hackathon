import json
from pathlib import Path

import pytest
import yaml

from conftest import REPO
from hbfabric import config as cfgmod
from hbfabric import deploy, ids, onelake, payloads, stage
from hbfabric.api import FabricClient, FabricError

EXAMPLE = REPO / "fabric" / "config" / "fabric.config.example.yml"


class FakeCredential:
    def get_token(self, *scopes):
        class T:
            token = "fake-token-not-real"
        return T()


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body if body is not None else {}, headers or {}
        self.content = b"x"
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, headers=None, timeout=None, json=None, **kw):
        self.calls.append((method, url.replace(deploy.__name__, ""), json))
        for (m, frag), reply in self.routes:
            if m == method and frag in url:
                return reply.pop(0) if isinstance(reply, list) else reply
        raise AssertionError(f"unexpected {method} {url}")


# ---------------------------------------------------------------- config

def test_example_config_loads_and_needs_a_capacity_for_a_new_workspace():
    cfg = cfgmod.load(EXAMPLE)
    assert cfg.environment == "dev" and cfg.workspace_name == "HomeBridge-Dev"
    assert any("capacity" in p for p in cfg.problems())
    cfg2 = cfgmod.load(EXAMPLE, overrides={"capacity_name": "my-capacity"})
    assert cfg2.problems() == []


def test_config_refuses_secrets_and_tenant_ids(tmp_path):
    for key in ("password", "client_secret", "tenant_id", "token", "username"):
        p = tmp_path / "c.yml"
        p.write_text(yaml.safe_dump({"environments": {"dev": {"workspace_name": "x", key: "y"}}}), encoding="utf-8")
        with pytest.raises(cfgmod.ConfigError, match="must not hold secrets"):
            cfgmod.load(p)


def test_config_rejects_unknown_keys_and_bad_guids(tmp_path):
    p = tmp_path / "c.yml"
    p.write_text(yaml.safe_dump({"environments": {"dev": {"workspace_name": "x", "colour": 1}}}), encoding="utf-8")
    with pytest.raises(cfgmod.ConfigError, match="Unknown settings"):
        cfgmod.load(p)
    cfg = cfgmod.load(EXAMPLE, overrides={"capacity_id": "not-a-guid"})
    assert any("capacity_id is not a GUID" in x for x in cfg.problems())


def test_env_vars_and_overrides_set_workspace_and_capacity(tmp_path, monkeypatch):
    monkeypatch.setenv("HB_FABRIC_WORKSPACE", "FromEnv")
    monkeypatch.setenv("HB_FABRIC_CAPACITY", "CapFromEnv")
    cfg = cfgmod.load(None)
    assert (cfg.workspace_name, cfg.capacity_name) == ("FromEnv", "CapFromEnv") and cfg.problems() == []
    assert cfgmod.load(None, overrides={"workspace_name": "Cli"}).workspace_name == "Cli"


def test_unknown_environment_is_an_error():
    with pytest.raises(cfgmod.ConfigError, match="not in the config file"):
        cfgmod.load(EXAMPLE, "prod")


# ---------------------------------------------------------------- fabric-cicd inputs

@pytest.fixture()
def deployer(tmp_path):
    cfg = cfgmod.load(EXAMPLE, overrides={"capacity_name": "cap", "workspace_id": "aaaaaaaa-0000-1111-2222-bbbbbbbbbbbb"})
    d = deploy.Deployer(cfg, credential=FakeCredential(), client=FabricClient(FakeCredential(), session=FakeSession([])), build_dir=tmp_path / "build", dry_run=True)
    d.workspace_id = cfg.workspace_id
    d.prepare()
    return d


def test_generated_cicd_configs_pass_the_fabric_cicd_validator(deployer):
    from fabric_cicd._common._config_validator import ConfigValidator

    for name, types, param in (("core", stage.CORE_TYPES, None), ("model", stage.MODEL_TYPES, deploy.model_parameters("dev"))):
        path = deployer._write_config(name, types, param)
        out = ConfigValidator().validate_config_file(str(path), "dev")
        assert out["core"]["item_types_in_scope"] == types


def test_model_parameter_file_filters_match_the_staged_items(deployer, caplog):
    import logging

    from fabric_cicd._parameter._parameter import Parameter

    deployer._write_config("model", stage.MODEL_TYPES, deploy.model_parameters("dev"))
    p = Parameter(repository_directory=deployer.stage_dir, item_type_in_scope=stage.MODEL_TYPES, environment="dev",
                  parameter_file_path=str(deployer.build_dir / "model.parameter.yml"))
    with caplog.at_level(logging.DEBUG):
        p._validate_parameter_file()
    assert "unmatched" not in caplog.text and "not in scope" not in caplog.text
    names = {d.name for d in stage.item_dirs(deployer.stage_dir)}
    assert f"{ids.SEMANTIC_MODEL}.SemanticModel" in names and f"{ids.LAKEHOUSE}.Lakehouse" in names


def test_model_placeholders_exist_in_the_expressions_file():
    text = (REPO / "reports" / "HomeBridgeMortgage.SemanticModel" / "definition" / "expressions.tmdl").read_text(encoding="utf-8")
    for entry in deploy.model_parameters("dev")["find_replace"]:
        assert entry["find_value"] in text
        assert entry["replace_value"]["dev"].startswith(f"$items.Lakehouse.{ids.LAKEHOUSE}.$sqlendpoint")


# ---------------------------------------------------------------- one as-of date

def test_as_of_is_off_by_default_and_must_be_a_utc_timestamp():
    assert cfgmod.load(EXAMPLE, overrides={"capacity_name": "c"}).run.as_of == ""
    assert deploy.core_parameters("dev", "") is None
    bad = cfgmod.load(EXAMPLE, overrides={"capacity_name": "c"})
    bad.run.as_of = "16 October"
    assert any("run.as_of" in x for x in bad.problems())
    bad.run.as_of = "2026-10-16T09:00:00Z"
    assert bad.problems() == []


def test_demo_profile_in_the_example_sets_the_as_of_date_and_turns_on_the_schedule():
    cfg = cfgmod.load(EXAMPLE, "demo", overrides={"capacity_name": "cap"})
    assert cfg.run.as_of == "2026-10-16T09:00:00Z" and cfg.schedule.enabled and cfg.schedule.interval_minutes == 15
    assert cfg.problems() == [] and cfgmod.load(EXAMPLE, "dev").run.as_of == ""


def test_as_of_sets_the_pipeline_parameter_default_through_a_regex_that_fabric_cicd_accepts(deployer, caplog):
    import logging
    import re

    from fabric_cicd._parameter._parameter import Parameter

    params = deploy.core_parameters("dev", "2026-10-16T09:00:00Z")
    deployer._write_config("core", stage.CORE_TYPES, params)
    p = Parameter(repository_directory=deployer.stage_dir, item_type_in_scope=stage.CORE_TYPES, environment="dev",
                  parameter_file_path=str(deployer.build_dir / "core.parameter.yml"))
    with caplog.at_level(logging.DEBUG):
        assert p._validate_parameter_file()
    entry = params["find_replace"][0]
    pipeline = deployer.stage_dir / f"{ids.PIPELINE_SCORING}.DataPipeline" / "pipeline-content.json"
    text = pipeline.read_text(encoding="utf-8")
    # The same replacement rule as fabric-cicd: only capture group 1 is replaced.
    assert re.search(entry["find_value"], text).group(1)  # fabric-cicd refuses an empty capture group (seen live)
    repl = entry["replace_value"]["dev"]
    out = re.sub(entry["find_value"], lambda m: m.group(0)[: m.start(1) - m.start(0)] + repl + m.group(0)[m.end(1) - m.start(0):], text)
    assert json.loads(out)["properties"]["parameters"]["as_of"]["defaultValue"] == "2026-10-16T09:00:00Z"
    assert json.loads(out)["properties"]["parameters"]["cutoff_at"]["defaultValue"] == ""  # nothing else changed


def test_as_of_reaches_notebook_06_and_the_pipeline_run_and_replaces_the_pipeline_cutoff(tmp_path, capsys):
    cfg = cfgmod.load(EXAMPLE, overrides={"capacity_name": "cap", "workspace_id": "aaaaaaaa-0000-1111-2222-bbbbbbbbbbbb"})
    assert dict(deploy.notebook_runs(cfg))["06_publish_delta_tables"] == {"AS_OF": ""}
    cfg.run.as_of = "2026-10-16T09:00:00Z"
    assert dict(deploy.notebook_runs(cfg))["06_publish_delta_tables"] == {"AS_OF": "2026-10-16T09:00:00Z"}
    d = deploy.Deployer(cfg, credential=FakeCredential(), client=FabricClient(FakeCredential(), session=FakeSession([])), build_dir=tmp_path / "b", dry_run=True)
    d.workspace_id = cfg.workspace_id
    d.pipeline()
    out = capsys.readouterr().out
    assert "'cutoff_at': ''" in out and "'as_of': '2026-10-16T09:00:00Z'" in out


# ---------------------------------------------------------------- skip item types

def test_skip_item_types_is_checked_and_empty_by_default():
    cfg = cfgmod.load(EXAMPLE, overrides={"capacity_name": "c"})
    assert cfg.skip_item_types == [] and cfg.problems() == []
    cfg.skip_item_types = ["Eventstream"]
    assert cfg.problems() == []
    cfg.skip_item_types = ["Lakehouse"]
    assert any("skip_item_types may hold only" in x for x in cfg.problems())
    cfg.skip_item_types = ["Eventhouse"]
    assert any("skip KQLDatabase together with Eventhouse" in x for x in cfg.problems())


def test_core_leaves_a_skipped_eventstream_alone_and_does_not_read_it(tmp_path):
    from fabric_cicd._common._config_validator import ConfigValidator

    published = []
    ws = "aaaaaaaa-0000-1111-2222-bbbbbbbbbbbb"
    session = FakeSession([(("GET", f"/v1/workspaces/{ws}/items"), Resp(body={"value": [
        {"id": "es-1", "type": "Eventstream", "displayName": "HomeBridgeRiskEventsV2"},
        {"id": "db-1", "type": "KQLDatabase", "displayName": ids.KQL_DATABASE}]}))])
    cfg = cfgmod.load(EXAMPLE, overrides={"capacity_name": "cap", "workspace_id": ws})
    cfg.skip_item_types = ["Eventstream"]
    d = deploy.Deployer(cfg, credential=FakeCredential(), client=FabricClient(FakeCredential(), session=session), build_dir=tmp_path / "b",
                        cicd=lambda config: published.append(config))
    d.workspace_id = ws
    d.prepare()
    d.core()
    out = ConfigValidator().validate_config_file(str(published[0]), "dev")
    assert "Eventstream" not in out["core"]["item_types_in_scope"] and "KQLDatabase" in out["core"]["item_types_in_scope"]
    assert not any("topology" in u or "eventstreams" in u for _, u, _ in session.calls)  # the live Eventstream is not read
    assert all(m == "GET" for m, _, _ in session.calls)
    assert not cfg.prune and "Eventstream" in stage.CORE_TYPES  # the default run still includes it


def test_dry_run_says_what_it_leaves_alone(tmp_path, capsys):
    cfg = cfgmod.load(EXAMPLE, overrides={"capacity_name": "cap", "workspace_id": "aaaaaaaa-0000-1111-2222-bbbbbbbbbbbb"})
    cfg.skip_item_types = ["Eventstream"]
    d = deploy.Deployer(cfg, credential=FakeCredential(), client=FabricClient(FakeCredential(), session=FakeSession([])), build_dir=tmp_path / "b", dry_run=True)
    d.workspace_id = cfg.workspace_id
    d.core()
    out = capsys.readouterr().out
    assert "leaves ['Eventstream'] alone" in out and "compatibility level" not in out and "'Eventstream'" not in out.split("deploy")[1].split("leaves")[0]


# ---------------------------------------------------------------- Eventstream compatibility level

def _core_with_live_eventstream(tmp_path, topology, published):
    ws = "aaaaaaaa-0000-1111-2222-bbbbbbbbbbbb"
    session = FakeSession([
        (("GET", f"/v1/workspaces/{ws}/items"), Resp(body={"value": [{"id": "es-1", "type": "Eventstream", "displayName": ids.EVENTSTREAM}]})),
        (("GET", "/eventstreams/es-1/topology"), Resp(body=topology)),
    ])
    cfg = cfgmod.load(EXAMPLE, overrides={"capacity_name": "cap", "workspace_id": ws})
    d = deploy.Deployer(cfg, credential=FakeCredential(), client=FabricClient(FakeCredential(), session=session), build_dir=tmp_path / "b",
                        cicd=lambda config: published.append(config))
    d.workspace_id = ws
    d.prepare()
    return d, session


def test_level_helpers_read_both_spellings_and_the_repository_file(tmp_path):
    assert payloads.level_key("1.1") == payloads.level_key("Compat_1_1") == (1, 1) and payloads.level_key("1.0") < payloads.level_key("1.1")
    assert payloads.level_key("") == (0,)
    es = tmp_path / "x.Eventstream"
    es.mkdir()
    (es / "eventstream.json").write_text('{"compatibilityLevel": "1.1"}', encoding="utf-8")
    assert payloads.eventstream_level(tmp_path) == "1.1" and payloads.eventstream_level(tmp_path / "none") == ""


def test_core_stops_before_publishing_when_the_live_eventstream_is_at_1_0(tmp_path):
    published = []
    d, session = _core_with_live_eventstream(tmp_path, {"sources": [], "compatibilityLevel": "1.0"}, published)
    with pytest.raises(FabricError, match=r"compatibility level 1\.0.*cannot upgrade an Eventstream in place.*Nothing was published"):
        d.core()
    assert published == [] and all(m == "GET" for m, _, _ in session.calls)  # nothing changed in the workspace


def test_core_goes_on_when_the_live_eventstream_is_at_1_1_or_does_not_exist_or_reports_no_level(tmp_path):
    for topology in ({"compatibilityLevel": "1.1"}, {"compatibilityLevel": "Compat_1_1"}, {"sources": []}):
        published = []
        d, _ = _core_with_live_eventstream(tmp_path, topology, published)
        d.client.find_item = lambda ws, t, n: {"id": "es-1"} if t == "Eventstream" else {"id": "x"}  # other items already exist
        d.core()
        assert published, topology
    published = []
    d, _ = _core_with_live_eventstream(tmp_path, {"compatibilityLevel": "1.0"}, published)
    d.client.find_item = lambda ws, t, n: None if t == "Eventstream" else {"id": "x"}  # a fresh workspace has no Eventstream
    d.core()
    assert published


# ---------------------------------------------------------------- dry-run and apply order

def test_dry_run_sends_no_change_request(tmp_path):
    session = FakeSession([
        (("GET", "/v1/workspaces/aaaaaaaa"), Resp(body={"id": "aaaaaaaa-0000-1111-2222-bbbbbbbbbbbb", "displayName": "W", "capacityId": "x"})),
        (("GET", "/v1/capacities"), Resp(body={"value": [{"displayName": "cap", "id": "cccccccc-0000-1111-2222-bbbbbbbbbbbb"}]})),
    ])
    cfg = cfgmod.load(EXAMPLE, overrides={"capacity_name": "cap", "workspace_id": "aaaaaaaa-0000-1111-2222-bbbbbbbbbbbb"})
    lines = []
    deploy.Deployer(cfg, credential=FakeCredential(), client=FabricClient(FakeCredential(), session=session), dry_run=True,
                    log=lines.append, build_dir=tmp_path / "b").run()
    assert {m for m, _, _ in session.calls} == {"GET"}
    text = "\n".join(lines)
    assert "Would deploy" in text and "Would run notebook 00_load_raw" in text and "Dry run finished" in text


def test_apply_runs_the_steps_in_order(tmp_path):
    cfg = cfgmod.load(EXAMPLE, overrides={"capacity_name": "cap", "workspace_name": "NewWs"})
    session = FakeSession([
        (("POST", "/jobs/instances"), Resp(202, headers={"Location": "https://api.fabric.microsoft.com/job/1", "Retry-After": "0"})),
        (("GET", "/items"), Resp(body={"value": [{"id": f"id-{n}", "type": t, "displayName": n} for n, t in
                                              [(ids.LAKEHOUSE, "Lakehouse"), (ids.PIPELINE_SCORING, "DataPipeline"), (ids.KQL_DATABASE, "KQLDatabase")] + [(ids.LAKEHOUSE, "SQLEndpoint"), (ids.SEMANTIC_MODEL, "SemanticModel")] + [(x, "Notebook") for x in ids.NOTEBOOKS]]})),
        (("GET", "/v1/workspaces"), Resp(body={"value": []})),
        (("GET", "/v1/capacities"), Resp(body={"value": [{"displayName": "cap", "id": "cccccccc-0000-1111-2222-bbbbbbbbbbbb"}]})),
        (("POST", "/v1/workspaces"), Resp(201, {"id": "wwwwwwww-0000-1111-2222-bbbbbbbbbbbb"})),
        (("GET", "/job/1"), Resp(body={"status": "Completed"})),
        (("POST", "/datasets/"), Resp(202, {})),
        (("GET", "/datasets/"), Resp(body={"value": [{"status": "Completed"}]})),
    ])
    events, uploaded, cicd = [], [], []
    d = deploy.Deployer(cfg, credential=FakeCredential(), client=FabricClient(FakeCredential(), session=session, sleep=lambda s: None),
                        log=events.append, build_dir=tmp_path / "b", uploader=lambda *a: uploaded.append(a[2:4]),
                        cicd=lambda path: cicd.append(yaml.safe_load(Path(path).read_text(encoding="utf-8"))))
    d.steps = ["workspace", "core", "notebooks", "pipeline", "model"]
    d.run()
    assert d.workspace_id == "wwwwwwww-0000-1111-2222-bbbbbbbbbbbb"
    assert [c["core"]["item_types_in_scope"] for c in cicd] == [stage.CORE_TYPES, stage.MODEL_TYPES]
    assert all(c["unpublish"]["skip"] is True for c in cicd)
    jobs = [(u, b) for m, u, b in session.calls if m == "POST" and "/jobs/instances" in u]
    kinds = [u.split("jobType=")[1] for u, _ in jobs]
    assert kinds == ["RunNotebook"] * 7 + ["Pipeline"] + ["RunNotebook"]
    first_params = jobs[4][1]["executionData"]["parameters"]  # notebook 04
    assert first_params["RUN_KIND"] == {"value": "checkpoint", "type": "string"} and first_params["PUBLISH"] == {"value": True, "type": "bool"}
    assert jobs[7][1]["executionData"]["parameters"]["run_kind"] == "scheduled"


def test_a_failed_job_stops_the_run(tmp_path):
    cfg = cfgmod.load(EXAMPLE, overrides={"capacity_name": "cap", "workspace_id": "aaaaaaaa-0000-1111-2222-bbbbbbbbbbbb"})
    session = FakeSession([
        (("GET", "/items"), Resp(body={"value": [{"id": "n1", "type": "Notebook", "displayName": "00_load_raw"}]})),
        (("POST", "/jobs/instances"), Resp(202, headers={"Location": "https://api.fabric.microsoft.com/job/1", "Retry-After": "0"})),
        (("GET", "/job/1"), Resp(body={"status": "Failed", "failureReason": {"message": "boom"}})),
    ])
    d = deploy.Deployer(cfg, credential=FakeCredential(), client=FabricClient(FakeCredential(), session=session, sleep=lambda s: None), build_dir=tmp_path / "b", log=lambda *_: None)
    with pytest.raises(FabricError, match="Failed"):
        d.notebooks()


def test_429_is_retried_after_the_wait(tmp_path):
    waits = []
    session = FakeSession([(("GET", "/v1/workspaces"), [Resp(429, headers={"Retry-After": "3"}), Resp(body={"value": [{"displayName": "W", "id": "1"}]})])])
    c = FabricClient(FakeCredential(), session=session, sleep=waits.append)
    assert c.find_workspace("W")["id"] == "1" and waits == [3.0]


def test_label_request_uses_the_documented_shape():
    session = FakeSession([(("POST", "/v1/admin/items/bulkSetLabels"), Resp(body={"itemsChangeLabelStatus": [{"id": "1", "type": "Report", "status": "Succeeded"}]}))])
    FabricClient(FakeCredential(), session=session).set_labels("lbl", [{"id": "1", "type": "Report", "displayName": "x"}])
    assert session.calls[0][2] == {"items": [{"id": "1", "type": "Report"}], "labelId": "lbl", "assignmentMethod": "Standard"}


# ---------------------------------------------------------------- OneLake upload list

def test_code_upload_list_has_the_layout_the_notebooks_expect():
    remote = {r for _, r in onelake.code_files(REPO)}
    assert "hb/code/data/hbdata/__init__.py" in remote
    assert "hb/code/data/contracts/mortgage_account.schema.json" in remote
    assert "hb/code/notebooks/models_eval/__init__.py" in remote
    assert not any("/tests/" in r or "__pycache__" in r or r.endswith(".pyc") for r in remote)


def test_a_missing_item_reports_which_step_to_run(tmp_path):
    cfg = cfgmod.load(EXAMPLE, overrides={"capacity_name": "cap", "workspace_id": "aaaaaaaa-0000-1111-2222-bbbbbbbbbbbb"})
    session = FakeSession([(("GET", "/items"), Resp(body={"value": []}))])
    d = deploy.Deployer(cfg, credential=FakeCredential(), client=FabricClient(FakeCredential(), session=session), build_dir=tmp_path / "b", log=lambda *_: None)
    with pytest.raises(FabricError, match="core"):
        d.upload()


def test_cli_validate_needs_no_config_and_no_sign_in(capsys):
    assert deploy.main(["--validate", "--config", str(REPO / "does-not-exist.yml")]) == 0
    assert "Nothing was sent to Fabric" in capsys.readouterr().out


# ---------------------------------------------------------------- SQL endpoint refresh before the model

SQLE = (("GET", "/items"), Resp(body={"value": [{"id": "sqle-1", "type": "SQLEndpoint", "displayName": ids.LAKEHOUSE},
                                                {"id": "model-1", "type": "SemanticModel", "displayName": ids.SEMANTIC_MODEL}]}))
MODEL_REFRESH = [(("POST", "/datasets/model-1/refreshes"), Resp(202, {})),
                 (("GET", "/datasets/model-1/refreshes"), Resp(body={"value": [{"status": "Completed", "refreshType": "DirectLakeFraming"}]}))]


def _model_deployer(tmp_path, routes, dry_run=False):
    cfg = cfgmod.load(EXAMPLE, overrides={"capacity_name": "cap", "workspace_id": "aaaaaaaa-0000-1111-2222-bbbbbbbbbbbb"})
    session = FakeSession(routes)
    lines, cicd = [], []
    d = deploy.Deployer(cfg, credential=FakeCredential(), client=FabricClient(FakeCredential(), session=session, sleep=lambda s: None),
                        dry_run=dry_run, log=lines.append, build_dir=tmp_path / "b", cicd=lambda p: cicd.append(p), env={})
    d.workspace_id = cfg.workspace_id
    d.prepare()
    return d, session, lines, cicd


def test_model_step_refreshes_the_sql_endpoint_before_it_publishes(tmp_path):
    routes = [SQLE, (("POST", "/sqlEndpoints/sqle-1/refreshMetadata"), Resp(200, {"value": [{"tableName": "mortgage", "status": "Success"}, {"tableName": "payment", "status": "NotRun"}]}))] + MODEL_REFRESH
    d, s, lines, cicd = _model_deployer(tmp_path, routes)
    order = []
    d.cicd = lambda p: order.append(("publish", [c[1] for c in s.calls if c[0] == "POST"]))
    d.model()
    assert order and order[0][1][0].endswith("/sqlEndpoints/sqle-1/refreshMetadata")
    assert s.calls[-2][0] == "POST" and s.calls[-2][1].endswith("/datasets/model-1/refreshes")  # the model refresh follows the publish
    assert any("refresh done for 2 table" in x for x in lines)


def test_model_step_follows_a_long_running_refresh(tmp_path):
    routes = [SQLE,
              (("POST", "/refreshMetadata"), Resp(202, headers={"Location": "https://api.fabric.microsoft.com/v1/operations/op1", "Retry-After": "0"})),
              (("GET", "/operations/op1/result"), Resp(body={"value": [{"tableName": "mortgage", "status": "Success"}]})),
              (("GET", "/operations/op1"), Resp(body={"status": "Succeeded"}))] + MODEL_REFRESH
    d, s, lines, cicd = _model_deployer(tmp_path, routes)
    d.model()
    assert len(cicd) == 1 and any("done for 1 table" in x for x in lines)


def test_model_step_stops_when_the_endpoint_cannot_sync(tmp_path):
    routes = [SQLE, (("POST", "/refreshMetadata"), Resp(200, {"value": [{"tableName": "mortgage", "status": "Failure", "error": {"errorCode": "SomeError"}}]}))]
    d, _, _, cicd = _model_deployer(tmp_path, routes)
    with pytest.raises(FabricError, match="mortgage: SomeError"):
        d.model()
    assert cicd == []


def test_model_step_dry_run_does_not_refresh(tmp_path):
    d, s, lines, cicd = _model_deployer(tmp_path, [], dry_run=True)
    d.model()
    assert s.calls == [] and cicd == [] and any("Would refresh the SQL analytics endpoint" in x for x in lines)


def test_sql_endpoint_refresh_sends_the_documented_timeout_shape():
    s = FakeSession([(("POST", "/refreshMetadata"), Resp(200, {"value": []}))])
    FabricClient(FakeCredential(), session=s).refresh_sql_endpoint("w", "e", timeout_minutes=15)
    assert s.calls[0][2] == {"timeout": {"value": 15, "timeUnit": "Minutes"}}  # a "00:15:00" string was refused by Fabric


def test_model_refresh_waits_until_completed_and_uses_the_power_bi_scope():
    scopes = []

    class Cred:
        def get_token(self, scope):
            scopes.append(scope)
            return type("T", (), {"token": "t"})()
    s = FakeSession([(("POST", "/datasets/m/refreshes"), Resp(202, {})),
                     (("GET", "/datasets/m/refreshes"), [Resp(body={"value": [{"status": "Unknown"}]}), Resp(body={"value": [{"status": "Completed"}]})])])
    out = FabricClient(Cred(), session=s, sleep=lambda x: None).refresh_semantic_model("w", "m")
    assert out["status"] == "Completed" and set(scopes) == {"https://analysis.windows.net/powerbi/api/.default"}
    assert s.calls[0][2] == {"type": "full"}


def test_model_refresh_failure_stops_the_deploy(tmp_path):
    s = FakeSession([(("POST", "/datasets/m/refreshes"), Resp(202, {})),
                     (("GET", "/datasets/m/refreshes"), Resp(body={"value": [{"status": "Failed", "serviceExceptionJson": "boom"}]}))])
    with pytest.raises(FabricError, match="Failed"):
        FabricClient(FakeCredential(), session=s, sleep=lambda x: None).refresh_semantic_model("w", "m")
