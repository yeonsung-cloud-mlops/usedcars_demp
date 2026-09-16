"""Compare trim-aware and accident-only models on identical historical holdouts."""
from pathlib import Path
import json
import joblib
import numpy as np
import pandas as pd
from price_model import PricePredictor, make_features, predict_bundle
from train_model import metrics

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "artifacts" / "validation_trim"


def main():
    OUT.mkdir(exist_ok=True)
    predictor = PricePredictor()
    new = predictor.bundle
    old = joblib.load(ROOT / "artifacts" / "baseline_4features" / "price_model.joblib")
    data = pd.read_pickle(ROOT / "artifacts" / "training_data.pkl")
    cutoff = pd.Timestamp(new["report"]["test_cutoff_inclusive"])
    test = data.loc[data.listed_date >= cutoff].copy()
    train = data.loc[(data.listed_date < cutoff) & ~data.vin.isin(test.vin)]
    assert len(train) == old["report"]["train_rows"]
    assert len(test) == old["report"]["test_rows"]
    assert not train.vin.isin(test.vin).any()
    X = make_features(test)
    before, after = predict_bundle(old, X), predict_bundle(new, X)
    y = test.price.to_numpy()
    np.testing.assert_allclose(metrics(y, before)["MAE"], old["report"]["model"]["MAE"], rtol=1e-10)
    report = {"full_holdout": comparison(y, before, after), "train_vin_overlap": 0,
              "unseen_trim_fallback_rows": int((~X.make_model_trim.isin(new["trim_support"])).sum()),
              "train_trim_combinations": len(new["trim_support"]),
              "train_trim_missing": int(train.trim_name.isna().sum()),
              "note": "Same previously inspected holdout, not a new external test. No tuning on this comparison."}
    for name, source in [("1000", "validation_1000_accidents/predictions_1000.csv"), ("100", "validation_extra_100/predictions_100.csv")]:
        previous = pd.read_csv(ROOT / "artifacts" / source)
        sample = test.loc[previous.prepared_row_id].copy()
        assert sample.vin.tolist() == previous.vin.tolist()
        np.testing.assert_array_equal(sample.price.to_numpy(), previous.actual_price.to_numpy())
        f = make_features(sample)
        old_pred, new_pred = predict_bundle(old, f), predict_bundle(new, f)
        np.testing.assert_allclose(old_pred, previous.predicted_price, rtol=1e-10)
        api = []
        for row in sample.itertuples():
            api.append(predictor.predict(row.make_name, row.model_name, int(row.vehicle_age), float(row.mileage), row.has_accidents, row.trim_name)["predicted_price"])
        np.testing.assert_allclose(api, new_pred, atol=.00501, rtol=0)
        report["same_" + name] = comparison(sample.price.to_numpy(), old_pred, new_pred)
        report["same_" + name]["api_success_rows"] = len(api)
        sample.insert(0, "prepared_row_id", sample.index)
        sample = sample.rename(columns={"price": "actual_price"})
        sample["previous_model_price"] = old_pred
        sample["predicted_price"] = new_pred
        sample["absolute_error"] = np.abs(new_pred - sample.actual_price)
        sample["absolute_percentage_error"] = sample.absolute_error / sample.actual_price * 100
        sample["trim_fallback"] = ~f.make_model_trim.isin(new["trim_support"])
        path = OUT / f"predictions_{name}.csv"
        sample.to_csv(path, index=False, encoding="utf-8-sig")
        saved = pd.read_csv(path)
        assert len(saved) == int(name)
        np.testing.assert_allclose(metrics(saved.actual_price, saved.predicted_price)["MAE"], report["same_"+name]["new"]["MAE"])
    (OUT / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


def comparison(actual, before, after):
    a, b = metrics(actual, before), metrics(actual, after)
    return {"rows": len(actual), "old": a, "new": b,
            "MAE_reduction_percent": (1 - b["MAE"] / a["MAE"]) * 100,
            "new_within_20_percent": int((np.abs(actual-after)/actual <= .2).sum())}


if __name__ == "__main__":
    main()
