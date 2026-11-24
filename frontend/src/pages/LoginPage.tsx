import { useState } from "react";
import { useNavigate } from "react-router";
import { KeyRound } from "lucide-react";
import { api, auth } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export default function LoginPage() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const submit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError("");
    try {
      const r = await api.login(password);
      auth.set(r.token);
      navigate("/backtest", { replace: true });
    } catch (err) {
      setError(err instanceof Error && err.message.includes("密码") ? "密码错误" : "无法连接后端");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-dvh items-center justify-center bg-background">
      {/* 背景网格装饰 */}
      <div className="pointer-events-none absolute inset-0 opacity-[0.04]"
           style={{
             backgroundImage:
               "linear-gradient(#22d3ee 1px, transparent 1px), linear-gradient(90deg, #22d3ee 1px, transparent 1px)",
             backgroundSize: "48px 48px",
           }} />

      <form onSubmit={submit}
            className="relative w-[340px] rounded-md border border-border bg-card p-8 shadow-2xl">
        <div className="mb-8 text-center">
          <div className="text-xl font-bold tracking-[0.25em]">
            RE<span className="text-primary">·</span>ANCHOR
          </div>
          <div className="mt-2 text-[11px] tracking-[0.3em] text-muted-foreground">
            SYSTEM ACCESS
          </div>
        </div>

        <div className="relative">
          <KeyRound size={14}
                    className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="password"
            autoFocus
            placeholder="访问密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={`h-10 bg-background pl-9 text-sm ${error ? "border-down" : ""}`}
          />
        </div>
        <div className="mt-2 h-4 text-[11px] text-down">{error}</div>

        <Button type="submit" className="mt-2 h-10 w-full text-sm tracking-[0.2em]"
                disabled={!password || busy}>
          {busy ? "验证中…" : "进 入"}
        </Button>

        <div className="mt-6 text-center text-[10px] leading-relaxed text-muted-foreground">
          单用户模式 · 密码经 TP_ADMIN_PASSWORD 配置
        </div>
      </form>
    </div>
  );
}
