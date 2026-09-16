"""Audit the saved model on 1,000 reproducibly sampled, previously held-out rows."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd
from price_model import PricePredictor, make_features
from train_model import metrics

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "artifacts" / "validation_1000_accidents"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    predictor = PricePredictor(ROOT / "artifacts" / "baseline_4features" / "price_model.joblib")
    bundle = predictor.bundle
    data = pd.read_pickle(ROOT / "artifacts" / "training_data.pkl")
    cutoff = pd.Timestamp(bundle["report"]["test_cutoff_inclusive"])
    holdout = data.loc[data.listed_date >= cutoff]
    train = data.loc[(data.listed_date < cutoff) & ~data.vin.isin(holdout.vin)]
    sample = holdout.sample(n=1000, random_state=42).copy()
    previous = pd.read_csv(ROOT / "artifacts" / "validation_1000" / "predictions_1000.csv")
    assert sample.vin.tolist() == previous.vin.tolist(), "Comparison sample changed."
    np.testing.assert_array_equal(sample.price.to_numpy(), previous.actual_price.to_numpy())
    assert len(sample) == 1000 and sample.index.is_unique
    assert set(sample.vin).isdisjoint(set(train.vin))
    # Confirm these sampled values are present in the user-supplied CSV.
    fields = ["make_name", "model_name", "year", "mileage", "price"]
    expected = sample.assign(year=sample.listed_date.dt.year - sample.vehicle_age)[fields]
    wanted = set(expected.itertuples(index=False, name=None))
    found = set()
    for chunk in pd.read_csv(ROOT / "used_cars_price_prediction.csv", usecols=fields, chunksize=100000):
        found.update(wanted.intersection(chunk[fields].itertuples(index=False, name=None)))
    assert wanted == found, "Sample contains values absent from the requested CSV."
    X = make_features(sample)
    predictions = bundle["estimator"].predict(X[list(bundle["estimator"].feature_names_in_)])
    assert np.isfinite(predictions).all() and (predictions > 0).all()
    api_errors, api_predictions = [], []
    for row in sample.itertuples():
        try:
            result = predictor.predict(row.make_name, row.model_name, int(row.vehicle_age), float(row.mileage), row.has_accidents)
            api_predictions.append(result["predicted_price"])
            api_errors.append("")
        except ValueError as exc:
            api_predictions.append(np.nan)
            api_errors.append(str(exc))
    valid = np.isfinite(api_predictions)
    np.testing.assert_allclose(np.asarray(api_predictions)[valid], predictions[valid], atol=.00501, rtol=0)
    train_keys = make_features(train).make_model
    baseline_medians = pd.Series(train.price.to_numpy(), index=train_keys).groupby(level=0).median()
    baseline = X.make_model.map(baseline_medians).fillna(train.price.median()).to_numpy()
    actual = sample.price.to_numpy()
    result = sample.copy()
    result.insert(0, "prepared_row_id", sample.index)
    result["year"] = result.listed_date.dt.year - result.vehicle_age
    result = result.rename(columns={"price": "actual_price"})
    result["predicted_price"] = predictions
    result["previous_model_price"] = previous.predicted_price.to_numpy()
    result["baseline_price"] = baseline
    result["signed_error"] = predictions - actual
    result["absolute_error"] = np.abs(predictions - actual)
    result["absolute_percentage_error"] = result.absolute_error / actual * 100
    result["api_predicted_price"] = api_predictions
    result["api_error"] = api_errors
    result.to_csv(OUT / "predictions_1000.csv", index=False, encoding="utf-8-sig")
    model_metrics, baseline_metrics = metrics(actual, predictions), metrics(actual, baseline)
    trees = bundle["estimator"].regressor_.named_steps["trees"]
    report = {
        "sample_rows": len(result), "sampling_seed": 42, "holdout_pool_rows": len(holdout),
        "sample_date_min": str(sample.listed_date.min().date()),
        "sample_date_max": str(sample.listed_date.max().date()),
        "csv_sample_values_verified": True, "train_vin_overlap": 0,
        "api_success_rows": int(valid.sum()), "api_failed_rows": int((~valid).sum()),
        "model_file_sha256": hashlib.sha256((ROOT / "artifacts" / "baseline_4features" / "price_model.joblib").read_bytes()).hexdigest(),
        "model": model_metrics, "make_model_median_baseline": baseline_metrics,
        "previous_model": metrics(actual, previous.predicted_price.to_numpy()),
        "mae_improvement_over_previous_percent": (1 - model_metrics["MAE"] / metrics(actual, previous.predicted_price.to_numpy())["MAE"]) * 100,
        "mae_improvement_percent": (1 - model_metrics["MAE"] / baseline_metrics["MAE"]) * 100,
        "median_absolute_percentage_error": float(result.absolute_percentage_error.median()),
        "mean_signed_error": float(result.signed_error.mean()),
        "within_error_percent": {str(p): int((result.absolute_percentage_error <= p).sum()) for p in [10,20,30]},
        "over_50_percent_error_rows": int((result.absolute_percentage_error > 50).sum()),
        "training_diagnostics": {
            "trees": int(trees.n_iter_),
            "initial_internal_train_log_loss": float(-trees.train_score_[0]),
            "final_internal_train_log_loss": float(-trees.train_score_[-1]),
            "initial_internal_validation_log_loss": float(-trees.validation_score_[0]),
            "final_internal_validation_log_loss": float(-trees.validation_score_[-1]),
            "note": "Internal loss is in log-price space and is not original-price MAE; external holdout metrics are primary.",
        },
    }
    result["age_band"] = pd.cut(result.vehicle_age, [-1,2,5,10,20,100], labels=["0-2","3-5","6-10","11-20","21+"])
    age = result.groupby("age_band", observed=True).agg(n=("actual_price","size"), MAE=("absolute_error","mean"), MAPE=("absolute_percentage_error","mean"))
    makes = result.groupby("make_name").agg(n=("actual_price","size"), MAE=("absolute_error","mean"), MAPE=("absolute_percentage_error","mean"))
    report["by_age"] = age.reset_index().to_dict(orient="records")
    report["by_make_at_least_20_rows"] = makes.loc[makes.n >= 20].sort_values("MAPE", ascending=False).reset_index().to_dict(orient="records")
    example_cols = ["make_name", "model_name", "vehicle_age", "mileage", "actual_price", "predicted_price", "absolute_percentage_error"]
    report["largest_absolute_errors"] = result.nlargest(5, "absolute_error")[example_cols].to_dict(orient="records")
    report["largest_percentage_errors"] = result.nlargest(5, "absolute_percentage_error")[example_cols].to_dict(orient="records")
    (OUT / "metrics.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    # Verify the delivered CSV preserves all 1,000 observations and reported metrics.
    saved = pd.read_csv(OUT / "predictions_1000.csv")
    assert len(saved) == 1000
    assert abs(metrics(saved.actual_price, saved.predicted_price)["MAE"] - model_metrics["MAE"]) < 1e-8
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
