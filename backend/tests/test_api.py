import asyncio
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app

PAYLOAD = {"make_name": "Toyota", "model_name": "Camry", "vehicle_age": 5,
           "mileage": 60000, "has_accidents": "false"}


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as client:
        yield client


def test_metadata_and_real_model_parity(client):
    metadata = client.get("/api/v1/metadata").json()
    assert len(metadata["model_version"]) == 16
    assert metadata["units_verified"] is False
    assert any(make["name"] == "toyota" for make in metadata["makes"])
    for history in ("true", "false", "unknown"):
        payload = {**PAYLOAD, "has_accidents": history}
        response = client.post("/api/v1/predict", json=payload)
        assert response.status_code == 200
        result = response.json()
        reference = client.app.state.predictor.predict(**payload)
        assert result["predicted_price"] == reference["predicted_price"]
        assert result["warnings"] == reference["warnings"]
        assert result["request_id"] == response.headers["X-Request-ID"]
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("patch", [
    {"vehicle_age": -1}, {"vehicle_age": 1.5}, {"vehicle_age": True},
    {"vehicle_age": "5"}, {"vehicle_age": 31}, {"mileage": -1},
    {"mileage": True}, {"mileage": "60000"}, {"mileage": 300001},
    {"model_name": "not-supported"}, {"make_name": " "},
    {"has_accidents": "maybe"}, {"unrecognized": 1},
])
def test_rejects_invalid_input(client, patch):
    response = client.post("/api/v1/predict", json={**PAYLOAD, **patch})
    assert response.status_code == 422
    assert "message" in response.json()["error"]


def test_nonfinite_and_malformed_json(client):
    for body in ('{"mileage": NaN}', '{"mileage": Infinity}', '{bad'):
        response = client.post("/api/v1/predict", content=body, headers={"Content-Type": "application/json"})
        assert response.status_code == 422


def test_readiness_and_errors(client):
    assert client.get("/api/health/ready").status_code == 200
    client.app.state.ready = False
    try:
        for method, path in [("get", "/api/health/ready"), ("get", "/api/v1/metadata"), ("post", "/api/v1/predict")]:
            kwargs = {"json": PAYLOAD} if method == "post" else {}
            assert getattr(client, method)(path, **kwargs).status_code == 503
    finally:
        client.app.state.ready = True
    assert client.get("/api/missing").status_code == 404


def test_missing_model_fails_startup(tmp_path):
    with pytest.raises(FileNotFoundError):
        with TestClient(create_app(tmp_path / "missing.joblib")):
            pass


def test_busy_and_cancelled_caller_does_not_release_running_job():
    async def check():
        app = create_app()
        async with app.router.lifespan_context(app):
            started, release = threading.Event(), threading.Event()
            original = app.state.predictor.predict

            def delayed(**payload):
                started.set()
                release.wait(timeout=5)
                return original(**payload)

            app.state.predictor.predict = delayed
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                pending = asyncio.create_task(client.post("/api/v1/predict", json=PAYLOAD))
                await asyncio.to_thread(started.wait, 3)
                assert started.is_set()
                try:
                    assert (await client.post("/api/v1/predict", json=PAYLOAD)).status_code == 503
                    pending.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await pending
                    assert app.state.busy
                    assert (await client.get("/api/health/live")).status_code == 200
                finally:
                    release.set()
                for _ in range(100):
                    if not app.state.busy:
                        break
                    await asyncio.sleep(.01)
                assert not app.state.busy
    asyncio.run(check())
