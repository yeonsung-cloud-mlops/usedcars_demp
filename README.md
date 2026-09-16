# 중고차 가격 예측 데모

과거 중고차 매물 데이터로 가격을 예측하는 머신러닝 프로젝트입니다. scikit-learn 학습·평가 파이프라인, Python/CLI 예측 인터페이스, FastAPI API, Next.js 웹 UI와 Docker Compose 배포 구성을 포함합니다.

현재 모델은 제조사·모델, 차령, 주행거리, 사고 이력, 트림 정보를 사용합니다. **과거 매물 가격을 추정하며 현재 시세나 실거래 가격을 의미하지 않습니다.** 가격과 주행거리는 원본 CSV 단위를 그대로 사용하며 통화 및 km/mile 단위는 검증되지 않았습니다.

## 구성

| 경로 | 역할 |
|---|---|
| `prepare_data.py` | 원본 행 대조, 중고차 선별, 등록일 기준 차령 생성 |
| `train_model.py` | 날짜 및 VIN 분리, 학습·평가, 모델 저장 |
| `price_model.py`, `predict.py` | 입력 검증, Python 및 CLI 예측 |
| `backend/` | FastAPI API와 API 테스트 |
| `frontend/` | Next.js·React·TypeScript 웹 앱 |
| `deploy/` | Nginx·Docker Compose 및 AWS 배포 스크립트 |
| `validate_*.py`, `compare_accident_model.py` | 표본 검증 및 모델 비교 |
| `artifacts/` | 로컬 모델·평가 결과·전처리 데이터, Git 제외 |

## 실행 준비

Python 3.11과 Next.js 16을 지원하는 Node.js 및 npm이 필요합니다. Python 패키지는 requirements 파일, 프런트엔드 패키지는 lockfile을 기준으로 설치합니다.

```bash
git clone https://github.com/yeonsung-cloud-mlops/usedcars_demp.git
cd usedcars_demp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
```

**저장소에는 원본 CSV와 학습된 모델이 포함되지 않습니다.** 예측 및 API 실행에는 신뢰할 수 있는 `artifacts/price_model.joblib`을 별도로 준비해야 합니다. `joblib`과 `pickle`은 신뢰할 수 있는 파일만 로드하세요.

## 예측

```bash
python predict.py --make Toyota --model Camry --vehicle-age 5 --mileage 60000 --has-accidents false --trim LE
```

```python
from price_model import PricePredictor

result = PricePredictor().predict(
    make_name="Toyota", model_name="Camry",
    vehicle_age=5, mileage=60000,
    has_accidents=False, trim_name="LE",
)
print(result["predicted_price"])
print(result["warnings"])
```

- 차령은 `매물 등록 연도 - 차량 연식`의 정수 차이입니다.
- CLI 사고 이력은 `true`, `false`, `unknown`이며 생략 시 `unknown`입니다.
- 트림은 선택 사항입니다. 학습에 없는 트림은 번들에 포함된 이전 4개 Feature 모델로 예측하고 안내를 반환합니다.
- 미학습 제조사·모델, 음수, 비유한 숫자, 소수 차령, 학습 전체 범위 밖 입력은 거부합니다.
- 표본 부족 및 차종별 관측 범위 이탈은 결과의 `warnings`로 안내합니다.

## 웹 앱과 API

저장소 루트에서 API를 실행합니다.

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

다른 터미널에서 프런트엔드를 실행하고 `http://127.0.0.1:3000`에 접속합니다. 개발 서버는 `/api/` 요청을 로컬 8000번 포트로 전달합니다.

```bash
cd frontend
npm ci
npm run dev
```

| 메서드 | 경로 | 기능 |
|---|---|---|
| GET | `/api/health/live` | 프로세스 상태 |
| GET | `/api/health/ready` | 모델 준비 상태 |
| GET | `/api/v1/metadata` | 지원 차종, 입력 범위, 모델 버전 및 평가 정보 |
| POST | `/api/v1/predict` | 가격 예측 |

