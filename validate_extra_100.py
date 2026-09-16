"""Evaluate 100 additional cars, excluding VINs from the previous 1,000-row sample."""
from pathlib import Path
import hashlib
import json
import joblib
import numpy as np
import pandas as pd
from price_model import PricePredictor, make_features
from train_model import metrics

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "artifacts" / "validation_extra_100"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    model_path = ROOT / "artifacts" / "baseline_4features" / "price_model.joblib"
    before_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
    predictor = PricePredictor(model_path)
    data = pd.read_pickle(ROOT / "artifacts" / "training_data.pkl")
    previous = pd.read_csv(ROOT / "artifacts" / "validation_1000_accidents" / "predictions_1000.csv")
    cutoff = pd.Timestamp(predictor.bundle["report"]["test_cutoff_inclusive"])
    holdout = data.loc[data.listed_date >= cutoff]
    train = data.loc[(data.listed_date < cutoff) & ~data.vin.isin(holdout.vin)]
    pool = holdout.loc[~holdout.vin.isin(previous.vin)]
    sample = pool.sample(n=100, random_state=20260916).copy()
    assert not sample.vin.isin(previous.vin).any()
    assert not sample.vin.isin(train.vin).any()
    assert len(sample) == 100
    features = make_features(sample)
    prediction = predictor.bundle["estimator"].predict(features[list(predictor.bundle["estimator"].feature_names_in_)])
    assert np.isfinite(prediction).all() and (prediction > 0).all()
    api_predictions, errors = [], []
    for row in sample.itertuples():
        try:
            result = predictor.predict(row.make_name, row.model_name, int(row.vehicle_age), float(row.mileage), row.has_accidents)
            api_predictions.append(result["predicted_price"])
            errors.append("")
        except ValueError as exc:
            api_predictions.append(np.nan)
            errors.append(str(exc))
    success = np.isfinite(api_predictions)
    np.testing.assert_allclose(np.asarray(api_predictions)[success], prediction[success], atol=.00501, rtol=0)
    old = joblib.load(ROOT / "artifacts" / "baseline_3features" / "price_model.joblib")
    old_prediction = old["estimator"].predict(features[list(old["estimator"].feature_names_in_)])
    actual = sample.price.to_numpy()
    sample.insert(0, "prepared_row_id", sample.index)
    sample = sample.rename(columns={"price": "actual_price"})
    sample["predicted_price"] = prediction
    sample["previous_model_price"] = old_prediction
    sample["absolute_error"] = np.abs(prediction - actual)
    sample["absolute_percentage_error"] = sample.absolute_error / actual * 100
    sample["api_predicted_price"] = api_predictions
    sample["api_error"] = errors
    sample.to_csv(OUT / "predictions_100.csv", index=False, encoding="utf-8-sig")
    report = {
        "sample_rows": 100, "sampling_seed": 20260916, "eligible_pool_rows": len(pool),
        "overlap_with_previous_1000_vins": 0, "overlap_with_training_vins": 0,
        "api_success_rows": int(success.sum()), "api_failed_rows": int((~success).sum()),
        "model_sha256": before_hash, "model_retrained": False,
        "model": metrics(actual, prediction), "previous_3_feature_model": metrics(actual, old_prediction),
        "within_error_percent_counts": {str(p): int((sample.absolute_percentage_error <= p).sum()) for p in [10,20,30]},
        "over_50_percent_error_count": int((sample.absolute_percentage_error > 50).sum()),
        "mean_signed_error": float(np.mean(prediction - actual)),
        "accident_counts": sample.has_accidents.fillna("Unknown").value_counts().to_dict(),
        "largest_absolute_errors": sample.nlargest(3, "absolute_error")[["make_name","model_name","has_accidents","actual_price","predicted_price","absolute_percentage_error"]].to_dict(orient="records"),
        "note": "Additional sample from the existing holdout; not new external data. No rows excluded based on prediction errors.",
    }
    (OUT / "metrics.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    saved = pd.read_csv(OUT / "predictions_100.csv")
    assert len(saved) == 100
    assert abs(metrics(saved.actual_price, saved.predicted_price)["MAE"] - report["model"]["MAE"]) < 1e-8
    assert hashlib.sha256(model_path.read_bytes()).hexdigest() == before_hash
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
