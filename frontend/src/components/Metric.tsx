import { pnlClass } from "@/lib/format";

/** 指标小卡：标签 + 大数字，终端密度。 */
export default function Metric({
  label, value, tone, hint,
}: {
  label: string;
  value: string;
  tone?: "up" | "down" | "plain";
  hint?: string;
}) {
  return (
    <div className="border border-border bg-card px-3 py-2.5">
      <div className="section-label">{label}</div>
      <div
        className={`num mt-1 text-lg font-medium leading-none ${
          tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-foreground"
        }`}
      >
        {value}
      </div>
      {hint && <div className="mt-1 text-[10px] text-muted-foreground">{hint}</div>}
    </div>
  );
}

export { pnlClass };
