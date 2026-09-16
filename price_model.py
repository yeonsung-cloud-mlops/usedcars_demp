"""Input validation and prediction interface for the accident-aware price model."""
from pathlib import Path
import json
import math
import joblib
import pandas as pd

DEFAULT_MODEL = Path(__file__).resolve().parent / "artifacts" / "price_model.joblib"


def model_key(make_name, model_name):
    if not isinstance(make_name, str) or not isinstance(model_name, str):
        raise ValueError("make_name and model_name must be strings.")
    make, model = make_name.strip().casefold(), model_name.strip().casefold()
    if not make or not model:
        raise ValueError("make_name and model_name must not be blank.")
    return json.dumps([make, model], ensure_ascii=False)


def accident_code(value):
    """Unordered categorical codes: no=0, yes=1, unknown=2."""
    if value is None or value is pd.NA:
        return 2
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "yes"}:
            return 1
        if normalized in {"false", "no"}:
            return 0
        if normalized in {"", "unknown"}:
            return 2
    elif isinstance(value, bool):
        return int(value)
    raise ValueError("has_accidents must be true, false, unknown, or None.")


def make_features(data):
    frame = pd.DataFrame({
        "make_model": [model_key(a, b) for a, b in zip(data.make_name, data.model_name)],
        "vehicle_age": data.vehicle_age.to_numpy(dtype=float),
        "mileage": data.mileage.to_numpy(dtype=float),
    }, index=data.index)
    frame["has_accidents"] = [accident_code(v) for v in data.has_accidents]
    frame["make_model_trim"] = [trim_key(a, b, t) for a, b, t in zip(data.make_name, data.model_name, data.trim_name)]
    return frame


def trim_key(make, model, trim):
    if trim is None or trim is pd.NA:
        normalized = None
    elif isinstance(trim, str):
        normalized = trim.strip().casefold() or None
    else:
        raise ValueError("trim_name must be a string or None.")
    return json.dumps([model_key(make, model), normalized], ensure_ascii=False)


def predict_bundle(bundle, frame):
    estimator = bundle["estimator"]
    result = estimator.predict(frame[list(estimator.feature_names_in_)])
    if "trim_support" in bundle:
        unknown = ~frame.make_model_trim.isin(bundle["trim_support"])
        if unknown.any():
            fallback = bundle["fallback_estimator"]
            result[unknown.to_numpy()] = fallback.predict(frame.loc[unknown, list(fallback.feature_names_in_)])
    return result


class PricePredictor:
    def __init__(self, model_path=DEFAULT_MODEL):
        # Only load artifacts you trust: joblib uses Python pickle.
        self.bundle = joblib.load(model_path)

    def predict(self, make_name, model_name, vehicle_age, mileage, has_accidents=None, trim_name=None):
        key = model_key(make_name, model_name)
        if key not in self.bundle["model_support"]:
            raise ValueError("Unseen make/model combination; no supported prediction is available.")
        for name, value in [("vehicle_age", vehicle_age), ("mileage", mileage)]:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number.")
            if value < 0:
                raise ValueError(f"{name} must be non-negative.")
        if not float(vehicle_age).is_integer():
            raise ValueError("vehicle_age must be an integer calendar-year difference.")
        limits = self.bundle["input_ranges"]
        for name, value in [("vehicle_age", vehicle_age), ("mileage", mileage)]:
            if not limits[name][0] <= value <= limits[name][1]:
                raise ValueError(f"{name} is outside the training range {limits[name]}.")
        row = pd.DataFrame({"make_model": [key], "vehicle_age": [vehicle_age], "mileage": [mileage]})
        code = accident_code(has_accidents)
        if "has_accidents" in self.bundle["estimator"].feature_names_in_:
            row["has_accidents"] = code
        trim = trim_key(make_name, model_name, trim_name)
        if "make_model_trim" in self.bundle["estimator"].feature_names_in_:
            row["make_model_trim"] = trim
        price = float(predict_bundle(self.bundle, row)[0])
        support = self.bundle["model_support"][key]
        warnings = ["Historical listing-price estimate in original CSV units; not a current-market valuation."]
        if "trim_support" in self.bundle:
            if trim not in self.bundle["trim_support"]:
                warnings.append("Unseen trim: using the previous model without trim.")
            elif self.bundle["trim_support"][trim] < 100:
                warnings.append("Fewer than 100 training examples for this make/model/trim.")
        if "has_accidents" in self.bundle["estimator"].feature_names_in_ and code == 2:
            count = self.bundle["report"].get("accident_train_counts", {}).get("Unknown", 0)
            warnings.append(f"Accident history unknown: only {count} training rows have unknown history.")
        if support["count"] < 100:
            warnings.append("Fewer than 100 training examples for this make/model.")
        for name, value in [("vehicle_age", vehicle_age), ("mileage", mileage)]:
            if not support[name + "_min"] <= value <= support[name + "_max"]:
                warnings.append(f"{name} is outside the observed range for this make/model.")
        return {
            "predicted_price": round(price, 2),
            "make_name": make_name.strip(), "model_name": model_name.strip(),
            "vehicle_age": vehicle_age, "mileage": mileage,
            "has_accidents": {0: "False", 1: "True", 2: "Unknown"}[code],
            "trim_name": trim_name,
            "training_examples_for_model": support["count"],
            "price_unit": "original CSV price units", "mileage_unit": "original CSV mileage units",
            "warnings": warnings,
        }
