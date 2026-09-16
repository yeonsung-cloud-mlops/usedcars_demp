"""Compare the saved three- and four-feature models on exactly the same holdout."""
from pathlib import Path
import json
import joblib
import numpy as np
import pandas as pd
from price_model import make_features
from train_model import metrics

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "artifacts"


def main():
    old = joblib.load(OUT / "baseline_3features" / "price_model.joblib")
    new = joblib.load(OUT / "baseline_4features" / "price_model.joblib")
    data = pd.read_pickle(OUT / "training_data.pkl")
    assert old["report"]["test_cutoff_inclusive"] == new["report"]["test_cutoff_inclusive"]
    test = data.loc[data.listed_date >= pd.Timestamp(new["report"]["test_cutoff_inclusive"])].copy()
    assert len(test) == old["report"]["test_rows"] == new["report"]["test_rows"]
    X = make_features(test)
    old_pred = old["estimator"].predict(X[list(old["estimator"].feature_names_in_)])
    new_pred = new["estimator"].predict(X[list(new["estimator"].feature_names_in_)])
    y = test.price.to_numpy()
    old_metrics, new_metrics = metrics(y, old_pred), metrics(y, new_pred)
    np.testing.assert_allclose(old_metrics["MAE"], old["report"]["model"]["MAE"], rtol=1e-10)
    groups = []
    for code, label in [(0, "False"), (1, "True"), (2, "Unknown")]:
        mask = X.has_accidents.eq(code).to_numpy()
        if mask.any():
            groups.append({"has_accidents": label, "n": int(mask.sum()),
                           "old": metrics(y[mask], old_pred[mask]), "new": metrics(y[mask], new_pred[mask])})
    sample = json.loads((OUT / "validation_1000_accidents" / "metrics.json").read_text())
    report = {
        "full_holdout_rows": len(test), "old": old_metrics, "new": new_metrics,
        "mae_reduction_percent": (1 - new_metrics["MAE"] / old_metrics["MAE"]) * 100,
        "by_accident_history": groups,
        "same_1000_rows": {"old": sample["previous_model"], "new": sample["model"],
                           "mae_reduction_percent": sample["mae_improvement_over_previous_percent"]},
        "note": "Feature addition evaluated on the previously inspected holdout, not a new untouched external test.",
    }
    (OUT / "accident_model_comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
