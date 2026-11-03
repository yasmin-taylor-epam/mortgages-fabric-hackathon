"""Static checks for the Power BI project (PBIP) in reports/.

These checks do not open Power BI and do not run DAX. They check that:
- every JSON file matches its published Microsoft schema (needs network; skipped offline),
- every report field exists in the semantic model,
- every DAX reference resolves to a model column or measure,
- every table is Direct Lake with no calculated columns, and the model matches gold_schema.sql,
- no file holds a connection secret.
"""

import json
import os
import re
import tempfile
import urllib.request
from functools import lru_cache
from pathlib import Path

import pytest

REPORTS = Path(__file__).resolve().parents[1]
MODEL = REPORTS / "HomeBridgeMortgage.SemanticModel"
REPORT = REPORTS / "HomeBridgeMortgage.Report"
TMDL = MODEL / "definition"
SCHEMA_HOST = "https://developer.microsoft.com/json-schemas/"
RAW_HOST = "https://raw.githubusercontent.com/microsoft/json-schemas/main/"
CACHE = Path(os.environ.get("HOMEBRIDGE_SCHEMA_CACHE", Path(tempfile.gettempdir()) / "homebridge-pbir-schemas"))


def parse_tmdl():
    tables = {}
    measures = {}
    partitions = {}
    calculated = []
    for path in sorted((TMDL / "tables").glob("*.tmdl")):
        table = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if m := re.match(r"^table (\S+)", line):
                table = m.group(1)
                tables[table] = set()
            elif m := re.match(r"^\tcolumn (\S+)(\s*=)?", line):
                tables[table].add(m.group(1))
                if m.group(2):
                    calculated.append(f"{table}.{m.group(1)}")
            elif m := re.match(r"^\tmeasure '?([^'=]+?)'? =", line):
                measures[m.group(1)] = table
            elif m := re.match(r"^\t\tmode: (\w+)", line):
                partitions[table] = m.group(1)
    return tables, measures, partitions, calculated


TABLES, MEASURES, PARTITIONS, CALCULATED = parse_tmdl()


def gold_schema():
    text = (REPORTS / "logic" / "gold_schema.sql").read_text(encoding="utf-8")
    result = {}
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", text, re.S):
        cols = set()
        for line in m.group(2).splitlines():
            line = line.split("--")[0].strip()
            if line and not line.startswith("PRIMARY KEY"):
                cols.add(line.split()[0])
        result[m.group(1)] = cols
    return result


def measure_expressions():
    text = (TMDL / "tables" / "mortgage_account.tmdl").read_text(encoding="utf-8")
    blocks = re.split(r"\n\tmeasure ", text)[1:]
    out = {}
    for block in blocks:
        name = re.match(r"'?([^'=]+?)'? =", block).group(1)
        body = [l for l in block.splitlines()[1:] if l.startswith("\t\t\t")]
        out[name] = "\n".join(l.split("--")[0] for l in body)
    return out


def report_json_files():
    files = [REPORTS / "HomeBridgeMortgage.pbip", MODEL / "definition.pbism", REPORT / "definition.pbir"]
    files += sorted((REPORT / "definition").rglob("*.json"))
    return files


def visual_fields():
    for path in (REPORT / "definition" / "pages").rglob("visual.json"):
        visual = json.loads(path.read_text(encoding="utf-8"))["visual"]
        for role in visual["query"]["queryState"].values():
            for proj in role["projections"]:
                (kind, expr), = proj["field"].items()
                yield path.parent.name, kind, expr["Expression"]["SourceRef"]["Entity"], expr["Property"]


