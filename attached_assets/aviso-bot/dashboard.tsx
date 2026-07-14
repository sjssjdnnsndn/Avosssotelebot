import { useState, useEffect } from "react";
import { formatDistanceToNow } from "date-fns";
import { Activity, Clock, Zap, Target, DollarSign, Database, Server, RefreshCw } from "lucide-react";
import { useGetAvisoStatus, getGetAvisoStatusQueryKey } from "@workspace/api-client-react";
import type { AvisoStatus } from "@workspace/api-client-react/src/generated/api.schemas";

type BotStatus = "working" | "sleep" | "offline" | "waiting" | "stale";

export default function Dashboard() {
  const { data, isLoading, isError } = useGetAvisoStatus(
    {},
    { query: { refetchInterval: 3000, queryKey: getGetAvisoStatusQueryKey(), retry: 2 } }
  );

  const getDerivedStatus = (botData?: AvisoStatus): BotStatus => {
    if (!botData) return "offline";
    if (botData.lastUpdated) {
      const timeDiff = new Date().getTime() - new Date(botData.lastUpdated).getTime();
      if (timeDiff > 120000) return "offline";
    }
    if (botData.status === "working") return "working";
    if (botData.status?.toLowerCase().includes("sleep")) return "sleep";
    return "waiting";
  };

  const status = isError ? (data ? "stale" : "offline") : getDerivedStatus(data);
  const isOffline = status === "offline";

  return (
    <div className="min-h-screen bg-background text-foreground selection:bg-primary/30 font-sans p-4 md:p-8 flex flex-col items-center">
      <div className="fixed inset-0 pointer-events-none opacity-[0.03] z-0"
           style={{ backgroundImage: 'linear-gradient(to right, #ffffff 1px, transparent 1px), linear-gradient(to bottom, #ffffff 1px, transparent 1px)', backgroundSize: '40px 40px' }}
      />
      <div className="w-full max-w-5xl z-10 flex flex-col gap-6">
        <header className="flex flex-col md:flex-row md:items-center justify-between border-b border-border/50 pb-6 gap-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded bg-card border border-border flex items-center justify-center shadow-lg shadow-black/50">
              <Activity className="w-5 h-5 text-primary" />
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-tight text-white/90">Aviso.bz / SYS_CONTROL</h1>
              <div className="text-xs font-mono text-muted-foreground flex items-center gap-2">
                <span>NODE_01</span>
                <span className="w-1 h-1 rounded-full bg-border"></span>
                <span>{new Date().toISOString().split('T')[0]}</span>
              </div>
            </div>
          </div>
          <div className="flex items-center">
            <StatusBadge status={status} />
          </div>
        </header>

        {isOffline && !data ? (
          <OfflineState />
        ) : (
          <main className="grid grid-cols-1 md:grid-cols-12 gap-6">
            <div className="md:col-span-8 flex flex-col gap-6">
              <div className="bg-card border border-border rounded-xl p-6 relative overflow-hidden group">
                <div className="absolute top-0 right-0 w-64 h-64 bg-primary/5 rounded-full blur-3xl -mr-10 -mt-10 pointer-events-none transition-opacity duration-1000 group-hover:bg-primary/10" />
                <div className="flex justify-between items-start mb-2 relative z-10">
                  <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-widest flex items-center gap-2">
                    <DollarSign className="w-4 h-4" />
                    Current Balance
                  </h2>
                  {isLoading && <RefreshCw className="w-4 h-4 text-muted-foreground animate-spin opacity-50" />}
                </div>
                <div className="relative z-10 mt-4 mb-6">
                  <div className="text-5xl md:text-7xl font-bold tracking-tighter text-white/95 font-mono drop-shadow-sm flex items-baseline gap-3">
                    <span className="text-primary">{data?.balanceRaw ? data.balanceRaw.toFixed(2) : "0.00"}</span>
                    <span className="text-xl md:text-3xl text-muted-foreground font-sans">руб</span>
                  </div>
                  <div className="text-sm text-muted-foreground mt-3 font-mono opacity-80">
                    {data?.balance || "Loading balance..."}
                  </div>
                </div>
                <div className="pt-4 border-t border-border/50 flex justify-between items-center text-xs font-mono text-muted-foreground">
                  <div className="flex items-center gap-2">
                    <Server className="w-3.5 h-3.5 opacity-70" />
                    SYNC_STATE: OK
                  </div>
                  <LastUpdatedDisplay lastUpdated={data?.lastUpdated} />
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <StatCard title="Total Surf Tasks" value={data?.totalTasks ?? 0} icon={<Target className="w-4 h-4 text-blue-400" />} />
                <StatCard title="Total YT Tasks" value={data?.totalYtDone ?? 0} icon={<Database className="w-4 h-4 text-red-400" />} />
                <StatCard
                  title="Total Earned"
                  value={`${((data?.totalEarned || 0) + (data?.totalYtEarned || 0)).toFixed(2)} руб`}
                  icon={<Zap className="w-4 h-4 text-amber-400" />}
                  highlight
                />
              </div>
            </div>

            <div className="md:col-span-4 flex flex-col gap-6">
              <div className="bg-card border border-border rounded-xl p-5 flex flex-col gap-4">
                <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1 flex items-center gap-2">
                  <Activity className="w-3.5 h-3.5" />
                  Active Operation
                </h3>
                {data?.sleepUntil && status === "sleep" ? (
                  <div className="bg-black/40 border border-amber-900/30 rounded-lg p-4 flex flex-col items-center justify-center gap-2 h-24">
                    <Clock className="w-5 h-5 text-amber-500 mb-1" />
                    <SleepCountdown sleepUntil={data.sleepUntil} />
                    <div className="text-[10px] uppercase tracking-widest text-muted-foreground">Waking up in</div>
                  </div>
                ) : (
                  <div className="bg-black/40 border border-border/50 rounded-lg p-4 flex flex-col justify-center h-24">
                    {data?.currentTask ? (
                      <div className="flex flex-col">
                        <span className="text-xs text-muted-foreground font-mono mb-1">TASK_ID: {String((data.currentTask as Record<string,unknown>)?.id || 'unknown')}</span>
                        <span className="text-sm font-medium text-white/90 uppercase tracking-wide">
                          {String((data.currentTask as Record<string,unknown>)?.type || 'processing')}
                        </span>
                      </div>
                    ) : (
                      <div className="flex items-center justify-center text-muted-foreground font-mono text-sm gap-2 opacity-50">
                        <span>— IDLE —</span>
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div className="flex-1 bg-black rounded-xl border border-border overflow-hidden flex flex-col h-[300px] md:h-auto">
                <div className="bg-card border-b border-border px-4 py-2 flex items-center justify-between">
                  <div className="text-xs font-mono text-muted-foreground flex items-center gap-2">
                    <div className="w-2 h-2 rounded-full bg-border" />
                    syslog / var/log/aviso.log
                  </div>
                </div>
                <TerminalLog logs={data?.log || []} />
              </div>
            </div>
          </main>
        )}
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: BotStatus }) {
  const config = {
    working: { label: "RUNNING", dotClass: "glow-dot-green", textClass: "text-green-500", borderClass: "border-green-500/20 bg-green-500/5" },
    sleep:   { label: "SLEEPING", dotClass: "glow-dot-amber", textClass: "text-amber-500", borderClass: "border-amber-500/20 bg-amber-500/5" },
    offline: { label: "OFFLINE",  dotClass: "glow-dot-red",   textClass: "text-red-500",   borderClass: "border-red-500/20 bg-red-500/5" },
    waiting: { label: "WAITING FOR TASK", dotClass: "glow-dot-blue", textClass: "text-blue-500", borderClass: "border-blue-500/20 bg-blue-500/5" },
    stale:   { label: "STALE DATA", dotClass: "glow-dot-amber", textClass: "text-orange-400", borderClass: "border-orange-400/20 bg-orange-400/5" },
  };
  const { label, dotClass, textClass, borderClass } = config[status];
  return (
    <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full border ${borderClass} shadow-sm backdrop-blur-sm transition-colors duration-500`}>
      <div className={`w-2 h-2 rounded-full ${dotClass}`} />
      <span className={`text-xs font-bold tracking-wider font-mono ${textClass}`}>{label}</span>
    </div>
  );
}

function StatCard({ title, value, icon, highlight = false }: { title: string; value: string | number; icon: React.ReactNode; highlight?: boolean }) {
  return (
    <div className={`bg-card border ${highlight ? 'border-primary/20 bg-primary/[0.02]' : 'border-border'} rounded-xl p-5 flex flex-col gap-2 relative overflow-hidden`}>
      <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider flex items-center gap-2">
        {icon}
        {title}
      </div>
      <div className={`text-2xl font-bold font-mono ${highlight ? 'text-primary' : 'text-white/90'}`}>{value}</div>
    </div>
  );
}

function LastUpdatedDisplay({ lastUpdated }: { lastUpdated?: string | null }) {
  const [text, setText] = useState("never");
  useEffect(() => {
    if (!lastUpdated) { setText("never"); return; }
    const updateText = () => {
      const date = new Date(lastUpdated);
      const diffSecs = Math.floor((new Date().getTime() - date.getTime()) / 1000);
      if (diffSecs < 5) setText("just now");
      else if (diffSecs < 60) setText(`${diffSecs}s ago`);
      else setText(formatDistanceToNow(date, { addSuffix: true }));
    };
    updateText();
    const interval = setInterval(updateText, 1000);
    return () => clearInterval(interval);
  }, [lastUpdated]);
  return (
    <span className="opacity-60 flex items-center gap-1.5">
      <Clock className="w-3 h-3" />
      UPDATED: {text.toUpperCase()}
    </span>
  );
}

function SleepCountdown({ sleepUntil }: { sleepUntil: string }) {
  const [timeLeft, setTimeLeft] = useState("");
  useEffect(() => {
    const update = () => {
      const diff = new Date(sleepUntil).getTime() - new Date().getTime();
      if (diff <= 0) { setTimeLeft("00:00"); return; }
      const mins = Math.floor(diff / 60000);
      const secs = Math.floor((diff % 60000) / 1000);
      setTimeLeft(`${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`);
    };
    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, [sleepUntil]);
  return <div className="text-2xl font-bold font-mono text-amber-500 tracking-widest">{timeLeft}</div>;
}

function TerminalLog({ logs }: { logs: string[] }) {
  const displayLogs = [...logs].reverse().slice(0, 50);
  return (
    <div className="p-4 font-mono text-xs overflow-y-auto flex-1 text-green-500/80 tracking-tight leading-relaxed select-text space-y-1.5">
      {displayLogs.length === 0 ? (
        <div className="opacity-30 italic">No logs available...</div>
      ) : (
        displayLogs.map((log, i) => (
          <div key={i} className="hover:text-green-400 hover:bg-green-500/5 px-1 -mx-1 rounded transition-colors break-all">
            <span className="opacity-50 mr-2">{'>'}</span>{log}
          </div>
        ))
      )}
    </div>
  );
}

function OfflineState() {
  return (
    <main className="flex-1 flex flex-col items-center justify-center min-h-[60vh]">
      <div className="flex flex-col items-center justify-center p-12 max-w-md w-full bg-card border border-border rounded-2xl relative overflow-hidden">
        <div className="absolute inset-0 bg-red-500/5" />
        <div className="w-20 h-20 rounded-full bg-red-500/10 border border-red-500/20 flex items-center justify-center mb-6 relative">
          <div className="absolute inset-0 rounded-full glow-dot-red opacity-50 blur-xl" />
          <Server className="w-8 h-8 text-red-500 relative z-10 opacity-80" />
        </div>
        <h2 className="text-xl font-bold text-white mb-2 tracking-tight">CONNECTION LOST</h2>
        <p className="text-muted-foreground text-center text-sm font-mono leading-relaxed max-w-[250px]">
          Bot is offline. Waiting for heartbeat signal...
        </p>
        <div className="mt-8 flex gap-1 items-center justify-center">
          <div className="w-1.5 h-1.5 rounded-full bg-red-500 animate-bounce" style={{ animationDelay: "0ms" }} />
          <div className="w-1.5 h-1.5 rounded-full bg-red-500 animate-bounce" style={{ animationDelay: "150ms" }} />
          <div className="w-1.5 h-1.5 rounded-full bg-red-500 animate-bounce" style={{ animationDelay: "300ms" }} />
        </div>
      </div>
    </main>
  );
}
