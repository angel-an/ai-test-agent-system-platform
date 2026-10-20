"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import {
  BarChart3,
  TrendingUp,
  TrendingDown,
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
  FileText,
  Download,
  Loader2,
  PlayCircle,
} from "lucide-react";
import { MainLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  getReportSummary,
  type ReportSummary,
} from "@/lib/api/reports";
import { ApiError } from "@/lib/api/client";

// 优先级颜色映射
const PRIORITY_COLORS: Record<string, string> = {
  critical: "bg-red-500",
  high: "bg-orange-500",
  medium: "bg-yellow-500",
  low: "bg-green-500",
};

// 类型颜色映射
const TYPE_COLORS: Record<string, string> = {
  functional: "bg-blue-500",
  regression: "bg-purple-500",
  smoke_sanity: "bg-green-500",
  performance: "bg-red-500",
  security: "bg-orange-500",
  acceptance: "bg-cyan-500",
  compatibility: "bg-pink-500",
  accessibility: "bg-teal-500",
  usability: "bg-indigo-500",
  destructive: "bg-rose-500",
  other: "bg-gray-500",
};

// 运行状态标签映射
const RUN_STATE_LABELS: Record<string, string> = {
  new_run: "新建",
  in_progress: "进行中",
  under_review: "评审中",
  rejected: "执行失败",
  approved: "已批准",
  done: "已完成",
  closed: "已关闭",
};

