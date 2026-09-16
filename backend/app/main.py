"""HTTP adapter for the trusted, locally trained price model."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import time
from typing import Annotated, Literal
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException

from price_model import DEFAULT_MODEL, PricePredictor

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("usedcar.api")


class PredictionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    make_name: Annotated[str, Field(min_length=1, max_length=100)]
    model_name: Annotated[str, Field(min_length=1, max_length=150)]
    vehicle_age: Annotated[int, Field(ge=0)]
    mileage: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    has_accidents: Literal["false", "true", "unknown"] = "unknown"


def error(request, status, code, message, field=None, headers=None):
    return JSONResponse(status_code=status, headers=headers, content={
        "error": {"code": code, "message": message, "field": field},
        "request_id": getattr(request.state, "request_id", ""),
    })


def build_metadata(predictor, version):
    bundle = predictor.bundle
    makes = {}
    for key, support in sorted(bundle["model_support"].items()):
        make, model = json.loads(key)
        makes.setdefault(make, []).append({"name": model, **support})
    report = bundle["report"]
    return {
        "model_version": version,
        "features": list(bundle["estimator"].feature_names_in_),
        "units_verified": False,
        "price_unit": "original CSV price units",
        "mileage_unit": "original CSV mileage units",
        "input_ranges": bundle["input_ranges"],
        "train_date_range": report["train_date_range"],
        "test_date_range": report["test_date_range"],
        "metrics": report["model"],
        "makes": [{"name": name, "models": models} for name, models in makes.items()],
    }


def create_app(model_path=None):
    path = Path(model_path or os.environ.get("MODEL_PATH", DEFAULT_MODEL))

    @asynccontextmanager
    async def lifespan(app):
        # Hash and load an immutable trusted deployment artifact, never user uploads.
        version = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        predictor = PricePredictor(path)
        metadata = build_metadata(predictor, version)
        first = metadata["makes"][0]
        support = first["models"][0]
        warm = predictor.predict(first["name"], support["name"],
                                 int(support["vehicle_age_min"]), support["mileage_min"])
        if not math.isfinite(warm["predicted_price"]):
            raise RuntimeError("Model warmup returned an invalid price")
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inference")
        app.state.predictor = predictor
        app.state.metadata = metadata
        app.state.pool = pool
        app.state.busy = False
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
            pool.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title="Used car price API", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ready = False

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("request_failed request_id=%s", request.state.request_id)
            response = error(request, 500, "internal_error", "요청 처리 중 오류가 발생했습니다.")
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        log.info("request_id=%s status=%s duration_ms=%.1f model=%s",
                 request.state.request_id, response.status_code,
                 (time.monotonic() - start) * 1000,
                 getattr(app.state, "metadata", {}).get("model_version", "unready"))
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        issue = exc.errors()[0]
        field = str(issue["loc"][-1]) if len(issue["loc"]) > 1 else None
        return error(request, 422, "invalid_input", "입력값의 형식과 허용 범위를 확인해 주세요.", field)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error(request, exc.status_code, "http_error", "요청한 경로 또는 방식을 확인해 주세요.")

    @app.get("/api/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/api/health/ready")
    async def ready(request: Request):
        if not app.state.ready:
            return error(request, 503, "not_ready", "예측 모델을 준비하고 있습니다.")
        return {"status": "ready", "model_version": app.state.metadata["model_version"]}

    @app.get("/api/v1/metadata")
    async def metadata(request: Request):
        if not app.state.ready:
            return error(request, 503, "not_ready", "예측 모델을 준비하고 있습니다.")
        return app.state.metadata

    @app.post("/api/v1/predict")
    async def predict(payload: PredictionInput, request: Request):
        if not app.state.ready:
            return error(request, 503, "not_ready", "예측 모델을 준비하고 있습니다.")
        # No await between check and assignment: exactly one job per event loop/worker.
        if app.state.busy:
            return error(request, 503, "busy", "다른 예측을 처리 중입니다. 잠시 후 다시 시도해 주세요.",
                         headers={"Retry-After": "1"})
        app.state.busy = True
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(app.state.pool,
                                      lambda: app.state.predictor.predict(**payload.model_dump()))

        def finished(task):
            app.state.busy = False
            # Consume background exceptions even after a disconnected request.
            if not task.cancelled():
                task.exception()

        future.add_done_callback(finished)
        try:
            result = await asyncio.shield(future)
        except ValueError as exc:
            detail = str(exc)
            if "Unseen" in detail:
                return error(request, 422, "unsupported_model", "지원하는 제조사와 모델 조합을 선택해 주세요.", "model_name")
            field = next((key for key in ("vehicle_age", "mileage", "has_accidents") if key in detail), None)
            return error(request, 422, "out_of_range", "해당 입력이 모델의 지원 범위를 벗어났습니다.", field)
        if not math.isfinite(result["predicted_price"]):
            raise RuntimeError("Non-finite model output")
        return {**result, "model_version": app.state.metadata["model_version"],
                "request_id": request.state.request_id}

    static_dir = os.environ.get("STATIC_DIR")
    if static_dir:
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    return app


app = create_app()
