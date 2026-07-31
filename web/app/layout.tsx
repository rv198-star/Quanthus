import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Quanthus 策略归因工作台",
  description: "读取版本化归因产物，分层查看模块干预、风险情景与审计证据。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
