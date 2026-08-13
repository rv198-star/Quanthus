"use client";

import {
  Activity,
  ArrowLeft,
  BadgeCheck,
  Ban,
  Check,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  ClipboardCheck,
  Copy,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  LogOut,
  Play,
  Plus,
  RefreshCw,
  Server,
  ShieldCheck,
  Square,
  UserRound,
  Users,
  X,
} from "lucide-react";
import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import {
  clearStoredSession,
  type GateFacts,
  type LoginResult,
  loadPublicationCatalog,
  loadStoredSession,
  PlatformApiError,
  type PlatformJob,
  type PlatformPrincipal,
  platformRequest,
  type PlatformRole,
  type PlatformUser,
  type PublicationCatalog,
  storeSession,
} from "./platform-api";
import styles from "./operator-console.module.css";

type View = "status" | "verification" | "users" | "security";

const terminalJobStates = new Set(["succeeded", "failed", "canceled"]);
const roleLabels: Record<PlatformRole, string> = {
  admin: "管理员",
  operator: "操作员",
  viewer: "只读用户",
};
const statusLabels: Record<string, string> = {
  accepted: "已验收",
  blocked: "阻塞",
  canceled: "已取消",
  deferred: "已延后",
  failed: "失败",
  "not-applicable": "不适用",
  open: "进行中",
  passed: "通过",
  pending: "等待中",
  running: "运行中",
  succeeded: "已通过",
};
const errorLabels: Record<string, string> = {
  authentication_required: "请先登录。",
  duplicate_username: "该用户名已存在。",
  internal_error: "服务暂时无法处理请求。",
  invalid_credentials: "用户名或密码不正确。",
  invalid_mfa: "动态验证码无效，请使用当前验证码重试。",
  invalid_or_expired_session: "会话已过期，请重新登录。",
  job_is_terminal: "该任务已经结束，不能取消。",
  login_rate_limited: "失败次数过多，请稍后再试。",
  mfa_already_enabled: "多因素认证已经启用。",
  mfa_enrollment_missing: "请先生成新的 MFA 配置。",
  permission_denied: "当前角色没有执行此操作的权限。",
  request_failed: "请求失败，请稍后重试。",
  "password must contain letters and digits": "密码必须同时包含字母和数字。",
  "password must be 12-256 characters": "密码长度必须为 12 至 256 个字符。",
};

function friendlyError(error: unknown) {
  if (error instanceof PlatformApiError) {
    const message = errorLabels[error.reason] ?? error.reason;
    return error.requestId ? `${message} 请求编号 ${error.requestId}` : message;
  }
  if (error instanceof Error && error.message === "release_catalog_unavailable") {
    return "归因发布目录暂时不可读取。";
  }
  if (error instanceof Error && error.message === "release_catalog_invalid") {
    return "归因发布目录格式无效。";
  }
  return "网络连接失败，请稍后重试。";
}

