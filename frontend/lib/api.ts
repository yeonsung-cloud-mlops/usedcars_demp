export type CarModel = { name: string; count: number; vehicle_age_min: number; vehicle_age_max: number; mileage_min: number; mileage_max: number };
export type Metadata = {
  model_version: string; features: string[]; units_verified: boolean;
  price_unit: string; mileage_unit: string;
  input_ranges: { vehicle_age: [number, number]; mileage: [number, number] };
  train_date_range: [string, string]; test_date_range: [string, string];
  makes: { name: string; models: CarModel[] }[];
};
export type Prediction = {
  predicted_price: number; make_name: string; model_name: string;
  vehicle_age: number; mileage: number; has_accidents: string;
  training_examples_for_model: number; model_version: string; warnings: string[];
};
export class ApiError extends Error {
  constructor(message: string, public field?: string) { super(message); }
}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...options, cache: "no-store" });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const fallback = response.status === 429 ? "요청이 많습니다. 잠시 후 다시 시도해 주세요."
      : response.status === 503 ? "예측 서비스를 준비 중입니다. 잠시 후 다시 시도해 주세요."
      : "서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.";
    throw new ApiError(body?.error?.message ?? fallback, body?.error?.field);
  }
  if (!body) throw new ApiError("서버 응답을 읽지 못했습니다. 다시 시도해 주세요.");
  return body as T;
}
export function warningText(warning: string): string {
  if (warning.startsWith("Historical listing-price")) return "과거 매물 가격 기준 추정으로, 현재 거래 시세와 다를 수 있습니다.";
  if (warning.startsWith("Accident history unknown:")) return "사고 이력 ‘모름’의 학습 표본이 적어 예측의 불확실성이 큽니다.";
  if (warning.startsWith("Fewer than 100")) return "이 차종은 학습 표본이 100건 미만입니다.";
  if (warning.startsWith("vehicle_age is outside")) return "입력한 차령이 이 차종에서 관측된 범위를 벗어납니다.";
  if (warning.startsWith("mileage is outside")) return "입력한 주행거리가 이 차종에서 관측된 범위를 벗어납니다.";
  return warning;
}
