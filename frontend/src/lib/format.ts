/** 数字格式化：终端风格，等宽对齐友好。 */

export function pct(v: number | null | undefined, digits = 1): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function usd(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  const abs = Math.abs(v);
  const digits = abs >= 1000 ? 0 : abs >= 10 ? 2 : 4;
  return `$${num(v, digits)}`;
}

export function px(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  const abs = Math.abs(v);
  const digits = abs >= 100 ? 1 : abs >= 1 ? 2 : 4;
  return num(v, digits);
}

/** ISO -> "MM-DD HH:mm"（UTC 原始展示，与后端口径一致） */
export function ts(iso: string | null | undefined): string {
  if (!iso) return "—";
  // 后端经 SQLite 回源的时间可能不带时区后缀，平台口径一律按 UTC 解释
  const hasTz = iso.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(iso);
  const d = new Date(hasTz ? iso : iso + "Z");
  if (Number.isNaN(d.getTime())) return iso;
  const p = (x: number) => String(x).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

export function pnlClass(v: number | null | undefined): string {
  if (v == null || v === 0) return "text-muted-foreground";
  return v > 0 ? "text-up" : "text-down";
}
