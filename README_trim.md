# 트림 추가 모델 검증

현재 기본 모델은 제조사+모델, 차령, 주행거리, 사고 이력, 제조사+모델+트림을 사용한다. `artifacts/price_model.joblib`에 저장했으며, 이전 사고 이력 모델은 `artifacts/baseline_4features/`에 보관했다.

## 구성

- 기존 원본 대조 전처리를 다시 실행해 `trim_name`을 보존했다. 기존과 같은 1,189,825행으로 학습하고 동일한 312,292행으로 평가했다. 학습·평가 VIN 중복은 없다.
- 모델명만 사용하는 기존 Feature를 유지하면서 제조사+모델+트림을 추가했다. 이름은 대소문자·공백을 정규화하고, 결측 트림은 별도 범주로 보존한다.
- 학습에서 제조사+모델+트림 조합은 13,209종, 트림 결측은 28,052행이다. 문자열을 단순 숫자 순서로 바꾸지 않고 교차 검증 방식의 TargetEncoder를 적용했다. 평가 정답은 인코딩에 사용하지 않았다.
- 트리 설정과 로그가격 학습 방식은 기존 모델과 같다. 사고 여부는 순서 없는 범주형으로 유지했다.
- 학습에 없는 트림 조합은 기존 사고 이력 모델로 예측한다. 전체 평가의 460행이 이에 해당하며, 아래 성능은 이 처리까지 포함한다. 결측 조합이 학습에 있었다면 그 결측 범주로 예측하며, 해당 조합도 처음이면 기존 모델로 예측한다.

## 동일 데이터 비교

| 평가 대상 | 기존 MAE | 트림 추가 MAE | MAE 감소 | 기존 MAPE | 트림 추가 MAPE |
|---|---:|---:|---:|---:|---:|
| 전체 312,292건 | 3,392.38 | 2,475.91 | 27.02% | 14.58% | 11.39% |
| 이전과 같은 1,000건 | 3,461.21 | 2,532.69 | 26.83% | 14.52% | 11.46% |
| 이전과 같은 추가 100건 | 2,646.91 | 2,289.28 | 13.51% | 12.75% | 11.33% |

전체 R²는 0.8174에서 0.9019로 높아졌다. 실제 가격 대비 오차 20% 이내는 1,000건에서 863건, 100건에서 88건이다. MAE는 원본 CSV 가격 단위이며, MAPE는 개별 예측의 오차 보장 범위가 아니다.

**이번 동일 데이터 비교에서는 트림 추가가 일관되게 오차를 줄였다.** 표본을 다시 뽑지 않고 이전 CSV의 행 식별자로 선택했으며, VIN·실제 가격·이전 모델 예측이 기존 결과와 일치함을 검증했다. 이는 이미 살펴본 평가 집합의 재비교이고 새로운 외부 검증은 아니다. 1,000건과 100건은 전체 평가 집합의 일부이며 별개의 세 독립 실험은 아니다.

1,100건 모두 Python 예측 인터페이스로도 호출해 저장된 모델의 일괄 예측과 일치함을 확인했다. 출력 CSV의 행 수와 MAE를 다시 읽어 검증했다. 저장 모델 재현, 입력 검사, 5개 Feature 계약, 사고 범주, 미학습 트림의 이전 모델 사용 등 6개 테스트를 통과했다.

## 사용

```bash
python3 predict.py --make Toyota --model Camry --vehicle-age 5 --mileage 60000 --has-accidents false --trim "LE"
```

트림 문자열은 데이터의 표기와 맞춰 입력해야 한다. 미학습 조합이라면 결과의 `warnings`에서 이전 모델 사용 여부를 확인할 수 있다.

```python
from price_model import PricePredictor
predictor = PricePredictor()
result = predictor.predict(
    "Toyota", "Camry", 5, 60000,
    has_accidents=False, trim_name="LE",
)
```

트림을 생략하면 미상으로 처리한다. 사고 미상 학습 표본은 여전히 29건으로 적으며 안내를 반환한다. 모델은 2020년까지의 매물 가격 기준이며, 현재 시장 가격이나 실거래 가격에 대한 성능은 확인하지 않았다.

## 재현과 결과 파일

```bash
python3 prepare_data.py
OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 python3 train_model.py
OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 python3 validate_trim_model.py
OMP_NUM_THREADS=4 python3 -m unittest -v test_price_model.py
```

이전 모델 파일과 기존 1,000건·100건 CSV는 비교와 미학습 트림 처리에 필요하다. 학습된 새 모델에는 이전 예측기를 함께 저장하므로, 예측 시에는 새 `price_model.joblib` 하나로 동작한다.

- `artifacts/validation_trim/comparison.json`: 전체·1,000건·100건 비교 수치
- `artifacts/validation_trim/predictions_1000.csv`: 트림, 실제값, 이전 예측, 새 예측, 오차
- `artifacts/validation_trim/predictions_100.csv`: 추가 100건의 동일 비교
- `artifacts/metrics.json`: 트림 모델 학습 및 평가 정보
- `validate_trim_model.py`: 비교 재현 코드

이전 `validate_1000.py`, `validate_extra_100.py`, `compare_accident_model.py`는 4개 Feature 보관 모델을 사용하도록 고정했다. 최신 트림 모델 비교에는 `validate_trim_model.py`를 사용한다.
