"use client";

/**
 * Web测试可视化报告按钮组件
 *
 * 在测试运行详情页面的脚本作业列表中，替换原有的"报告"按钮，
 * 提供图标可视化的报告展示方式。
 *
 * 使用方式:
 * 1. 在测试运行详情页面中引入此组件
 * 2. 点击"可视化报告"按钮打开仪表盘弹窗
 */

import * as React from "react";
import {
  BarChart3,
  TrendingUp,
  PieChart,
  FileText,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  VisualReportDialog,
  VisualReportDashboard,
} from "./visual-report-dashboard";
import type { WebTestReport, ScriptType, ApiIo } from "./visual-report-dashboard";

// ==============================================================================
// 报告解析工具
// ==============================================================================

/**
 * 从Playwright JSON报告解析为可视化数据
 */
export function parsePlaywrightJsonReport(jsonData: any): WebTestReport {
  const suites: any[] = [];
  let totalTests = 0;
  let passed = 0;
  let failed = 0;
  let skipped = 0;
  let timedOut = 0;
  let totalDuration = 0;

  // 遍历suites
  jsonData.suites?.forEach((suite: any) => {
    const tests: any[] = [];

    suite.specs?.forEach((spec: any) => {
      spec.tests?.forEach((test: any) => {
        const result = test.results?.[0];
        const status = result?.status || "skipped";
        const duration = result?.duration || 0;

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
          errors: result?.errors?.map((e: any) => e.message || e.text || String(e)) || [],
          steps: result?.steps?.map((step: any) => ({
            title: step.title || step.name || "未命名步骤",
            status: step.error ? "failed" : "passed",
            duration: step.duration || 0,
          })) || [],
        });
      });
    });

    suites.push({
      title: suite.title || "未命名套件",
      tests,
      totalDuration: tests.reduce((sum: number, t: any) => sum + t.duration, 0),
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
    startTime: jsonData.startTime || new Date().toISOString(),
    endTime: jsonData.endTime || new Date().toISOString(),
    browser: jsonData.config?.projects?.[0]?.name || "Chromium",
    viewport: `${jsonData.config?.projects?.[0]?.use?.viewport?.width || 1280}x${jsonData.config?.projects?.[0]?.use?.viewport?.height || 720}`,
  };
}

const SUITE_TITLE_BY_TYPE: Record<ScriptType, string> = {
  api_test: "API 自动化测试",
  web_test: "Web 自动化测试",
  scenario: "场景自动化测试",
  test_case: "测试用例执行",
};

/**
 * 从result_summary解析报告数据
 */
/** 后端 summary.tests[].request / .response → 前端 ApiIo（缺失或非法返回 undefined） */
function toApiIo(raw: unknown): ApiIo | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const value = raw as Record<string, unknown>;
  return {
    method: value.method ? String(value.method) : undefined,
    url: value.url ? String(value.url) : undefined,
    status: typeof value.status === "number" ? value.status : undefined,
    headers:
      value.headers && typeof value.headers === "object"
        ? (value.headers as Record<string, unknown>)
        : undefined,
    body: value.body,
    error: value.error ? String(value.error) : undefined,
  };
}

export function parseResultSummary(
  summary: Record<string, unknown> | null,
  scriptType: ScriptType = "web_test"
): WebTestReport {
  const total = Number(summary?.total || 0);
  const passed = Number(summary?.passed || 0);
  const failed = Number(summary?.failed || 0);
  const skipped = Number(summary?.skipped || 0);
  const tests = Array.isArray(summary?.tests) ? summary.tests : [];

  if (tests.length > 0) {
    return {
      totalTests: total,
      passed,
      failed,
      skipped,
      timedOut: 0,
      totalDuration: tests.reduce((sum, test: any) => sum + Number(test?.duration_ms || 0), 0),
      suites: [{
        title: SUITE_TITLE_BY_TYPE[scriptType],
        tests: tests.map((test: any) => ({
          title: String(test?.title || "未命名测试"),
          status: test?.status === "failed" ? "failed" : test?.status === "passed" ? "passed" : "skipped",
          duration: Number(test?.duration_ms || 0),
          errors: Array.isArray(test?.errors) ? test.errors.map(String) : [],
          steps: Array.isArray(test?.steps) ? test.steps.map((step: any) => ({
            title: String(step?.title || "未命名步骤"),
            status: step?.status === "failed" ? "failed" : "passed",
            duration: Number(step?.duration_ms || 0),
          })) : [],
          // ---- 详细内容：请求 / 断言 / 原因分析 ----
          location: test?.location ? String(test.location) : undefined,
          // ---- 入参 / 出参（脚本侧 testInfo.attach 留证，后端离线解析）----
          request: toApiIo(test?.request),
          response: toApiIo(test?.response),
          apiSteps: Array.isArray(test?.api_steps) ? test.api_steps.map((s: any) => ({
            title: String(s?.title || ""),
            status: s?.status === "failed" ? "failed" : "passed",
            duration: Number(s?.duration_ms || 0),
            assertions: Array.isArray(s?.assertions) ? s.assertions.map((a: any) => ({
              matcher: String(a?.matcher || "断言"),
              expression: String(a?.expression || ""),
              expected: a?.expected ?? null,
              received: a?.received ?? null,
              passed: Boolean(a?.passed),
            })) : [],
            location: s?.location ? String(s.location) : undefined,
          })) : [],
          assertions: Array.isArray(test?.assertions) ? test.assertions.map((a: any) => ({
            matcher: String(a?.matcher || "断言"),
            expression: String(a?.expression || ""),
            expected: a?.expected ?? null,
            received: a?.received ?? null,
            passed: Boolean(a?.passed),
          })) : [],
          failureAnalysis: test?.failure_analysis ? {
            category: String(test.failure_analysis.category || "unknown"),
            label: String(test.failure_analysis.label || ""),
            detail: String(test.failure_analysis.detail || ""),
            suggestion: String(test.failure_analysis.suggestion || ""),
            http_status_code: test.failure_analysis.http_status_code ?? null,
            is_product_defect: Boolean(test.failure_analysis.is_product_defect),
          } : undefined,
        })),
        totalDuration: tests.reduce((sum, test: any) => sum + Number(test?.duration_ms || 0), 0),
      }],
      startTime: new Date().toISOString(),
      endTime: new Date().toISOString(),
      browser: scriptType === "api_test" ? "API request" : "Chromium",
      viewport: "-",
      analysis: summary?.analysis ? {
        total: Number((summary.analysis as Record<string, unknown>).total || 0),
        passed: Number((summary.analysis as Record<string, unknown>).passed || 0),
        failed: Number((summary.analysis as Record<string, unknown>).failed || 0),
        pass_rate: Number((summary.analysis as Record<string, unknown>).pass_rate || 0),
        product_defect_count: Number((summary.analysis as Record<string, unknown>).product_defect_count || 0),
        env_issue_count: Number((summary.analysis as Record<string, unknown>).env_issue_count || 0),
        failed_by_category: ((summary.analysis as Record<string, unknown>).failed_by_category || {}) as Record<string, number>,
        conclusion: String((summary.analysis as Record<string, unknown>).conclusion || ""),
      } : undefined,
    };
  }

  return {
    totalTests: total,
    passed,
    failed,
    skipped,
    timedOut: 0,
    totalDuration: Number(summary?.duration_ms || 0),
    suites: [
      {
        title: SUITE_TITLE_BY_TYPE[scriptType],
        tests: [
          {
            title: "测试执行汇总",
            status: failed > 0 ? "failed" : "passed",
            duration: Number(summary?.duration_ms || 0),
            errors: [],
            steps: [],
          },
        ],
        totalDuration: Number(summary?.duration_ms || 0),
      },
    ],
    startTime: new Date().toISOString(),
    endTime: new Date().toISOString(),
    browser: "Chromium",
    viewport: "1280x720",
  };
}

