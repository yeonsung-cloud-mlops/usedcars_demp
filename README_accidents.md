# 사고 이력을 추가한 중고차 가격 예측

이 문서는 이전 4개 Feature 모델의 기록이다. 해당 모델은 현재 `artifacts/baseline_4features/`에 보존되어 있다. 최신 트림 모델은 [README.md](README.md)를 참조한다. 아래 당시 실행 예시 및 기본 모델 경로는 이전 버전을 설명한다.

현재 기본 모델은 `제조사+모델`, `vehicle_age`, `mileage`, `has_accidents` 네 가지 Feature를 사용한다. 기존 모델은 `artifacts/baseline_3features/`에 보존했고, 새 모델은 `artifacts/price_model.joblib`에 저장했다.

## 변경 내용

- 원본 21개 컬럼으로 행을 대조하는 기존 전처리를 다시 실행해 `has_accidents`를 유지했다. 학습·평가 행 수와 분리 기준은 기존과 동일하다.
- 사고 이력은 `False`(없음), `True`(있음), `Unknown`(미상)으로 구분한다. 내부 코드는 각각 0, 1, 2이며 트리에는 **순서가 없는 범주형 변수**로 지정했다. 미상을 무사고로 채우지 않았다.
- 차령은 등록 연도에서 차량 연식을 뺀 정수다. 가격과 주행거리는 원본 CSV 단위를 사용한다.
- 기존과 동일한 제조사·모델 TargetEncoder, 로그가격 변환, HistGradientBoosting 설정을 유지했다. 250개 트리, 학습률 0.08, 최대 말단 노드 31, 최소 말단 표본 40, L2 규제 5, 난수 시드 42다.
- 학습 1,189,825행, 평가 312,292행이다. 평가 기간은 2020-09-01~2020-09-13이며, 학습과 평가 VIN은 겹치지 않는다. 평가 행을 합쳐 재학습하지 않았다.

## 성능 비교

| 대상 / 지표 | 기존 3개 Feature | 사고 이력 추가 |
|---|---:|---:|
| 전체 평가 MAE | 3,395.90 | 3,392.38 |
| 전체 평가 RMSE | 5,771.75 | 5,766.06 |
| 전체 평가 MAPE | 14.5922% | 14.5763% |
| 전체 평가 R² | 0.8171 | 0.8174 |
| 동일 1,000건 MAE | 3,454.07 | 3,461.21 |
| 동일 1,000건 MAPE | 14.5031% | 14.5198% |

**전체 MAE는 0.10% 줄었지만, 동일한 1,000건에서는 0.21% 늘었다. 이번 설정에서 사고 이력 추가의 전체 개선 효과는 매우 작으며, 뚜렷한 성능 개선이라고 판단하기 어렵다.** 표본 VIN 순서와 실제 가격이 이전 1,000건과 같음을 확인했다. 모델 추가 비교는 이미 살펴본 평가 집합에서 수행했으므로 새로운 외부 검증은 아니다.

사고 이력별 전체 평가 MAE:

| 사고 이력 | 평가 건수 | 기존 | 추가 후 |
|---|---:|---:|---:|
| 없음 | 264,577 | 3,512.37 | 3,497.42 |
| 있음 | 45,829 | 2,698.06 | 2,758.55 |
| 미상 | 1,886 | 4,014.16 | 4,059.08 |

사고 차량의 MAE는 오히려 커졌다. 이는 이번 모델의 결과이며 사고 정보가 일반적으로 쓸모없다는 의미는 아니다. 이력의 심각도나 트림·상태 정보는 포함하지 않았다. 인과적인 사고 감가율도 추정하지 않는다.

학습 데이터의 사고 없음은 995,486건, 있음은 194,310건, 미상은 **29건**이다. 미상 표본이 매우 적고 평가에서는 1,886건으로 분포가 달라져, 미상 입력에는 표본 부족 안내를 반환한다.

## 예측

```bash
python3 predict.py --make Toyota --model Camry --vehicle-age 5 --mileage 60000 --has-accidents true
```

`--has-accidents`는 `true`, `false`, `unknown`을 받는다. 생략하면 `unknown`이다.

```python
from price_model import PricePredictor

model = PricePredictor()
result = model.predict("Toyota", "Camry", 5, 60000, has_accidents=True)
print(result["predicted_price"])
```

Python에서는 `True`, `False`, `None` 또는 문자열 `true`, `false`, `unknown`을 사용할 수 있다. `None`은 미상이다. 알 수 없는 입력 문자열은 거부한다. 예측 인터페이스로 동일 1,000건을 모두 호출하고 일괄 예측값과의 일치를 확인했다. 저장 모델 재현, 입력 검증, 네 Feature 계약, 사고 범주 구분 등 5개 테스트가 통과했다.

이 모델은 2020년까지의 매물 가격 기준이며 현재 시세나 실거래 가격을 예측한다고 볼 수 없다. 사고 여부를 바꾸었을 때 모든 차량에서 일정한 감가가 나타나도록 강제한 모델도 아니다.

## 재현 및 결과

```bash
python3 prepare_data.py
OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 python3 train_model.py
OMP_NUM_THREADS=4 python3 validate_1000.py
OMP_NUM_THREADS=4 python3 compare_accident_model.py
OMP_NUM_THREADS=4 python3 -m unittest -v test_price_model.py
```

기존 모델 및 기존 1,000건 검증 파일이 있어야 이전 결과와 비교할 수 있다. 의존성은 기존 `requirements.txt`와 같다.

- `artifacts/price_model.joblib`: 현재 4개 Feature 모델
- `artifacts/metrics.json`: 현재 모델 전체 평가 결과
- `artifacts/accident_model_comparison.json`: 기존 모델 대비 전체·사고 이력별·동일 1,000건 비교
- `artifacts/validation_1000_accidents/predictions_1000.csv`: 사고 이력, 실제 가격, 새 예측, 기존 예측 및 오차
- `artifacts/validation_1000_accidents/metrics.json`: 동일 1,000건의 상세 결과
- `artifacts/baseline_3features/`: 이전 모델과 평가 수치
- `artifacts/validation_1000/`: 이전 1,000건 결과와 보고서

기존 `summarize_validation.py`는 이전 3개 Feature 검증 파일을 읽는 기록용 스크립트다. 최신 비교는 이 문서와 `accident_model_comparison.json`을 참조한다.
