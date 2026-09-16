"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { api, ApiError, Metadata, Prediction, warningText } from "@/lib/api";

const number = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 });
const display = (name: string) => name.replace(/\b[a-z]/g, (letter) => letter.toUpperCase());
const accidentLabels: Record<string, string> = { False: "사고 이력 없음", True: "사고 이력 있음", Unknown: "사고 이력 모름" };

export default function Home() {
  const [metadata, setMetadata] = useState<Metadata | null>(null);
  const [metaError, setMetaError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [make, setMake] = useState("");
  const [model, setModel] = useState("");
  const [age, setAge] = useState("");
  const [mileage, setMileage] = useState("");
  const [accidents, setAccidents] = useState("unknown");
  const [result, setResult] = useState<Prediction | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [errorField, setErrorField] = useState<string>();
  const request = useRef<AbortController | null>(null);
  const generation = useRef(0);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    setMetaError("");
    api<Metadata>("/api/v1/metadata", { signal: controller.signal }).then((value) => { if (active) { setMetadata(value); setMetaError(""); } })
      .catch(() => { if (active) setMetaError("차종 정보를 불러오지 못했습니다. 연결 상태를 확인하고 다시 시도해 주세요."); })
      .finally(() => clearTimeout(timeout));
    return () => { active = false; clearTimeout(timeout); controller.abort(); };
  }, [attempt]);
  useEffect(() => () => request.current?.abort(), []);

  const models = metadata?.makes.find((item) => item.name === make)?.models ?? [];
  const supportsAccidents = metadata?.features.includes("has_accidents");
  const ready = Boolean(metadata && make && model && age !== "" && mileage !== "");
  function changed() {
    generation.current += 1;
    request.current?.abort();
    setLoading(false); setResult(null); setError(""); setErrorField(undefined);
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ready || loading) return;
    changed();
    const id = generation.current;
    const controller = new AbortController();
    request.current = controller;
    const timeout = setTimeout(() => controller.abort(), 12000);
    setLoading(true);
    try {
      const prediction = await api<Prediction>("/api/v1/predict", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
        body: JSON.stringify({ make_name: make, model_name: model, vehicle_age: Number(age), mileage: Number(mileage), has_accidents: accidents }),
      });
      if (generation.current === id) setResult(prediction);
    } catch (failure) {
      if (generation.current === id) {
        setError(failure instanceof ApiError ? failure.message : controller.signal.aborted
          ? "응답 시간이 길어지고 있습니다. 잠시 후 다시 시도해 주세요." : "연결이 끊어졌습니다. 다시 시도해 주세요.");
        setErrorField(failure instanceof ApiError ? failure.field : undefined);
      }
    } finally {
      clearTimeout(timeout);
      if (generation.current === id) setLoading(false);
    }
  }
  const invalid = (field: string) => ({ "aria-invalid": errorField === field, "aria-describedby": errorField === field ? "form-error" : undefined });

  return <div className="min-h-screen">
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5 sm:px-10">
        <span className="text-base font-bold tracking-tight">AUTO<span className="text-blue-600"> / </span>ESTIMATE</span>
        <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">과거 매물 기반</span>
      </div>
    </header>
    <main className="mx-auto max-w-6xl px-6 py-10 sm:px-10 sm:py-14">
      <div className="mb-8">
        <p className="mb-3 text-sm font-semibold tracking-wider text-blue-700">차량 가격 알아보기</p>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">중고차 가격 예측</h1>
        <p className="mt-4 text-base leading-7 text-slate-600">차량 정보를 입력하면 학습 데이터에 기반한 예상 가격을 확인할 수 있습니다.</p>
      </div>
      <div className="grid items-start gap-6 lg:grid-cols-[1.12fr_1fr]">
        <section className="rounded-2xl border border-slate-200 bg-white p-6 sm:p-8" aria-labelledby="form-title">
          <div className="mb-7 flex items-center gap-3"><span className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-50 text-sm font-semibold text-blue-700">01</span><h2 id="form-title" className="text-lg font-bold">차량 정보</h2></div>
          {metaError ? <div role="alert" className="mb-5 rounded-lg bg-red-50 p-4 text-sm leading-6 text-red-800">{metaError}<button type="button" className="mt-2 block font-semibold underline" onClick={() => setAttempt((value) => value + 1)}>다시 불러오기</button></div>
            : !metadata ? <p role="status" className="mb-5 text-sm text-slate-500">지원 차종을 불러오는 중입니다…</p> : null}
          <form onSubmit={submit}>
            <fieldset disabled={!metadata} className="space-y-5">
              <div className="grid gap-5 sm:grid-cols-2">
                <div><label htmlFor="make_name" className="mb-2 block text-sm font-semibold">제조사</label><select id="make_name" className="control" required value={make} {...invalid("make_name")} onChange={(event) => { changed(); setMake(event.target.value); setModel(""); }}><option value="">제조사 선택</option>{metadata?.makes.map((item) => <option key={item.name} value={item.name}>{display(item.name)}</option>)}</select></div>
                <div><label htmlFor="model_name" className="mb-2 block text-sm font-semibold">모델</label><select id="model_name" className="control" required disabled={!make} value={model} {...invalid("model_name")} onChange={(event) => { changed(); setModel(event.target.value); }}><option value="">모델 선택</option>{models.map((item) => <option key={item.name} value={item.name}>{display(item.name)}</option>)}</select></div>
              </div>
              <div><label htmlFor="vehicle_age" className="mb-2 block text-sm font-semibold">차령 <span className="font-normal text-slate-500">(년)</span></label><input id="vehicle_age" className="control" type="number" inputMode="numeric" required step="1" min={metadata?.input_ranges.vehicle_age[0]} max={metadata?.input_ranges.vehicle_age[1]} placeholder="예: 5" value={age} {...invalid("vehicle_age")} onChange={(event) => { changed(); setAge(event.target.value); }} /><p className="mt-2 text-sm leading-5 text-slate-500">매물 등록 연도에서 차량 연식을 뺀 값{metadata && ` · ${metadata.input_ranges.vehicle_age.join("–")}년`}</p></div>
              <div><label htmlFor="mileage" className="mb-2 block text-sm font-semibold">주행거리 <span className="font-normal text-slate-500">(원본 데이터 단위)</span></label><input id="mileage" className="control" type="number" inputMode="decimal" required step="any" min={metadata?.input_ranges.mileage[0]} max={metadata?.input_ranges.mileage[1]} placeholder="예: 60000" value={mileage} {...invalid("mileage")} onChange={(event) => { changed(); setMileage(event.target.value); }} /><p className="mt-2 text-sm text-slate-500">{metadata ? `${number.format(metadata.input_ranges.mileage[0])}–${number.format(metadata.input_ranges.mileage[1])} 입력 가능` : "지원 범위를 확인하는 중입니다."}</p></div>
              {supportsAccidents && <fieldset><legend className="mb-2 text-sm font-semibold">사고 이력</legend><div className="grid grid-cols-3 gap-2">{[["false", "없음"], ["true", "있음"], ["unknown", "모름"]].map(([value, label]) => <label key={value} className={`flex cursor-pointer items-center justify-center gap-2 rounded-lg border px-2 py-3 text-sm ${accidents === value ? "border-blue-600 bg-blue-50 font-semibold text-blue-800" : "border-slate-200 text-slate-600"}`}><input type="radio" name="has_accidents" value={value} checked={accidents === value} onChange={() => { changed(); setAccidents(value); }} className="accent-blue-600" />{label}</label>)}</div></fieldset>}
              {error && <p id="form-error" role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm leading-6 text-red-800">{error}</p>}
              <button type="submit" disabled={!ready || loading} className="mt-2 flex min-h-13 w-full items-center justify-center gap-3 rounded-xl bg-blue-600 px-5 py-4 font-semibold text-white transition hover:bg-blue-700 disabled:bg-slate-200 disabled:text-slate-500">{loading ? "가격을 계산하고 있습니다…" : "가격 예측하기"}{!loading && <span aria-hidden="true">→</span>}</button>
            </fieldset>
          </form>
        </section>
        <div className="space-y-5">
          <section className="overflow-hidden rounded-2xl bg-[#15233e] text-white" aria-labelledby="result-title" aria-live="polite" aria-busy={loading}>
            <div className="px-6 pt-7 sm:px-8"><div className="flex items-center gap-3"><span className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/10 text-sm font-semibold text-blue-200">02</span><h2 id="result-title" className="text-lg font-bold">예측 결과</h2></div></div>
            {result ? <div className="px-6 pb-7 pt-8 sm:px-8">
              <p className="text-sm text-blue-200">예상 매물 가격</p><p className="mt-3 break-words text-4xl font-bold tracking-tight sm:text-5xl">{number.format(result.predicted_price)}</p><p className="mt-2 text-sm text-slate-300">원본 가격 단위 · 통화 미확정</p>
              <div className="mt-8 border-t border-white/15 pt-5"><p className="text-lg font-semibold">{display(result.make_name)} {display(result.model_name)}</p><p className="mt-2 text-sm leading-6 text-slate-300">차령 {result.vehicle_age}년 · 주행거리 {number.format(result.mileage)}<br />{supportsAccidents && accidentLabels[result.has_accidents]}</p><dl className="mt-5 flex justify-between gap-4 text-sm"><dt className="text-slate-300">해당 차종 학습 표본</dt><dd>{number.format(result.training_examples_for_model)}건</dd></dl></div>
            </div> : <div className="flex min-h-72 flex-col items-center justify-center px-8 py-12 text-center"><div aria-hidden="true" className="mb-5 flex h-14 w-14 items-center justify-center rounded-full border border-white/20 text-2xl text-blue-200">{loading ? "…" : "₋"}</div><p className="text-lg font-semibold">{loading ? "차량 조건을 살펴보고 있습니다" : "차량의 예상 가격을 확인하세요"}</p><p className="mt-3 text-sm leading-6 text-slate-300">{loading ? "잠시만 기다려 주세요." : "차량 정보를 입력하고\n가격 예측하기를 눌러 주세요."}</p></div>}
            <div className="border-t border-white/10 bg-white/5 px-6 py-4 text-sm leading-6 text-slate-300 sm:px-8">현재 거래 시세가 아닌, 과거 매물 가격 기준 추정입니다.</div>
          </section>
          {result && result.warnings.filter((warning) => !warning.startsWith("Historical listing-price")).length > 0 && <div className="rounded-xl border border-amber-200 bg-amber-50 p-5"><p className="mb-2 text-sm font-semibold text-amber-900">이 예측을 해석할 때</p><ul className="list-disc space-y-2 pl-4 text-sm leading-6 text-amber-900">{result.warnings.filter((warning) => !warning.startsWith("Historical listing-price")).map((warning) => <li key={warning}>{warningText(warning)}</li>)}</ul></div>}
          <aside className="px-1 py-1"><h3 className="text-sm font-semibold text-slate-700">데이터 안내</h3><p className="mt-2 text-sm leading-6 text-slate-500">{metadata ? `${metadata.train_date_range[0]}부터 ${metadata.train_date_range[1]}까지 등록된 매물로 학습했습니다. ` : "과거에 등록된 매물로 학습한 모델입니다. "}가격과 주행거리는 원본 데이터 단위이며, 원화 또는 km로 환산되지 않았습니다.</p></aside>
        </div>
      </div>
      <footer className="mt-10 flex flex-wrap justify-between gap-3 border-t border-slate-200 pt-5 text-xs leading-5 text-slate-500"><span>예측값은 참고용이며 실제 매매 가격을 보장하지 않습니다.</span>{metadata && <span>모델 {metadata.model_version.slice(0, 8)}</span>}</footer>
    </main>
  </div>;
}
