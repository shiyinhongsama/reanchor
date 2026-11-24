import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router";
import { Activity, FlaskConical, GitCompareArrows, LogOut } from "lucide-react";
import { api, auth, type LiveStatus } from "@/lib/api";
import BacktestPage from "@/pages/BacktestPage";
import ComparePage from "@/pages/ComparePage";
import ExecutorsPage from "@/pages/ExecutorsPage";
import LoginPage from "@/pages/LoginPage";
import { Toaster } from "@/components/ui/sonner";

function useUtcClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  const p = (x: number) => String(x).padStart(2, "0");
  return `${now.getUTCFullYear()}-${p(now.getUTCMonth() + 1)}-${p(now.getUTCDate())} ${p(now.getUTCHours())}:${p(now.getUTCMinutes())}:${p(now.getUTCSeconds())} UTC`;
}

function Header() {
  const clock = useUtcClock();
  const [alive, setAlive] = useState<boolean | null>(null);
  const [live, setLive] = useState<LiveStatus | null>(null);
  const navigate = useNavigate();

  const logout = () => {
    auth.clear();
    navigate("/login", { replace: true });
  };

  useEffect(() => {
    let stop = false;
    const poll = async () => {
      const ok = await api.health();
      if (stop) return;
      setAlive(ok);
      if (ok) {
        try {
          setLive(await api.liveStatus());
        } catch { /* 状态接口失败不阻塞 */ }
      }
    };
    poll();
    const id = setInterval(poll, 10_000);
    return () => { stop = true; clearInterval(id); };
  }, []);

  const tab = ({ isActive }: { isActive: boolean }) =>
    `px-4 py-2 text-xs tracking-[0.15em] transition-colors border-b-2 cursor-pointer ${
      isActive
        ? "border-primary text-primary"
        : "border-transparent text-muted-foreground hover:text-foreground"
    }`;

  return (
    <header className="flex h-12 items-center border-b border-border bg-card px-4">
      <div className="flex items-center gap-2.5">
        <span className={alive ? "led-live" : "led-off"} />
        <span className="text-sm font-bold tracking-[0.2em]">
          RE<span className="text-primary">·</span>ANCHOR
        </span>
      </div>

      <nav className="ml-10 flex items-center gap-1 self-stretch">
        <NavLink to="/backtest" className={tab}>
          <span className="inline-flex items-center gap-1.5"><FlaskConical size={13} />回测工作台</span>
        </NavLink>
        <NavLink to="/executors" className={tab}>
          <span className="inline-flex items-center gap-1.5"><Activity size={13} />执行器</span>
        </NavLink>
        <NavLink to="/compare" className={tab}>
          <span className="inline-flex items-center gap-1.5"><GitCompareArrows size={13} />对比</span>
        </NavLink>
      </nav>

      <div className="ml-auto flex items-center gap-5 text-[11px] text-muted-foreground">
        {live && (
          <span className="flex items-center gap-1.5">
            <span className={live.enabled ? "led-warn" : "led-off"} />
            {live.enabled ? "实盘已启用" : "DRY-RUN"}
            <span className="text-foreground">{live.running_executors}</span> 运行中
          </span>
        )}
        <span className="num text-muted-foreground">{clock}</span>
        <button
          className="flex items-center gap-1 text-muted-foreground transition-colors hover:text-down"
          onClick={logout}
          title="退出登录"
        >
          <LogOut size={13} />
        </button>
      </div>
    </header>
  );
}

export default function App() {
  const { pathname } = useLocation();

  // 登录页独立全屏渲染，不带工作台框架
  if (pathname === "/login") {
    return (
      <>
        <LoginPage />
        <Toaster theme="dark" position="bottom-right" />
      </>
    );
  }
  // 路由守卫：无令牌一律去登录
  if (!auth.token()) {
    return <Navigate to="/login" replace />;
  }

  return (
    <div className="flex min-h-dvh flex-col bg-background lg:h-dvh lg:overflow-hidden">
      <Header />
      <main className="flex min-h-0 flex-1 flex-col">
        <Routes>
          <Route path="/" element={<Navigate to="/backtest" replace />} />
          <Route path="/backtest" element={<BacktestPage />} />
          <Route path="/executors" element={<ExecutorsPage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="*" element={<Navigate to="/backtest" replace />} />
        </Routes>
      </main>
      <Toaster theme="dark" position="bottom-right" />
    </div>
  );
}