export default function ReportsPage() {
  const params = useParams();
  const projectId = params.projectId as string;

  const [dateRange, setDateRange] = React.useState("all");
  const [loading, setLoading] = React.useState(true);
  const [data, setData] = React.useState<ReportSummary | null>(null);
  const [exporting, setExporting] = React.useState(false);

  // 项目切换时重置数据，避免显示旧项目报告
  React.useEffect(() => {
    setData(null);
    setLoading(true);
  }, [projectId]);

  // 加载报告数据
  const loadData = React.useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const response = await getReportSummary(projectId, dateRange);
      setData(response.data);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "加载报告数据失败";
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }, [projectId, dateRange]);

  React.useEffect(() => {
    loadData();
  }, [loadData]);

  // 导出报告为 JSON
  const handleExport = React.useCallback(() => {
    if (!data) return;
    setExporting(true);
    try {
      const exportData = {
        ...data,
        exported_at: new Date().toISOString(),
        project_id: projectId,
      };
      const blob = new Blob([JSON.stringify(exportData, null, 2)], {
        type: "application/json",
      });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `test-report-${projectId}-${new Date().toISOString().split("T")[0]}.json`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      toast.success("报告导出成功");
    } catch {
      toast.error("导出失败");
    } finally {
      setExporting(false);
    }
  }, [data, projectId]);

  // 导出 CSV
  const handleExportCSV = React.useCallback(() => {
    if (!data) return;
    setExporting(true);
    try {
      // CSV 头部
      const headers = ["指标", "数值"];
      const rows = [
        ["测试用例总数", String(data.total_test_cases)],
        ["测试运行总数", String(data.total_test_runs)],
        ["整体通过率", `${data.pass_rate}%`],
        ["待处理缺陷", String(data.open_defects)],
        ["", ""],
        ["优先级", "数量", "百分比"],
        ...data.priority_distribution.map((item) => [
          item.priority,
          String(item.count),
          `${item.percentage}%`,
        ]),
        ["", ""],
        ["类型", "数量"],
        ...data.type_distribution.map((item) => [item.type, String(item.count)]),
        ["", ""],
        ["最近测试运行", "通过", "失败", "跳过", "总计", "通过率"],
        ...data.recent_test_runs.map((run) => [
          run.name,
          String(run.passed),
          String(run.failed),
          String(run.skipped),
          String(run.total),
          `${run.pass_rate}%`,
        ]),
      ];

      const csvContent = rows.map((row) => row.join(",")).join("\n");
      const blob = new Blob(["﻿" + csvContent], { type: "text/csv;charset=utf-8;" });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `test-report-${projectId}-${new Date().toISOString().split("T")[0]}.csv`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      toast.success("CSV 报告导出成功");
    } catch {
      toast.error("导出失败");
    } finally {
      setExporting(false);
    }
  }, [data, projectId]);

  if (loading) {
    return (
      <MainLayout title="报告">
        <div className="flex h-64 items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      </MainLayout>
    );
  }

  if (!data) {
    return (
      <MainLayout title="报告">
        <div className="flex h-64 flex-col items-center justify-center gap-2">
          <AlertTriangle className="h-8 w-8 text-muted-foreground" />
          <p className="text-muted-foreground">加载报告数据失败</p>
          <Button variant="outline" onClick={loadData}>
            重试
          </Button>
        </div>
      </MainLayout>
    );
  }

  const stats = data;

  return (
    <MainLayout title="报告">
      <div className="space-y-6">
        {/* 工具栏 */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Select value={dateRange} onValueChange={setDateRange}>
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="7d">最近 7 天</SelectItem>
                <SelectItem value="30d">最近 30 天</SelectItem>
                <SelectItem value="90d">最近 90 天</SelectItem>
                <SelectItem value="all">全部时间</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleExportCSV}
              disabled={exporting}
            >
              {exporting ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Download className="mr-2 h-4 w-4" />
              )}
              导出 CSV
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleExport}
              disabled={exporting}
            >
              {exporting ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <FileText className="mr-2 h-4 w-4" />
              )}
              导出 JSON
            </Button>
          </div>
        </div>

        {/* 概览卡片 */}
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-lg border bg-card p-4">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">测试用例总数</span>
              <FileText className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className="mt-2 text-2xl font-bold">{stats.total_test_cases}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              {stats.total_test_runs} 个测试运行
            </div>
          </div>

          <div className="rounded-lg border bg-card p-4">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">通过率</span>
              <CheckCircle2 className="h-4 w-4 text-green-500" />
            </div>
            <div className="mt-2 flex items-baseline gap-2">
              <span className="text-2xl font-bold">{stats.pass_rate}%</span>
              <span className="flex items-center text-xs text-green-500">
                <TrendingUp className="h-3 w-3" />
              </span>
            </div>
            <Progress value={stats.pass_rate} className="mt-2" />
          </div>

          <div className="rounded-lg border bg-card p-4">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">平均执行时间</span>
              <Clock className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className="mt-2 flex items-baseline gap-2">
              <span className="text-2xl font-bold">{stats.avg_execution_time}</span>
              <span className="flex items-center text-xs text-green-500">
                <TrendingDown className="h-3 w-3" />
              </span>
            </div>
            <div className="mt-1 text-xs text-muted-foreground">基于最近测试运行</div>
          </div>

          <div className="rounded-lg border bg-card p-4">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">待处理缺陷</span>
              <AlertTriangle className="h-4 w-4 text-yellow-500" />
            </div>
            <div className="mt-2 flex items-baseline gap-2">
              <span className="text-2xl font-bold">{stats.open_defects}</span>
              <span className="flex items-center text-xs text-green-500">
                <TrendingDown className="h-3 w-3" />
              </span>
            </div>
            <div className="mt-1 text-xs text-muted-foreground">较上周减少</div>
          </div>
        </div>

        {/* 详细报告 */}
        <Tabs defaultValue="runs" className="space-y-4">
          <TabsList>
            <TabsTrigger value="runs">测试运行统计</TabsTrigger>
            <TabsTrigger value="priority">优先级分布</TabsTrigger>
            <TabsTrigger value="type">类型分布</TabsTrigger>
          </TabsList>

          <TabsContent value="runs" className="space-y-4">
            <div className="rounded-lg border bg-card">
              <div className="border-b p-4">
                <h3 className="font-medium">最近测试运行结果</h3>
              </div>
              <div className="divide-y">
                {stats.recent_test_runs.length === 0 ? (
                  <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
                    暂无测试运行数据
                  </div>
                ) : (
                  stats.recent_test_runs.map((run) => (
                    <div key={run.id} className="flex items-center gap-4 p-4">
                      <div className="flex-1">
                        <div className="font-medium">{run.name}</div>
                        <div className="mt-1 flex items-center gap-4 text-sm">
                          <span className="flex items-center gap-1 text-green-600">
                            <CheckCircle2 className="h-4 w-4" />
                            {run.passed} 通过
                          </span>
                          <span className="flex items-center gap-1 text-red-600">
                            <XCircle className="h-4 w-4" />
                            {run.failed} 失败
                          </span>
                          <span className="text-muted-foreground">
                            共 {run.total} 个用例
                          </span>
                          <Badge variant="outline" className="text-xs">
                            {RUN_STATE_LABELS[run.run_state] || run.run_state}
                          </Badge>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <Progress
                          value={run.pass_rate}
                          className="w-32"
                        />
                        <span className="w-12 text-right text-sm">
                          {run.pass_rate}%
                        </span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </TabsContent>

          <TabsContent value="priority" className="space-y-4">
            <div className="rounded-lg border bg-card">
              <div className="border-b p-4">
                <h3 className="font-medium">用例优先级分布</h3>
              </div>
              <div className="p-4">
                <div className="space-y-4">
                  {stats.priority_distribution.map((item) => (
                    <div key={item.priority} className="flex items-center gap-4">
                      <div className="w-20 text-sm font-medium capitalize">
                        <span
                          className={`inline-block h-3 w-3 rounded-full mr-2 ${
                            PRIORITY_COLORS[item.priority] || "bg-gray-500"
                          }`}
                        />
                        {item.priority}
                      </div>
                      <div className="flex-1">
                        <Progress value={item.percentage} />
                      </div>
                      <div className="w-20 text-right text-sm text-muted-foreground">
                        {item.count} ({item.percentage}%)
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="type" className="space-y-4">
            <div className="rounded-lg border bg-card">
              <div className="border-b p-4">
                <h3 className="font-medium">用例类型分布</h3>
              </div>
              <div className="grid gap-4 p-4 md:grid-cols-2 lg:grid-cols-3">
                {stats.type_distribution.map((item) => (
                  <div
                    key={item.type}
                    className="flex items-center justify-between rounded-lg border p-4"
                  >
                    <div className="flex items-center gap-2">
                      <span
                        className={`inline-block h-3 w-3 rounded-full ${
                          TYPE_COLORS[item.type] || "bg-gray-500"
                        }`}
                      />
                      <span className="font-medium capitalize">{item.type}</span>
                    </div>
                    <Badge variant="secondary">{item.count}</Badge>
                  </div>
                ))}
                {stats.type_distribution.length === 0 && (
                  <div className="col-span-full text-center text-sm text-muted-foreground py-8">
                    暂无类型分布数据
                  </div>
                )}
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </MainLayout>
  );
}
