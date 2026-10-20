"use client";

/**
 * Web测试可视化报告组件
 *
 * 将Playwright HTML报告解析为图标可视化的仪表盘格式
 * 替代原有的zip文件下载方式
 */

import * as React from "react";
import {
  CheckCircle2,
  XCircle,
  AlertCircle,
  Clock,
  Play,
  FileText,
  Camera,
  TrendingUp,
  Activity,
  Monitor,
  Bug,
  Timer,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Send,
  ShieldCheck,
  Lightbulb,
  FileSearch,
  StickyNote,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

// ==============================================================================
// 类型定义
// ==============================================================================

export interface TestCaseResult {
  title: string;
  status: "passed" | "failed" | "skipped" | "timedOut";
  duration: number;
  errors: string[];
  steps: TestStep[];
  screenshot?: string;
  // ---- 详细内容（请求 / 断言 / 原因分析），由后端 report-summary 提供 ----
  location?: string;
  apiSteps?: ApiRequestStep[];
  assertions?: AssertionDetail[];
  failureAnalysis?: FailureAnalysis;
  /** 入参（该用例实际发送的请求，已脱敏） */
  request?: ApiIo;
  /** 出参（该用例实际收到的响应，已脱敏） */
  response?: ApiIo;
}

/** API 请求步骤（含断言明细），用于报告详情核对 */
export interface ApiRequestStep {
  title: string;
  status: "passed" | "failed" | "skipped";
  duration: number;
  assertions?: AssertionDetail[];
  location?: string;
}

/** 断言明细：期望值 / 实际值 / 断言类型 */
/** 入参 / 出参：脚本侧 testInfo.attach 留证，后端 report-summary 离线解析 */
export interface ApiIo {
  method?: string;
  url?: string;
  status?: number;
  headers?: Record<string, unknown>;
  body?: unknown;
  error?: string;
}

/** 断言明细：期望值 / 实际值 / 断言类型 */
export interface AssertionDetail {
  matcher: string;
  expression: string;
  expected: string | null;
  received: string | null;
  passed: boolean;
}

/** 失败原因分析（确定性规则分类） */
export interface FailureAnalysis {
  category: string;
  label: string;
  detail: string;
  suggestion: string;
  http_status_code?: number | null;
  is_product_defect?: boolean;
}

/** 报告级分析结论 */
export interface ReportAnalysis {
  total: number;
  passed: number;
  failed: number;
  pass_rate: number;
  product_defect_count: number;
  env_issue_count: number;
  failed_by_category: Record<string, number>;
  conclusion: string;
}

export interface TestStep {
  title: string;
  status: "passed" | "failed" | "skipped";
  duration: number;
  screenshot?: string;
}

export interface TestSuite {
  title: string;
  tests: TestCaseResult[];
  totalDuration: number;
}

export interface WebTestReport {
  totalTests: number;
  passed: number;
  failed: number;
  skipped: number;
  timedOut: number;
  totalDuration: number;
  suites: TestSuite[];
  startTime: string;
  endTime: string;
  browser: string;
  viewport: string;
  /** 报告级分析结论（后端确定性规则生成，可选） */
  analysis?: ReportAnalysis;
}

export type ScriptType = "api_test" | "scenario" | "web_test" | "test_case";

// ==============================================================================
// 脚本类型配置
// ==============================================================================

const SCRIPT_TYPE_CONFIG: Record<ScriptType, { title: string; icon: React.ElementType; showBrowserInfo: boolean }> = {
  api_test: { title: "API 测试执行报告", icon: Activity, showBrowserInfo: false },
  web_test: { title: "Web 测试执行报告", icon: Activity, showBrowserInfo: true },
  scenario: { title: "场景测试执行报告", icon: Activity, showBrowserInfo: false },
  test_case: { title: "测试用例执行报告", icon: Activity, showBrowserInfo: false },
};

// ==============================================================================
// 状态图标映射
// ==============================================================================

const STATUS_CONFIG = {
  passed: {
    icon: CheckCircle2,
    color: "text-green-500",
    bgColor: "bg-green-50 dark:bg-green-950/30",
    borderColor: "border-green-200 dark:border-green-800",
    label: "通过",
    progressColor: "bg-green-500",
  },
  failed: {
    icon: XCircle,
    color: "text-red-500",
    bgColor: "bg-red-50 dark:bg-red-950/30",
    borderColor: "border-red-200 dark:border-red-800",
    label: "失败",
    progressColor: "bg-red-500",
  },
  skipped: {
    icon: AlertCircle,
    color: "text-amber-500",
    bgColor: "bg-amber-50 dark:bg-amber-950/30",
    borderColor: "border-amber-200 dark:border-amber-800",
    label: "跳过",
    progressColor: "bg-amber-400",
  },
  timedOut: {
    icon: Clock,
    color: "text-orange-500",
    bgColor: "bg-orange-50 dark:bg-orange-950/30",
    borderColor: "border-orange-200 dark:border-orange-800",
    label: "超时",
    progressColor: "bg-orange-500",
  },
};

// ==============================================================================
// 辅助函数
// ==============================================================================

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}min`;
}

function parsePlaywrightReport(htmlContent: string): WebTestReport | null {
  try {
    // 尝试从Playwright HTML报告中提取数据
    // Playwright报告通常包含一个嵌入的JSON数据
    const jsonMatch = htmlContent.match(/window\.playwrightReport\s*=\s*({.+?});/);
    if (jsonMatch) {
      const data = JSON.parse(jsonMatch[1]);
      return transformPlaywrightData(data);
    }

    // 备选：尝试从DOM结构解析
    const parser = new DOMParser();
    const doc = parser.parseFromString(htmlContent, "text/html");

    // 提取统计信息
    const summaryText = doc.querySelector(".test-summary, .summary, [class*='summary']")?.textContent || "";
    const totalMatch = summaryText.match(/(\d+)\s*passed/);
    const failedMatch = summaryText.match(/(\d+)\s*failed/);

    return {
      totalTests: parseInt(totalMatch?.[1] || "0") + parseInt(failedMatch?.[1] || "0"),
      passed: parseInt(totalMatch?.[1] || "0"),
      failed: parseInt(failedMatch?.[1] || "0"),
      skipped: 0,
      timedOut: 0,
      totalDuration: 0,
      suites: [],
      startTime: new Date().toISOString(),
      endTime: new Date().toISOString(),
      browser: "Chromium",
      viewport: "1280x720",
    };
  } catch (e) {
    console.error("解析报告失败:", e);
    return null;
  }
}

function transformPlaywrightData(data: any): WebTestReport {
  const suites: TestSuite[] = [];
  let totalTests = 0;
  let passed = 0;
  let failed = 0;
  let skipped = 0;
  let timedOut = 0;
  let totalDuration = 0;

  // 遍历suites
  data.suites?.forEach((suite: any) => {
    const tests: TestCaseResult[] = [];

    suite.specs?.forEach((spec: any) => {
      spec.tests?.forEach((test: any) => {
        const status = test.results[0]?.status || "skipped";
        const duration = test.results[0]?.duration || 0;

        totalTests++;
        if (status === "passed") passed++;
        else if (status === "failed") failed++;
        else if (status === "skipped") skipped++;
        else if (status === "timedOut") timedOut++;

        totalDuration += duration;

        tests.push({
          title: spec.title,
          status,
          duration,
          errors: test.results[0]?.errors?.map((e: any) => e.message) || [],
          steps: [],
        });
      });
    });

    suites.push({
      title: suite.title,
      tests,
      totalDuration: tests.reduce((sum, t) => sum + t.duration, 0),
    });
  });

  return {
    totalTests,
    passed,
    failed,
    skipped,
    timedOut,
    totalDuration,
    suites,
    startTime: data.startTime || new Date().toISOString(),
    endTime: data.endTime || new Date().toISOString(),
    browser: data.config?.projects?.[0]?.name || "Chromium",
    viewport: `${data.config?.projects?.[0]?.use?.viewport?.width || 1280}x${data.config?.projects?.[0]?.use?.viewport?.height || 720}`,
  };
}

// ==============================================================================
// 子组件
// ==============================================================================

/**
 * 统计卡片组件
 */
function StatCard({
  icon: Icon,
  label,
  value,
  color,
  bgColor,
  subtext,
}: {
  icon: React.ElementType;
  label: string;
  value: string | number;
  color: string;
  bgColor: string;
  subtext?: string;
}) {
  return (
    <div className={cn("rounded-xl border p-4 transition-all hover:shadow-md", bgColor)}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className={cn("rounded-lg p-2", color.replace("text-", "bg-").replace("500", "100"))}>
            <Icon className={cn("h-5 w-5", color)} />
          </div>
          <span className="text-sm text-muted-foreground">{label}</span>
        </div>
      </div>
      <div className="mt-3">
        <span className="text-3xl font-bold">{value}</span>
        {subtext && (
          <span className="ml-2 text-sm text-muted-foreground">{subtext}</span>
        )}
      </div>
    </div>
  );
}

/**
 * 环形进度组件
 */
function CircularProgress({
  value,
  size = 120,
  strokeWidth = 8,
  color = "#22c55e",
  label,
}: {
  value: number;
  size?: number;
  strokeWidth?: number;
  color?: string;
  label?: string;
}) {
  const radius = (size - strokeWidth) / 2;
  const circumference = radius * 2 * Math.PI;
  const offset = circumference - (value / 100) * circumference;

  return (
    <div className="relative inline-flex items-center justify-center">
      <svg width={size} height={size} className="transform -rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="#e5e7eb"
          strokeWidth={strokeWidth}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          className="transition-all duration-1000 ease-out"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl font-bold">{value}%</span>
        {label && <span className="text-xs text-muted-foreground">{label}</span>}
      </div>
    </div>
  );
}

/**
 * 测试套件展开组件
 */
function TestSuiteItem({ suite, index }: { suite: TestSuite; index: number }) {
  const [expanded, setExpanded] = React.useState(false);
  const passed = suite.tests.filter((t) => t.status === "passed").length;
  const failed = suite.tests.filter((t) => t.status === "failed").length;
  const passRate = suite.tests.length > 0 ? Math.round((passed / suite.tests.length) * 100) : 0;

  return (
    <div className="rounded-lg border bg-card overflow-hidden">
      {/* 套件头部 */}
      <button
        className="w-full px-4 py-3 flex items-center justify-between hover:bg-muted/50 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-3">
          {expanded ? (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
          )}
          <div className="flex items-center gap-2">
            <Monitor className="h-4 w-4 text-blue-500" />
            <span className="font-medium">{suite.title}</span>
          </div>
          <Badge variant="outline" className="text-xs">
            {suite.tests.length} 个测试
          </Badge>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1 text-sm">
            <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
            <span className="text-green-600">{passed}</span>
            {failed > 0 && (
              <>
                <XCircle className="h-3.5 w-3.5 text-red-500 ml-2" />
                <span className="text-red-600">{failed}</span>
              </>
            )}
          </div>
          <span className="text-xs text-muted-foreground w-12 text-right">
            {formatDuration(suite.totalDuration)}
          </span>
        </div>
      </button>

      {/* 测试列表 */}
      {expanded && (
        <div className="border-t divide-y">
          {suite.tests.map((test, i) => (
            <TestCaseDetailItem key={i} test={test} />
          ))}
        </div>
      )}

      {/* 进度条 */}
      <div className="px-4 py-2 bg-muted/20 border-t">
        <div className="flex items-center gap-2">
          <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
            <div
              className="h-full bg-green-500 rounded-full transition-all"
              style={{ width: `${passRate}%` }}
            />
          </div>
          <span className="text-xs text-muted-foreground w-10 text-right">
            {passRate}%
          </span>
        </div>
      </div>
    </div>
  );
}

/**
 * 单个测试用例明细（可展开：请求 / 断言 / 错误 / 原因分析）
 */
/** 入参 / 出参明细块：方法 / URL / 状态码 + 报文（脚本侧已脱敏） */
function ApiIoBlock({ label, io }: { label: string; io: ApiIo }) {
  const meta = [io.method, io.url, io.status !== undefined ? `HTTP ${io.status}` : undefined]
    .filter(Boolean)
    .join(" ");
  const payload = io.error
    ? io.error
    : JSON.stringify(io.body ?? io.headers ?? {}, null, 2);
  return (
    <div className="rounded-md border bg-background p-2.5 space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium">{label}</span>
        {meta && (
          <span className="text-[11px] text-muted-foreground font-mono break-all">{meta}</span>
        )}
      </div>
      <pre className="max-h-56 overflow-auto rounded bg-muted/40 p-2 text-[11px] font-mono whitespace-pre-wrap break-all">
        {payload}
      </pre>
    </div>
  );
}

function TestCaseDetailItem({ test }: { test: TestCaseResult }) {
  const [detailOpen, setDetailOpen] = React.useState(false);
  const config = STATUS_CONFIG[test.status];
  const Icon = config.icon;
  const hasDetail =
    (test.apiSteps && test.apiSteps.length > 0) ||
    (test.assertions && test.assertions.length > 0) ||
    Boolean(test.request || test.response) ||
    test.errors.length > 0 ||
    (test.failureAnalysis && test.failureAnalysis.category !== "none");

  return (
    <div
      className={cn(
        "px-4 py-3 transition-colors",
        test.status === "failed" ? "bg-red-50/50 dark:bg-red-950/10" : "hover:bg-muted/30"
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <Icon className={cn("h-4 w-4 shrink-0", config.color)} />
          <span className="text-sm truncate">{test.title}</span>
          {test.location && (
            <span className="text-[11px] text-muted-foreground font-mono shrink-0 hidden md:inline">
              {test.location}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 shrink-0">
          {test.failureAnalysis && test.failureAnalysis.category !== "none" && (
            <Badge
              variant={test.failureAnalysis.is_product_defect ? "destructive" : "secondary"}
              className="text-xs"
            >
              {test.failureAnalysis.is_product_defect ? (
                <Bug className="h-3 w-3 mr-1" />
              ) : (
                <AlertCircle className="h-3 w-3 mr-1" />
              )}
              {test.failureAnalysis.label}
            </Badge>
          )}
          {test.errors.length > 0 && (
            <Badge variant="destructive" className="text-xs">
              <Bug className="h-3 w-3 mr-1" />
              {test.errors.length} 错误
            </Badge>
          )}
          <span className="text-xs text-muted-foreground font-mono">
            {formatDuration(test.duration)}
          </span>
          {hasDetail && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs"
              onClick={() => setDetailOpen(!detailOpen)}
            >
              {detailOpen ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
              详情
            </Button>
          )}
        </div>
      </div>

      {/* 概要步骤（兼容旧数据） */}
      {!detailOpen && test.steps.length > 0 && (
        <div className="ml-7 mt-2 space-y-1 border-l pl-3 text-xs">
          {test.steps.map((step, stepIndex) => (
            <div
              key={stepIndex}
              className={cn(
                "flex items-center justify-between gap-3",
                step.status === "failed" ? "text-red-600" : "text-muted-foreground"
              )}
            >
              <span>{step.title}</span>
              <span className="shrink-0 font-mono">{formatDuration(step.duration)}</span>
            </div>
          ))}
        </div>
      )}

      {/* 详细内容：请求 / 断言 / 错误 / 原因分析 */}
      {detailOpen && (
        <div className="ml-7 mt-3 space-y-3 border-l-2 border-muted pl-4">
          {/* 失败原因分析 */}
          {test.failureAnalysis && test.failureAnalysis.category !== "none" && (
            <div className="rounded-md border bg-background p-3 space-y-1.5">
              <div className="flex items-center gap-2 text-xs font-medium">
                <FileSearch className="h-3.5 w-3.5 text-purple-500" />
                失败原因分析
                {test.failureAnalysis.http_status_code && (
                  <Badge variant="outline" className="text-[10px] font-mono">
                    HTTP {test.failureAnalysis.http_status_code}
                  </Badge>
                )}
                {test.failureAnalysis.is_product_defect ? (
                  <Badge variant="destructive" className="text-[10px]">疑似产品缺陷</Badge>
                ) : (
                  <Badge variant="secondary" className="text-[10px]">非产品缺陷</Badge>
                )}
              </div>
              <p className="text-xs text-muted-foreground">{test.failureAnalysis.detail}</p>
              {test.failureAnalysis.suggestion && (
                <div className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
                  <Lightbulb className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                  <span>{test.failureAnalysis.suggestion}</span>
                </div>
              )}
            </div>
          )}

          {/* API 请求步骤 + 断言 */}
          {/* 入参 / 出参（实际发送的请求 / 收到的响应） */}
          {(test.request || test.response) && (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <Send className="h-3.5 w-3.5 text-blue-500" />
                入参 / 出参
              </div>
              {test.request && <ApiIoBlock label="入参（实际请求）" io={test.request} />}
              {test.response && <ApiIoBlock label="出参（实际响应）" io={test.response} />}
            </div>
          )}
          {test.apiSteps && test.apiSteps.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <Send className="h-3.5 w-3.5 text-blue-500" />
                请求明细（{test.apiSteps.length}）
              </div>
              {test.apiSteps.map((step, idx) => (
                <div key={idx} className="rounded-md border bg-background p-2.5 space-y-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <code className="text-[11px] font-mono break-all">{step.title}</code>
                    <span className="flex items-center gap-2 shrink-0 text-[11px] text-muted-foreground">
                      {step.location && (
                        <span className="font-mono hidden lg:inline">{step.location}</span>
                      )}
                      <span className="font-mono">{formatDuration(step.duration)}</span>
                      {step.status === "failed" ? (
                        <XCircle className="h-3.5 w-3.5 text-red-500" />
                      ) : (
                        <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
                      )}
                    </span>
                  </div>
                  {step.assertions && step.assertions.length > 0 && (
                    <div className="space-y-1 border-t pt-1.5">
                      {step.assertions.map((a, ai) => (
                        <div key={ai} className="text-[11px] space-y-0.5">
                          <div className="flex items-center gap-1.5">
                            <ShieldCheck className="h-3 w-3 text-muted-foreground shrink-0" />
                            <span className="text-muted-foreground">{a.matcher}</span>
                            {a.passed ? (
                              <Badge variant="secondary" className="text-[10px] h-4 px-1">通过</Badge>
                            ) : (
                              <Badge variant="destructive" className="text-[10px] h-4 px-1">失败</Badge>
                            )}
                          </div>
                          <code className="block font-mono text-muted-foreground break-all pl-4.5">
                            {a.expression}
                          </code>
                          {(a.expected !== null || a.received !== null) && (
                            <div className="pl-4.5 space-y-0.5 font-mono">
                              {a.expected !== null && (
                                <div className="text-green-700 dark:text-green-400 break-all">
                                  期望: {a.expected}
                                </div>
                              )}
                              {a.received !== null && (
                                <div className="text-red-700 dark:text-red-400 break-all">
                                  实际: {a.received}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* 用例级断言汇总（无请求步骤时的兜底展示） */}
          {(!test.apiSteps || test.apiSteps.length === 0) &&
            test.assertions &&
            test.assertions.length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                  <ShieldCheck className="h-3.5 w-3.5 text-blue-500" />
                  断言明细（{test.assertions.length}）
                </div>
                {test.assertions.map((a, ai) => (
                  <div key={ai} className="rounded-md border bg-background p-2.5 text-[11px] space-y-1">
                    <div className="flex items-center gap-1.5">
                      <span className="text-muted-foreground">{a.matcher}</span>
                      {a.passed ? (
                        <Badge variant="secondary" className="text-[10px] h-4 px-1">通过</Badge>
                      ) : (
                        <Badge variant="destructive" className="text-[10px] h-4 px-1">失败</Badge>
                      )}
                    </div>
                    <code className="block font-mono text-muted-foreground break-all">{a.expression}</code>
                    {(a.expected !== null || a.received !== null) && (
                      <div className="space-y-0.5 font-mono">
                        {a.expected !== null && (
                          <div className="text-green-700 dark:text-green-400 break-all">期望: {a.expected}</div>
                        )}
                        {a.received !== null && (
                          <div className="text-red-700 dark:text-red-400 break-all">实际: {a.received}</div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

          {/* 原始错误 */}
          {test.errors.length > 0 && (
            <div className="space-y-1.5">
              <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <Bug className="h-3.5 w-3.5 text-red-500" />
                错误详情（{test.errors.length}）
              </div>
              {test.errors.map((err, ei) => (
                <pre
                  key={ei}
                  className="text-[11px] font-mono bg-muted/50 rounded p-2 overflow-x-auto whitespace-pre-wrap break-all max-h-48"
                >
                  {err}
                </pre>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ==============================================================================
// 主组件：可视化报告仪表盘
// ==============================================================================

interface VisualReportDashboardProps {
  reportHtml?: string;
  reportData?: WebTestReport;
  jobName?: string;
  scriptType?: ScriptType;
  onClose?: () => void;
}

export function VisualReportDashboard({
  reportHtml,
  reportData: initialData,
  jobName,
  scriptType = "web_test",
  onClose,
}: VisualReportDashboardProps) {
  const [report, setReport] = React.useState<WebTestReport | null>(initialData || null);
  const [loading, setLoading] = React.useState(!initialData && !!reportHtml);
  const [selectedSuite, setSelectedSuite] = React.useState<number | null>(null);

  React.useEffect(() => {
    if (reportHtml && !initialData) {
      const parsed = parsePlaywrightReport(reportHtml);
      setReport(parsed);
      setLoading(false);
    }
  }, [reportHtml, initialData]);

  if (loading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
        <span className="ml-3 text-muted-foreground">解析报告中...</span>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="flex h-96 flex-col items-center justify-center gap-4">
        <AlertCircle className="h-12 w-12 text-muted-foreground" />
        <p className="text-muted-foreground">无法解析测试报告</p>
        {reportHtml && (
          <Button variant="outline" onClick={() => window.open(reportHtml, "_blank")}>
            <ExternalLink className="mr-2 h-4 w-4" />
            查看原始报告
          </Button>
        )}
      </div>
    );
  }

  const typeConfig = SCRIPT_TYPE_CONFIG[scriptType];
  const TypeIcon = typeConfig.icon;
  const passRate = report.totalTests > 0
    ? Math.round((report.passed / report.totalTests) * 100)
    : 0;

  return (
    <div className="space-y-6">
      {/* 头部信息 */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold flex items-center gap-2">
            <TypeIcon className="h-5 w-5 text-blue-500" />
            {typeConfig.title}
          </h2>
          {jobName && (
            <p className="text-sm text-muted-foreground mt-1">{jobName}</p>
          )}
        </div>
        {typeConfig.showBrowserInfo && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Monitor className="h-4 w-4" />
            <span>{report.browser}</span>
            <span className="mx-1">·</span>
            <span>{report.viewport}</span>
          </div>
        )}
      </div>

      {/* 核心指标 - 环形图 + 统计卡片 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 通过率环形图 */}
        <div className="lg:col-span-1 rounded-xl border bg-card p-6 flex flex-col items-center justify-center">
          <CircularProgress
            value={passRate}
            size={140}
            strokeWidth={10}
            color={passRate >= 80 ? "#22c55e" : passRate >= 50 ? "#f59e0b" : "#ef4444"}
            label="通过率"
          />
          <div className="mt-4 text-center">
            <div className="text-sm text-muted-foreground">
              总测试数: <span className="font-semibold text-foreground">{report.totalTests}</span>
            </div>
            <div className="text-xs text-muted-foreground mt-1">
              耗时: {formatDuration(report.totalDuration)}
            </div>
          </div>
        </div>

        {/* 统计卡片网格 */}
        <div className="lg:col-span-2 grid grid-cols-2 gap-4">
          <StatCard
            icon={CheckCircle2}
            label="通过"
            value={report.passed}
            color="text-green-500"
            bgColor="bg-green-50/50 dark:bg-green-950/20"
            subtext={`${report.totalTests > 0 ? Math.round((report.passed / report.totalTests) * 100) : 0}%`}
          />
          <StatCard
            icon={XCircle}
            label="失败"
            value={report.failed}
            color="text-red-500"
            bgColor="bg-red-50/50 dark:bg-red-950/20"
            subtext={report.failed > 0 ? "需关注" : ""}
          />
          <StatCard
            icon={AlertCircle}
            label="跳过"
            value={report.skipped}
            color="text-amber-500"
            bgColor="bg-amber-50/50 dark:bg-amber-950/20"
          />
          <StatCard
            icon={Timer}
            label="总耗时"
            value={formatDuration(report.totalDuration)}
            color="text-blue-500"
            bgColor="bg-blue-50/50 dark:bg-blue-950/20"
          />
        </div>
      </div>

      {/* 总体进度条 */}
      <div className="rounded-xl border bg-card p-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium">执行概览</span>
          <span className="text-sm text-muted-foreground">
            {report.passed} 通过 · {report.failed} 失败 · {report.skipped} 跳过 · 共 {report.totalTests}
          </span>
        </div>
        <div className="flex h-3 w-full overflow-hidden rounded-full bg-muted">
          {report.passed > 0 && (
            <div
              className="bg-green-500 transition-all"
              style={{ width: `${(report.passed / report.totalTests) * 100}%` }}
              title={`通过: ${report.passed}`}
            />
          )}
          {report.failed > 0 && (
            <div
              className="bg-red-500 transition-all"
              style={{ width: `${(report.failed / report.totalTests) * 100}%` }}
              title={`失败: ${report.failed}`}
            />
          )}
          {report.skipped > 0 && (
            <div
              className="bg-amber-400 transition-all"
              style={{ width: `${(report.skipped / report.totalTests) * 100}%` }}
              title={`跳过: ${report.skipped}`}
            />
          )}
        </div>
        <div className="flex gap-4 mt-2 text-xs">
          {report.passed > 0 && (
            <span className="flex items-center gap-1">
              <div className="w-2 h-2 rounded-full bg-green-500" />
              通过 {report.passed}
            </span>
          )}
          {report.failed > 0 && (
            <span className="flex items-center gap-1">
              <div className="w-2 h-2 rounded-full bg-red-500" />
              失败 {report.failed}
            </span>
          )}
          {report.skipped > 0 && (
            <span className="flex items-center gap-1">
              <div className="w-2 h-2 rounded-full bg-amber-400" />
              跳过 {report.skipped}
            </span>
          )}
        </div>
      </div>

      {/* 报告级分析结论（准确性评估 + 原因分析） */}
      {report.analysis && report.analysis.total > 0 && (
        <div className="rounded-xl border bg-card p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold flex items-center gap-2">
              <StickyNote className="h-4 w-4 text-indigo-500" />
              测试准确性分析
            </h3>
            <div className="flex items-center gap-2 text-xs">
              {report.analysis.product_defect_count > 0 && (
                <Badge variant="destructive">
                  <Bug className="h-3 w-3 mr-1" />
                  疑似产品缺陷 {report.analysis.product_defect_count}
                </Badge>
              )}
              {report.analysis.env_issue_count > 0 && (
                <Badge variant="secondary">
                  <AlertCircle className="h-3 w-3 mr-1" />
                  环境/脚本问题 {report.analysis.env_issue_count}
                </Badge>
              )}
            </div>
          </div>
          <p className="text-sm text-muted-foreground leading-relaxed">
            {report.analysis.conclusion}
          </p>
          {Object.keys(report.analysis.failed_by_category || {}).length > 0 && (
            <div className="flex flex-wrap gap-2">
              {Object.entries(report.analysis.failed_by_category).map(([cat, count]) => (
                <Badge key={cat} variant="outline" className="text-[11px] font-mono">
                  {cat}: {count}
                </Badge>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 测试套件列表 */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold flex items-center gap-2">
            <FileText className="h-5 w-5 text-purple-500" />
            测试套件详情
          </h3>
          <Badge variant="outline">{report.suites.length} 个套件</Badge>
        </div>

        <div className="space-y-3">
          {report.suites.map((suite, index) => (
            <TestSuiteItem key={index} suite={suite} index={index} />
          ))}
        </div>
      </div>

      {/* 底部操作 */}
      {onClose && (
        <div className="flex justify-end gap-2 pt-4 border-t">
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
          {reportHtml && (
            <Button variant="outline" onClick={() => window.open(reportHtml, "_blank")}>
              <ExternalLink className="mr-2 h-4 w-4" />
              查看原始HTML报告
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

// ==============================================================================
// 对话框包装组件
// ==============================================================================

interface VisualReportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  reportHtml?: string;
  reportData?: WebTestReport;
  jobName?: string;
  scriptType?: ScriptType;
}

export function VisualReportDialog({
  open,
  onOpenChange,
  reportHtml,
  reportData,
  jobName,
  scriptType = "web_test",
}: VisualReportDialogProps) {
  const typeConfig = SCRIPT_TYPE_CONFIG[scriptType];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl w-[90vw] max-h-[90vh] flex flex-col p-0 overflow-hidden">
        <DialogHeader className="px-6 pt-6 pb-2 shrink-0">
          <DialogTitle className="flex items-center gap-2">
            <TrendingUp className="h-5 w-5 text-blue-500" />
            {typeConfig.title}可视化
          </DialogTitle>
          <DialogDescription>
            {jobName || typeConfig.title + "执行结果"}
          </DialogDescription>
        </DialogHeader>
        <div className="flex-1 overflow-y-auto px-6 pb-6 min-h-0">
          <VisualReportDashboard
            reportHtml={reportHtml}
            reportData={reportData}
            jobName={jobName}
            scriptType={scriptType}
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default VisualReportDashboard;