function formatDate(value: string | null | undefined) {
  if (!value) return "未记录";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function shortId(value: string | null | undefined) {
  if (!value) return "-";
  return value.length > 24 ? `${value.slice(0, 14)}...${value.slice(-6)}` : value;
}

function StatusBadge({ value }: { value: string }) {
  const tone = ["accepted", "not-applicable", "passed", "succeeded", "ready"].includes(value)
    ? styles.good
    : ["failed", "blocked"].includes(value)
      ? styles.bad
      : ["open", "running"].includes(value)
        ? styles.active
        : styles.neutral;
  return <span className={`${styles.statusBadge} ${tone}`}>{statusLabels[value] ?? value}</span>;
}

export function OperatorConsole() {
  const [initialSession] = useState(() =>
    typeof window === "undefined" ? null : loadStoredSession(),
  );
  const [phase, setPhase] = useState<"checking" | "anonymous" | "authenticated">(
    initialSession ? "checking" : "anonymous",
  );
  const [token, setToken] = useState<string | null>(initialSession?.token ?? null);
  const [principal, setPrincipal] = useState<PlatformPrincipal | null>(null);
  const [view, setView] = useState<View>("status");
  const [ready, setReady] = useState<boolean | null>(null);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [gates, setGates] = useState<GateFacts | null>(null);
  const [users, setUsers] = useState<PlatformUser[]>([]);
  const [catalog, setCatalog] = useState<PublicationCatalog | null>(null);
  const [jobs, setJobs] = useState<PlatformJob[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const expireSession = useCallback(() => {
    clearStoredSession();
    setToken(null);
    setPrincipal(null);
    setPhase("anonymous");
    setView("status");
  }, []);

  const guarded = useCallback(
    async <T,>(work: () => Promise<T>): Promise<T> => {
      try {
        return await work();
      } catch (caught) {
        if (
          caught instanceof PlatformApiError &&
          caught.status === 401 &&
          caught.reason === "invalid_or_expired_session"
        ) {
          expireSession();
        }
        throw caught;
      }
    },
    [expireSession],
  );

  const refresh = useCallback(
    async (activeToken: string, activePrincipal?: PlatformPrincipal | null) => {
      setBusy(true);
      setError(null);
      try {
        const shouldLoadUsers = activePrincipal?.role === "admin";
        const [healthResult, readyResult, gateResult, catalogResult, userResult] =
          await guarded(() =>
            Promise.all([
              platformRequest<{ status: string }>("/health"),
              platformRequest<{ status: string; checks: { postgres: boolean } }>("/ready"),
              platformRequest<GateFacts>("/api/platform/v1/quanthus/gates", {
                token: activeToken,
              }),
              loadPublicationCatalog(),
              shouldLoadUsers
                ? platformRequest<{ users: PlatformUser[] }>(
                    "/api/platform/v1/admin/users",
                    { token: activeToken },
                  )
                : Promise.resolve({ users: [] }),
            ]),
          );
        setHealthy(healthResult.status === "healthy");
        setReady(readyResult.status === "ready" && readyResult.checks.postgres);
        setGates(gateResult);
        setCatalog(catalogResult);
        setUsers(userResult.users);
        setLastRefresh(new Date());
      } catch (caught) {
        setError(friendlyError(caught));
      } finally {
        setBusy(false);
      }
    },
    [guarded],
  );

  useEffect(() => {
    const session = initialSession;
    if (!session) return;
    platformRequest<PlatformPrincipal>("/api/platform/v1/auth/me", {
      token: session.token,
    })
      .then((observedPrincipal) => {
        setPrincipal(observedPrincipal);
        setPhase("authenticated");
        return refresh(session.token, observedPrincipal);
      })
      .catch((caught) => {
        expireSession();
        if (!(caught instanceof PlatformApiError && caught.status === 401)) {
          setError(friendlyError(caught));
        }
      });
  }, [expireSession, initialSession, refresh]);

  useEffect(() => {
    if (!token || !jobs.some((job) => !terminalJobStates.has(job.state))) return;
    const timer = window.setInterval(() => {
      jobs
        .filter((job) => !terminalJobStates.has(job.state))
        .forEach((job) => {
          guarded(() =>
            platformRequest<PlatformJob>(`/api/platform/v1/jobs/${job.job_id}`, {
              token,
            }),
          )
            .then((next) =>
              setJobs((current) =>
                current.map((item) => (item.job_id === next.job_id ? next : item)),
              ),
            )
            .catch((caught) => setError(friendlyError(caught)));
        });
    }, 900);
    return () => window.clearInterval(timer);
  }, [guarded, jobs, token]);

  async function completeLogin(result: LoginResult) {
    storeSession(result);
    setToken(result.token);
    const observedPrincipal = await platformRequest<PlatformPrincipal>(
      "/api/platform/v1/auth/me",
      { token: result.token },
    );
    setPrincipal(observedPrincipal);
    setPhase("authenticated");
    await refresh(result.token, observedPrincipal);
  }

  async function logout() {
    if (token) {
      await platformRequest("/api/platform/v1/auth/logout", {
        method: "POST",
        token,
        body: {},
      }).catch(() => undefined);
    }
    expireSession();
  }

  async function updatePrincipal() {
    if (!token) return;
    const observed = await guarded(() =>
      platformRequest<PlatformPrincipal>("/api/platform/v1/auth/me", { token }),
    );
    setPrincipal(observed);
  }

  if (phase === "checking") {
    return (
      <main className={styles.loadingScreen} aria-label="正在恢复平台会话">
        <span className={styles.brandMark}>Q</span>
        <LoaderCircle className={styles.spin} aria-hidden="true" />
      </main>
    );
  }

  if (phase === "anonymous") {
    return <LoginScreen onLogin={completeLogin} externalError={error} />;
  }

  if (!principal || !token) return null;

  const navigation: Array<{ id: View; label: string; icon: typeof Activity }> = [
    { id: "status", label: "运行状态", icon: Activity },
    { id: "verification", label: "归因验证", icon: ClipboardCheck },
    ...(principal.role === "admin"
      ? [{ id: "users" as const, label: "用户管理", icon: Users }]
      : []),
    { id: "security", label: "安全设置", icon: ShieldCheck },
  ];

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <div className={styles.brand}>
          <span className={styles.brandMark}>Q</span>
          <div>
            <strong>Quanthus</strong>
            <span>平台控制台</span>
          </div>
        </div>
        <nav className={styles.nav} aria-label="平台控制台视图">
          {navigation.map(({ id, label, icon: Icon }) => (
            <button
              type="button"
              key={id}
              className={view === id ? styles.navActive : undefined}
              aria-current={view === id ? "page" : undefined}
              onClick={() => setView(id)}
            >
              <Icon aria-hidden="true" />
              {label}
            </button>
          ))}
        </nav>
        <div className={styles.accountActions}>
          <Link href="/" className={styles.iconButton} title="返回策略归因工作台">
            <ArrowLeft aria-hidden="true" />
          </Link>
          <button
            type="button"
            className={styles.iconButton}
            title="刷新"
            aria-label="刷新控制台"
            onClick={() => refresh(token, principal)}
            disabled={busy}
          >
            <RefreshCw className={busy ? styles.spin : undefined} aria-hidden="true" />
          </button>
          <div className={styles.accountIdentity}>
            <UserRound aria-hidden="true" />
            <span>
              <strong>{principal.username}</strong>
              <small>{roleLabels[principal.role]}</small>
            </span>
          </div>
          <button
            type="button"
            className={styles.iconButton}
            title="退出登录"
            aria-label="退出登录"
            onClick={logout}
          >
            <LogOut aria-hidden="true" />
          </button>
        </div>
      </header>

      {error && (
        <div className={styles.errorBanner} role="alert">
          <CircleAlert aria-hidden="true" />
          <span>{error}</span>
          <button type="button" onClick={() => setError(null)} aria-label="关闭错误提示">
            <X aria-hidden="true" />
          </button>
        </div>
      )}

      <main className={styles.main}>
        {view === "status" && (
          <StatusView
            healthy={healthy}
            ready={ready}
            gates={gates}
            releases={catalog?.releases.length ?? 0}
            lastRefresh={lastRefresh}
          />
        )}
        {view === "verification" && (
          <VerificationView
            token={token}
            principal={principal}
            catalog={catalog}
            jobs={jobs}
            setJobs={setJobs}
            onError={(caught) => setError(friendlyError(caught))}
            guarded={guarded}
          />
        )}
        {view === "users" && principal.role === "admin" && (
          <UsersView
            token={token}
            users={users}
            onCreated={(user) => setUsers((current) => [...current, user])}
            onError={(caught) => setError(friendlyError(caught))}
            guarded={guarded}
          />
        )}
        {view === "security" && (
          <SecurityView
            token={token}
            principal={principal}
            onChanged={updatePrincipal}
            onError={(caught) => setError(friendlyError(caught))}
            guarded={guarded}
          />
        )}
      </main>
    </div>
  );
}

function LoginScreen({
  onLogin,
  externalError,
}: {
  onLogin: (result: LoginResult) => Promise<void>;
  externalError: string | null;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [mfaRequired, setMfaRequired] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(externalError);
  const [ready, setReady] = useState<boolean | null>(null);

  useEffect(() => {
    platformRequest<{ status: string; checks: { postgres: boolean } }>("/ready")
      .then((result) => setReady(result.status === "ready" && result.checks.postgres))
      .catch(() => setReady(false));
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await platformRequest<LoginResult>("/api/platform/v1/auth/login", {
        method: "POST",
        body: {
          username,
          password,
          ...(mfaRequired ? { mfa_code: mfaCode } : {}),
        },
      });
      await onLogin(result);
    } catch (caught) {
      if (
        caught instanceof PlatformApiError &&
        caught.status === 428 &&
        caught.reason === "mfa_required"
      ) {
        setMfaRequired(true);
        setError(null);
      } else {
        setError(friendlyError(caught));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className={styles.loginScreen}>
      <section className={styles.loginIdentity} aria-labelledby="login-product-title">
        <div className={styles.brand}>
          <span className={styles.brandMark}>Q</span>
          <div>
            <strong>Quanthus</strong>
            <span>平台控制台</span>
          </div>
        </div>
        <div>
          <p className={styles.eyebrow}>Operator access</p>
          <h1 id="login-product-title">进入 Quanthus 运行控制面</h1>
          <p>身份、门禁、归因验证与平台作业统一从这里操作。</p>
        </div>
        <div className={styles.loginRuntime}>
          <Server aria-hidden="true" />
          <span>
            <small>平台服务</small>
            <strong>{ready === null ? "检查中" : ready ? "已就绪" : "不可用"}</strong>
          </span>
          <span className={`${styles.runtimeDot} ${ready ? styles.runtimeReady : ""}`} />
        </div>
      </section>

      <section className={styles.loginPanel} aria-labelledby="login-title">
        <form onSubmit={submit}>
          <div className={styles.formHeading}>
            <LockKeyhole aria-hidden="true" />
            <div>
              <h2 id="login-title">登录</h2>
              <p>使用已授权的平台账户</p>
            </div>
          </div>
          {(error || externalError) && (
            <div className={styles.inlineError} role="alert">
              <CircleAlert aria-hidden="true" />
              {error ?? externalError}
            </div>
          )}
          <label className={styles.field}>
            <span>用户名</span>
            <input
              name="username"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
              autoFocus
            />
          </label>
          <label className={styles.field}>
            <span>密码</span>
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {mfaRequired && (
            <label className={styles.field}>
              <span>动态验证码</span>
              <input
                name="mfa_code"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]{6}"
                maxLength={6}
                value={mfaCode}
                onChange={(event) => setMfaCode(event.target.value.replace(/\D/g, ""))}
                required
                autoFocus
              />
            </label>
          )}
          <button type="submit" className={styles.primaryButton} disabled={busy}>
            {busy ? <LoaderCircle className={styles.spin} aria-hidden="true" /> : <KeyRound aria-hidden="true" />}
            {mfaRequired ? "验证并登录" : "登录平台"}
          </button>
          <Link href="/" className={styles.backLink}>
            <ArrowLeft aria-hidden="true" /> 返回策略归因工作台
          </Link>
        </form>
      </section>
    </main>
  );
}

function StatusView({
  healthy,
  ready,
  gates,
  releases,
  lastRefresh,
}: {
  healthy: boolean | null;
  ready: boolean | null;
  gates: GateFacts | null;
  releases: number;
  lastRefresh: Date | null;
}) {
  return (
    <>
      <header className={styles.pageHeading}>
        <div>
          <p className={styles.eyebrow}>Runtime overview</p>
          <h1>运行状态</h1>
          <p>平台内核、PostgreSQL 与交付门禁的当前事实。</p>
        </div>
        <span>更新于 {lastRefresh ? formatDate(lastRefresh.toISOString()) : "尚未完成"}</span>
      </header>

      <section className={styles.runtimeStrip} aria-label="平台运行摘要">
        <div>
          <span className={healthy ? styles.signalGood : styles.signalBad}>
            {healthy ? <CircleCheck aria-hidden="true" /> : <CircleAlert aria-hidden="true" />}
          </span>
          <p>API 进程</p>
          <strong>{healthy === null ? "检查中" : healthy ? "健康" : "异常"}</strong>
        </div>
        <div>
          <span className={ready ? styles.signalGood : styles.signalBad}>
            {ready ? <CircleCheck aria-hidden="true" /> : <CircleAlert aria-hidden="true" />}
          </span>
          <p>PostgreSQL</p>
          <strong>{ready === null ? "检查中" : ready ? "已连接" : "不可用"}</strong>
        </div>
        <div>
          <span className={styles.signalActive}><Activity aria-hidden="true" /></span>
          <p>当前门禁</p>
          <strong>{gates?.current_gate ?? "读取中"}</strong>
        </div>
        <div>
          <span className={styles.signalNeutral}><ClipboardCheck aria-hidden="true" /></span>
          <p>归因发布</p>
          <strong>{releases} 个</strong>
        </div>
      </section>

      <div className={styles.statusGrid}>
        <section className={styles.section} aria-labelledby="delivery-gates-title">
          <header className={styles.sectionHeading}>
            <div>
              <p className={styles.eyebrow}>Delivery gates</p>
              <h2 id="delivery-gates-title">Platform Kernel 里程碑</h2>
            </div>
            {gates && <StatusBadge value={gates.current_status.endsWith("-open") ? "open" : gates.current_status} />}
          </header>
          <div className={styles.gateList}>
            {gates?.gates.map((gate) => (
              <div key={gate.id} className={gate.id === gates.current_gate ? styles.currentGate : undefined}>
                <span>{gate.id}</span>
                <strong>{gate.name}</strong>
                <StatusBadge value={gate.status} />
              </div>
            )) ?? <p className={styles.emptyState}>正在读取门禁事实。</p>}
          </div>
        </section>

        <section className={styles.section} aria-labelledby="acceptance-title">
          <header className={styles.sectionHeading}>
            <div>
              <p className={styles.eyebrow}>Acceptance facts</p>
              <h2 id="acceptance-title">验收结果</h2>
            </div>
            <ShieldCheck aria-hidden="true" />
          </header>
          <div className={styles.acceptanceList}>
            {Object.entries(gates?.acceptance_results ?? {}).map(([name, value]) => (
              <div key={name}>
                {value === "passed" ? <BadgeCheck aria-hidden="true" /> : <CircleAlert aria-hidden="true" />}
                <span>
                  <strong>{name.replaceAll("_", " ")}</strong>
                  <small>{statusLabels[value] ?? value}</small>
                </span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </>
  );
}

function VerificationView({
  token,
  principal,
  catalog,
  jobs,
  setJobs,
  onError,
  guarded,
}: {
  token: string;
  principal: PlatformPrincipal;
  catalog: PublicationCatalog | null;
  jobs: PlatformJob[];
  setJobs: React.Dispatch<React.SetStateAction<PlatformJob[]>>;
  onError: (error: unknown) => void;
  guarded: <T>(work: () => Promise<T>) => Promise<T>;
}) {
  const [releaseId, setReleaseId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const canRun = principal.role !== "viewer";
  const releases = catalog?.releases ?? [];
  const effectiveReleaseId = releaseId || releases[0]?.release_id || "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!effectiveReleaseId || !canRun) return;
    setSubmitting(true);
    try {
      const idempotencyKey = `web-${effectiveReleaseId}-${crypto.randomUUID()}`;
      const job = await guarded(() =>
        platformRequest<PlatformJob>("/api/platform/v1/jobs/attribution-verifications", {
          method: "POST",
          token,
          idempotencyKey,
          body: { release_id: effectiveReleaseId },
        }),
      );
      setJobs((current) => [job, ...current.filter((item) => item.job_id !== job.job_id)]);
    } catch (caught) {
      onError(caught);
    } finally {
      setSubmitting(false);
    }
  }

  async function cancel(job: PlatformJob) {
    try {
      const updated = await guarded(() =>
        platformRequest<PlatformJob>(`/api/platform/v1/jobs/${job.job_id}/cancel`, {
          method: "POST",
          token,
          body: {},
        }),
      );
      setJobs((current) =>
        current.map((item) => (item.job_id === updated.job_id ? updated : item)),
      );
    } catch (caught) {
      onError(caught);
    }
  }

  return (
    <>
      <header className={styles.pageHeading}>
        <div>
          <p className={styles.eyebrow}>Authoritative verification</p>
          <h1>归因发布验证</h1>
          <p>由 Quanthus 校验发布身份、校验和与归因产物链路。</p>
        </div>
      </header>
      <section className={`${styles.section} ${styles.verificationLauncher}`}>
        <form onSubmit={submit}>
          <label className={styles.field}>
            <span>归因发布</span>
            <select value={effectiveReleaseId} onChange={(event) => setReleaseId(event.target.value)} disabled={!releases.length}>
              {!releases.length && <option value="">没有可验证的发布</option>}
              {releases.map((release) => (
                <option key={release.release_id} value={release.release_id}>{release.release_id}</option>
              ))}
            </select>
          </label>
          <div className={styles.launcherFact}>
            <span>执行权限</span>
            <strong>{canRun ? roleLabels[principal.role] : "只读"}</strong>
          </div>
          <button className={styles.primaryButton} type="submit" disabled={!effectiveReleaseId || !canRun || submitting}>
            {submitting ? <LoaderCircle className={styles.spin} aria-hidden="true" /> : <Play aria-hidden="true" />}
            提交验证
          </button>
        </form>
        {!releases.length && (
          <p className={styles.emptyState}>发布目录为空。新的完整发布进入目录后会自动出现在这里。</p>
        )}
      </section>

      <section className={styles.section} aria-labelledby="job-list-title">
        <header className={styles.sectionHeading}>
          <div>
            <p className={styles.eyebrow}>Current session</p>
            <h2 id="job-list-title">验证任务</h2>
          </div>
          <span className={styles.count}>{jobs.length}</span>
        </header>
        {!jobs.length ? (
          <div className={styles.largeEmpty}>
            <ClipboardCheck aria-hidden="true" />
            <strong>本次会话还没有验证任务</strong>
          </div>
        ) : (
          <div className={styles.jobList}>
            {jobs.map((job) => (
              <article key={job.job_id}>
                <header>
                  <span className={styles.jobIcon}>
                    {job.state === "succeeded" ? <BadgeCheck aria-hidden="true" /> : job.state === "failed" ? <CircleAlert aria-hidden="true" /> : <Activity aria-hidden="true" />}
                  </span>
                  <div>
                    <strong>{job.input_ref.release_id ?? "未知发布"}</strong>
                    <code>{shortId(job.job_id)}</code>
                  </div>
                  <StatusBadge value={job.state} />
                  {!terminalJobStates.has(job.state) && canRun && (
                    <button type="button" className={styles.iconButton} onClick={() => cancel(job)} title="取消任务" aria-label="取消验证任务">
                      <Square aria-hidden="true" />
                    </button>
                  )}
                </header>
                <div className={styles.progressTrack} aria-label={`完成度 ${job.progress}%`}>
                  <span style={{ width: `${job.progress}%` }} />
                </div>
                <dl className={styles.jobFacts}>
                  <div><dt>进度</dt><dd>{job.progress}%</dd></div>
                  <div><dt>尝试</dt><dd>{job.attempt}</dd></div>
                  <div><dt>提交时间</dt><dd>{formatDate(job.created_at)}</dd></div>
                  <div><dt>发布摘要</dt><dd><code>{shortId(String(job.result_ref?.release_digest ?? ""))}</code></dd></div>
                </dl>
                {job.error_reason && <p className={styles.jobError}>{job.error_reason}</p>}
              </article>
            ))}
          </div>
        )}
      </section>
    </>
  );
}

function UsersView({
  token,
  users,
  onCreated,
  onError,
  guarded,
}: {
  token: string;
  users: PlatformUser[];
  onCreated: (user: PlatformUser) => void;
  onError: (error: unknown) => void;
  guarded: <T>(work: () => Promise<T>) => Promise<T>;
}) {
  const [creating, setCreating] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<PlatformRole>("operator");

  async function createUser(event: FormEvent) {
    event.preventDefault();
    setCreating(true);
    try {
      const user = await guarded(() =>
        platformRequest<PlatformUser>("/api/platform/v1/admin/users", {
          method: "POST",
          token,
          body: { username, password, role },
        }),
      );
      onCreated(user);
      setUsername("");
      setPassword("");
      setRole("operator");
    } catch (caught) {
      onError(caught);
    } finally {
      setCreating(false);
    }
  }

  return (
    <>
      <header className={styles.pageHeading}>
        <div>
          <p className={styles.eyebrow}>Access control</p>
          <h1>用户管理</h1>
          <p>创建平台账户并分配最小必要角色。</p>
        </div>
      </header>
      <div className={styles.userGrid}>
        <section className={styles.section} aria-labelledby="users-title">
          <header className={styles.sectionHeading}>
            <div><p className={styles.eyebrow}>Directory</p><h2 id="users-title">平台用户</h2></div>
            <span className={styles.count}>{users.length}</span>
          </header>
          <div className={styles.userList} role="list">
            {users.map((user) => (
              <div
                key={user.user_id}
                role="listitem"
                aria-label={`${user.username}，${roleLabels[user.role]}`}
              >
                <span className={styles.userAvatar}>{user.username.slice(0, 1).toUpperCase()}</span>
                <span><strong>{user.username}</strong><small>创建于 {formatDate(user.created_at)}</small></span>
                <span className={styles.roleBadge}>{roleLabels[user.role]}</span>
                <span className={user.mfa_enabled ? styles.mfaOn : styles.mfaOff} title={user.mfa_enabled ? "MFA 已启用" : "MFA 未启用"}>
                  {user.mfa_enabled ? <ShieldCheck aria-hidden="true" /> : <Ban aria-hidden="true" />}
                </span>
              </div>
            ))}
          </div>
        </section>
        <section className={styles.section} aria-labelledby="new-user-title" role="region">
          <header className={styles.sectionHeading}>
            <div><p className={styles.eyebrow}>New account</p><h2 id="new-user-title">创建用户</h2></div>
            <Plus aria-hidden="true" />
          </header>
          <form className={styles.stackForm} onSubmit={createUser}>
            <label className={styles.field}><span>用户名</span><input value={username} onChange={(event) => setUsername(event.target.value)} minLength={3} maxLength={64} required /></label>
            <label className={styles.field}><span>初始密码</span><input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={12} required /></label>
            <label className={styles.field}><span>角色</span><select value={role} onChange={(event) => setRole(event.target.value as PlatformRole)}><option value="operator">操作员</option><option value="viewer">只读用户</option><option value="admin">管理员</option></select></label>
            <button type="submit" className={styles.primaryButton} disabled={creating}>{creating ? <LoaderCircle className={styles.spin} aria-hidden="true" /> : <Plus aria-hidden="true" />}创建用户</button>
          </form>
        </section>
      </div>
    </>
  );
}

function SecurityView({
  token,
  principal,
  onChanged,
  onError,
  guarded,
}: {
  token: string;
  principal: PlatformPrincipal;
  onChanged: () => Promise<void>;
  onError: (error: unknown) => void;
  guarded: <T>(work: () => Promise<T>) => Promise<T>;
}) {
  const [enrollment, setEnrollment] = useState<{ secret: string; provisioning_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  async function begin() {
    setBusy(true);
    try {
      setEnrollment(
        await guarded(() =>
          platformRequest<{ secret: string; provisioning_uri: string }>(
            "/api/platform/v1/auth/mfa/enroll",
            { method: "POST", token, body: {} },
          ),
        ),
      );
      setCode("");
    } catch (caught) {
      onError(caught);
    } finally {
      setBusy(false);
    }
  }

  async function confirm(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await guarded(() => platformRequest("/api/platform/v1/auth/mfa/confirm", { method: "POST", token, body: { code } }));
      setEnrollment(null);
      setCode("");
      await onChanged();
    } catch (caught) {
      onError(caught);
    } finally {
      setBusy(false);
    }
  }

  async function disable(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await guarded(() => platformRequest("/api/platform/v1/auth/mfa", { method: "DELETE", token, body: { code } }));
      setCode("");
      await onChanged();
    } catch (caught) {
      onError(caught);
    } finally {
      setBusy(false);
    }
  }

  async function copySecret() {
    if (!enrollment) return;
    await navigator.clipboard.writeText(enrollment.secret);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }

  return (
    <>
      <header className={styles.pageHeading}>
        <div><p className={styles.eyebrow}>Account security</p><h1>安全设置</h1><p>管理当前账户的动态验证码保护。</p></div>
      </header>
      <div className={styles.securityGrid}>
        <section className={styles.section}>
          <header className={styles.securitySummary}>
            <span className={principal.mfa_enabled ? styles.securityOn : styles.securityOff}><ShieldCheck aria-hidden="true" /></span>
            <div><p>多因素认证</p><h2>{principal.mfa_enabled ? "已启用" : "未启用"}</h2><span>{principal.username} · {roleLabels[principal.role]}</span></div>
            <StatusBadge value={principal.mfa_enabled ? "accepted" : "pending"} />
          </header>
        </section>
        <section className={styles.section}>
          {!principal.mfa_enabled && !enrollment && (
            <div className={styles.securityAction}>
              <div><KeyRound aria-hidden="true" /><span><strong>配置身份验证器</strong><small>绑定支持 TOTP 的身份验证应用</small></span></div>
              <button type="button" className={styles.primaryButton} onClick={begin} disabled={busy}>{busy ? <LoaderCircle className={styles.spin} aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}开始配置</button>
            </div>
          )}
          {!principal.mfa_enabled && enrollment && (
            <form className={styles.enrollment} onSubmit={confirm}>
              <div><p className={styles.eyebrow}>Authenticator secret</p><strong>在身份验证器中添加密钥</strong></div>
              <div className={styles.secretValue}><code>{enrollment.secret}</code><button type="button" onClick={copySecret} className={styles.iconButton} title="复制密钥" aria-label="复制 MFA 密钥">{copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}</button></div>
              <label className={styles.field}><span>应用生成的 6 位验证码</span><input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))} required autoFocus /></label>
              <div className={styles.formActions}><button type="button" className={styles.secondaryButton} onClick={() => setEnrollment(null)}>取消</button><button type="submit" className={styles.primaryButton} disabled={busy || code.length !== 6}>{busy ? <LoaderCircle className={styles.spin} aria-hidden="true" /> : <ShieldCheck aria-hidden="true" />}确认启用</button></div>
            </form>
          )}
          {principal.mfa_enabled && (
            <form className={styles.enrollment} onSubmit={disable}>
              <div><p className={styles.eyebrow}>Disable protection</p><strong>关闭多因素认证</strong></div>
              <label className={styles.field}><span>当前 6 位动态验证码</span><input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))} required /></label>
              <button type="submit" className={styles.dangerButton} disabled={busy || code.length !== 6}>{busy ? <LoaderCircle className={styles.spin} aria-hidden="true" /> : <Ban aria-hidden="true" />}关闭 MFA</button>
            </form>
          )}
        </section>
      </div>
    </>
  );
}
