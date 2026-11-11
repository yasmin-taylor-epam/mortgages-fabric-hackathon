"""Deploy the HomeBridge build to a Microsoft Fabric workspace.

Steps, in order (select with --only or --skip):
  capacity    OPTIONAL. Create a Fabric capacity through Azure Resource Manager.
  workspace   find or create the workspace, assign the capacity
  roles       OPTIONAL. Assign workspace roles from the settings.
  core        deploy Lakehouse, Environment, Eventhouse, KQL database, notebooks, pipeline and Eventstream (fabric-cicd)
  streaming   OPTIONAL (--only). Turn streaming ingestion off for RawEventsLanding. The core step does this by itself when Fabric needs it.
  retention   OPTIONAL. Set the soft-delete retention of RawEventsLanding (Kusto management endpoint).
  upload      generate the synthetic exports, upload them and the Python code to the Lakehouse Files area
  notebooks   run 00 to 05 in order, then 06 (Parquet to Delta tables)
  pipeline    run pl_scheduled_scoring once, then 06 again
  schedule    OPTIONAL. Schedule pl_scheduled_scoring (default 15 minutes).
  model       deploy the semantic model and report (fabric-cicd). Needs the Delta tables from step "notebooks".
  rls         OPTIONAL. Add members to the semantic model roles through the XMLA endpoint.
  labels      set a Purview sensitivity label on the model and report (Fabric administrator only)
  sharepoint  OPTIONAL. Create the review-case lists through Microsoft Graph.
  eventstream OPTIONAL. Read the Eventstream custom endpoint details (names only).
  replay      OPTIONAL. Send the replay events to the Eventstream. Reads the connection at run time.
  git         OPTIONAL. Connect the workspace to a GitHub branch. The PAT comes from HB_GITHUB_PAT.

An OPTIONAL step runs when its settings section has `enabled: true`, or when you name it with --only.
Modes: --validate checks everything offline. --dry-run signs in, reads, and prints what it would do. The default applies.
Not yet run against a real Fabric tenant. Nothing here claims otherwise.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import yaml

from . import config as cfgmod
from . import ids, onelake, payloads, stage
from .api import SCOPE, FabricClient, FabricError
from .extra_steps import ExtraSteps, default_runner, make_graph_credential

REPO = stage.REPO
BUILD = REPO / "build" / "fabric"
STEPS = ["capacity", "workspace", "roles", "core", "streaming", "retention", "upload", "notebooks", "pipeline", "schedule", "model",
         "rls", "labels", "sharepoint", "eventstream", "replay", "git"]
# Optional steps and the settings section that switches each on. eventstream has no section: use --only.
OPTIONAL = {"capacity": "capacity_create", "roles": "roles", "retention": "retention", "schedule": "schedule", "rls": "rls",
            "sharepoint": "sharepoint", "eventstream": None, "streaming": None, "replay": "replay", "git": "git"}
NO_WORKSPACE_NEEDED = {"capacity", "workspace", "sharepoint"}


def default_steps(cfg: cfgmod.EnvSettings) -> list[str]:
    return [s for s in STEPS if s not in OPTIONAL or (OPTIONAL[s] and getattr(cfg, OPTIONAL[s]).enabled)]


def model_parameters(environment: str) -> dict:
    """fabric-cicd parameter file for the semantic model. It fills the two Direct Lake placeholders."""
    target = f"$items.Lakehouse.{ids.LAKEHOUSE}"
    common = {"item_type": "SemanticModel", "item_name": ids.SEMANTIC_MODEL}
    return {"find_replace": [
        {"find_value": "REPLACE-WITH-SQL-ANALYTICS-ENDPOINT", "replace_value": {environment: f"{target}.$sqlendpoint"}, **common},
        {"find_value": "REPLACE-WITH-LAKEHOUSE-ID", "replace_value": {environment: f"{target}.$sqlendpointid"}, **common},
    ]}


def core_parameters(environment: str, as_of: str) -> dict | None:
    """fabric-cicd parameter file for the core items. With an as-of date it sets the default of the pipeline parameter
    "as_of", so that a scheduled run (which passes no parameters) also uses it. fabric-cicd replaces
    only the first capture group of a regex, and refuses an empty group (a first live run failed on that), so the group is the
    whole `"defaultValue": ""` text. Not yet run in Fabric."""
    if not as_of:
        return None
    return {"find_replace": [{
        "find_value": r'"as_of":\s*\{\s*"type":\s*"string",\s*("defaultValue":\s*"")',
        "replace_value": {environment: f'"defaultValue": "{as_of}"'}, "is_regex": "true",
        "item_type": "DataPipeline", "item_name": ids.PIPELINE_SCORING,
    }]}


def cicd_config(workspace_id: str, stage_dir: Path, types: list[str], parameter: Path | None, prune: bool) -> dict:
    core = {"workspace_id": workspace_id, "repository_directory": str(stage_dir), "item_types_in_scope": types}
    if parameter:
        core["parameter"] = str(parameter)
    return {"core": core, "publish": {"skip": False}, "unpublish": {"skip": not prune}}


def notebook_runs(cfg: cfgmod.EnvSettings) -> list[tuple[str, dict]]:
    r = cfg.run
    return [
        ("00_load_raw", {}),
        ("01_validate_histories", {}),
        ("02_feature_snapshots", {"MODE": "checkpoint"}),
        ("03_train_models", {}),
        ("04_score_and_segment", {"RUN_KIND": "checkpoint", "CUTOFF": r.checkpoint_cutoff, "STARTED_AT": r.checkpoint_started_at, "PUBLISH": True}),
        ("05_evaluate", {}),
        ("06_publish_delta_tables", {"AS_OF": r.as_of}),
    ]


def _auth_record_path(kind: str) -> Path:
    """Where the (non-secret) record of who signed in is kept. Outside the repository, in the user profile."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "homebridge-fabric-deploy" / f"auth-record-{kind}.json"


