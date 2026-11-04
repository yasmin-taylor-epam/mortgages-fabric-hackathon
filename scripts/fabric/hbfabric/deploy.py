"""Deploy the HomeBridge build to a Microsoft Fabric workspace.

Steps, in order (select with --only or --skip):
  workspace   find or create the workspace, assign the capacity
  core        deploy Lakehouse, Environment, Eventhouse, KQL database, notebooks, pipeline and Eventstream (fabric-cicd)
  upload      generate the synthetic exports, upload them and the Python code to the Lakehouse Files area
  notebooks   run 00 to 05 in order, then 06 (Parquet to Delta tables)
  pipeline    run pl_scheduled_scoring once, then 06 again
  model       deploy the semantic model and report (fabric-cicd). Needs the Delta tables from step "notebooks".
  labels      set a Purview sensitivity label on the model and report (Fabric administrator only)

Modes: --validate checks everything offline. --dry-run signs in, reads, and prints what it would do. The default applies.
Not yet run against a real Fabric tenant. Nothing here claims otherwise.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import yaml

from . import config as cfgmod
from . import ids, onelake, stage
from .api import FabricClient, FabricError

REPO = stage.REPO
BUILD = REPO / "build" / "fabric"
STEPS = ["workspace", "core", "upload", "notebooks", "pipeline", "model", "labels"]


def model_parameters(environment: str) -> dict:
    """fabric-cicd parameter file for the semantic model. It fills the two Direct Lake placeholders."""
    target = f"$items.Lakehouse.{ids.LAKEHOUSE}"
    common = {"item_type": "SemanticModel", "item_name": ids.SEMANTIC_MODEL}
    return {"find_replace": [
        {"find_value": "REPLACE-WITH-SQL-ANALYTICS-ENDPOINT", "replace_value": {environment: f"{target}.$sqlendpoint"}, **common},
        {"find_value": "REPLACE-WITH-LAKEHOUSE-ID", "replace_value": {environment: f"{target}.$sqlendpointid"}, **common},
    ]}


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
        ("06_publish_delta_tables", {}),
    ]


def make_credential(kind: str, tenant: str | None):
    from azure.identity import AzureCliCredential, DeviceCodeCredential, InteractiveBrowserCredential

    if kind == "azcli":
        return AzureCliCredential(tenant_id=tenant) if tenant else AzureCliCredential()
    if kind == "device":
        return DeviceCodeCredential(tenant_id=tenant) if tenant else DeviceCodeCredential()
    return InteractiveBrowserCredential(tenant_id=tenant) if tenant else InteractiveBrowserCredential()


def generate_exports(cfg: cfgmod.EnvSettings, out_dir: Path) -> Path:
    sys.path.insert(0, str(REPO / "data"))
    from hbdata import generator

    kw = {k: v for k, v in (("seed", cfg.data.seed), ("per_cohort", cfg.data.per_cohort), ("open_cohort_size", cfg.data.open_cohort_size)) if v is not None}
    frames, manifest = generator.generate(generator.GenConfig(**kw))
    generator.write_exports(frames, manifest, out_dir / "exports")
    return out_dir / "exports"


class Deployer:
    def __init__(self, cfg: cfgmod.EnvSettings, credential=None, client: FabricClient | None = None, dry_run: bool = False,
                 steps: list[str] | None = None, log=print, cicd=None, uploader=None, build_dir: Path = BUILD):
        self.cfg, self.credential, self.dry_run, self.log = cfg, credential, dry_run, log
        self.client = client or (FabricClient(credential) if credential else None)
        self.steps = steps or list(STEPS)
        self.cicd = cicd or self._deploy_with_config
        self.uploader = uploader or onelake.upload
        self.build_dir = Path(build_dir)
        self.workspace_id = cfg.workspace_id
        self.stage_dir = self.build_dir / "stage"

    # ----- helpers
    def say(self, text: str) -> None:
        self.log(("[dry-run] " if self.dry_run else "") + text)

    def _deploy_with_config(self, config_path: Path) -> None:
        from fabric_cicd import deploy_with_config

        deploy_with_config(config_file_path=str(config_path), token_credential=self.credential, environment=self.cfg.environment)

    def _write_config(self, name: str, types: list[str], parameter: dict | None) -> Path:
        self.build_dir.mkdir(parents=True, exist_ok=True)
        param_path = None
        if parameter:
            param_path = self.build_dir / f"{name}.parameter.yml"
            param_path.write_text(yaml.safe_dump(parameter, sort_keys=False), encoding="utf-8")
        path = self.build_dir / f"{name}.config.yml"
        path.write_text(yaml.safe_dump(cicd_config(self.workspace_id, self.stage_dir, types, param_path, self.cfg.prune), sort_keys=False), encoding="utf-8")
        return path

    # ----- steps
    def prepare(self) -> None:
        stage.build(self.stage_dir)
        problems = stage.validate(self.stage_dir)
        if problems:
            raise FabricError("Offline checks failed:\n  " + "\n  ".join(problems))
        self.say(f"Staged and checked {len(stage.item_dirs(self.stage_dir))} items in {self.stage_dir}.")

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
                cap = cfg.capacity_id or (c.find_capacity(cfg.capacity_name)["id"] if cfg.capacity_name else None)
                if self.dry_run:
                    self.say(f"Would create workspace '{cfg.workspace_name}' on capacity {cap}.")
                    self.workspace_id = "<new workspace>"
                    return
                ws = c.create_workspace(cfg.workspace_name, cap)
                self.workspace_id = ws["id"]
                self.say(f"Created workspace '{cfg.workspace_name}' ({ws['id']}).")
                return
        if cfg.capacity_name or cfg.capacity_id:
            cap = cfg.capacity_id or c.find_capacity(cfg.capacity_name)["id"]
            if ws.get("capacityId") != cap:
                if self.dry_run:
                    self.say(f"Would assign workspace to capacity {cap}.")
                else:
                    c.assign_capacity(self.workspace_id, cap)
                    self.say(f"Assigned workspace to capacity {cap}.")

    def core(self) -> None:
        types = stage.CORE_TYPES
        if self.dry_run:
            self.say(f"Would deploy {types} with fabric-cicd to workspace {self.workspace_id}.")
            return
        self.say(f"Deploying {types} with fabric-cicd.")
        self.cicd(self._write_config("core", types, None))

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
        params = {"run_kind": "scheduled", "cutoff_at": self.cfg.run.pipeline_cutoff, "model_version": "", "cluster_model_version": ""}
        if self.dry_run:
            self.say(f"Would run pipeline {ids.PIPELINE_SCORING} with {params}, then notebook 06 again.")
            return
        p = self.client.find_item(self.workspace_id, "DataPipeline", ids.PIPELINE_SCORING)
        if not p:
            raise FabricError(f"Pipeline '{ids.PIPELINE_SCORING}' not found. Run the 'core' step first.")
        self.say(f"Running pipeline {ids.PIPELINE_SCORING} ...")
        self.client.run_pipeline(self.workspace_id, p["id"], params, self.cfg.run.notebook_timeout_minutes)
        self._run_notebooks([("06_publish_delta_tables", {})])

    def model(self) -> None:
        types = stage.MODEL_TYPES
        if self.dry_run:
            self.say(f"Would deploy {types} with fabric-cicd. Placeholders in expressions.tmdl get the SQL endpoint of {ids.LAKEHOUSE}.")
            return
        self.say(f"Deploying {types} with fabric-cicd.")
        self.cicd(self._write_config("model", types, model_parameters(self.cfg.environment)))

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
                if name != "workspace" and not self.workspace_id:
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
    ap.add_argument("--only", nargs="+", choices=STEPS)
    ap.add_argument("--skip", nargs="+", choices=STEPS, default=[])
    return ap.parse_args(argv)


def main(argv=None) -> int:
    a = parse(argv)
    overrides = {"workspace_name": a.workspace_name, "workspace_id": a.workspace_id, "capacity_name": a.capacity_name, "capacity_id": a.capacity_id}
    try:
        cfg_path = a.config if a.config.exists() else None
        if a.validate and cfg_path is None:
            cfg = None
        else:
            cfg = cfgmod.load(cfg_path or a.config, a.environment, overrides)
            problems = cfg.problems()
            if problems:
                raise cfgmod.ConfigError("; ".join(problems))
        steps = [s for s in (a.only or STEPS) if s not in a.skip]
        if a.validate:
            with tempfile.TemporaryDirectory() as tmp:
                d = Deployer(cfg or cfgmod.EnvSettings(environment="dev", workspace_name="validate-only"), build_dir=Path(tmp), steps=[])
                d.prepare()
            print("Offline checks passed. Nothing was sent to Fabric.")
            return 0
        credential = make_credential(a.auth, a.tenant)
        Deployer(cfg, credential=credential, dry_run=a.dry_run, steps=steps).run()
        return 0
    except (cfgmod.ConfigError, FabricError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
