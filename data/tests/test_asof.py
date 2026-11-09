"""One as-of date in the Data and replay package: HB_AS_OF replaces the real clock in the default times. Offline."""
import pandas as pd
import pytest
from hbdata import __main__ as cli
from hbdata import asof


def test_as_of_is_unset_by_default_and_parsed_as_utc(monkeypatch):
    monkeypatch.delenv("HB_AS_OF", raising=False)
    assert asof.as_of() is None and abs((asof.now() - pd.Timestamp.now(tz="UTC")).total_seconds()) < 5
    monkeypatch.setenv("HB_AS_OF", "2026-10-16T09:00:00")
    assert asof.as_of() == pd.Timestamp("2026-10-16T09:00:00Z") == asof.now()
    monkeypatch.setenv("HB_AS_OF", "garbage")
    with pytest.raises(ValueError, match="HB_AS_OF"):
        asof.as_of()


def test_cli_defaults_follow_the_as_of_time(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(cli.pipelines_local, "export_journeys", lambda eh, lake, cutoff, exported: seen.update(cutoff=cutoff, exported=exported) or {})
    monkeypatch.setattr(cli.pipelines_local, "copy_context", lambda lake, eh, at: seen.update(copy_at=at) or {})
    monkeypatch.setenv("HB_AS_OF", "2026-10-16T09:00:00Z")
    cli.main(["--out", str(tmp_path), "export-journeys"])
    assert seen["cutoff"] == seen["exported"] == pd.Timestamp("2026-10-16T09:00:00Z")
    cli.main(["--out", str(tmp_path), "export-journeys", "--at", "2026-10-15T00:15:00Z"])
    assert seen["cutoff"] == pd.Timestamp("2026-10-15T00:15:00Z")  # an explicit --at still wins
