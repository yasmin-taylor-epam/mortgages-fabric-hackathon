"""The SharePoint list schema must match the local reference and the provisioning script must parse."""

import dataclasses
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from homebridge_action import ActionFamily, CaseStatus, ReviewCase

SHAREPOINT = Path(__file__).resolve().parents[1] / "sharepoint"
SCHEMA = json.loads((SHAREPOINT / "review-cases.list.json").read_text(encoding="utf-8"))
LISTS = {lst["title"]: {c["name"]: c for c in lst["columns"]} for lst in SCHEMA["lists"]}
CASES = LISTS["HomeBridge Review Cases"]


def test_every_review_case_field_has_a_list_column():
    fields = {f.name for f in dataclasses.fields(ReviewCase)}
    assert fields <= set(CASES)


def test_case_key_is_indexed_and_unique():
    assert CASES["case_key"]["unique"] is True
    assert CASES["case_key"]["indexed"] is True
    assert [name for name, col in CASES.items() if col.get("unique")] == ["case_key"]


def test_choices_match_the_local_enums():
    assert CASES["action_family"]["choices"] == [f.value for f in ActionFamily]
    assert CASES["status"]["choices"] == [s.value for s in CaseStatus]


def test_exception_kinds_match_the_writer():
    source = (Path(__file__).resolve().parents[1] / "homebridge_action" / "case_writer.py").read_text(encoding="utf-8")
    for kind in LISTS["HomeBridge Routing Exceptions"]["kind"]["choices"]:
        assert f'"{kind}"' in source


def test_no_list_holds_message_or_secret_columns():
    banned = ("password", "secret", "token", "message_body", "email", "phone", "note_text")
    for columns in LISTS.values():
        for name in columns:
            assert not any(word in name.lower() for word in banned)


@pytest.mark.skipif(not (shutil.which("pwsh") or shutil.which("powershell")), reason="PowerShell not installed")
def test_provisioning_script_parses():
    shell = shutil.which("pwsh") or shutil.which("powershell")
    script = SHAREPOINT / "provision-list.ps1"
    command = (
        "$errors = $null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{script}', [ref]$null, [ref]$errors) | Out-Null; "
        "if ($errors) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )
    result = subprocess.run([shell, "-NoProfile", "-Command", command], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
