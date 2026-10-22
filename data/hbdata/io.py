"""Parquet read and write that follow the contract column order and types."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from . import contracts

DATA_DIR = contracts.DATA_DIR
OUTPUT_DIR = DATA_DIR / "output"


def coerce(df: pd.DataFrame, name: str) -> pd.DataFrame:
    """Return a frame with the contract columns, in order, with UTC timestamps."""
    schema = contracts.arrow_schema(name)
    out = pd.DataFrame(index=df.index)
    for field in schema:
        col = df[field.name] if field.name in df.columns else pd.Series([None] * len(df), index=df.index, dtype=object)
        if pa.types.is_timestamp(field.type):
            col = pd.to_datetime(col, utc=True, format="mixed").dt.floor("us")
        out[field.name] = col
    return out.reset_index(drop=True)


def to_arrow(df: pd.DataFrame, name: str) -> pa.Table:
    df = coerce(df, name)
    schema = contracts.arrow_schema(name)
    arrays = []
    for field in schema:
        col = df[field.name]
        if pa.types.is_list(field.type):
            values = [None if v is None or (isinstance(v, float) and pd.isna(v)) else [str(x) for x in v] for v in col]
        elif pa.types.is_string(field.type):
            values = [None if v is None or (not isinstance(v, (list, dict)) and pd.isna(v)) else (json.dumps(v) if isinstance(v, dict) else str(v)) for v in col]
        elif pa.types.is_integer(field.type):
            values = [None if pd.isna(v) else int(v) for v in col]
        elif pa.types.is_boolean(field.type):
            values = [None if v is None or (isinstance(v, float) and pd.isna(v)) else bool(v) for v in col]
        elif pa.types.is_floating(field.type):
            values = [None if v is None or pd.isna(v) else float(v) for v in col]
        else:
            values = col
        arrays.append(pa.array(values, type=field.type) if not pa.types.is_timestamp(field.type) else pa.Array.from_pandas(col, type=field.type))
    return pa.Table.from_arrays(arrays, schema=schema)


def write_table(df: pd.DataFrame, path: Path, name: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(to_arrow(df, name), path)
    return path


def read_table(path: Path) -> pd.DataFrame:
    return pq.read_table(path).to_pandas()


def write_json(obj, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str) + "\n", encoding="utf-8")
    return path