def make_credential(kind: str, tenant: str | None, cache: bool = True):
    """Sign-in object. With cache on, the token cache is encrypted by the operating system and lives in the user profile.
    The second run then signs in silently until the refresh token expires. Nothing goes in the repository."""
    from azure.identity import AuthenticationRecord, AzureCliCredential, DeviceCodeCredential, InteractiveBrowserCredential

    if kind == "azcli":
        return AzureCliCredential(tenant_id=tenant) if tenant else AzureCliCredential()
    cls = DeviceCodeCredential if kind == "device" else InteractiveBrowserCredential
    kw = {"tenant_id": tenant} if tenant else {}
    if not cache:
        return cls(**kw)
    try:
        from azure.identity import TokenCachePersistenceOptions

        record_path = _auth_record_path(kind)
        record = AuthenticationRecord.deserialize(record_path.read_text(encoding="utf-8")) if record_path.exists() else None
        cred = cls(cache_persistence_options=TokenCachePersistenceOptions(name="homebridge-fabric-deploy"),
                   authentication_record=record, **kw)
        if record is None:
            record = cred.authenticate(scopes=[SCOPE])
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record_path.write_text(record.serialize(), encoding="utf-8")
        return cred
    except Exception as exc:  # the cache is a convenience. Fall back to a plain sign-in.
        print(f"Note: the sign-in cache is not available ({type(exc).__name__}). You will sign in again on the next run.", file=sys.stderr)
        return cls(**kw)


def generate_exports(cfg: cfgmod.EnvSettings, out_dir: Path) -> Path:
    sys.path.insert(0, str(REPO / "data"))
    from hbdata import generator

    kw = {k: v for k, v in (("seed", cfg.data.seed), ("per_cohort", cfg.data.per_cohort), ("open_cohort_size", cfg.data.open_cohort_size)) if v is not None}
    frames, manifest = generator.generate(generator.GenConfig(**kw))
    generator.write_exports(frames, manifest, out_dir / "exports")
    return out_dir / "exports"


