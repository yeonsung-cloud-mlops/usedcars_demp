"""Functional smoke checks for the deployed static UI and actual model API."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import statistics
import time

import httpx
from price_model import DEFAULT_MODEL, PricePredictor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    expected = PricePredictor()
    payload = {"make_name": "Toyota", "model_name": "Camry", "vehicle_age": 5,
               "mileage": 60000, "has_accidents": "false"}
    report = {}
    with httpx.Client(base_url=args.url, timeout=15) as client:
        page = client.get("/")
        page.raise_for_status()
        assert "중고차 가격 예측" in page.text
        assert page.headers["x-content-type-options"] == "nosniff"
        assets = sorted(set(re.findall(r'(?:src|href)="(/_next/[^\"]+)"', page.text)))
        assert assets
        for asset in assets:
            client.get(asset).raise_for_status()
        report["static_assets_ok"] = len(assets)
        metadata = client.get("/api/v1/metadata").json()
        assert metadata["model_version"] == hashlib.sha256(Path(DEFAULT_MODEL).read_bytes()).hexdigest()[:16]
        report["model_version"] = metadata["model_version"]
        for history in ("false", "true", "unknown"):
            row = {**payload, "has_accidents": history}
            response = client.post("/api/v1/predict", json=row)
            response.raise_for_status()
            result = response.json()
            assert result["predicted_price"] == expected.predict(**row)["predicted_price"]
            assert result["warnings"] == expected.predict(**row)["warnings"]
            time.sleep(1.05)
        report["model_parity"] = "all 3 accident states"
        invalid = client.post("/api/v1/predict", json={**payload, "vehicle_age": 31})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["field"] == "vehicle_age"
        oversized = client.post("/api/v1/predict", content="x" * 5000)
        assert oversized.status_code == 413
        with ThreadPoolExecutor(max_workers=15) as pool:
            statuses = list(pool.map(lambda _: client.post("/api/v1/predict", json=payload).status_code, range(15)))
        assert 429 in statuses
        assert all(code in (200, 429, 503) for code in statuses)
        report["rate_limit_statuses"] = {str(code): statuses.count(code) for code in set(statuses)}
        # Allow a full burst bucket to drain before the steady-state sample.
        time.sleep(6)
        latencies = []
        for _ in range(args.samples):
            start = time.monotonic()
            result = client.post("/api/v1/predict", json=payload)
            result.raise_for_status()
            elapsed = time.monotonic() - start
            latencies.append(elapsed * 1000)
            time.sleep(max(0, 1.05 - elapsed))
        report["steady_state"] = {"requests": len(latencies), "errors": 0,
                                  "p50_ms": round(statistics.median(latencies), 1),
                                  "p95_ms": round(sorted(latencies)[max(0, int(.95 * len(latencies)) - 1)], 1)}
    destination = Path(__file__).resolve().parent / ".local/http-verification.json"
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
