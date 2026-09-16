import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "중고차 가격 예측",
  description: "차종, 차령, 주행거리와 사고 이력으로 과거 매물 기준 가격을 추정합니다.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ko"><body>{children}</body></html>;
}
