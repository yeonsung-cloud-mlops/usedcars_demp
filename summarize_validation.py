"""Create a readable report and diagnostic chart from the saved 1,000-row evaluation."""
from pathlib import Path
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

OUT = Path(__file__).resolve().parent / "artifacts" / "validation_1000"
r = json.loads((OUT / "metrics.json").read_text())
d = pd.read_csv(OUT / "predictions_1000.csv")
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
fig, axes = plt.subplots(1, 2, figsize=(12, 4.7), layout="constrained")
axes[0].scatter(d.actual_price, d.predicted_price, s=12, alpha=.4, color="#2867A3", edgecolors="none")
limit = max(d.actual_price.max(), d.predicted_price.max()) * 1.05
axes[0].plot([0,limit], [0,limit], color="#D66837", linestyle="--", label="Perfect prediction")
axes[0].set(xlim=(0,limit), ylim=(0,limit), xlabel="Actual price (CSV units)", ylabel="Predicted price (CSV units)", title="Actual vs. predicted: 1,000 held-out cars")
axes[0].legend(frameon=False)
axes[0].ticklabel_format(style="plain")
axes[1].hist(d.absolute_percentage_error, bins=range(0, 141, 5), color="#2867A3", edgecolor="white")
axes[1].axvline(20, color="#D66837", linestyle="--", label="20% error threshold")
axes[1].set(xlabel="Absolute percentage error (%)", ylabel="Number of cars", title="744 / 1,000 predictions within 20%")
axes[1].legend(frameon=False)
for ax in axes:
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=.15)
fig.savefig(OUT / "validation_plot.png", dpi=170)
plt.close(fig)

lines = [
    "# 1,000건 예측 검증 결과", "",
    "기존 모델을 재학습하지 않고, 학습에서 제외했던 평가 데이터 312,292건 중 1,000건을 무작위 추출했다. 난수 시드는 42다. 원본 CSV에 제조사·모델·연식·주행거리·가격 값이 존재함을 확인했다. 차령과 VIN·등록일은 기존 원본 대조 전처리 결과를 사용했다.", "",
    "평가 데이터는 2020-09-01~2020-09-12 등록 매물이다. 학습 데이터와 VIN 중복은 0건이다. 입력 조건이나 오차를 보고 표본을 골라내지 않았으며, 추출한 1,000건 모두 포함했다.", "",
    "## 판단", "",
    "모델이 가격과 입력의 관계를 학습했고, 별도 평가 표본에서도 기준 모델보다 낮은 오차를 보였다. 다만 정밀한 개별 매물 가격 산정에는 오차가 크고, 전체적으로 저평가하는 경향이 있어 보완이 필요하다. 이 표본은 기존 평가 집합의 일부이므로 새로운 외부 데이터 검증은 아니다.", "",
    "| 지표 | 결과 |", "|---|---:|",
    f"| MAE | {r['model']['MAE']:,.2f} |",
    f"| RMSE | {r['model']['RMSE']:,.2f} |",
    f"| MAPE | {r['model']['MAPE_percent']:.2f}% |",
    f"| 절대 백분율 오차 중앙값 | {r['median_absolute_percentage_error']:.2f}% |",
    f"| R² | {r['model']['R2']:.4f} |",
    f"| 제조사·모델별 중앙값 기준 MAE | {r['make_model_median_baseline']['MAE']:,.2f} |",
    f"| 기준 대비 MAE 감소 | {r['mae_improvement_percent']:.2f}% |",
    "| 오차율 10% 이내 | 454건 / 45.4% |",
    "| 오차율 20% 이내 | 744건 / 74.4% |",
    "| 오차율 30% 이내 | 900건 / 90.0% |",
    "| 오차율 50% 초과 | 19건 / 1.9% |", "",
    "가격 수치는 원본 CSV 단위다. 오차율은 `abs(예측값 - 실제값) / 실제값 × 100`이다. R²는 분류 정확도가 아니며 MAPE는 개별 예측의 오차 보장 범위가 아니다.", "",
    "## 학습 및 실행 확인", "",
    "- 저장된 모델에는 250개 트리가 있다. 내부 학습 로그가격 손실은 0.1818에서 0.02155로, 내부 검증 손실은 0.1809에서 0.02161로 감소했다. 두 손실이 비슷하지만 이것만으로 과적합이 없다고 확정할 수는 없다.",
    "- 별도 평가 1,000건의 MAPE 14.50%는 기존 전체 평가의 14.59%와 유사하다.",
    "- Python 예측 인터페이스로 1,000건을 각각 호출했으며 실패는 0건이다. 일괄 예측과 개별 예측값도 반올림 오차 이내에서 일치했다.",
    "- 내보낸 CSV를 다시 읽어 1,000행과 MAE를 재검증했다.", "",
    "## 발견된 한계", "",
    f"평균 부호 오차(예측−실제)는 {r['mean_signed_error']:,.2f}로, 이 표본에서는 평균적으로 낮게 예측했다. 로그가격 학습의 영향일 수 있으나 이번 검증만으로 원인을 단정할 수 없다.", "",
    "차령 11~20년의 90건은 MAPE 23.53%로, 차령 0~2년(12.82%)과 3~5년(12.91%)보다 상대 오차가 컸다. 21년 이상은 3건뿐이어서 성능을 일반화할 수 없다. 제조사별 집계도 차종 구성과 표본 크기의 영향을 받는다.", "",
    "절대 오차가 큰 5건:", "",
    "| 제조사·모델 | 차령 | 주행거리 | 실제 가격 | 예측 가격 | 오차율 |", "|---|---:|---:|---:|---:|---:|",
]
for e in r["largest_absolute_errors"]:
    lines.append(f"| {e['make_name']} {e['model_name']} | {e['vehicle_age']} | {e['mileage']:,.0f} | {e['actual_price']:,.0f} | {e['predicted_price']:,.0f} | {e['absolute_percentage_error']:.1f}% |")
lines += ["", "세 가지 입력만으로는 트림·옵션·사고·차량 상태의 차이를 구별할 수 없다. 이 정보의 추가 효과는 별도 검증이 필요하며, 위 사례의 오차 원인이 해당 변수 때문이라고 확정한 것은 아니다. 현재 모델은 2020년까지의 매물 가격 기준이다.", "",
          "## 결과 파일", "", "- `predictions_1000.csv`: 추출한 1,000건, 실제값·예측값·기준값·오차·VIN·등록일",
          "- `metrics.json`: 검증 방법, 수치, 차령·제조사별 결과, 큰 오차 사례, 모델 해시",
          "- `validation_plot.png`: 실제값 대 예측값 및 오차율 분포", "",
          "재현: 프로젝트 디렉터리에서 `OMP_NUM_THREADS=4 python3 validate_1000.py` 실행 후 `python3 summarize_validation.py`를 실행한다.", ""]
(OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
print(OUT / "report.md")
