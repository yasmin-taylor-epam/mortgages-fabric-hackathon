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
FORBIDDEN_KEYS = ("password", "secret", "token", "client_secret", "username", "tenant_id", "key",
                  "pat", "personal_access_token", "connection_string", "api_key", "sas", "email", "upn")
# A value that looks like a credential, a connection string or a user name (an e-mail address) is refused too.
FORBIDDEN_VALUES = [re.compile(p, re.I) for p in (
    r"\bghp_[A-Za-z0-9]{10,}", r"github_pat_[A-Za-z0-9_]{10,}", r"SharedAccessKey", r"Endpoint=sb://", r"AccountKey=",
    r"\bBearer\s+\S{10,}", r"^\S+@\S+\.\S+$")]
ROLES = ("Admin", "Member", "Contributor", "Viewer")
PRINCIPAL_TYPES = ("User", "Group", "ServicePrincipal")
TIMESPAN = re.compile(r"^\d+[dh]$")
SKU = re.compile(r"^F(2|4|8|16|32|64|128|256|512|1024|2048)$")
GIT_STRATEGIES = ("None", "PreferRemote", "PreferWorkspace")


SKIPPABLE_TYPES = ("Eventstream", "KQLDatabase", "Eventhouse")


class ConfigError(ValueError):
    pass


@dataclass
class RunSettings:
    checkpoint_cutoff: str = "2026-10-15T00:00:00Z"
    checkpoint_started_at: str = "2026-10-15T00:05:00Z"
    run_pipeline: bool = True
    pipeline_cutoff: str = "2026-10-15T00:15:00Z"
    notebook_timeout_minutes: int = 60
    # One as-of date for the whole run. Empty (the default): the real clock. The demo value is 2026-10-16T09:00:00Z.
    # When set, it replaces pipeline_cutoff, is the default of the pipeline parameter "as_of", and is written to report_settings.
    as_of: str = ""

    def problems(self) -> list[str]:
        if self.as_of and not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", self.as_of):
            return ["run.as_of must look like 2026-10-16T09:00:00Z, or be empty"]
        return []


@dataclass
class DataSettings:
    per_cohort: int | None = None  # None keeps the generator default
    open_cohort_size: int | None = None
    seed: int | None = None


def _is_guid(value) -> bool:
    return isinstance(value, str) and bool(GUID.match(value))


@dataclass
class CapacitySettings:
    """Create a Fabric capacity through Azure Resource Manager. The admin list comes from HB_FABRIC_CAPACITY_ADMINS."""
    enabled: bool = False
    subscription_id: str = ""
    resource_group: str = ""
    location: str = ""
    sku: str = "F2"

    def problems(self) -> list[str]:
        if not self.enabled:
            return []
        out = [f"capacity_create.{k} is required" for k in ("subscription_id", "resource_group", "location") if not getattr(self, k)]
        if self.subscription_id and not _is_guid(self.subscription_id):
            out.append("capacity_create.subscription_id is not a GUID")
        if not SKU.match(self.sku):
            out.append("capacity_create.sku must be an F SKU such as F2")
        return out


@dataclass
class RetentionSettings:
    enabled: bool = False
    table: str = "RawEventsLanding"
    softdelete: str = "7d"

    def problems(self) -> list[str]:
        if not self.enabled:
            return []
        out = []
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", self.table):
            out.append("retention.table must be a plain table name")
        if not TIMESPAN.match(self.softdelete):
            out.append("retention.softdelete must look like 7d or 36h")
        return out


@dataclass
class ScheduleSettings:
    enabled: bool = False
    interval_minutes: int = 15
    time_zone: str = "UTC"
    start: str = ""  # UTC, YYYY-MM-DDTHH:mm:ssZ. Empty: the schedule starts when the step runs.
    end: str = ""

    def problems(self) -> list[str]:
        if not self.enabled:
            return []
        out = []
        if not isinstance(self.interval_minutes, int) or isinstance(self.interval_minutes, bool) or not 1 <= self.interval_minutes <= 5270400:
            out.append("schedule.interval_minutes must be a whole number from 1 to 5270400")
        for key in ("start", "end"):
            value = getattr(self, key)
            if value and not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", value):
                out.append(f"schedule.{key} must look like 2026-11-05T00:00:00Z")
        return out