class Deployer(ExtraSteps):
    def __init__(self, cfg: cfgmod.EnvSettings, credential=None, client: FabricClient | None = None, dry_run: bool = False,
                 steps: list[str] | None = None, log=print, cicd=None, uploader=None, build_dir: Path = BUILD,
                 env: dict | None = None, runner=None, graph_credential=None, repo: Path = REPO, sleep=time.sleep):
        self.cfg, self.credential, self.dry_run, self.log = cfg, credential, dry_run, log
        self.client = client or (FabricClient(credential) if credential else None)
        self.steps = steps or default_steps(cfg)
        self.cicd = cicd or self._deploy_with_config
        self.uploader = uploader or onelake.upload
        self.build_dir = Path(build_dir)
        self.workspace_id = cfg.workspace_id
        self.stage_dir = self.build_dir / "stage"
        self.env = dict(os.environ if env is None else env)
        self.sleep = sleep
        self.runner = runner or default_runner
        self.repo = Path(repo)
        self.graph_credential = graph_credential or (make_graph_credential(self.env, credential) if credential else None)

    # ----- helpers
    def say(self, text: str) -> None:
        self.log(("[dry-run] " if self.dry_run else "") + text)

    def _deploy_with_config(self, config_path: Path) -> None:
        from fabric_cicd import deploy_with_config

        deploy_with_config(config_file_path=str(config_path), token_credential=self.credential, environment=self.cfg.environment)

    def _write_config(self, name: str, types: list[str], parameter: dict | None, stage_dir: Path | None = None) -> Path:
        self.build_dir.mkdir(parents=True, exist_ok=True)
        param_path = None
        if parameter:
            param_path = self.build_dir / f"{name}.parameter.yml"
            param_path.write_text(yaml.safe_dump(parameter, sort_keys=False), encoding="utf-8")
        path = self.build_dir / f"{name}.config.yml"
        path.write_text(yaml.safe_dump(cicd_config(self.workspace_id, stage_dir or self.stage_dir, types, param_path, self.cfg.prune), sort_keys=False), encoding="utf-8")
        return path

    def _stage_without_update_policies(self) -> Path:
        """A copy of the staging folder whose KQL database script has no update policies."""
        out = self.build_dir / "stage_no_policy"
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(self.stage_dir, out)
        for f in out.rglob("DatabaseSchema.kql"):
            f.write_text(payloads.without_update_policies(f.read_text(encoding="utf-8")), encoding="utf-8")
        return out

    # ----- steps
    def prepare(self) -> None:
        stage.build(self.stage_dir)
        problems = stage.validate(self.stage_dir)
        if problems:
            raise FabricError("Offline checks failed:\n  " + "\n  ".join(problems))
        self.say(f"Staged and checked {len(stage.item_dirs(self.stage_dir))} items in {self.stage_dir}.")

    def _capacity_id(self) -> str | None:
        cfg = self.cfg
        if cfg.capacity_id:
            return cfg.capacity_id
        if not cfg.capacity_name:
            return None
        try:
            return self.client.find_capacity(cfg.capacity_name)["id"]
        except FabricError:
            if self.dry_run and "capacity" in self.steps:  # the capacity step would create it
                return f"<new capacity {cfg.capacity_name}>"
            raise

    def workspace(self) -> None:
        c, cfg = self.client, self.cfg
        if cfg.workspace_id:
            ws = c.get_workspace(cfg.workspace_id)
            self.say(f"Using workspace '{ws.get('displayName')}' ({ws['id']}).")
            self.workspace_id = ws["id"]
        else:
            ws = c.find_workspace(cfg.workspace_name)
            if ws:
                self.say(f"Found workspace '{cfg.workspace_name}' ({ws['id']}).")
                self.workspace_id = ws["id"]
            elif not cfg.create_workspace:
                raise FabricError(f"Workspace '{cfg.workspace_name}' does not exist and create_workspace is false.")
            else:
                cap = self._capacity_id()
                if self.dry_run:
                    self.say(f"Would create workspace '{cfg.workspace_name}' on capacity {cap}.")
                    self.workspace_id = "<new workspace>"
                    return
                ws = c.create_workspace(cfg.workspace_name, cap)
                self.workspace_id = ws["id"]
                self.say(f"Created workspace '{cfg.workspace_name}' ({ws['id']}).")
                return
        if cfg.capacity_name or cfg.capacity_id:
            cap = self._capacity_id()
            if ws.get("capacityId") != cap:
                if self.dry_run:
                    self.say(f"Would assign workspace to capacity {cap}.")
                else:
                    c.assign_capacity(self.workspace_id, cap)
                    self.say(f"Assigned workspace to capacity {cap}.")

    def check_eventstream_level(self) -> None:
        """Stop early when the live Eventstream is at an older compatibility level than the repository file.

        Fabric refuses to upgrade an Eventstream in place (400 "Upgrading the compatibility level from Compat_1_0 to
        Compat_1_1 is not allowed."). fabric-cicd would fail late, after other items were published.
        """
        es = self.client.find_item(self.workspace_id, "Eventstream", ids.EVENTSTREAM)
        if not es:
            return
        want = payloads.eventstream_level(self.stage_dir)
        have = str(self.client.eventstream_topology(self.workspace_id, es["id"]).get("compatibilityLevel") or "")
        if have and want and payloads.level_key(have) < payloads.level_key(want):
            raise FabricError(
                f"The Eventstream '{ids.EVENTSTREAM}' in this workspace is at compatibility level {have}. The repository needs {want}, "
                "and Fabric cannot upgrade an Eventstream in place. Nothing was published. Delete that one Eventstream in the Fabric "
                "portal (its data is only the live event feed) and run the 'core' step again, or deploy to a new workspace. See docs/fabric-deployment.md.")

    def core(self) -> None:
        skip = set(self.cfg.skip_item_types)
        types = [t for t in stage.CORE_TYPES if t not in skip]
        left_alone = f" It leaves {sorted(skip)} alone (skip_item_types)." if skip else ""
        if self.dry_run:
            self.say(f"Would {'' if 'Eventstream' in skip else 'check the Eventstream compatibility level, then '}deploy {types} with fabric-cicd to workspace {self.workspace_id}.{left_alone}")
            return
        if "Eventstream" not in skip:
            self.check_eventstream_level()
        self.say(f"Deploying {types} with fabric-cicd.{left_alone}")
        config = self._write_config("core", types, core_parameters(self.cfg.environment, self.cfg.run.as_of))
        if "KQLDatabase" not in skip and not self.client.find_item(self.workspace_id, "KQLDatabase", ids.KQL_DATABASE):
            # Fabric runs the database script as one unit and removes the database when a command fails. The update
            # policies fail while streaming ingestion is on. So publish the database without them first.
            self.say("The KQL database does not exist yet. Publishing it without the update policies first, then turning streaming ingestion off.")
            self._publish_with_waits(self._write_config("core_no_policy", ["Eventhouse", "KQLDatabase"], None, self._stage_without_update_policies()))
            self.streaming()
        try:
            self.cicd(config)
        except Exception as exc:
            if not payloads.is_streaming_conflict(exc):
                raise
            self.say("Fabric refused the update policies because streaming ingestion is on for the landing table. Turning it off, then deploying again.")
            self.streaming()
            self.cicd(config)

    def _publish_with_waits(self, config: Path, tries: int = 12, wait_seconds: float = 30) -> None:
        """Fabric keeps the name of a database that it just removed for a few minutes. Wait and try again."""
        for attempt in range(tries):
            try:
                self.cicd(config)
                return
            except Exception as exc:
                if "not available yet" not in str(exc).lower() and not any("not available yet" in str(e).lower() for _, e in getattr(exc, "errors", [])):
                    raise
                if attempt == tries - 1:
                    raise
                self.say(f"Fabric still holds the earlier database name. Waiting {int(wait_seconds)} seconds ({attempt + 1}/{tries - 1}).")
                self.sleep(wait_seconds)
    def upload(self) -> None:
        if self.dry_run:
            n = len(onelake.code_files(REPO))
            self.say(f"Would generate the synthetic exports, then upload {n} code files and the exports to Files/hb.")
            return
        lh = self.client.find_item(self.workspace_id, "Lakehouse", ids.LAKEHOUSE)
        if not lh:
            raise FabricError(f"Lakehouse '{ids.LAKEHOUSE}' not found. Run the 'core' step first.")
        exports = generate_exports(self.cfg, self.build_dir / "data")
        self.uploader(self.credential, self.workspace_id, lh["id"], onelake.code_files(REPO) + onelake.data_files(exports), self.log)

    def _run_notebooks(self, runs: list[tuple[str, dict]]) -> None:
        for name, params in runs:
            if self.dry_run:
                self.say(f"Would run notebook {name} with {params or 'default parameters'}.")
                continue
            nb = self.client.find_item(self.workspace_id, "Notebook", name)
            if not nb:
                raise FabricError(f"Notebook '{name}' not found. Run the 'core' step first.")
            self.say(f"Running notebook {name} ...")
            self.client.run_notebook(self.workspace_id, nb["id"], params, self.cfg.run.notebook_timeout_minutes)
            self.say(f"  {name} completed.")

    def notebooks(self) -> None:
        self._run_notebooks(notebook_runs(self.cfg))

    def pipeline(self) -> None:
        if not self.cfg.run.run_pipeline:
            self.say("Skipping the pipeline run (run.run_pipeline is false).")
            return
        r = self.cfg.run
        # With an as-of date the pipeline derives its cutoff and run start from it. Without one, pipeline_cutoff is used.
        params = {"run_kind": "scheduled", "cutoff_at": "" if r.as_of else r.pipeline_cutoff, "as_of": r.as_of,
                  "model_version": "", "cluster_model_version": ""}
        if self.dry_run:
            self.say(f"Would run pipeline {ids.PIPELINE_SCORING} with {params}, then notebook 06 again.")
            return
        p = self.client.find_item(self.workspace_id, "DataPipeline", ids.PIPELINE_SCORING)
        if not p:
            raise FabricError(f"Pipeline '{ids.PIPELINE_SCORING}' not found. Run the 'core' step first.")
        self.say(f"Running pipeline {ids.PIPELINE_SCORING} ...")
        self.client.run_pipeline(self.workspace_id, p["id"], params, self.cfg.run.notebook_timeout_minutes)
        self._run_notebooks([("06_publish_delta_tables", {"AS_OF": r.as_of})])

    def refresh_endpoint(self) -> None:
        """Sync the Lakehouse SQL analytics endpoint with the Delta tables that the notebooks just wrote.

        Without this, the endpoint can lag behind new tables. The Direct Lake model then fails with
        "Invalid object name 'dbo.mortgage'" (seen on 6 Nov 2026 after the first deploy).
        """
        if self.dry_run:
            self.say(f"Would refresh the SQL analytics endpoint of {ids.LAKEHOUSE} so that it sees the Delta tables.")
            return
        ep = self.client.find_item(self.workspace_id, "SQLEndpoint", ids.LAKEHOUSE)
        if not ep:
            raise FabricError(f"SQL analytics endpoint of '{ids.LAKEHOUSE}' not found. Run the 'core' step first.")
        self.say("Refreshing the SQL analytics endpoint ...")
        rows = self.client.refresh_sql_endpoint(self.workspace_id, ep["id"])
        failed = [r for r in rows if r.get("status") == "Failure"]
        if failed:
            raise FabricError("The SQL endpoint could not sync: " + "; ".join(f"{r.get('tableName')}: {(r.get('error') or {}).get('errorCode', 'Failure')}" for r in failed))
        self.say(f"  SQL endpoint refresh done for {len(rows)} table(s).")

    def model(self) -> None:
        types = stage.MODEL_TYPES
        self.refresh_endpoint()
        if self.dry_run:
            self.say(f"Would deploy {types} with fabric-cicd. Placeholders in expressions.tmdl get the SQL endpoint of {ids.LAKEHOUSE}.")
            self.say(f"Would then refresh the semantic model '{ids.SEMANTIC_MODEL}' so that it is framed on the Delta tables.")
            return
        self.say(f"Deploying {types} with fabric-cicd.")
        self.cicd(self._write_config("model", types, model_parameters(self.cfg.environment)))
        model = self.client.find_item(self.workspace_id, "SemanticModel", ids.SEMANTIC_MODEL)
        if not model:
            raise FabricError(f"Semantic model '{ids.SEMANTIC_MODEL}' not found after the publish.")
        self.say("Refreshing the semantic model (frames Direct Lake on the Delta tables) ...")
        self.client.refresh_semantic_model(self.workspace_id, model["id"])
        self.say("  Semantic model refresh completed.")

    def labels(self) -> None:
        if not self.cfg.sensitivity_label_id:
            self.say("Skipping labels: sensitivity_label_id is empty.")
            return
        if self.dry_run:
            self.say("Would set the sensitivity label on the semantic model and the report (needs a Fabric administrator).")
            return
        items = [i for i in self.client.list_items(self.workspace_id) if i["type"] in {"SemanticModel", "Report"} and i["displayName"] == ids.SEMANTIC_MODEL]
        for row in self.client.set_labels(self.cfg.sensitivity_label_id, items):
            self.say(f"  label {row.get('type')} {row.get('id')}: {row.get('status')}")

    def run(self) -> None:
        self.prepare()
        for name in STEPS:
            if name in self.steps:
                if name not in NO_WORKSPACE_NEEDED and not self.workspace_id:
                    raise FabricError("No workspace ID. Run the 'workspace' step or set workspace_id.")
                getattr(self, name)()
        self.say("Finished." if not self.dry_run else "Dry run finished. Nothing was changed.")


