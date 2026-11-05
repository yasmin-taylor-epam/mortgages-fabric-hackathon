"""Load the JSON Schema contracts, derive Parquet schemas and validate rows."""
from __future__ import annotations

import json
import math
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow as pa
from jsonschema import Draft202012Validator, FormatChecker

DATA_DIR = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = DATA_DIR / "contracts"


def list_contracts() -> list[str]:
    return sorted(p.name.removesuffix(".schema.json") for p in CONTRACTS_DIR.glob("*.schema.json"))


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict:
    return json.loads((CONTRACTS_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(name), format_checker=FormatChecker())


def columns(name: str) -> list[str]:
    return list(load_schema(name)["properties"].keys())


def primary_key(name: str) -> list[str]:
    return list(load_schema(name)["x-primary-key"])


def _non_null_type(prop: dict) -> str:
    t = prop.get("type")
    if isinstance(t, list):
        t = next(x for x in t if x != "null")
    if t is None:
        const = prop.get("const")
        if isinstance(const, bool):
            return "boolean"
        if isinstance(const, (int, float)):
            return "number"
        return "string"
    return t


def _arrow_type(prop: dict) -> pa.DataType:
    t = _non_null_type(prop)
    if t == "string":
        return pa.timestamp("us", tz="UTC") if prop.get("format") == "date-time" else pa.string()
    if t == "number":
        return pa.float64()
    if t == "integer":
        return pa.int64()
    if t == "boolean":
        return pa.bool_()
    if t == "array":
        return pa.list_(pa.string())
    if t == "object":
        return pa.string()  # nested objects are stored as JSON text
    raise ValueError(f"unsupported type {t}")


@lru_cache(maxsize=None)
def arrow_schema(name: str) -> pa.Schema:
    props = load_schema(name)["properties"]
    return pa.schema([pa.field(col, _arrow_type(p)) for col, p in props.items()])


def is_timestamp_col(name: str, col: str) -> bool:
    return _arrow_type(load_schema(name)["properties"][col]) == pa.timestamp("us", tz="UTC")


def _json_value(v: Any, prop: dict) -> Any:
    if v is None:
        return None
    if isinstance(v, (list, tuple, np.ndarray)):
        return [str(x) for x in v]
    if isinstance(v, float) and math.isnan(v):
        return None
    if v is pd.NaT:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    t = _non_null_type(prop)
    if isinstance(v, (pd.Timestamp, datetime)):
        ts = pd.Timestamp(v)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        return ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    if t == "object" and isinstance(v, str):
        return json.loads(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return int(f) if t == "integer" and f.is_integer() else f
    if t == "integer" and isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def to_records(df: pd.DataFrame, name: str) -> list[dict]:
    props = load_schema(name)["properties"]
    cols = [c for c in df.columns if c in props]
    out = []
    for row in df[cols].itertuples(index=False, name=None):
        out.append({c: _json_value(v, props[c]) for c, v in zip(cols, row)})
    return out


def validate_records(records: Iterable[dict], name: str, max_errors: int = 20) -> list[str]:
    v = validator(name)
    errors: list[str] = []
    for i, rec in enumerate(records):
        for err in v.iter_errors(rec):
            path = "/".join(str(p) for p in err.absolute_path)
            errors.append(f"{name}[{i}] {path}: {err.message}")
            if len(errors) >= max_errors:
                return errors
    return errors


def validate_frame(df: pd.DataFrame, name: str, max_errors: int = 20) -> list[str]:
    missing = [c for c in load_schema(name)["required"] if c not in df.columns]
    if missing:
        return [f"{name}: missing columns {missing}"]
    return validate_records(to_records(df, name), name, max_errors)
