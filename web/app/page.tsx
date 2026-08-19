import type { Metadata } from "next";
import { AttributionWorkbench } from "./attribution-workbench";

export const metadata: Metadata = {
  title: "策略归因工作台",
  description: "读取版本化归因产物，分层查看模块干预、风险情景与审计证据。",
};

export default function Home() {
  return <AttributionWorkbench />;
}
