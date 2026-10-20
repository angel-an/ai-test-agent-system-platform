"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import {
  Plus, RefreshCw, Clock, Trash2, Pencil, Play,
  ChevronDown, ChevronRight, CheckCircle2, XCircle, ExternalLink,
  Calendar, Globe, FileCode2, Info, Workflow, Power, Copy,
} from "lucide-react";
import { MainLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip, TooltipContent, TooltipProvider, TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog";
import {
  listScheduledRuns, createScheduledRun, updateScheduledRun, deleteScheduledRun,
  triggerScheduledRun, listExecutions, type ScheduledRun, type ScheduledRunExecution,
} from "@/lib/api/scheduled-runs";
import { getFolders } from "@/lib/api/folders";
import { listScenarios } from "@/lib/api/scenarios";
import type { FolderInfo } from "@/lib/api/types";
import type { Scenario } from "@/types/scenario";

// ---- 类型 ----
interface APIEndpoint {
  id: string;
  display_name: string;
  method: string;
  path: string;
  tag_group?: string | null;
}

// 任务类型：API接口 或 场景用例
type TaskSourceTab = "api" | "scenario";

const STATUS_MAP: Record<string, { label: string; variant: "default" | "secondary" | "destructive" | "outline" | "success" | "warning" }> = {
  pending:   { label: "等待中", variant: "secondary" },
  running:   { label: "执行中", variant: "default" },
  completed: { label: "已完成", variant: "outline" },
  failed:    { label: "失败",   variant: "destructive" },
};

const CRON_PRESETS = [
  { label: "每30分钟", value: "*/30 * * * *" },
  { label: "每小时",   value: "0 * * * *" },
  { label: "每天8点",  value: "0 8 * * *" },
  { label: "每天凌晨2点", value: "0 2 * * *" },
  { label: "每周一9点", value: "0 9 * * 1" },
];

/** 将 5 段 Cron 表达式翻译成中文描述，无法解析时返回 null */
function describeCron(cron: string): string | null {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const [min, hour, dom, mon, dow] = parts;

  // 常用预设匹配
  const presets: Record<string, string> = {
    "*/30 * * * *": "每30分钟",
    "0 * * * *": "每小时",
    "0 0 * * *": "每天凌晨0:00",
    "0 2 * * *": "每天凌晨2:00",
    "0 8 * * *": "每天上午8:00",
    "0 9 * * *": "每天上午9:00",
    "0 12 * * *": "每天中午12:00",
    "0 18 * * *": "每天下午6:00",
    "0 20 * * *": "每天晚上8:00",
    "0 22 * * *": "每天晚上10:00",
    "0 23 * * *": "每天晚上11:00",
    "0 9 * * 1": "每周一上午9:00",
    "0 9 * * 5": "每周五上午9:00",
    "0 0 * * 0": "每周日凌晨0:00",
    "0 0 * * 1": "每周一凌晨0:00",
    "0 0 1 * *": "每月1日凌晨0:00",
    "0 0 1 1 *": "每年1月1日凌晨0:00",
  };
  if (presets[cron.trim()]) return presets[cron.trim()];

  // 通配模式解析
  if (min === "0" && hour === "0" && dom === "*" && mon === "*" && dow === "*") return "每天凌晨0:00";
  if (min.startsWith("*/") && hour === "*" && dom === "*" && mon === "*" && dow === "*") {
    return `每${min.replace("*/", "")}分钟`;
  }
  if (min === "0" && hour.startsWith("*/") && dom === "*" && mon === "*" && dow === "*") {
    return `每${hour.replace("*/", "")}小时`;
  }
  if (min === "0" && hour !== "*" && dom === "*" && mon === "*" && dow === "*") {
    return `每天${formatHour(hour)}`;
  }
  if (min === "0" && hour !== "*" && dom === "*" && mon === "*" && dow !== "*") {
    const weekdays: Record<string, string> = { "0": "周日", "1": "周一", "2": "周二", "3": "周三", "4": "周四", "5": "周五", "6": "周六", "7": "周日" };
    return `每${weekdays[dow] ?? "周" + dow}${formatHour(hour)}`;
  }
  if (min === "0" && hour === "0" && dom !== "*" && mon === "*" && dow === "*") {
    return `每月${dom}日凌晨0:00`;
  }

  return null;
}

function formatHour(hour: string): string {
  const h = parseInt(hour, 10);
  if (isNaN(h)) return hour;
  const m = "00";
  if (h < 6) return `凌晨${h}:${m}`;
  if (h < 9) return `早上${h}:${m}`;
  if (h < 12) return `上午${h}:${m}`;
  if (h === 12) return `中午12:${m}`;
  if (h < 18) return `下午${h - 12}:${m}`;
  if (h === 18) return `晚上6:${m}`;
  return `晚上${h - 12}:${m}`;
}

interface FormState {
  name: string; description: string; cron_expression: string;
  timezone: string; api_endpoint_ids: string[]; scenario_ids: string[]; execution_config: string;
  is_active: boolean;
}
const DEFAULT_FORM: FormState = {
  name: "", description: "", cron_expression: "0 2 * * *",
  timezone: "Asia/Shanghai", api_endpoint_ids: [], scenario_ids: [], execution_config: "{}",
  is_active: true,
};

const EXECUTION_CONFIG_EXAMPLE = `{
  "base_url": "https://uat-insight.eatizen.com.sg/yundt-saas-gateway/api/icdp/",
  "environment": "UAT"
}`;

export default function ScheduledRunsPage() {
  const { projectId } = useParams<{ projectId: string }>();

  const [runs, setRuns] = React.useState<ScheduledRun[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [expandedId, setExpandedId] = React.useState<string | null>(null);
  const [executions, setExecutions] = React.useState<Record<string, ScheduledRunExecution[]>>({});
  const [execLoading, setExecLoading] = React.useState<string | null>(null);

  // 对话框状态
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<ScheduledRun | null>(null);
  const [form, setForm] = React.useState<FormState>(DEFAULT_FORM);
  const [submitting, setSubmitting] = React.useState(false);
  const [deleteTarget, setDeleteTarget] = React.useState<ScheduledRun | null>(null);
  const [triggering, setTriggering] = React.useState<string | null>(null);

  // 端点选择相关
  const [folders, setFolders] = React.useState<FolderInfo[]>([]);
  const [selectedFolderId, setSelectedFolderId] = React.useState<string | "all">("all");
  const [endpoints, setEndpoints] = React.useState<APIEndpoint[]>([]);
  const [endpointsLoading, setEndpointsLoading] = React.useState(false);
  const [endpointSearch, setEndpointSearch] = React.useState("");

  // 场景选择相关
  const [taskSourceTab, setTaskSourceTab] = React.useState<TaskSourceTab>("api");
  const [scenarios, setScenarios] = React.useState<Scenario[]>([]);
  const [scenariosLoading, setScenariosLoading] = React.useState(false);
  const [scenarioSearch, setScenarioSearch] = React.useState("");

  const filteredEndpoints = React.useMemo(() => {
    if (!endpointSearch.trim()) return endpoints;
    const kw = endpointSearch.trim().toLowerCase();
    return endpoints.filter(ep =>
      ep.display_name.toLowerCase().includes(kw) ||
      ep.path.toLowerCase().includes(kw) ||
      ep.method.toLowerCase().includes(kw)
    );
  }, [endpoints, endpointSearch]);

  const filteredScenarios = React.useMemo(() => {
    if (!scenarioSearch.trim()) return scenarios;
    const kw = scenarioSearch.trim().toLowerCase();
    return scenarios.filter(s =>
      s.name.toLowerCase().includes(kw) ||
      (s.description ?? "").toLowerCase().includes(kw) ||
      s.identifier.toLowerCase().includes(kw)
    );
  }, [scenarios, scenarioSearch]);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const res = await listScheduledRuns(projectId, { p: 1, page_size: 50 });
      const data = (res as any).data ?? res;
      setRuns(data.items ?? []);
    } catch { toast.error("加载失败"); }
    finally { setLoading(false); }
  }, [projectId]);

  React.useEffect(() => { load(); }, [load]);

  // 对话框打开时加载文件夹列表
  React.useEffect(() => {
    if (!dialogOpen) return;
    getFolders(projectId, "api_test").then(res => {
      if (res.success) setFolders(res.data ?? []);
    }).catch(() => {});
  }, [dialogOpen, projectId]);

  // 对话框打开时加载场景列表
  React.useEffect(() => {
    if (!dialogOpen) return;
    setScenariosLoading(true);
    listScenarios(projectId, { page: 1, page_size: 200 })
      .then(res => {
        setScenarios(res.items ?? []);
      })
      .catch(() => setScenarios([]))
      .finally(() => setScenariosLoading(false));
  }, [dialogOpen, projectId]);

  // 切换文件夹时加载端点列表
  React.useEffect(() => {
    if (!dialogOpen) return;
    setEndpointsLoading(true);
    const url = selectedFolderId === "all"
      ? `/api/v2/projects/${projectId}/api-endpoints?p=1&page_size=500`
      : `/api/v2/projects/${projectId}/api-endpoints?folder_id=${selectedFolderId}&p=1&page_size=500`;
    fetch(url)
      .then(r => r.json())
      .then(d => {
        const raw = d.data ?? d;
        const items: APIEndpoint[] = Array.isArray(raw) ? raw : (raw.items ?? []);
        setEndpoints(items);
      })
      .catch(() => setEndpoints([]))
      .finally(() => setEndpointsLoading(false));
  }, [dialogOpen, selectedFolderId, projectId]);

  const openCreate = () => {
    setEditing(null);
    setForm(DEFAULT_FORM);
    setSelectedFolderId("all");
    setTaskSourceTab("api");
    setDialogOpen(true);
  };

  const openEdit = (r: ScheduledRun) => {
    setEditing(r);
    const config = r.execution_config ?? {};
    const scenarioIds: string[] = (config as any).scenario_ids ?? [];
    setForm({
      name: r.name, description: r.description ?? "",
      cron_expression: r.cron_expression, timezone: r.timezone,
      api_endpoint_ids: r.api_endpoint_ids,
      scenario_ids: scenarioIds,
      is_active: r.is_active,
      execution_config: JSON.stringify(
        Object.fromEntries(Object.entries(config).filter(([k]) => k !== "scenario_ids")),
        null, 2
      ),
    });
    setSelectedFolderId("all");
    setTaskSourceTab(r.api_endpoint_ids.length > 0 ? "api" : scenarioIds.length > 0 ? "scenario" : "api");
    setDialogOpen(true);
  };

  const handleSubmit = async () => {
    if (!form.name.trim() || !form.cron_expression.trim()) { toast.error("名称和 Cron 不能为空"); return; }
    if (form.api_endpoint_ids.length === 0 && form.scenario_ids.length === 0) {
      toast.error("请至少选择一个 API 接口或场景用例"); return;
    }
    let execution_config: Record<string, unknown> = {};
    try { execution_config = JSON.parse(form.execution_config || "{}"); }
    catch { toast.error("执行配置 JSON 格式错误"); return; }
    // 将场景ID存入 execution_config
    if (form.scenario_ids.length > 0) {
      execution_config.scenario_ids = form.scenario_ids;
    }
    try {
      setSubmitting(true);
      const payload: any = {
        name: form.name, description: form.description,
        cron_expression: form.cron_expression, timezone: form.timezone,
        api_endpoint_ids: form.api_endpoint_ids, execution_config,
        is_active: form.is_active,
      };
      if (editing) { await updateScheduledRun(projectId, editing.id, payload); toast.success("更新成功"); }
      else { await createScheduledRun(projectId, payload); toast.success("创建成功"); }
      setDialogOpen(false); load();
    } catch (e: any) { toast.error(e?.message ?? "操作失败"); }
    finally { setSubmitting(false); }
  };

  const handleToggleActive = async (run: ScheduledRun) => {
    try {
      await updateScheduledRun(projectId, run.id, { is_active: !run.is_active });
      toast.success(run.is_active ? "已禁用" : "已启用");
      load();
    } catch { toast.error("操作失败"); }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try { await deleteScheduledRun(projectId, deleteTarget.id); toast.success("已删除"); setDeleteTarget(null); load(); }
    catch { toast.error("删除失败"); }
  };

  const handleTrigger = async (run: ScheduledRun) => {
    setTriggering(run.id);
    try {
      await triggerScheduledRun(projectId, run.id);
      toast.success(`「${run.name}」已触发执行`);
      setExpandedId(run.id);
      setTimeout(() => loadExecutions(run.id), 1500);
    } catch { toast.error("触发失败"); }
    finally { setTriggering(null); }
  };

  const loadExecutions = async (runId: string) => {
    setExecLoading(runId);
    try {
      const res = await listExecutions(projectId, runId, { p: 1, page_size: 10 });
      const data = (res as any).data ?? res;
      setExecutions(prev => ({ ...prev, [runId]: data.items ?? [] }));
    } catch {}
    finally { setExecLoading(null); }
  };

  const toggleExpand = (runId: string) => {
    if (expandedId === runId) { setExpandedId(null); return; }
    setExpandedId(runId);
    if (!executions[runId]) loadExecutions(runId);
  };

  const toggleEndpoint = (id: string) => setForm(f => ({
    ...f,
    api_endpoint_ids: f.api_endpoint_ids.includes(id)
      ? f.api_endpoint_ids.filter(x => x !== id)
      : [...f.api_endpoint_ids, id],
  }));

  const toggleScenario = (id: string) => setForm(f => ({
    ...f,
    scenario_ids: f.scenario_ids.includes(id)
      ? f.scenario_ids.filter(x => x !== id)
      : [...f.scenario_ids, id],
  }));

  const toggleSelectAll = () => {
    const allIds = endpoints.map(e => e.id);
    const allSelected = allIds.every(id => form.api_endpoint_ids.includes(id));
    setForm(f => ({
      ...f,
      api_endpoint_ids: allSelected
        ? f.api_endpoint_ids.filter(id => !allIds.includes(id))
        : Array.from(new Set([...f.api_endpoint_ids, ...allIds])),
    }));
  };

  const toggleSelectAllScenarios = () => {
    const allIds = filteredScenarios.map(s => s.id);
    const allSelected = allIds.every(id => form.scenario_ids.includes(id));
    setForm(f => ({
      ...f,
      scenario_ids: allSelected
        ? f.scenario_ids.filter(id => !allIds.includes(id))
        : Array.from(new Set([...f.scenario_ids, ...allIds])),
    }));
  };

  const METHOD_COLOR: Record<string, string> = {
    GET: "text-green-600", POST: "text-blue-600", PUT: "text-yellow-600",
    PATCH: "text-orange-500", DELETE: "text-red-600",
  };

  return (
    <TooltipProvider delayDuration={200}>
      <MainLayout>
        <div className="p-4 md:p-6 space-y-4">
          {/* Header */}
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <div>
              <h1 className="text-lg md:text-xl font-semibold flex items-center gap-2">
                <Clock className="h-5 w-5 text-primary" />
                测试调度
              </h1>
              <p className="text-xs md:text-sm text-muted-foreground mt-0.5">
                统一管理 API 接口与场景用例的定时执行计划，支持手动触发
              </p>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={load} disabled={loading}>
                <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              </Button>
              <Button size="sm" onClick={openCreate}>
                <Plus className="h-4 w-4 mr-1" />
                新建调度
              </Button>
            </div>
          </div>

          {/* List */}
          <div className="space-y-2">
            {loading ? (
              <div className="rounded-lg border bg-card p-8 text-center">
                <RefreshCw className="h-6 w-6 mx-auto mb-2 text-muted-foreground animate-spin" />
                <p className="text-sm text-muted-foreground">加载中...</p>
              </div>
            ) : runs.length === 0 ? (
              <div className="rounded-lg border bg-card text-center py-12 text-muted-foreground">
                <Clock className="h-10 w-10 mx-auto mb-3 opacity-30" />
                <p className="text-sm font-medium">暂无测试调度</p>
                <p className="text-xs mt-1">点击「新建调度」开始创建</p>
              </div>
            ) : (
              runs.map(run => {
                const cronDesc = describeCron(run.cron_expression);
                const isExpanded = expandedId === run.id;
                const runExecs = executions[run.id] ?? [];
                return (
                  <div key={run.id} className="rounded-lg border bg-card overflow-hidden">
                    {/* 主卡片行 */}
                    <div
                      className="flex items-start gap-2 md:gap-3 px-3 md:px-4 py-3 cursor-pointer hover:bg-muted/30 transition-colors"
                      onClick={() => toggleExpand(run.id)}
                    >
                      {/* 展开箭头 */}
                      <div className="mt-0.5 shrink-0">
                        {isExpanded ? (
                          <ChevronDown className="h-4 w-4 text-muted-foreground" />
                        ) : (
                          <ChevronRight className="h-4 w-4 text-muted-foreground" />
                        )}
                      </div>

                      {/* 信息区 */}
                      <div className="flex-1 min-w-0">
                        {/* 名称行 */}
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium text-sm truncate">{run.name}</span>
                          <Badge
                            variant={run.is_active ? "success" : "secondary"}
                            className="text-[10px] h-5 px-1.5"
                          >
                            {run.is_active ? "启用" : "停用"}
                          </Badge>
                          <code className="text-[10px] bg-muted px-1.5 py-0.5 rounded border font-mono text-muted-foreground">
                            {run.cron_expression}
                          </code>
                          {cronDesc && (
                            <span className="text-[10px] text-muted-foreground flex items-center gap-0.5">
                              <Calendar className="h-3 w-3" />
                              {cronDesc}
                            </span>
                          )}
                        </div>

                        {/* 描述 */}
                        {run.description && (
                          <p className="text-xs text-muted-foreground mt-1 truncate">{run.description}</p>
                        )}

                        {/* 元信息 */}
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 mt-1.5 text-[11px] text-muted-foreground">
                          {run.api_endpoint_ids.length > 0 && (
                            <span className="flex items-center gap-0.5">
                              <FileCode2 className="h-3 w-3" />
                              {run.api_endpoint_ids.length} 个接口
                            </span>
                          )}
                          {((run.execution_config as any)?.scenario_ids?.length ?? 0) > 0 && (
                            <span className="flex items-center gap-0.5">
                              <Workflow className="h-3 w-3 text-orange-500" />
                              {(run.execution_config as any).scenario_ids.length} 个场景
                            </span>
                          )}
                          <span className="flex items-center gap-0.5">
                            <Globe className="h-3 w-3" />
                            {run.timezone}
                          </span>
                          {run.last_executed_at && (
                            <span className="flex items-center gap-0.5">
                              <Info className="h-3 w-3" />
                              上次执行: {new Date(run.last_executed_at).toLocaleString("zh-CN")}
                            </span>
                          )}
                        </div>
                      </div>

                      {/* 操作区 */}
                      <div
                        className="flex items-center gap-0.5 shrink-0"
                        onClick={e => e.stopPropagation()}
                      >
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8"
                              disabled={triggering === run.id}
                              onClick={() => handleTrigger(run)}
                            >
                              <Play className={`h-4 w-4 ${triggering === run.id ? "animate-pulse text-primary" : ""}`} />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>
                            <p>{triggering === run.id ? "触发中..." : "立即执行"}</p>
                          </TooltipContent>
                        </Tooltip>

                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className={`h-8 w-8 ${run.is_active ? "text-green-600 hover:text-orange-600" : "text-muted-foreground hover:text-green-600"}`}
                              onClick={() => handleToggleActive(run)}
                            >
                              <Power className="h-4 w-4" />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>
                            <p>{run.is_active ? "禁用任务" : "启用任务"}</p>
                          </TooltipContent>
                        </Tooltip>

                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8"
                              onClick={() => openEdit(run)}
                            >
                              <Pencil className="h-4 w-4" />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>
                            <p>编辑</p>
                          </TooltipContent>
                        </Tooltip>

                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8 text-destructive hover:text-destructive"
                              onClick={() => setDeleteTarget(run)}
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>
                            <p>删除</p>
                          </TooltipContent>
                        </Tooltip>
                      </div>
                    </div>

                    {/* 执行记录展开 */}
                    {isExpanded && (
                      <div className="bg-muted/20 px-8 md:px-11 pb-3 border-t">
                        <div className="flex items-center justify-between pt-2 pb-1.5">
                          <p className="text-xs font-medium text-muted-foreground">最近执行记录</p>
                          {execLoading !== run.id && runExecs.length > 0 && (
                            <button
                              className="text-xs text-primary hover:underline"
                              onClick={() => loadExecutions(run.id)}
                            >
                              刷新
                            </button>
                          )}
                        </div>

                        {execLoading === run.id ? (
                          <div className="flex items-center gap-2 py-3 text-sm text-muted-foreground">
                            <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                            加载中...
                          </div>
                        ) : runExecs.length === 0 ? (
                          <div className="flex items-center gap-2 py-3 text-sm text-muted-foreground">
                            <Info className="h-3.5 w-3.5" />
                            暂无执行记录
                          </div>
                        ) : (
                          <div className="space-y-1.5">
                            {runExecs.map(exec => {
                              const status = STATUS_MAP[exec.status] ?? { label: exec.status, variant: "secondary" };
                              return (
                                <div
                                  key={exec.id}
                                  className="flex flex-col sm:flex-row sm:items-center gap-1.5 sm:gap-3 rounded-md border bg-background px-3 py-2 text-sm"
                                >
                                  <div className="flex items-center gap-2">
                                    <Badge variant={status.variant} className="text-[10px] h-5">
                                      {status.label}
                                    </Badge>
                                    <span className="text-muted-foreground font-mono text-[11px]">{exec.identifier}</span>
                                  </div>
                                  <div className="flex items-center gap-3">
                                    <span className="flex items-center gap-1 text-green-600 text-xs">
                                      <CheckCircle2 className="h-3.5 w-3.5" />
                                      {exec.passed_tests}
                                    </span>
                                    <span className="flex items-center gap-1 text-red-600 text-xs">
                                      <XCircle className="h-3.5 w-3.5" />
                                      {exec.failed_tests}
                                    </span>
                                  </div>
                                  <div className="sm:ml-auto flex items-center gap-2">
                                    <span className="text-muted-foreground text-[11px]">
                                      {new Date(exec.created_at).toLocaleString("zh-CN")}
                                    </span>
                                    {exec.report_path && (
                                      <a
                                        href={`/api/v2/files/${exec.report_path}`}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="flex items-center gap-0.5 text-blue-500 hover:underline text-xs"
                                      >
                                        <ExternalLink className="h-3.5 w-3.5" />
                                        报告
                                      </a>
                                    )}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* 新建/编辑对话框 */}
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogContent className="max-w-lg w-[calc(100vw-1.5rem)] sm:w-full max-h-[90vh] p-0 gap-0 overflow-hidden flex flex-col">
            <DialogHeader className="px-5 pt-5 pb-2 shrink-0">
              <DialogTitle>{editing ? "编辑测试调度" : "新建测试调度"}</DialogTitle>
              <DialogDescription>配置定时执行 API 接口或场景用例的计划任务</DialogDescription>
            </DialogHeader>
            <div className="flex-1 overflow-y-auto px-5 py-2 space-y-4">
              <div className="space-y-1.5">
                <Label>名称 *</Label>
                <Input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="如：每日回归测试" />
              </div>
              <div className="space-y-1.5">
                <Label>描述</Label>
                <Textarea value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} rows={2} />
              </div>
              <div className="space-y-1.5">
                <Label>执行频率 *</Label>
                <div className="flex gap-1.5 flex-wrap mb-1.5">
                  {CRON_PRESETS.map(p => (
                    <button key={p.value} onClick={() => setForm(f => ({ ...f, cron_expression: p.value }))}
                      className={`text-xs px-2 py-1 rounded border transition-colors ${form.cron_expression === p.value ? "bg-primary text-primary-foreground border-primary" : "hover:bg-muted"}`}>
                      {p.label}
                    </button>
                  ))}
                </div>
                <Input value={form.cron_expression} onChange={e => setForm(f => ({ ...f, cron_expression: e.target.value }))} placeholder="Cron 表达式，如 0 2 * * *" />
                {describeCron(form.cron_expression) && (
                  <p className="text-xs text-muted-foreground mt-1">
                    解析: {describeCron(form.cron_expression)}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label>时区</Label>
                <Input value={form.timezone} onChange={e => setForm(f => ({ ...f, timezone: e.target.value }))} placeholder="Asia/Shanghai" />
              </div>

              {/* 测试目标选择 - Tab 切换 */}
              <div className="space-y-1.5">
                <Label>选择测试目标</Label>
                {/* Tab 切换 */}
                <div className="flex border-b mb-2">
                  <button
                    onClick={() => setTaskSourceTab("api")}
                    className={`px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
                      taskSourceTab === "api"
                        ? "border-primary text-primary"
                        : "border-transparent text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <span className="flex items-center gap-1">
                      <FileCode2 className="h-3.5 w-3.5" />
                      API 接口
                      {form.api_endpoint_ids.length > 0 && (
                        <Badge variant="secondary" className="text-[10px] h-4 px-1 ml-1">{form.api_endpoint_ids.length}</Badge>
                      )}
                    </span>
                  </button>
                  <button
                    onClick={() => setTaskSourceTab("scenario")}
                    className={`px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
                      taskSourceTab === "scenario"
                        ? "border-primary text-primary"
                        : "border-transparent text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <span className="flex items-center gap-1">
                      <Workflow className="h-3.5 w-3.5" />
                      场景用例
                      {form.scenario_ids.length > 0 && (
                        <Badge variant="secondary" className="text-[10px] h-4 px-1 ml-1">{form.scenario_ids.length}</Badge>
                      )}
                    </span>
                  </button>
                </div>

                {/* API 接口选择 */}
                {taskSourceTab === "api" && (
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-muted-foreground">已选 {form.api_endpoint_ids.length} 个接口</span>
                      {endpoints.length > 0 && (
                        <button onClick={toggleSelectAll} className="text-xs text-primary hover:underline shrink-0 ml-2">
                          {endpoints.every(e => form.api_endpoint_ids.includes(e.id)) ? "取消全选" : "全选"}
                        </button>
                      )}
                    </div>
                    {/* 文件夹筛选 */}
                    <select
                      value={selectedFolderId}
                      onChange={e => setSelectedFolderId(e.target.value)}
                      className="w-full text-sm rounded border bg-background px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring"
                    >
                      <option value="all">全部文件夹</option>
                      {folders.map(f => (
                        <option key={f.id} value={f.id}>{f.name}</option>
                      ))}
                    </select>
                    {/* 搜索 */}
                    <Input
                      value={endpointSearch}
                      onChange={e => setEndpointSearch(e.target.value)}
                      placeholder="搜索接口..."
                      className="text-sm"
                    />
                    <div className="max-h-52 overflow-y-auto rounded border divide-y">
                      {endpointsLoading ? (
                        <div className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
                          <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                          加载中...
                        </div>
                      ) : filteredEndpoints.length === 0 ? (
                        <p className="p-3 text-sm text-muted-foreground">
                          {endpointSearch.trim() ? "未找到匹配的接口" : "该文件夹下暂无已生成测试脚本的接口"}
                        </p>
                      ) : (
                        <>
                          <div className="px-3 py-1.5 bg-muted/40 text-[11px] text-muted-foreground flex justify-between">
                            <span>共 {filteredEndpoints.length} 个接口</span>
                            <span>已选 {filteredEndpoints.filter(ep => form.api_endpoint_ids.includes(ep.id)).length} 个</span>
                          </div>
                          {filteredEndpoints.map(ep => (
                            <label key={ep.id} className="flex items-center gap-2 px-3 py-1.5 hover:bg-muted/50 cursor-pointer">
                              <input type="checkbox" checked={form.api_endpoint_ids.includes(ep.id)} onChange={() => toggleEndpoint(ep.id)} />
                              <span className={`text-[11px] font-mono font-semibold shrink-0 w-12 text-right ${METHOD_COLOR[ep.method] ?? "text-muted-foreground"}`}>{ep.method}</span>
                              <div className="min-w-0 flex-1">
                                <p className="text-sm truncate">{ep.display_name}</p>
                                <p className="text-[11px] text-muted-foreground truncate">{ep.path}</p>
                              </div>
                            </label>
                          ))}
                        </>
                      )}
                    </div>
                  </div>
                )}

                {/* 场景用例选择 */}
                {taskSourceTab === "scenario" && (
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-muted-foreground">已选 {form.scenario_ids.length} 个场景</span>
                      {filteredScenarios.length > 0 && (
                        <button onClick={toggleSelectAllScenarios} className="text-xs text-primary hover:underline shrink-0 ml-2">
                          {filteredScenarios.every(s => form.scenario_ids.includes(s.id)) ? "取消全选" : "全选"}
                        </button>
                      )}
                    </div>
                    {/* 搜索 */}
                    <Input
                      value={scenarioSearch}
                      onChange={e => setScenarioSearch(e.target.value)}
                      placeholder="搜索场景..."
                      className="text-sm"
                    />
                    <div className="max-h-52 overflow-y-auto rounded border divide-y">
                      {scenariosLoading ? (
                        <div className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
                          <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                          加载中...
                        </div>
                      ) : filteredScenarios.length === 0 ? (
                        <p className="p-3 text-sm text-muted-foreground">
                          {scenarioSearch.trim() ? "未找到匹配的场景" : "暂无场景用例，请先在场景测试中创建"}
                        </p>
                      ) : (
                        <>
                          <div className="px-3 py-1.5 bg-muted/40 text-[11px] text-muted-foreground flex justify-between">
                            <span>共 {filteredScenarios.length} 个场景</span>
                            <span>已选 {filteredScenarios.filter(s => form.scenario_ids.includes(s.id)).length} 个</span>
                          </div>
                          {filteredScenarios.map(s => (
                            <label key={s.id} className="flex items-center gap-2 px-3 py-2 hover:bg-muted/50 cursor-pointer">
                              <input type="checkbox" checked={form.scenario_ids.includes(s.id)} onChange={() => toggleScenario(s.id)} />
                              <Workflow className="h-3.5 w-3.5 text-orange-500 shrink-0" />
                              <div className="min-w-0 flex-1">
                                <div className="flex items-center gap-2">
                                  <p className="text-sm truncate font-medium">{s.name}</p>
                                  <Badge variant={s.status === "active" ? "success" : "secondary"} className="text-[10px] h-4 px-1">
                                    {s.status === "active" ? "启用" : s.status === "draft" ? "草稿" : "归档"}
                                  </Badge>
                                </div>
                                <div className="flex items-center gap-2 mt-0.5">
                                  <span className="text-[11px] text-muted-foreground">{s.identifier}</span>
                                  <span className="text-[11px] text-muted-foreground">·</span>
                                  <span className="text-[11px] text-muted-foreground">{s.total_steps} 个步骤</span>
                                  {s.description && (
                                    <>
                                      <span className="text-[11px] text-muted-foreground">·</span>
                                      <span className="text-[11px] text-muted-foreground truncate">{s.description}</span>
                                    </>
                                  )}
                                </div>
                              </div>
                            </label>
                          ))}
                        </>
                      )}
                    </div>
                  </div>
                )}
              </div>

              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <Label>执行配置 (JSON)</Label>
                  <button
                    type="button"
                    onClick={() => {
                      setForm(f => ({ ...f, execution_config: EXECUTION_CONFIG_EXAMPLE }));
                      toast.success("已填入示例配置");
                    }}
                    className="text-xs text-primary hover:underline flex items-center gap-0.5"
                  >
                    <Copy className="h-3 w-3" />
                    填入示例
                  </button>
                </div>
                <Textarea value={form.execution_config} onChange={e => setForm(f => ({ ...f, execution_config: e.target.value }))}
                  rows={4} className="font-mono text-xs" placeholder='{"base_url": "http://localhost:8000"}' />
                <div className="rounded-md bg-muted/50 border px-3 py-2 space-y-1.5">
                  <p className="text-[11px] font-medium text-muted-foreground">字段说明</p>
                  <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px]">
                    <code className="text-primary font-mono">base_url</code>
                    <span className="text-muted-foreground">API 基础地址，执行测试时的目标服务 URL</span>
                    <code className="text-primary font-mono">environment</code>
                    <span className="text-muted-foreground">环境标识，如 DEV / UAT / PROD</span>
                    <code className="text-primary font-mono">timeout</code>
                    <span className="text-muted-foreground">请求超时时间（毫秒），默认 30000</span>
                    <code className="text-primary font-mono">headers</code>
                    <span className="text-muted-foreground">全局请求头，如 {"{"}&quot;Authorization&quot;: &quot;Bearer xxx&quot;{"}"}</span>
                  </div>
                </div>
              </div>

              {/* 启用状态 */}
              <div className="flex items-center justify-between rounded-md border px-3 py-2.5">
                <div>
                  <p className="text-sm font-medium">任务状态</p>
                  <p className="text-[11px] text-muted-foreground">禁用后定时任务不会自动执行，但仍可手动触发</p>
                </div>
                <button
                  type="button"
                  onClick={() => setForm(f => ({ ...f, is_active: !f.is_active }))}
                  className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                    form.is_active ? "bg-primary" : "bg-muted-foreground/30"
                  }`}
                >
                  <span
                    className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                      form.is_active ? "translate-x-4" : "translate-x-0"
                    }`}
                  />
                </button>
              </div>
            </div>
            <DialogFooter className="px-5 py-4 border-t shrink-0 gap-2">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>取消</Button>
              <Button onClick={handleSubmit} disabled={submitting}>{submitting ? "保存中..." : editing ? "保存" : "创建"}</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* 删除确认 */}
        <Dialog open={!!deleteTarget} onOpenChange={v => !v && setDeleteTarget(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>删除测试调度</DialogTitle>
              <DialogDescription>确定要删除「{deleteTarget?.name}」吗？此操作不可撤销。</DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
              <Button variant="destructive" onClick={handleDelete}>删除</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </MainLayout>
    </TooltipProvider>
  );
}
