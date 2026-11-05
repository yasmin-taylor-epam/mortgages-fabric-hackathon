"""Pure builders for the request bodies of the optional deploy steps. No network, no sign-in.

`python scripts/fabric/deploy_fabric.py --validate` calls `validate_offline` so that a mistake in the
settings or in the SharePoint schema shows up before anyone signs in.
Every body follows the Microsoft Learn page named in the docstring of its builder. None has run in Fabric yet.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from . import config as cfgmod
from . import ids

PIPELINE_JOB_TYPE = "Pipeline"
SEMANTIC_ROLES = ("General viewer", "Authorised adviser")  # role names in reports/HomeBridgeMortgage.SemanticModel/definition/roles


# ----- KQL retention. Kusto management command; the KQL database Git item cannot hold it.
def retention_command(table: str, softdelete: str) -> str:
    return f".alter-merge table {table} policy retention softdelete = {softdelete}"


def timespan_seconds(text: str) -> float:
    """'7d' or '36h' or a Kusto timespan such as '7.00:00:00' to seconds."""
    m = re.match(r"^(\d+)([dh])$", text)
    if m:
        return int(m.group(1)) * (86400 if m.group(2) == "d" else 3600)
    m = re.match(r"^(?:(\d+)\.)?(\d+):(\d+):(\d+)$", text)
    if not m:
        raise ValueError(f"Unknown timespan {text!r}")
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def retention_matches(rows: list[list], softdelete: str) -> bool:
    """True when the rows of `.show table T policy retention` already hold the wanted soft-delete period."""
    for row in rows:
        for cell in row:
            if isinstance(cell, str) and cell.startswith("{"):
                try:
                    period = json.loads(cell).get("SoftDeletePeriod")
                except ValueError:
                    continue
                if period:
                    return timespan_seconds(period) == timespan_seconds(softdelete)
    return False


# ----- Job Scheduler: Create Item Schedule (Cron type, interval in minutes)
def schedule_body(s: cfgmod.ScheduleSettings, now: datetime | None = None) -> dict:
    start = s.start or (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conf = {"type": "Cron", "interval": s.interval_minutes, "startDateTime": start, "localTimeZoneId": s.time_zone}
    if s.end:
        conf["endDateTime"] = s.end
    return {"enabled": True, "configuration": conf}


def schedule_matches(existing: dict, s: cfgmod.ScheduleSettings) -> bool:
    conf = existing.get("configuration") or {}
    return bool(existing.get("enabled")) and conf.get("type") == "Cron" and conf.get("interval") == s.interval_minutes


# ----- Capacity: ARM Microsoft.Fabric/capacities, api-version 2023-11-01
def capacity_body(c: cfgmod.CapacitySettings, admins: list[str]) -> dict:
    return {"location": c.location, "sku": {"name": c.sku, "tier": "Fabric"},
            "properties": {"administration": {"members": list(admins)}}}


# ----- Git integration
def github_connection_body(g: cfgmod.GitSettings, pat: str) -> dict:
    """Create Connection body for a GitHub PAT. The PAT is a run-time argument. It is never stored."""
    return {
        "connectivityType": "ShareableCloud",
        "displayName": g.connection_name,
        "connectionDetails": {
            "type": "GitHubSourceControl", "creationMethod": "GitHubSourceControl.Contents",
            "parameters": [{"dataType": "Text", "name": "url", "value": f"https://github.com/{g.owner}/{g.repository}"}],
        },
        "credentialDetails": {"credentials": {"credentialType": "Key", "key": pat}},
    }


def git_connect_body(g: cfgmod.GitSettings, connection_id: str) -> dict:
    details = {"gitProviderType": "GitHub", "ownerName": g.owner, "repositoryName": g.repository, "branchName": g.branch}
    details["directoryName"] = g.directory or "/"
    return {"gitProviderDetails": details, "myGitCredentials": {"source": "ConfiguredConnection", "connectionId": connection_id}}


# ----- Workspace roles
def role_plan(assignments: list[dict], existing: list[dict]) -> list[tuple[str, dict, str | None]]:
    """(action, assignment, existing assignment id). action is add, update or keep."""
    by_principal = {(a.get("principal") or {}).get("id", "").lower(): a for a in existing}
    out = []
    for a in assignments:
        hit = by_principal.get(a["principal_id"].lower())
        if not hit:
            out.append(("add", a, None))
        elif hit.get("role") != a["role"]:
            out.append(("update", a, hit["id"]))
        else:
            out.append(("keep", a, hit["id"]))
    return out


# ----- RLS members (the XMLA step runs scripts/fabric/set_rls_members.ps1 with this plan)
def rls_plan(r: cfgmod.RlsSettings, workspace_name: str) -> dict:
    return {"workspace": workspace_name, "model": ids.SEMANTIC_MODEL, "memberType": r.member_type,
            "members": {role: list(groups) for role, groups in r.members.items()}}


def rls_problems(r: cfgmod.RlsSettings, model_dir: Path) -> list[str]:
    """Every role in the settings must exist in the semantic model files."""
    text = "\n".join(p.read_text(encoding="utf-8") for p in Path(model_dir).rglob("*.tmdl"))
    known = set(re.findall(r"^role '([^']+)'", text, re.M)) | set(re.findall(r"^role (\w+)\s*$", text, re.M))
    return [f"rls.members names role '{role}', which is not in the semantic model. Known: {sorted(known)}"
            for role in r.members if role not in known]


# ----- SharePoint lists through Microsoft Graph (POST /sites/{id}/lists, POST /sites/{id}/lists/{id}/columns)
def site_parts(site_url: str) -> tuple[str, str]:
    u = urlparse(site_url)
    return u.netloc, u.path.strip("/")


def column_body(col: dict) -> dict:
    kind = col["type"]
    body: dict = {"name": col["name"], "displayName": col["name"]}
    if col.get("description"):
        body["description"] = col["description"]
    if col.get("required"):
        body["required"] = True
    if col.get("indexed") or col.get("unique"):
        body["indexed"] = True
    if col.get("unique"):
        body["enforceUniqueValues"] = True  # INV-3: one row per case_key
    if kind == "Text":
        body["text"] = {}
    elif kind == "Note":
        body["text"] = {"allowMultipleLines": True}
    elif kind == "Choice":
        body["choice"] = {"allowTextEntry": False, "choices": list(col["choices"]), "displayAs": "dropDownMenu"}
    elif kind == "DateTime":
        body["dateTime"] = {"displayAs": "default", "format": "dateTime"}
    elif kind == "Boolean":
        body["boolean"] = {}
    elif kind == "Number":
        body["number"] = {}
    else:
        raise ValueError(f"Column {col['name']}: unsupported type {kind}")
    return body


def list_body(lst: dict) -> dict:
    return {"displayName": lst["title"], "list": {"template": "genericList"}, "columns": [column_body(c) for c in lst["columns"]]}


def sharepoint_lists(schema_path: Path) -> list[dict]:
    return json.loads(Path(schema_path).read_text(encoding="utf-8"))["lists"]


# ----- one offline check for the whole set
def validate_offline(cfg: cfgmod.EnvSettings, repo: Path) -> list[str]:
    """Problems in the optional settings and in the files that the optional steps read. No network."""
    problems = list(cfg.section_problems())
    schema = repo / (cfg.sharepoint.schema_path or "action/sharepoint/review-cases.list.json")
    try:
        for lst in sharepoint_lists(schema):
            list_body(lst)
    except (OSError, ValueError, KeyError) as exc:
        problems.append(f"SharePoint schema {schema}: {exc}")
    problems += rls_problems(cfg.rls, repo / "reports" / f"{ids.SEMANTIC_MODEL}.SemanticModel") if cfg.rls.enabled else []
    try:
        timespan_seconds(cfg.retention.softdelete)
    except ValueError as exc:
        problems.append(f"retention.softdelete: {exc}")
    return problems
