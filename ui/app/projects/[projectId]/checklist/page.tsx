"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import {
  CheckCircle2,
  ClipboardList,
  ExternalLink,
  Loader2,
  RefreshCw,
  XCircle,
  HelpCircle,
} from "lucide-react";
import { MainLayout } from "@/components/layout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  getChecklistRows,
  getChecklistRuns,
  getChecklistRun,
  type ChecklistRow,
  type ChecklistRunSummary,
} from "@/lib/api/checklist";

type RowResult = {
  row_id: string;
  status: string;
  error?: string | null;
  evidence?: string | null;
  consistency?: string | null;
  feature?: string | null;
  main_module?: string | null;
};

type RunDetail = {
  run_id: string;
  run_verdict: string;
  profile_name: string;
  summary?: Record<string, number> | null;
  skipped_flow?: number;
  report_url?: string | null;
  results: RowResult[];
};

/** 行表一行：清单静态信息 + 本次判定 */
type ViewRow = ChecklistRow & {
  status: string;
  error?: string | null;
  evidence?: string | null;
  consistency?: string | null;
};

/**
 * 合并「清单行」与「执行结果」。
 * 清单行缺失时（如表格已删行但结果还在）用 run 结果里的模块/功能点兜底，
 * 其余字段给空串，避免表格里出现 undefined。
 */
function toViewRow(row: Partial<ChecklistRow> | undefined, res: RowResult): ViewRow {
  return {
    row_id: res.row_id,
    source_row: row?.source_row ?? null,
    title: row?.title ?? "",
    main_module: row?.main_module || res.main_module || "",
    sub_module: row?.sub_module ?? "",
    feature: row?.feature || res.feature || "",
    expected: row?.expected ?? "",
    priority: row?.priority ?? "",
    probe_kind: row?.probe_kind ?? "",
    data_mode: row?.data_mode ?? "",
    content_md5: row?.content_md5 ?? "",
    latest_status: row?.latest_status ?? null,
    latest_error: row?.latest_error ?? null,
    latest_evidence: row?.latest_evidence ?? null,
    latest_consistency: row?.latest_consistency ?? null,
    latest_run_at: row?.latest_run_at ?? null,
    status: res.status,
    error: res.error ?? null,
    evidence: res.evidence ?? null,
    consistency: res.consistency ?? null,
  };
}

const STATUS_LABEL: Record<string, string> = {
  pass: "通过",
  fail: "失败",
  unverified: "未验证",
  empty: "无结果",
};

function statusBadgeVariant(status?: string | null) {
  switch (status) {
    case "pass":
      return "default";
    case "fail":
      return "destructive";
    default:
      return "secondary";
  }
}

function StatusIcon({ status }: { status?: string | null }) {
  if (status === "pass") {
    return <CheckCircle2 className="h-4 w-4 text-green-600" />;
  }
  if (status === "fail") {
    return <XCircle className="h-4 w-4 text-red-600" />;
  }
  return <HelpCircle className="h-4 w-4 text-muted-foreground" />;
}

/**
 * 证据单元格。
 * - http(s) 链接（如在线报告）：原样外链；
 * - checklist-evidence/ 对象名（P0 之后探针回传的新格式）：走后端代理下载，永不过期；
 * - 其余（旧 run 的探针侧相对路径，文件已不可达）：只展示不可点，避免 404。
 */
function EvidenceLink({ evidence, projectId }: { evidence: string; projectId: string }) {
  if (/^https?:\/\//.test(evidence)) {
    return (
      <a
        href={evidence}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1 text-blue-600 hover:underline"
      >
        截图 <ExternalLink className="h-3 w-3" />
      </a>
    );
  }
  if (evidence.includes("checklist-evidence/")) {
    const href = `/api/v2/projects/${projectId}/checklist/evidence-file?object_name=${encodeURIComponent(evidence)}`;
    return (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1 text-blue-600 hover:underline"
      >
        截图 <ExternalLink className="h-3 w-3" />
      </a>
    );
  }
  return (
    <span className="text-xs text-muted-foreground" title={evidence}>
      截图（旧路径不可达）
    </span>
  );
}