// ==============================================================================
// 可视化报告按钮组件
// ==============================================================================

interface VisualReportButtonProps {
  /** 报告HTML内容（可选） */
  reportHtml?: string;
  /** 报告JSON数据（可选） */
  reportJson?: any;
  /** 结果摘要（可选） */
  resultSummary?: Record<string, unknown> | null;
  /** 作业名称 */
  jobName?: string;
  /** 脚本类型 */
  scriptType?: ScriptType;
  /** 按钮变体 */
  variant?: "default" | "outline" | "ghost";
  /** 按钮大小 */
  size?: "default" | "sm" | "lg" | "icon";
  /** 自定义类名 */
  className?: string;
}

export function VisualReportButton({
  reportHtml,
  reportJson,
  resultSummary,
  jobName,
  scriptType = "web_test",
  variant = "ghost",
  size = "sm",
  className,
}: VisualReportButtonProps) {
  const [open, setOpen] = React.useState(false);
  const [reportData, setReportData] = React.useState<WebTestReport | undefined>();

  const handleClick = () => {
    // 尝试解析报告数据
    if (reportJson) {
      try {
        const parsed = parsePlaywrightJsonReport(reportJson);
        setReportData(parsed);
      } catch (e) {
        console.error("解析报告JSON失败:", e);
      }
    } else if (resultSummary) {
      setReportData(parseResultSummary(resultSummary, scriptType));
    }

    setOpen(true);
  };

  return (
    <>
      <Button
        variant={variant}
        size={size}
        className={className}
        onClick={handleClick}
      >
        <BarChart3 className="mr-1.5 h-3.5 w-3.5" />
        可视化报告
      </Button>

      <VisualReportDialog
        open={open}
        onOpenChange={setOpen}
        reportHtml={reportHtml}
        reportData={reportData}
        jobName={jobName}
        scriptType={scriptType}
      />
    </>
  );
}

// ==============================================================================
// 内联可视化报告组件（用于嵌入页面）
// ==============================================================================

interface InlineVisualReportProps {
  reportJson?: any;
  resultSummary?: Record<string, unknown> | null;
  jobName?: string;
  scriptType?: ScriptType;
}

export function InlineVisualReport({
  reportJson,
  resultSummary,
  jobName,
  scriptType = "web_test",
}: InlineVisualReportProps) {
  const [reportData, setReportData] = React.useState<WebTestReport | undefined>();

  React.useEffect(() => {
    if (reportJson) {
      try {
        const parsed = parsePlaywrightJsonReport(reportJson);
        setReportData(parsed);
      } catch (e) {
        console.error("解析报告失败:", e);
      }
    } else if (resultSummary) {
      setReportData(parseResultSummary(resultSummary, scriptType));
    }
  }, [reportJson, resultSummary]);

  if (!reportData) {
    return (
      <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
        <TrendingUp className="mr-2 h-4 w-4" />
        暂无报告数据
      </div>
    );
  }

  return (
    <VisualReportDashboard
      reportData={reportData}
      jobName={jobName}
      scriptType={scriptType}
    />
  );
}

export { VisualReportDialog, VisualReportDashboard };
export type { WebTestReport };
