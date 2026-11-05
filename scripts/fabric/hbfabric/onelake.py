"""Upload files to the Lakehouse Files area through the OneLake ADLS Gen2 endpoint.

Documented by Microsoft Learn ("Connect to OneLake with Python"): the account URL is
https://onelake.dfs.fabric.microsoft.com, the file system is the workspace, the path starts with the item.
Not yet run against a real tenant.
"""
from __future__ import annotations

from pathlib import Path

ACCOUNT_URL = "https://onelake.dfs.fabric.microsoft.com"
SKIP_DIRS = {"__pycache__", ".pytest_cache", "tests", "output", ".venv"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def collect(source_dir: Path, remote_prefix: str, skip_dirs: set[str] = SKIP_DIRS) -> list[tuple[Path, str]]:
    """(local file, remote path relative to the Lakehouse Files folder) for every file under source_dir."""
    source_dir = Path(source_dir)
    out = []
    for f in sorted(source_dir.rglob("*")):
        rel = f.relative_to(source_dir)
        if not f.is_file() or f.suffix in SKIP_SUFFIXES or any(part in skip_dirs for part in rel.parts):
            continue
        out.append((f, f"{remote_prefix}/{rel.as_posix()}"))
    return out


def code_files(repo: Path) -> list[tuple[Path, str]]:
    """The Python code that the notebooks import, in the same layout as the repository: code/data/... and code/notebooks/..."""
    repo = Path(repo)
    files = []
    files += collect(repo / "data" / "hbdata", "hb/code/data/hbdata")
    files += collect(repo / "data" / "contracts", "hb/code/data/contracts", skip_dirs=SKIP_DIRS - {"output"})
    files += collect(repo / "notebooks" / "models_eval", "hb/code/notebooks/models_eval")
    return files


def data_files(exports_dir: Path) -> list[tuple[Path, str]]:
    return collect(exports_dir, "hb/output/exports", skip_dirs=set())


def upload(credential, workspace_id: str, lakehouse_id: str, files: list[tuple[Path, str]], log=print) -> int:
    from azure.storage.filedatalake import DataLakeServiceClient

    service = DataLakeServiceClient(ACCOUNT_URL, credential=credential)
    fs = service.get_file_system_client(workspace_id)
    for local, remote in files:
        client = fs.get_file_client(f"{lakehouse_id}/Files/{remote}")
        with open(local, "rb") as fh:
            client.upload_data(fh, overwrite=True)
    log(f"Uploaded {len(files)} files to Files/hb in the Lakehouse.")
    return len(files)