export default function ChecklistPage() {
  const params = useParams();
  const projectId = params.projectId as string;

  const [rows, setRows] = React.useState<ChecklistRow[]>([]);
  const [runs, setRuns] = React.useState<ChecklistRunSummary[]>([]);
  const [selectedRun, setSelectedRun] = React.useState<RunDetail | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [runLoading, setRunLoading] = React.useState(false);

  // 筛选
  const [mainModule, setMainModule] = React.useState<string>("all");
  const [priority, setPriority] = React.useState<string>("all");
  const [probeKind, setProbeKind] = React.useState<string>("all");

  const loadRows = React.useCallback(async () => {
    const filters: Record<string, string> = {};
    if (mainModule !== "all") filters.main_module = mainModule;
    if (priority !== "all") filters.priority = priority;
    if (probeKind !== "all") filters.probe_kind = probeKind;
    const response = await getChecklistRows(projectId, filters);
    if (response.success) {
      setRows(response.data);
    }
  }, [projectId, mainModule, priority, probeKind]);

  const loadRuns = React.useCallback(async () => {
    const response = await getChecklistRuns(projectId);
    if (response.success) {
      setRuns(response.data);
    }
  }, [projectId]);

  const loadAll = React.useCallback(async () => {
    try {
      setLoading(true);
      await Promise.all([loadRows(), loadRuns()]);
    } catch (error) {
      console.error("Failed to load checklist:", error);
      toast.error("加载上线Checklist失败");
    } finally {
      setLoading(false);
    }
  }, [loadRows, loadRuns]);

  React.useEffect(() => {
    if (projectId) {
      loadAll();
    }
  }, [projectId, loadAll]);

  const openRun = async (runId: string) => {
    try {
      setRunLoading(true);
      const response = await getChecklistRun(projectId, runId);
      if (response.success) {
        setSelectedRun(response.data);
      }
    } catch (error) {
      console.error("Failed to load run detail:", error);
      toast.error("加载执行详情失败");
    } finally {
      setRunLoading(false);
    }
  };

  const mainModules = React.useMemo(
    () => Array.from(new Set(rows.map((r) => r.main_module).filter(Boolean))),
    [rows]
  );

  // 行表当前视图：选中 run 时以 run.results 为主关联行信息
  // 合并必须显式逐字段取值：展开顺序会让 run 结果里的 null 覆盖行上的真实值
  const viewRows: ViewRow[] = React.useMemo(() => {
    if (selectedRun) {
      const byRowId = new Map(rows.map((r) => [r.row_id, r]));
      return selectedRun.results.map((res) => toViewRow(byRowId.get(res.row_id), res));
    }
    return rows.map((r) =>
      toViewRow(r, {
        row_id: r.row_id,
        status: r.latest_status || "unverified",
        error: r.latest_error,
        evidence: r.latest_evidence,
        consistency: r.latest_consistency,
        feature: r.feature,
        main_module: r.main_module,
      })
    );
  }, [selectedRun, rows]);

  const statusCount = React.useMemo(() => {
    const c = { pass: 0, fail: 0, unverified: 0 };
    viewRows.forEach((r) => {
      if (r.status === "pass") c.pass += 1;
      else if (r.status === "fail") c.fail += 1;
      else c.unverified += 1;
    });
    return c;
  }, [viewRows]);

  return (
    <MainLayout>
      <div className="flex flex-1 flex-col gap-4 p-6">
        {/* 头部 */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ClipboardList className="h-5 w-5" />
            <h1 className="text-xl font-semibold">上线Checklist 回归</h1>
            <Badge variant="outline">{viewRows.length} 行</Badge>
          </div>
          <Button variant="outline" onClick={loadAll} disabled={loading}>
            {loading ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="mr-2 h-4 w-4" />
            )}
            刷新
          </Button>
        </div>

        {/* 执行历史 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">执行历史</CardTitle>
          </CardHeader>
          <CardContent>
            {runs.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                暂无执行记录。通过 miniprogram-probe-run 执行探针并回传结果后，这里会展示每次回归的判定与汇总。
              </p>
            ) : (
              <div className="flex flex-col gap-2">
                {runs.map((run) => (
                  <div
                    key={run.run_id}
                    className="flex flex-wrap items-center gap-3 rounded-md border p-3"
                  >
                    <Badge
                      variant={statusBadgeVariant(run.run_verdict)}
                      className="shrink-0"
                    >
                      {STATUS_LABEL[run.run_verdict] || run.run_verdict}
                    </Badge>
                    <span className="text-sm font-medium">
                      {run.profile_name || "checklist"}
                    </span>
                    {run.summary && (
                      <span className="text-sm text-muted-foreground">
                        通过 {run.summary.pass ?? 0} · 失败 {run.summary.fail ?? 0} ·
                        未验证 {run.summary.unverified ?? 0}
                      </span>
                    )}
                    {!!run.skipped_flow && (
                      <span className="text-sm text-amber-600">
                        流程类跳过 {run.skipped_flow}
                      </span>
                    )}
                    <span className="text-xs text-muted-foreground">
                      {run.created_at ? new Date(run.created_at).toLocaleString() : ""}
                    </span>
                    <div className="ml-auto flex items-center gap-2">
                      {run.report_url && (
                        <a
                          href={run.report_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-sm text-blue-600 hover:underline"
                        >
                          报告 <ExternalLink className="h-3 w-3" />
                        </a>
                      )}
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => openRun(run.run_id)}
                        disabled={runLoading}
                      >
                        查看明细
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* 筛选 */}
        <div className="flex flex-wrap items-center gap-3">
          <Select value={mainModule} onValueChange={setMainModule}>
            <SelectTrigger className="w-44">
              <SelectValue placeholder="主模块" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部主模块</SelectItem>
              {mainModules.map((m) => (
                <SelectItem key={m} value={m}>
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={priority} onValueChange={setPriority}>
            <SelectTrigger className="w-32">
              <SelectValue placeholder="优先级" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部优先级</SelectItem>
              {["P0", "P1", "P2"].map((p) => (
                <SelectItem key={p} value={p}>
                  {p}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={probeKind} onValueChange={setProbeKind}>
            <SelectTrigger className="w-36">
              <SelectValue placeholder="探针类型" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部探针</SelectItem>
              {["reachable", "visible", "navigate", "flow"].map((k) => (
                <SelectItem key={k} value={k}>
                  {k}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {selectedRun && (
            <Button variant="ghost" size="sm" onClick={() => setSelectedRun(null)}>
              退出明细视图，回到清单总览
            </Button>
          )}
          <span className="ml-auto text-sm text-muted-foreground">
            通过 {statusCount.pass} · 失败 {statusCount.fail} · 未验证{" "}
            {statusCount.unverified}
          </span>
        </div>

        {/* 行表 */}
        <Card>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-muted/50 text-left">
                    <th className="px-3 py-2 font-medium">判定</th>
                    <th className="px-3 py-2 font-medium">主模块</th>
                    <th className="px-3 py-2 font-medium">功能点</th>
                    <th className="px-3 py-2 font-medium">预期</th>
                    <th className="px-3 py-2 font-medium">优先级</th>
                    <th className="px-3 py-2 font-medium">探针</th>
                    <th className="px-3 py-2 font-medium">数据模式</th>
                    <th className="px-3 py-2 font-medium">说明</th>
                    <th className="px-3 py-2 font-medium">凭证</th>
                  </tr>
                </thead>
                <tbody>
                  {loading ? (
                    <tr>
                      <td colSpan={9} className="px-3 py-8 text-center">
                        <Loader2 className="mx-auto h-5 w-5 animate-spin" />
                      </td>
                    </tr>
                  ) : viewRows.length === 0 ? (
                    <tr>
                      <td colSpan={9} className="px-3 py-8 text-center text-muted-foreground">
                        暂无数据。先执行 checklist-sync 导入 Checklist 行。
                      </td>
                    </tr>
                  ) : (
                    viewRows.map((r) => (
                      <tr key={r.row_id} className="border-b align-top">
                        <td className="px-3 py-2">
                          <div className="flex items-center gap-1.5">
                            <StatusIcon status={r.status} />
                            <span>
                              {STATUS_LABEL[r.status] || r.status || "未验证"}
                            </span>
                          </div>
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap">
                          {r.main_module || "-"}
                        </td>
                        <td className="px-3 py-2">{r.feature || r.title || "-"}</td>
                        <td className="max-w-[320px] px-3 py-2 text-muted-foreground">
                          {r.expected || "-"}
                        </td>
                        <td className="px-3 py-2">
                          {r.priority ? <Badge variant="outline">{r.priority}</Badge> : "-"}
                        </td>
                        <td className="px-3 py-2 text-muted-foreground">
                          {r.probe_kind || "-"}
                        </td>
                        <td className="px-3 py-2 text-muted-foreground">
                          {r.data_mode || "-"}
                        </td>
                        <td className="max-w-[280px] px-3 py-2 text-xs text-red-600">
                          {r.error || r.consistency || ""}
                        </td>
                        <td className="px-3 py-2">
                          {r.evidence ? (
                            <EvidenceLink evidence={r.evidence} projectId={projectId} />
                          ) : (
                            "-"
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </div>
    </MainLayout>
  );
}
