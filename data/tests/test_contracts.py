import json

import pytest
from jsonschema import Draft202012Validator

from hbdata import contracts, generator


@pytest.mark.parametrize("name", contracts.list_contracts())
def test_schema_is_valid_json_schema(name):
    schema = contracts.load_schema(name)
    Draft202012Validator.check_schema(schema)
    assert schema["title"] == name
    assert set(schema["x-primary-key"]) <= set(schema["properties"])
    assert set(schema["required"]) <= set(schema["properties"])


@pytest.mark.parametrize("name", generator.DATASETS)
def test_generated_exports_meet_contract(frames, name):
    df = frames[name]
    # Bad records are injected on purpose; check the rows that validation keeps.
    sample = df.sample(min(len(df), 4000), random_state=1) if len(df) > 4000 else df
    errors = contracts.validate_frame(sample, name, max_errors=50)
    allowed = ("HB-M-98", "HB-M-99", "PAY-9")  # injected unknown accounts and invalid reversals
    real = [e for e in errors if not any(a in e for a in allowed)]
    assert real == [], real[:5]


def test_clean_tables_meet_contract(clean):
    for name, df in clean.items():
        sample = df.sample(min(len(df), 3000), random_state=2)
        assert contracts.validate_frame(sample, name) == [], name


def test_snapshots_meet_contract(checkpoint_snapshots):
    assert contracts.validate_frame(checkpoint_snapshots, "feature_snapshot") == []


def test_parquet_schema_follows_contract(tmp_path, frames):
    from hbdata import io
    path = io.write_table(frames["payment"].head(50), tmp_path / "p.parquet", "payment")
    import pyarrow.parquet as pq
    assert pq.read_schema(path).names == contracts.columns("payment")


def test_risk_updated_example_is_valid():
    from hbdata import fixtures
    event = fixtures.load_case("T06")["inputs"]["risk_updated"][0]
    assert contracts.validate_records([event], "risk_updated") == []
    bad = {**event, "target": "external_refinancing_90d"}
    assert contracts.validate_records([bad], "risk_updated")


def test_outcome_contract_enforces_inv4():
    base = {"mortgage_id": "HB-M-000001", "cycle_id": "C01", "checkpoint_at": "2026-01-01T00:00:00Z",
            "observation_window_end_at": "2026-05-01T00:00:00Z", "outcome_at": None, "outcome_observed_at": None,
            "evidence_source": "none"}
    ok = {**base, "outcome_type": "not_yet_observed", "label_status": "pending", "label": None}
    assert contracts.validate_records([ok], "observed_outcome") == []
    for bad in ({**ok, "label": 0}, {**base, "outcome_type": "redemption_unknown_reason", "label_status": "labelled", "label": 0},
                {**base, "outcome_type": "not_yet_observed", "label_status": "labelled", "label": 0}):
        assert contracts.validate_records([bad], "observed_outcome"), json.dumps(bad)
