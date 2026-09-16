"""Enrich the supplied prediction CSV with matching source metadata, without editing it."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts"
NUMERIC = {"engine_displacement", "horsepower", "mileage", "owner_count", "price", "year"}


def prepare():
    ARTIFACTS.mkdir(exist_ok=True)
    path = ROOT / "used_cars_price_prediction.csv"
    columns = pd.read_csv(path, nrows=0).columns.tolist()
    dtype = {c: "float64" if c in NUMERIC else "string" for c in columns}
    target = pd.read_csv(path, dtype=dtype)
    keys = pd.util.hash_pandas_object(target[columns], index=False).to_numpy()
    # Hashes accelerate matching; duplicate hashes in the selected file are rejected.
    if pd.Series(keys).duplicated().any():
        raise ValueError("Duplicate feature rows or hash keys in input; inspect before matching.")
    key_index = pd.Index(keys)
    chunks = []
    raw_rows = 0
    for i, raw in enumerate(pd.read_csv(
        ROOT / "used_cars_data.csv", usecols=columns + ["vin", "listed_date", "is_new"],
        dtype={**dtype, "vin": "string", "listed_date": "string", "is_new": "string"},
        chunksize=100_000,
    )):
        hashes = pd.util.hash_pandas_object(raw[columns], index=False).to_numpy()
        positions = key_index.get_indexer(hashes)
        hit = positions >= 0
        if hit.any():
            # Verify full row equality, so a hash collision cannot create a false match.
            left = raw.loc[hit, columns].reset_index(drop=True)
            right = target.iloc[positions[hit]][columns].reset_index(drop=True)
            equal = (left.eq(right) | (left.isna() & right.isna())).fillna(False).all(axis=1)
            if not equal.all():
                raise ValueError("Hash collision or inconsistent source match.")
            meta = raw.loc[hit, ["vin", "listed_date", "is_new"]].copy()
            meta["row_id"] = positions[hit]
            chunks.append(meta)
        raw_rows += len(raw)
        if i % 5 == 0:
            print(f"Scanned {raw_rows:,} source rows", flush=True)
    metadata = pd.concat(chunks, ignore_index=True).drop_duplicates()
    ambiguous = metadata.row_id.duplicated(keep=False)
    ambiguous_ids = metadata.loc[ambiguous, "row_id"].nunique()
    unique = metadata.loc[~ambiguous].set_index("row_id")
    joined = target.join(unique, how="inner")
    used = joined.loc[joined.is_new.str.lower().eq("false")].copy()
    used["listed_date"] = pd.to_datetime(used.listed_date, errors="coerce")
    used["vehicle_age"] = used.listed_date.dt.year - used.year
    valid = used.listed_date.notna() & used.vin.notna() & used.vin.str.strip().ne("")
    valid &= used.vehicle_age.ge(0) & used.mileage.ge(0) & used.price.gt(0)
    clean = used.loc[valid, ["make_name", "model_name", "trim_name", "vehicle_age", "mileage", "has_accidents", "price", "vin", "listed_date"]].copy()
    clean["vehicle_age"] = clean.vehicle_age.astype("int64")
    clean = clean.reset_index(drop=True)
    clean.to_pickle(ARTIFACTS / "training_data.pkl")
    audit = {
        "input_rows": len(target), "source_rows": raw_rows,
        "matched_input_rows": int(metadata.row_id.nunique()),
        "ambiguous_input_rows_removed": int(ambiguous_ids),
        "uniquely_matched_rows": len(joined),
        "is_new_counts_unique_matches": joined.is_new.fillna("Unknown").value_counts().to_dict(),
        "used_before_validity_filter": len(used),
        "invalid_date_age_or_vin_rows_removed": int((~valid).sum()),
        "training_eligible_rows": len(clean),
        "listing_date_min": str(clean.listed_date.min().date()),
        "listing_date_max": str(clean.listed_date.max().date()),
        "age_definition": "listed_date.year - year; integer calendar-year difference",
        "price_unit": "CSV original price units; no currency conversion",
        "mileage_unit": "CSV original mileage units; no unit conversion",
    }
    (ARTIFACTS / "data_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps(audit, indent=2), flush=True)


if __name__ == "__main__":
    prepare()