@dataclass
class RoleSettings:
    """Workspace role assignments. principal_id is an Entra object ID."""
    enabled: bool = False
    assignments: list = field(default_factory=list)

    def problems(self) -> list[str]:
        if not self.enabled:
            return []
        out = [] if self.assignments else ["roles.assignments is empty"]
        for i, a in enumerate(self.assignments):
            if not isinstance(a, dict) or set(a) - {"principal_id", "type", "role"}:
                out.append(f"roles.assignments[{i}] must have principal_id, type and role only")
                continue
            if not _is_guid(a.get("principal_id")):
                out.append(f"roles.assignments[{i}].principal_id is not a GUID")
            if a.get("type") not in PRINCIPAL_TYPES:
                out.append(f"roles.assignments[{i}].type must be one of {PRINCIPAL_TYPES}")
            if a.get("role") not in ROLES:
                out.append(f"roles.assignments[{i}].role must be one of {ROLES}")
        return out


@dataclass
class RlsSettings:
    """Members of the semantic model roles. Group object IDs only."""
    enabled: bool = False
    members: dict = field(default_factory=dict)  # role name -> list of group object IDs
    member_type: str = "Group"

    def problems(self) -> list[str]:
        if not self.enabled:
            return []
        out = [] if self.members else ["rls.members is empty"]
        for role, groups in (self.members or {}).items():
            if not isinstance(groups, list) or not groups:
                out.append(f"rls.members['{role}'] must be a list of group object IDs")
                continue
            out += [f"rls.members['{role}'] has a value that is not a GUID" for g in groups if not _is_guid(g)]
        if self.member_type not in ("Group", "User"):
            out.append("rls.member_type must be Group or User")
        return out


@dataclass
class GitSettings:
    """Connect the workspace to a GitHub branch. The GitHub PAT comes from HB_GITHUB_PAT at run time, never from this file."""
    enabled: bool = False
    owner: str = ""
    repository: str = ""
    branch: str = ""
    directory: str = ""
    connection_name: str = "HomeBridge-GitHub"
    connection_id: str = ""  # use an existing Fabric connection instead of creating one from HB_GITHUB_PAT
    initialization_strategy: str = "PreferWorkspace"

    def problems(self) -> list[str]:
        if not self.enabled:
            return []
        out = [f"git.{k} is required" for k in ("owner", "repository", "branch") if not getattr(self, k)]
        if self.connection_id and not _is_guid(self.connection_id):
            out.append("git.connection_id is not a GUID")
        if self.initialization_strategy not in GIT_STRATEGIES:
            out.append(f"git.initialization_strategy must be one of {GIT_STRATEGIES}")
        return out


@dataclass
class SharePointSettings:
    enabled: bool = False
    site_url: str = ""
    schema_path: str = "action/sharepoint/review-cases.list.json"

    def problems(self) -> list[str]:
        if not self.enabled:
            return []
        if not re.match(r"^https://[^/]+/(sites|teams)/[^/]+/?$", self.site_url or ""):
            return ["sharepoint.site_url must look like https://<tenant>.sharepoint.com/sites/<site>"]
        return []


@dataclass
class ReplaySettings:
    """Send live events to the Eventstream custom endpoint. The connection details come from the Fabric API at run time."""
    enabled: bool = False
    fixture: str = ""  # JSON lines file of live_event envelopes. Empty: use backfill.
    backfill: bool = True

    def problems(self) -> list[str]:
        if self.enabled and not self.backfill and not self.fixture:
            return ["replay needs backfill: true or a fixture file"]
        return []


