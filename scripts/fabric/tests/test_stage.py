import shutil

import pytest

from hbfabric import ids, stage


@pytest.fixture()
def staged(tmp_path):
    stage.build(tmp_path / "stage")
    return tmp_path / "stage"


def test_stage_contains_all_items_side_by_side(staged):
    names = sorted(p.name for p in stage.item_dirs(staged))
    assert len(names) == 16  # 8 notebooks, the pipeline, Lakehouse, Environment, Eventhouse, KQL database, Eventstream, model and report
    assert "HomeBridgeMortgage.SemanticModel" in names and "HomeBridgeMortgage.Report" in names
    assert all("/" not in n for n in names)


def test_real_stage_passes_the_offline_checks(staged):
    assert stage.validate(staged) == []


def test_duplicate_logical_id_is_reported(staged):
    a = staged / "00_load_raw.Notebook" / ".platform"
    a.write_text(a.read_text(encoding="utf-8").replace(ids.logical_id("Notebook", "00_load_raw"), ids.logical_id("Notebook", "01_validate_histories")), encoding="utf-8")
    assert any("duplicate logicalId" in p for p in stage.validate(staged))


def test_dangling_notebook_reference_is_reported(staged):
    f = staged / "pl_scheduled_scoring.DataPipeline" / "pipeline-content.json"
    f.write_text(f.read_text(encoding="utf-8").replace(ids.logical_id("Notebook", "02_feature_snapshots"), "11111111-1111-1111-1111-111111111111"), encoding="utf-8")
    assert any("notebookId" in p and "not the logicalId" in p for p in stage.validate(staged))


def test_missing_required_file_is_reported(staged):
    (staged / "HomeBridgeRiskEvents.Eventstream" / "eventstream.json").unlink()
    assert any("missing eventstream.json" in p for p in stage.validate(staged))


def test_folder_name_must_match_the_display_name(staged):
    shutil.move(staged / "00_load_raw.Notebook", staged / "renamed.Notebook")
    assert any("folder name must be" in p for p in stage.validate(staged))


def test_secret_looking_text_is_reported(staged):
    f = staged / "HomeBridgeRiskEvents.Eventstream" / "eventstreamProperties.json"
    f.write_text('{"cs": "Endpoint=sb://x.servicebus.windows.net/;SharedAccessKey=abc"}', encoding="utf-8")
    assert any("looks like a secret" in p for p in stage.validate(staged))


def test_unsupported_kql_command_is_reported(staged):
    f = staged / "HomeBridgeEventhouse.Eventhouse" / ".children" / "HomeBridgeEvents.KQLDatabase" / "DatabaseSchema.kql"
    f.write_text(f.read_text(encoding="utf-8") + "\n.drop table RawEvents\n", encoding="utf-8")
    assert any("unsupported command" in p for p in stage.validate(staged))


def test_publish_order_matches_fabric_cicd():
    from fabric_cicd import constants

    order = {t.value: n for n, t in constants.SERIAL_ITEM_PUBLISH_ORDER.items()}
    for item_type, n in stage.PUBLISH_ORDER.items():
        assert order[item_type] == n, item_type