class TestSemanticModel:
    def test_model_tables_match_the_gold_schema(self):
        assert TABLES == gold_schema()

    def test_every_table_is_direct_lake(self):
        assert set(PARTITIONS) == set(TABLES)
        assert set(PARTITIONS.values()) == {"directLake"}

    def test_no_calculated_columns(self):
        assert CALCULATED == []

    def test_dax_column_references_resolve(self):
        for name, dax in measure_expressions().items():
            for table, column in re.findall(r"\b([a-z_]+)\[([^\]]+)\]", dax):
                assert column in TABLES.get(table, set()), f"{name}: {table}[{column}]"

    def test_dax_measure_references_resolve(self):
        for name, dax in measure_expressions().items():
            for ref in re.findall(r"(?<![\w\]])\[([^\]]+)\]", dax):
                assert ref in MEASURES, f"{name}: [{ref}]"

    def test_relationship_columns_exist(self):
        text = (TMDL / "relationships.tmdl").read_text(encoding="utf-8")
        for table, column in re.findall(r"(?:from|to)Column: (\w+)\.(\w+)", text):
            assert column in TABLES[table], f"{table}.{column}"

    def test_general_viewer_role_hides_payment_support_routing(self):
        text = (TMDL / "roles" / "general-viewer.tmdl").read_text(encoding="utf-8")
        assert 'tablePermission servicing_case = [support_route] <> "payment_support"' in text
        assert 'tablePermission review_cases = [action_family] <> "support"' in text

    def test_no_connection_secrets(self):
        pattern = re.compile(r"(password|pwd=|secret|accountkey|sharedaccesssignature|bearer )", re.I)
        for path in list(MODEL.rglob("*")) + list(REPORT.rglob("*")):
            if path.is_file():
                assert not pattern.search(path.read_text(encoding="utf-8")), path
        expr = (TMDL / "expressions.tmdl").read_text(encoding="utf-8")
        assert "REPLACE-WITH-SQL-ANALYTICS-ENDPOINT" in expr


class TestReport:
    def test_every_visual_field_exists_in_the_model(self):
        fields = list(visual_fields())
        assert fields
        for visual, kind, table, prop in fields:
            if kind == "Measure":
                assert MEASURES.get(prop) == table, f"{visual}: measure {prop}"
            else:
                assert prop in TABLES[table], f"{visual}: {table}.{prop}"

    def test_pages_listed_in_order_exist(self):
        pages = json.loads((REPORT / "definition" / "pages" / "pages.json").read_text(encoding="utf-8"))
        folders = {p.name for p in (REPORT / "definition" / "pages").iterdir() if p.is_dir()}
        assert set(pages["pageOrder"]) == folders
        assert pages["activePageName"] in folders

    def test_report_binds_to_the_local_semantic_model(self):
        pbir = json.loads((REPORT / "definition.pbir").read_text(encoding="utf-8"))
        assert pbir["datasetReference"]["byPath"]["path"] == "../HomeBridgeMortgage.SemanticModel"

    def test_required_report_content_is_present(self):
        measures_used = {prop for _, kind, _, prop in visual_fields() if kind == "Measure"}
        assert {"Score Status", "Score Age Minutes", "Payment Position", "Payment Support Priority",
                "Segment As Of", "Contacts", "Case Resolution", "Verified Portal Faults",
                "Current Request Stage", "Review Owner"} <= measures_used


@lru_cache(maxsize=None)
def fetch_schema(url):
    if not url.startswith(SCHEMA_HOST):
        raise ValueError(f"unexpected schema host: {url}")
    relative = url[len(SCHEMA_HOST):]
    cached = CACHE / relative
    if not cached.exists():
        cached.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(RAW_HOST + relative, timeout=20) as response:
            cached.write_bytes(response.read())
    return json.loads(cached.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def registry():
    referencing = pytest.importorskip("referencing")
    pytest.importorskip("jsonschema")
    try:
        fetch_schema(SCHEMA_HOST + "fabric/item/report/definitionProperties/2.0.0/schema.json")
    except Exception as exc:  # network unavailable
        pytest.skip(f"cannot download Microsoft JSON schemas: {exc}")

    def retrieve(uri):
        return referencing.Resource.from_contents(fetch_schema(uri), default_specification=referencing.jsonschema.DRAFT7)

    return referencing.Registry(retrieve=retrieve)


@pytest.mark.parametrize("path", report_json_files(), ids=lambda p: str(p.relative_to(REPORTS)))
def test_json_matches_published_schema(path, registry):
    import jsonschema

    document = json.loads(path.read_text(encoding="utf-8"))
    schema = fetch_schema(document["$schema"])
    validator = jsonschema.Draft7Validator(schema, registry=registry)
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors[:5])