OPTIONAL_SECTIONS = {
    "capacity_create": CapacitySettings, "retention": RetentionSettings, "schedule": ScheduleSettings, "roles": RoleSettings,
    "rls": RlsSettings, "git": GitSettings, "sharepoint": SharePointSettings, "replay": ReplaySettings,
}


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
    # Item types that the `core` step leaves alone. Use ["Eventstream"] when the Eventstream destination was made in the portal
    # (a deploy would try to update the item and fail, or replace it). Allowed: Eventstream, KQLDatabase, Eventhouse.
    skip_item_types: list = field(default_factory=list)
    run: RunSettings = field(default_factory=RunSettings)
    data: DataSettings = field(default_factory=DataSettings)
    capacity_create: CapacitySettings = field(default_factory=CapacitySettings)
    retention: RetentionSettings = field(default_factory=RetentionSettings)
    schedule: ScheduleSettings = field(default_factory=ScheduleSettings)
    roles: RoleSettings = field(default_factory=RoleSettings)
    rls: RlsSettings = field(default_factory=RlsSettings)
    git: GitSettings = field(default_factory=GitSettings)
    sharepoint: SharePointSettings = field(default_factory=SharePointSettings)
    replay: ReplaySettings = field(default_factory=ReplaySettings)

    def section_problems(self) -> list[str]:
        out = []
        for name in OPTIONAL_SECTIONS:
            out += getattr(self, name).problems()
        return out

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
        bad = [x for x in self.skip_item_types if x not in SKIPPABLE_TYPES]
        if bad:
            out.append(f"skip_item_types may hold only {list(SKIPPABLE_TYPES)}, not {bad}")
        if "Eventhouse" in self.skip_item_types and "KQLDatabase" not in self.skip_item_types:
            out.append("skip_item_types: skip KQLDatabase together with Eventhouse")
        if self.capacity_create.enabled and not self.capacity_name:
            out.append("capacity_create needs capacity_name, the name of the capacity to create")
        if self.capacity_create.enabled and self.capacity_name and not re.match(r"^[a-z][a-z0-9]{2,62}$", self.capacity_name):
            out.append("capacity_name must be 3 to 63 lower-case letters or digits, starting with a letter, to create it in Azure")
        return out + self.run.problems() + self.section_problems()


def _walk_keys(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield f"{path}{k}", k
            yield from _walk_keys(v, f"{path}{k}.")


def _walk_values(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_values(v, f"{path}{k}.")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_values(v, f"{path}{i}.")
    elif isinstance(node, str):
        yield path.rstrip("."), node


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
    for full, value in _walk_values(doc):
        if any(p.search(value) for p in FORBIDDEN_VALUES):
            raise ConfigError(f"'{full}': the config file must not hold secrets, users or tenant IDs. This value looks like one. Use sign-in or an environment variable at run time.")
    env = environment or os.environ.get("HB_FABRIC_ENV") or doc.get("default_environment") or "dev"
    section = dict((doc.get("environments") or {}).get(env) or {})
    if doc and env not in (doc.get("environments") or {}):
        raise ConfigError(f"Environment '{env}' is not in the config file. Known: {sorted((doc.get('environments') or {}))}")
    section.setdefault("workspace_name", os.environ.get("HB_FABRIC_WORKSPACE", ""))
    section.setdefault("capacity_name", os.environ.get("HB_FABRIC_CAPACITY", ""))
    section.update({k: v for k, v in (overrides or {}).items() if v not in (None, "")})
    run = RunSettings(**(section.pop("run", None) or {}))
    data = DataSettings(**(section.pop("data", None) or {}))
    extra = {}
    for name, cls in OPTIONAL_SECTIONS.items():
        raw = section.pop(name, None) or {}
        bad = set(raw) - set(cls.__dataclass_fields__)
        if bad:
            raise ConfigError(f"Unknown settings in '{name}': {sorted(bad)}")
        extra[name] = cls(**raw)
    unknown = set(section) - set(EnvSettings.__dataclass_fields__)
    if unknown:
        raise ConfigError(f"Unknown settings: {sorted(unknown)}")
    cfg = EnvSettings(environment=env, run=run, data=data, **extra, **section)
    for key in ("workspace_name", "workspace_id", "capacity_name", "capacity_id", "sensitivity_label_id"):
        setattr(cfg, key, str(getattr(cfg, key) or "").strip())
    return cfg