def parse(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="deploy_fabric", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--validate", action="store_true", help="Offline checks only. No sign-in.")
    mode.add_argument("--dry-run", action="store_true", help="Sign in, read the tenant, print the plan. Change nothing.")
    ap.add_argument("--config", type=Path, default=REPO / "fabric" / "config" / "fabric.config.yml", help="Settings file (default: fabric/config/fabric.config.yml).")
    ap.add_argument("--environment", "-e", help="Environment key in the config file (default: default_environment).")
    ap.add_argument("--workspace-name")
    ap.add_argument("--workspace-id")
    ap.add_argument("--capacity-name")
    ap.add_argument("--capacity-id")
    ap.add_argument("--tenant", help="Entra tenant (domain or ID) to sign in to. Not stored anywhere.")
    ap.add_argument("--auth", choices=["interactive", "device", "azcli"], default="interactive", help="Sign-in method. 'azcli' re-uses an existing 'az login'.")
    ap.add_argument("--no-token-cache", action="store_true", help="Do not keep the sign-in between runs (the default keeps it in your user profile).")
    ap.add_argument("--only", nargs="+", choices=STEPS)
    ap.add_argument("--skip", nargs="+", choices=STEPS, default=[])
    return ap.parse_args(argv)


def main(argv=None) -> int:
    a = parse(argv)
    overrides = {"workspace_name": a.workspace_name, "workspace_id": a.workspace_id, "capacity_name": a.capacity_name, "capacity_id": a.capacity_id}
    try:
        cfg_path = a.config if a.config.exists() else None
        if a.validate and cfg_path is None:
            cfg = cfgmod.EnvSettings(environment="dev", workspace_name="validate-only")
            checked = False
        else:
            cfg = cfgmod.load(cfg_path or a.config, a.environment, overrides)
            checked = True
        for step in a.only or []:  # naming an optional step with --only switches it on
            if OPTIONAL.get(step):
                getattr(cfg, OPTIONAL[step]).enabled = True
        problems = (cfg.problems() if checked else cfg.section_problems()) + payloads.validate_offline(cfg, REPO)
        if problems:
            raise cfgmod.ConfigError("; ".join(problems))
        steps = [s for s in (a.only or default_steps(cfg)) if s not in a.skip]
        if a.validate:
            with tempfile.TemporaryDirectory() as tmp:
                d = Deployer(cfg, build_dir=Path(tmp), steps=["workspace"])
                d.prepare()
            print("Offline checks passed. Nothing was sent to Fabric.")
            print("Steps that a run with these settings would do: " + ", ".join(steps))
            return 0
        credential = make_credential(a.auth, a.tenant, cache=not a.no_token_cache)
        Deployer(cfg, credential=credential, dry_run=a.dry_run, steps=steps).run()
        return 0
    except (cfgmod.ConfigError, FabricError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
