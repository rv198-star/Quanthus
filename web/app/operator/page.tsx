import type { Metadata } from "next";
import { OperatorConsole } from "../operator-console";

export const metadata: Metadata = {
  title: "平台控制台",
};

export default function OperatorPage() {
  return <OperatorConsole />;
}
