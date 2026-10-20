"use client";

/**
 * 小杨生煎 - B端储值充值 可视化报告演示页面
 *
 * 此页面用于演示新的图标可视化报告格式
 * 访问路径: /projects/{projectId}/web-tests/xysjg-report-demo
 */

import * as React from "react";
import { useParams } from "next/navigation";
import { MainLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Play,
  BarChart3,
  TrendingUp,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Clock,
  Monitor,
  FileText,
  ExternalLink,
} from "lucide-react";
import { VisualReportDialog, VisualReportDashboard } from "@/components/web-tests/visual-report-dashboard";
import { mockXYSJGReport } from "@/components/web-tests/mock-report-demo";

export default function XYSJGReportDemoPage() {
  const params = useParams();
  const projectId = params.projectId as string;
  const [reportOpen, setReportOpen] = React.useState(false);

  return (
    <MainLayout title="小杨生煎 - B端储值充值 测试报告">
      <div className="space-y-6 max-w-6xl mx-auto">
        {/* 头部信息 */}
        <div className="rounded-lg border bg-card p-6">
          <div className="flex items-start justify-between">
            <div>
              <div className="flex items-center gap-2 mb-2">
                <Monitor className="h-5 w-5 text-blue-500" />
                <h1 className="text-xl font-bold">小杨生煎 - B端储值充值</h1>
                <Badge variant="default">Web测试</Badge>
                <Badge variant="outline" className="text-xs">WT-AB02C9FF</Badge>
              </div>
              <p className="text-sm text-muted-foreground">
                验证路径: 企业控制台 → 门户中心 → 数盈营销云 → 忠诚度 → 储值管理 → 储值账户列表
              </p>
              <div className="flex items-center gap-4 mt-2 text-xs text-muted-foreground">
                <span>账号: andan</span>
                <span>环境: UAT</span>
                <span>浏览器: Chromium</span>
                <span>视口: 1280x1800</span>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setReportOpen(true)}
              >
                <BarChart3 className="mr-2 h-4 w-4" />
                查看可视化报告
              </Button>
              <Button
                variant="outline"
                size="sm"
              >
                <FileText className="mr-2 h-4 w-4" />
                原始HTML报告
              </Button>
            </div>
          </div>
        </div>

        {/* 执行摘要 */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="rounded-xl border bg-green-50/50 dark:bg-green-950/20 p-4">
            <div className="flex items-center gap-2 mb-2">
              <CheckCircle2 className="h-5 w-5 text-green-500" />
              <span className="text-sm text-muted-foreground">通过</span>
            </div>
            <div className="text-3xl font-bold text-green-600">12</div>
            <div className="text-xs text-muted-foreground mt-1">80% 通过率</div>
          </div>
          <div className="rounded-xl border bg-red-50/50 dark:bg-red-950/20 p-4">
            <div className="flex items-center gap-2 mb-2">
              <XCircle className="h-5 w-5 text-red-500" />
              <span className="text-sm text-muted-foreground">失败</span>
            </div>
            <div className="text-3xl font-bold text-red-600">2</div>
            <div className="text-xs text-muted-foreground mt-1">需关注</div>
          </div>
          <div className="rounded-xl border bg-amber-50/50 dark:bg-amber-950/20 p-4">
            <div className="flex items-center gap-2 mb-2">
              <AlertCircle className="h-5 w-5 text-amber-500" />
              <span className="text-sm text-muted-foreground">跳过</span>
            </div>
            <div className="text-3xl font-bold text-amber-600">1</div>
            <div className="text-xs text-muted-foreground mt-1">未执行</div>
          </div>
          <div className="rounded-xl border bg-blue-50/50 dark:bg-blue-950/20 p-4">
            <div className="flex items-center gap-2 mb-2">
              <Clock className="h-5 w-5 text-blue-500" />
              <span className="text-sm text-muted-foreground">总耗时</span>
            </div>
            <div className="text-3xl font-bold text-blue-600">2m 5s</div>
            <div className="text-xs text-muted-foreground mt-1">15 个测试</div>
          </div>
        </div>

        {/* 报告说明 */}
        <div className="rounded-lg border bg-card p-6">
          <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <TrendingUp className="h-5 w-5 text-purple-500" />
            报告改进说明
          </h2>
          <div className="space-y-3 text-sm">
            <div className="flex items-start gap-3">
              <div className="w-6 h-6 rounded-full bg-green-100 flex items-center justify-center shrink-0 mt-0.5">
                <CheckCircle2 className="h-3.5 w-3.5 text-green-600" />
              </div>
              <div>
                <span className="font-medium">图标可视化展示</span>
                <p className="text-muted-foreground">
                  使用彩色图标和进度环替代原有的zip文件下载，通过率、通过/失败/跳过数量一目了然
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <div className="w-6 h-6 rounded-full bg-green-100 flex items-center justify-center shrink-0 mt-0.5">
                <CheckCircle2 className="h-3.5 w-3.5 text-green-600" />
              </div>
              <div>
                <span className="font-medium">测试套件层级展示</span>
                <p className="text-muted-foreground">
                  按阶段分组展示测试用例（登录导航、页面检查、表单验证等），支持展开/折叠
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <div className="w-6 h-6 rounded-full bg-green-100 flex items-center justify-center shrink-0 mt-0.5">
                <CheckCircle2 className="h-3.5 w-3.5 text-green-600" />
              </div>
              <div>
                <span className="font-medium">彩色进度条</span>
                <p className="text-muted-foreground">
                  绿色=通过、红色=失败、黄色=跳过，直观展示每个套件的执行结果分布
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <div className="w-6 h-6 rounded-full bg-green-100 flex items-center justify-center shrink-0 mt-0.5">
                <CheckCircle2 className="h-3.5 w-3.5 text-green-600" />
              </div>
              <div>
                <span className="font-medium">保留原始报告</span>
                <p className="text-muted-foreground">
                  可视化报告的同时保留原始HTML报告链接，满足详细排查需求
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* 测试用例列表预览 */}
        <div className="rounded-lg border bg-card">
          <div className="border-b p-4">
            <h2 className="font-semibold flex items-center gap-2">
              <FileText className="h-5 w-5 text-blue-500" />
              测试用例概览
            </h2>
          </div>
          <div className="divide-y">
            {[
              { name: "TC-001: 登录企业控制台并导航到储值账户列表", status: "passed" },
              { name: "TC-002: 储值账户列表页面表单检查", status: "passed" },
              { name: "TC-003: 企业储值充值-手动输入-成功充值", status: "passed" },
              { name: "TC-004: 企业储值充值-手动输入-非会员提示", status: "passed" },
              { name: "TC-005: 企业储值充值-手动输入-手机号格式校验", status: "passed" },
              { name: "TC-006: 企业储值充值-手动输入-充值金额边界值", status: "failed" },
              { name: "TC-007: 企业储值退款-手动输入-成功退款", status: "passed" },
              { name: "TC-008: 企业储值充值-批量导入-成功", status: "passed" },
              { name: "TC-009: 企业储值充值-批量导入-文件格式校验", status: "passed" },
              { name: "TC-010: 企业储值充值-批量导入-文件大小校验", status: "passed" },
              { name: "TC-011: 企业储值充值-批量导入-非会员清单提示", status: "passed" },
              { name: "TC-012: 储值流水-充值流水检查", status: "passed" },
              { name: "TC-013: 储值流水-退款流水检查", status: "skipped" },
              { name: "TC-014: 变动类型切换-充值与退款切换", status: "passed" },
              { name: "TC-015: 变更方式切换-手动与批量切换", status: "failed" },
            ].map((tc, i) => (
              <div key={i} className="flex items-center justify-between p-3 hover:bg-muted/50">
                <span className="text-sm">{tc.name}</span>
                <Badge
                  variant={tc.status === "passed" ? "default" : tc.status === "failed" ? "destructive" : "secondary"}
                  className="text-xs"
                >
                  {tc.status === "passed" ? (
                    <CheckCircle2 className="h-3 w-3 mr-1" />
                  ) : tc.status === "failed" ? (
                    <XCircle className="h-3 w-3 mr-1" />
                  ) : (
                    <AlertCircle className="h-3 w-3 mr-1" />
                  )}
                  {tc.status === "passed" ? "通过" : tc.status === "failed" ? "失败" : "跳过"}
                </Badge>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 可视化报告弹窗 */}
      <VisualReportDialog
        open={reportOpen}
        onOpenChange={setReportOpen}
        reportData={mockXYSJGReport}
        jobName="小杨生煎-B端储值充值 自动化测试"
      />
    </MainLayout>
  );
}
