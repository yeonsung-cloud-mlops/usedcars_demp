"""Train and evaluate a price model with make/model, trim, age, mileage and accident history."""
from pathlib import Path
import json
import platform
import time
import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer, TransformedTargetRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, r2_score, root_mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import TargetEncoder
from price_model import make_features, predict_bundle

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "artifacts"
SEED = 42


def metrics(y, pred):
    return {
        "MAE": float(mean_absolute_error(y, pred)),
        "RMSE": float(root_mean_squared_error(y, pred)),
        "MAPE_percent": float(mean_absolute_percentage_error(y, pred) * 100),
        "R2": float(r2_score(y, pred)),
    }


def train():
    start = time.monotonic()
    data = pd.read_pickle(OUT / "training_data.pkl")
    cutoff = data.listed_date.quantile(.8).normalize()
    test = data.loc[data.listed_date >= cutoff].copy()
    # No VIN can cross the temporal holdout boundary.
    test_vins = set(test.vin)
    train = data.loc[(data.listed_date < cutoff) & ~data.vin.isin(test_vins)].copy()
    assert set(train.vin).isdisjoint(test_vins)
    assert train.listed_date.max() < test.listed_date.min()
    X_train, X_test = make_features(train), make_features(test)
    y_train, y_test = train.price.to_numpy(), test.price.to_numpy()
    preprocessing = ColumnTransformer([
        # fit_transform performs out-of-fold encoding on the training set.
        ("car_model", TargetEncoder(target_type="continuous", smooth="auto", cv=5,
                                    shuffle=True, random_state=SEED), ["make_model", "make_model_trim"]),
        ("numeric", "passthrough", ["vehicle_age", "mileage", "has_accidents"]),
    ])
    regressor = Pipeline([
        ("features", preprocessing),
        ("trees", HistGradientBoostingRegressor(
            learning_rate=.08, max_iter=250, max_leaf_nodes=31, min_samples_leaf=40,
            l2_regularization=5.0, early_stopping=True, validation_fraction=.1,
            n_iter_no_change=15, random_state=SEED,
            categorical_features=[4],
        )),
    ])
    estimator = TransformedTargetRegressor(regressor=regressor, func=np.log1p, inverse_func=np.expm1)
    print(f"Training rows={len(train):,}; test rows={len(test):,}; cutoff={cutoff.date()}", flush=True)
    estimator.fit(X_train, y_train)
    trim_support = X_train.make_model_trim.value_counts().to_dict()
    fallback = joblib.load(OUT / "baseline_4features" / "price_model.joblib")["estimator"]
    prediction_bundle = {"estimator": estimator, "trim_support": trim_support, "fallback_estimator": fallback}
    pred = predict_bundle(prediction_bundle, X_test)
    overall_baseline = np.full(len(test), np.median(y_train))
    medians = pd.Series(y_train, index=X_train.make_model).groupby(level=0).median()
    model_baseline = X_test.make_model.map(medians).fillna(np.median(y_train)).to_numpy()
    support_frame = X_train.assign(price=y_train)
    support = support_frame.groupby("make_model").agg(
        count=("price", "size"), vehicle_age_min=("vehicle_age", "min"),
        vehicle_age_max=("vehicle_age", "max"), mileage_min=("mileage", "min"), mileage_max=("mileage", "max"),
    ).to_dict(orient="index")
    known = X_test.make_model.isin(support).to_numpy()
    ranges = {c: [float(X_train[c].min()), float(X_train[c].max())] for c in ["vehicle_age", "mileage"]}
    supported = known.copy()
    for c, (lo, hi) in ranges.items():
        supported &= X_test[c].between(lo, hi).to_numpy()
    report = {
        "features": list(X_train.columns),
        "unique_train_trims": len(trim_support),
        "missing_trim_train_rows": int(train.trim_name.isna().sum()),
        "unseen_trim_test_rows_using_fallback": int((~X_test.make_model_trim.isin(trim_support)).sum()),
        "accident_encoding": {"False": 0, "True": 1, "Unknown": 2},
        "accident_train_counts": train.has_accidents.fillna("Unknown").value_counts().to_dict(),
        "accident_test_counts": test.has_accidents.fillna("Unknown").value_counts().to_dict(),
        "train_rows": len(train), "test_rows": len(test),
        "vin_overlap_rows_removed_from_train": int(((data.listed_date < cutoff) & data.vin.isin(test_vins)).sum()),
        "test_cutoff_inclusive": str(cutoff.date()),
        "train_date_range": [str(train.listed_date.min().date()), str(train.listed_date.max().date())],
        "test_date_range": [str(test.listed_date.min().date()), str(test.listed_date.max().date())],
        "unique_train_models": len(support), "unknown_model_test_rows": int((~known).sum()),
        "api_supported_test_rows": int(supported.sum()),
        "model": metrics(y_test, pred),
        "global_median_baseline": metrics(y_test, overall_baseline),
        "make_model_median_baseline": metrics(y_test, model_baseline),
        "api_supported_subset": metrics(y_test[supported], pred[supported]),
        "trees_used": int(estimator.regressor_.named_steps["trees"].n_iter_),
        "input_ranges": ranges,
        "training_seconds": round(time.monotonic() - start, 2),
        "versions": {"python": platform.python_version(), "sklearn": sklearn.__version__,
                     "pandas": pd.__version__, "numpy": np.__version__, "joblib": joblib.__version__},
        "notes": [
            "Holdout is later listing dates, not later observed sale prices; price observation dates are unavailable.",
            "User-requested feature addition compared on previously inspected holdout; no hyperparameter search.",
            "Saved estimator is exactly the estimator evaluated here; holdout was not used for final refitting.",
            "MAPE is mean absolute percentage error, not a prediction interval.",
            "Unknown models use the encoder training mean during evaluation; the prediction API rejects them.",
        ],
    }
    bundle = {**prediction_bundle, "model_support": support, "input_ranges": ranges, "report": report}
    joblib.dump(bundle, OUT / "price_model.joblib", compress=3)
    (OUT / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    example = test.loc[supported, ["make_name", "model_name", "trim_name", "vehicle_age", "mileage", "has_accidents", "price"]].copy()
    example["predicted_price"] = pred[supported]
    example.sample(n=min(10, len(example)), random_state=SEED).to_json(
        OUT / "prediction_examples.json", orient="records", indent=2, force_ascii=False)
    breakdown = test[["vehicle_age", "price"]].copy()
    breakdown["absolute_error"] = np.abs(y_test - pred)
    breakdown["age_band"] = pd.cut(breakdown.vehicle_age, [-1, 2, 5, 10, 20, 100],
                                     labels=["0-2", "3-5", "6-10", "11-20", "21+"])
    by_age = breakdown.groupby("age_band", observed=True).agg(
        count=("price", "size"), median_price=("price", "median"), MAE=("absolute_error", "mean"))
    report["age_band_metrics"] = by_age.reset_index().to_dict(orient="records")
    (OUT / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    train()
