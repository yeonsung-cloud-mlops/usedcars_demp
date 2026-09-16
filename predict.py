"""Example: python3 predict.py --make Toyota --model Camry --vehicle-age 5 --mileage 60000"""
import argparse
import json
from price_model import DEFAULT_MODEL, PricePredictor


def main():
    parser = argparse.ArgumentParser(description="Historical used-car listing-price prediction")
    parser.add_argument("--make", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--vehicle-age", required=True, type=int)
    parser.add_argument("--mileage", "--milage", required=True, type=float)
    parser.add_argument("--has-accidents", choices=["true", "false", "unknown"], default="unknown")
    parser.add_argument("--trim", default=None)
    parser.add_argument("--model-file", default=str(DEFAULT_MODEL))
    args = parser.parse_args()
    try:
        result = PricePredictor(args.model_file).predict(args.make, args.model, args.vehicle_age, args.mileage, args.has_accidents, args.trim)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
