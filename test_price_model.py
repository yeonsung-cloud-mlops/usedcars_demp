"""Run after training: python3 -m unittest -v test_price_model.py"""
import json
import unittest
import numpy as np
import pandas as pd
from price_model import DEFAULT_MODEL, PricePredictor, make_features, accident_code


class PredictionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.predictor = PricePredictor()
        cls.example = json.loads((DEFAULT_MODEL.parent / "prediction_examples.json").read_text())[0]

    def call(self, **changes):
        values = {k: self.example[k] for k in ["make_name", "model_name", "trim_name", "vehicle_age", "mileage", "has_accidents"]}
        values.update(changes)
        return self.predictor.predict(**values)

    def test_saved_model_reproduces_heldout_prediction(self):
        result = self.call()
        self.assertAlmostEqual(result["predicted_price"], self.example["predicted_price"], places=2)
        self.assertGreater(result["predicted_price"], 0)

    def test_case_and_whitespace_normalization(self):
        base = self.call()["predicted_price"]
        result = self.call(make_name=" " + self.example["make_name"].upper() + " ",
                           model_name=self.example["model_name"].lower())
        self.assertEqual(base, result["predicted_price"])

    def test_invalid_values_rejected(self):
        for changes in [dict(mileage=-1), dict(mileage=float("nan")), dict(mileage=float("inf")),
                        dict(vehicle_age=-1), dict(vehicle_age=2.5), dict(vehicle_age=True),
                        dict(make_name=""), dict(model_name="NONEXISTENT_MODEL"), dict(mileage=1e9), dict(has_accidents="maybe")]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.call(**changes)

    def test_feature_contract(self):
        frame = make_features(pd.DataFrame([self.example]))
        self.assertEqual(frame.columns.tolist(), ["make_model", "vehicle_age", "mileage", "has_accidents", "make_model_trim"])
        estimator = self.predictor.bundle["estimator"]
        np.testing.assert_array_equal(estimator.feature_names_in_, frame.columns)
        transformed = estimator.regressor_.named_steps["features"].transform(frame)
        self.assertEqual(transformed.shape, (1, 5))
        self.assertTrue(np.isfinite(transformed).all())

    def test_accident_categories_remain_distinct(self):
        self.assertEqual([accident_code(v) for v in [False, True, None]], [0, 1, 2])
        self.assertEqual(accident_code("unknown"), 2)
        for value in [True, False, None]:
            self.assertGreater(self.call(has_accidents=value)["predicted_price"], 0)

    def test_unknown_trim_uses_previous_model(self):
        from price_model import PricePredictor
        result = self.call(trim_name="UNSEEN_TEST_TRIM_12345")
        old = PricePredictor(DEFAULT_MODEL.parent / "baseline_4features" / "price_model.joblib")
        expected = old.predict(self.example["make_name"], self.example["model_name"], self.example["vehicle_age"], self.example["mileage"], self.example["has_accidents"])
        self.assertEqual(result["predicted_price"], expected["predicted_price"])
        self.assertTrue(any("Unseen trim" in w for w in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
