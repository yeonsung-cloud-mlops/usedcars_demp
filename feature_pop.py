import pandas as pd

INPUT_FILE = "used_cars_data.csv"
OUTPUT_FILE = "used_cars_price_prediction.csv"

FEATURE_COLUMNS = [
    "make_name",
    "model_name",
    "trim_name",
    "year",
    "body_type",
    "mileage",
    "owner_count",
    "has_accidents",
    "frame_damaged",
    "salvage",
    "theft_title",
    "fuel_type",
    "transmission",
    "wheel_system",
    "engine_cylinders",
    "engine_displacement",
    "horsepower",
    "maximum_seating",
    "listing_color",
    "city",
]

TARGET_COLUMN = "price"

# 데이터 로드
df = pd.read_csv(
    INPUT_FILE,
    usecols=FEATURE_COLUMNS + [TARGET_COLUMN]
)

print("Original shape:", df.shape)

# --------------------------------------------------
# 1. 중고차만 남기기 위한 기본 가격/연식/주행거리 검증
# --------------------------------------------------

df = df[
    (df["price"] > 0) &
    (df["year"].notna()) &
    (df["mileage"].notna())
].copy()

# --------------------------------------------------
# 2. 명백한 이상치 제거
#    정확한 기준은 EDA 후 다시 조정하는 것을 권장
# --------------------------------------------------

df = df[
    (df["price"] >= 1000) &
    (df["price"] <= 200000) &
    (df["mileage"] >= 0) &
    (df["mileage"] <= 300000) &
    (df["year"] >= 1990) &
    (df["year"] <= 2020)
].copy()

# --------------------------------------------------
# 3. 핵심 차량 정보가 없는 데이터 제거
# --------------------------------------------------

required_columns = [
    "make_name",
    "model_name",
    "year",
    "mileage",
    "price"
]

df = df.dropna(subset=required_columns)

# --------------------------------------------------
# 4. 중복 데이터 제거
# --------------------------------------------------

df = df.drop_duplicates()

# --------------------------------------------------
# 5. 저장
# --------------------------------------------------

df.to_csv(
    OUTPUT_FILE,
    index=False
)

print("Cleaned shape:", df.shape)
print(f"Saved: {OUTPUT_FILE}")
