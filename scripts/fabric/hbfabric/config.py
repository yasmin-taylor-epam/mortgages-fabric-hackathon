"""Deployment settings. They come from a YAML file, then from environment variables, then from command-line options.

The file holds names and non-secret IDs only. It never holds a password, a token or a client secret.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
ENV_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
FORBIDDEN_KEYS = ("password", "secret", "token", "client_secret", "username", "tenant_id", "key")


class ConfigError(ValueError):
    pass


@dataclass
class RunSettings:
    checkpoint_cutoff: str = "2026-10-15T00:00:00Z"
    checkpoint_started_at: str = "2026-10-15T00:05:00Z"
    run_pipeline: bool = True
    pipeline_cutoff: str = "2026-10-15T00:15:00Z"
    notebook_timeout_minutes: int = 60


@dataclass
class DataSettings:
    per_cohort: int | None = None  # None keeps the generator default
    open_cohort_size: int | None = None
    seed: int | None = None


@dataclass
class EnvSettings:
    environment: str
    workspace_name: str = ""
    workspace_id: str = ""
    capacity_name: str = ""
    capacity_id: str = ""
    create_workspace: bool = True
    sensitivity_label_id: str = ""
    prune: bool = False
    run: RunSettings = field(default_factory=RunSettings)
    data: DataSettings = field(default_factory=DataSettings)

    def problems(self) -> list[str]:
        out = []
        if not ENV_NAME.match(self.environment):
            out.append(f"environment name {self.environment!r} must start with a letter and use letters, digits, - or _")
        if not self.workspace_id and not self.workspace_name:
            out.append("set workspace_name or workspace_id")
        for key in ("workspace_id", "capacity_id", "sensitivity_label_id"):
            value = getattr(self, key)
            if value and not GUID.match(value):
                out.append(f"{key} is not a GUID")
        if self.capacity_id and self.capacity_name:
            out.append("set capacity_name or capacity_id, not both")
        if not self.workspace_id and self.create_workspace and not (self.capacity_name or self.capacity_id):
            out.append("a new workspace needs capacity_name or capacity_id (or set create_workspace: false)")
        return out


def _walk_keys(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield f"{path}{k}", k
            yield from _walk_keys(v, f"{path}{k}.")


def load(path: Path | None, environment: str | None = None, overrides: dict | None = None) -> EnvSettings:
    doc: dict = {}
    if path:
        path = Path(path)
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}. Copy fabric/config/fabric.config.example.yml to it.")
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for full, key in _walk_keys(doc):
        if key.lower() in FORBIDDEN_KEYS:
            raise ConfigError(f"'{full}': the config file must not hold secrets, users or tenant IDs. Use sign-in at run time.")
    env = environment or os.environ.get("HB_FABRIC_ENV") or doc.get("default_environment") or "dev"
    section = dict((doc.get("environments") or {}).get(env) or {})
    if doc and env not in (doc.get("environments") or {}):
        raise ConfigError(f"Environment '{env}' is not in the config file. Known: {sorted((doc.get('environments') or {}))}")
    section.setdefault("workspace_name", os.environ.get("HB_FABRIC_WORKSPACE", ""))
    section.setdefault("capacity_name", os.environ.get("HB_FABRIC_CAPACITY", ""))
    section.update({k: v for k, v in (overrides or {}).items() if v not in (None, "")})
    run = RunSettings(**(section.pop("run", None) or {}))
    data = DataSettings(**(section.pop("data", None) or {}))
    unknown = set(section) - set(EnvSettings.__dataclass_fields__)
    if unknown:
        raise ConfigError(f"Unknown settings: {sorted(unknown)}")
    cfg = EnvSettings(environment=env, run=run, data=data, **section)
    for key in ("workspace_name", "workspace_id", "capacity_name", "capacity_id", "sensitivity_label_id"):
        setattr(cfg, key, str(getattr(cfg, key) or "").strip())
    return cfg
