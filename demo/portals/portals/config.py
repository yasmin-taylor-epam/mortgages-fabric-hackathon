"""Settings of the demo portals.

The settings file is demo/portals/config.local.yml. Git ignores it. It holds no secret: sign-in uses your own
Microsoft account, and the Eventstream key is read from Fabric at start-up and kept in memory only.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

PORTALS_DIR = Path(__file__).resolve().parents[1]
REPO = PORTALS_DIR.parents[1]
LOCAL_CONFIG = PORTALS_DIR / "config.local.yml"

# Shared hackathon lab accounts are refused unless config.local.yml sets allow_lab_account: true.
LAB_ACCOUNT_MARKERS = ("odl_user", "sandboxailabs")


@dataclass
class Persona:
    mortgage_id: str
    name: str
    note: str = ""


@dataclass
class Settings:
    workspace_name: str = "azure-mafia-branch-luke"
    tenant_id: str = ""
    auth: str = "interactive"  # interactive | device | azcli
    login_hint: str = ""
    allow_lab_account: bool = False  # true only when the hackathon lab account is the account that owns the workspace
    demo_start: str = "2026-10-16T09:00:00Z"  # the synthetic data ends on 15 Oct 2026. The demo clock starts after it
    demo_step_minutes: int = 60  # each event the customer portal sends moves the demo clock on by this much
    cache_seconds: int = 15
    policy_file: str = "action/policy/routing-policy.candidate.json"
    notebook_name: str = "07_route_review_cases"
    personas: list[Persona] = field(default_factory=lambda: [
        Persona("HB-M-900001", "Alex", "Renewal stuck at documents received. Asks for payment help."),
        Persona("HB-M-003121", "Customer HB-M-003121", "High risk. Renewal case for adviser ADV-027."),
        Persona("HB-M-900002", "Sam", "Renewed. No cases."),
    ])

    @property
    def policy_path(self) -> Path:
        return REPO / self.policy_file


def check_account(name: str | None, allow_lab_account: bool = False) -> None:
    if allow_lab_account:
        return
    if name and any(m in name.lower() for m in LAB_ACCOUNT_MARKERS):
        raise SystemExit(f"Sign-in account '{name}' is a shared lab account. Use your own account, "
                         "or set allow_lab_account: true in config.local.yml if the lab account owns the workspace.")


def load(path: Path | None = None) -> Settings:
    path = path or LOCAL_CONFIG
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = data or {}
    known = {f.name for f in fields(Settings)}
    unknown = set(data) - known
    if unknown:
        raise SystemExit(f"Unknown settings in {path.name}: {sorted(unknown)}")
    if "personas" in data:
        data["personas"] = [Persona(**p) for p in data["personas"]]
    settings = Settings(**data)
    check_account(settings.login_hint, settings.allow_lab_account)
    return settings