```bash
curl http://127.0.0.1:8000/api/v1/predict -H 'Content-Type: application/json' -d '{"make_name":"Toyota","model_name":"Camry","vehicle_age":5,"mileage":60000,"has_accidents":"false"}'
```

현재 HTTP 요청 스키마는 트림 입력을 제공하지 않습니다. 트림을 지정하려면 Python 또는 CLI를 사용하세요. `MODEL_PATH` 환경 변수로 모델 경로를 변경할 수 있으며, `STATIC_DIR`을 지정하면 API 서버에서 정적 파일을 제공합니다.

## 학습과 평가

전처리에는 저장소 루트의 `used_cars_data.csv`와 `used_cars_price_prediction.csv`가 필요합니다. 원본 데이터는 약 10GB이며 충분한 메모리와 디스크 공간이 필요합니다.

현재 학습 스크립트는 미학습 트림의 대체 예측을 위해 **기존 `artifacts/baseline_4features/price_model.joblib`을 필수로 사용합니다.** 이 파일은 Git에 포함되지 않으며 현재 스크립트가 새로 생성하지 않습니다. CSV만 있는 새 체크아웃에서는 전체 학습을 바로 재현할 수 없습니다.

필요한 데이터와 이전 모델을 준비한 다음 실행합니다.

```bash
python prepare_data.py
OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 python train_model.py
```

제조사·모델 및 트림 범주에 교차 Target Encoding을 적용하고, `HistGradientBoostingRegressor`로 로그 가격을 학습합니다. 등록일 기준으로 학습·평가를 분리하고 양쪽에 같은 VIN이 포함되지 않도록 처리합니다. 평가 행을 합쳐 최종 모델을 재학습하지 않습니다.

로컬 `artifacts/metrics.json`의 트림 모델 평가 기록:

| 항목 | 값 |
|---|---:|
| 학습 행 | 1,189,825 |
| 평가 행 | 312,292 |
| 학습 기간 | 2010-11-03 ~ 2020-08-31 |
| 평가 기간 | 2020-09-01 ~ 2020-09-13 |
| MAE | 2,475.91 |
| RMSE | 4,226.86 |
| MAPE | 11.39% |
| R² | 0.9019 |

이 수치는 과거 등록 매물에 대한 평가이며 미래 시세 예측 성능을 입증하지 않습니다. MAPE는 개별 예측의 오차 보장 범위가 아니며 R²는 정확도 비율이 아닙니다.

## 검증

모델 테스트에는 현재 모델, `artifacts/prediction_examples.json`, 이전 4개 Feature 모델이 필요합니다. API 테스트에도 현재 모델이 필요합니다.

```bash
OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 python -m pytest -q test_price_model.py backend/tests/test_api.py
cd frontend
npm run typecheck
npm run build
```

## 컨테이너 실행

현재 모델과 프런트엔드 정적 빌드를 준비한 뒤 저장소 루트에서 실행합니다. Nginx가 80번 포트에서 웹 페이지와 API를 제공합니다.

```bash
npm --prefix frontend ci
npm --prefix frontend run build
docker compose -f deploy/compose.yaml up --build -d
```

`deploy/provision.py`와 `deploy/release.py`는 AWS 리소스 생성 및 배포용 스크립트입니다. 별도의 `boto3`, AWS 프로필·권한과 로컬 배포 상태가 필요합니다. 실행 옵션은 각 스크립트의 `--help`를 확인하세요. 로컬 실행에는 AWS가 필요하지 않습니다.

## 관련 문서

- [트림 모델 검증 기록](README_trim.md)
- [웹 서비스 운영 가이드](deploy/README.md)
- [기존 3개 Feature 모델 기록](docs/model_baseline_3features.md)
- [사고 이력 모델 비교 기록](README_accidents.md)
- [웹 앱 설계 문서](WEB_APP_DESIGN.md): 설계 기록이며 실제 구현과 차이가 있을 수 있습니다.
- [개발 에이전트 작업 지침](AGENTS.md)

CSV, 학습 산출물, 환경 변수 파일, 인증 키, `deploy/.local/`, 의존성 및 빌드 결과물은 `.gitignore`로 제외합니다.
