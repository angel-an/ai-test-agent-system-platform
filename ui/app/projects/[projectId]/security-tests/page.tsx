"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import {
  Shield,
  Plus,
  Search,
  Trash2,
  AlertTriangle,
  FileText,
  RefreshCw,
  Target,
  MessageSquare,
  X,
  Eye,
  Download,
  Square,
  Pencil,
  Sparkles,
} from "lucide-react";
import { MainLayout } from "@/components/layout";
import { useLanguage } from "@/providers/LanguageProvider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { AIChatContainer } from "@/components/langgraph/AIChatContainer";
import { ClientProvider } from "@/providers/ClientProvider";
import { Assistant } from "@langchain/langgraph-sdk";
import { cn } from "@/lib/utils";
import {
  listSecurityTests,
  createSecurityTest,
  deleteSecurityTest,
  updateSecurityTest,
  updateSecurityTestStatus,
  updateSecurityTestThreadId,
  listReports,
  downloadReport,
  type SecurityTest,
  type SecurityReport,
} from "@/lib/api/security-tests";

export default function SecurityTestsPage() {
  const params = useParams();
  const projectId = params.projectId as string;
  const { t } = useLanguage();

  // 渗透测试列表状态
  const [tests, setTests] = React.useState<SecurityTest[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [searchQuery, setSearchQuery] = React.useState("");
  const [showCreateDialog, setShowCreateDialog] = React.useState(false);
  const [selectedTest, setSelectedTest] = React.useState<SecurityTest | null>(null);

  // 报告列表状态
  const [reports, setReports] = React.useState<SecurityReport[]>([]);
  const [reportsLoading, setReportsLoading] = React.useState(false);
  const [showReportDialog, setShowReportDialog] = React.useState(false);
  const [selectedReport, setSelectedReport] = React.useState<SecurityReport | null>(null);

  // 创建表单状态
  const [createName, setCreateName] = React.useState("");
  const [createTarget, setCreateTarget] = React.useState("");
  const [createDescription, setCreateDescription] = React.useState("");
  const [creating, setCreating] = React.useState(false);

  // 编辑表单状态
  const [editingTest, setEditingTest] = React.useState<SecurityTest | null>(null);
  const [showEditDialog, setShowEditDialog] = React.useState(false);
  const [editName, setEditName] = React.useState("");
  const [editTarget, setEditTarget] = React.useState("");
  const [editDescription, setEditDescription] = React.useState("");
  const [updating, setUpdating] = React.useState(false);

  // AI 聊天状态
  const [aiChatOpen, setAiChatOpen] = React.useState(false);
  const [aiChatInitialPrompt, setAiChatInitialPrompt] = React.useState<string>("");
  const [aiChatKey, setAiChatKey] = React.useState<number>(0);
  const [assistant, setAssistant] = React.useState<Assistant | null>(null);

  // 初始化 Assistant
  React.useEffect(() => {
    const initAssistant = async () => {
      try {
        // 使用正确的 LangGraph assistant UUID（不是 graph_id）
        const assistantId = "7aa94c66-336f-5514-8ee9-57a8ce2a5040";
        const mockAssistant: Assistant = {
          assistant_id: assistantId,
          graph_id: "security_agent",
          config: {
            configurable: {
              project_identifier: projectId,
              target: selectedTest?.target || "",
              pentest_id: selectedTest?.id || "",
              template_type: "pentest",
            },
          },
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          metadata: {},
          version: 1,
          name: "渗透测试助手",
          context: {},
        };
        setAssistant(mockAssistant);
      } catch (error) {
        console.error("Failed to initialize assistant:", error);
      }
    };
    initAssistant();
  }, [projectId, selectedTest?.target]);

  // 加载渗透测试列表
  const loadTests = React.useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const result = await listSecurityTests(projectId, {
        search: searchQuery || undefined,
      });
      setTests(result.items);
    } catch (error) {
      toast.error("加载渗透测试列表失败");
      console.error(error);
    } finally {
      setLoading(false);
    }
  }, [projectId, searchQuery]);

  // 加载报告列表
  const loadReports = React.useCallback(async (testId: string) => {
    if (!projectId || !testId) return;
    setReportsLoading(true);
    try {
      const result = await listReports(projectId, testId);
      setReports(result.items);
    } catch (error) {
      toast.error("加载报告列表失败");
      console.error(error);
    } finally {
      setReportsLoading(false);
    }
  }, [projectId]);

  React.useEffect(() => {
    loadTests();
  }, [loadTests]);

  // 当选中渗透测试时加载报告
  React.useEffect(() => {
    if (selectedTest) {
      loadReports(selectedTest.id);
    } else {
      setReports([]);
    }
  }, [selectedTest, loadReports]);

  // 报告内容容器 ref，用于处理 HTML 内部的链接点击
  const reportContentRef = React.useRef<HTMLDivElement>(null);

  // 处理报告 HTML 内容中的链接点击，阻止默认跳转，改为新窗口打开
  React.useEffect(() => {
    if (!showReportDialog || !reportContentRef.current || selectedReport?.format !== "html") return;

    const container = reportContentRef.current;

    const handleClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;

      // 处理 <a> 标签点击
      const anchor = target.closest("a");
      if (anchor) {
        e.preventDefault();
        const href = anchor.getAttribute("href");
        if (href && href !== "#" && !href.startsWith("javascript:")) {
          window.open(href, "_blank", "noopener,noreferrer");
        }
        return;
      }

      // 处理 <img> 标签点击 - 在新窗口中打开图片
      const img = target.closest("img");
      if (img) {
        e.preventDefault();
        const src = img.getAttribute("src");
        if (src) {
          window.open(src, "_blank", "noopener,noreferrer");
        }
        return;
      }
    };

    container.addEventListener("click", handleClick);
    return () => {
      container.removeEventListener("click", handleClick);
    };
  }, [showReportDialog, selectedReport?.format, selectedReport?.content]);

  const handleCreate = async () => {
    if (!createName.trim() || !createTarget.trim()) {
      toast.error("请填写名称和目标");
      return;
    }
    setCreating(true);
    try {
      await createSecurityTest(projectId, {
        name: createName,
        target: createTarget,
        description: createDescription || undefined,
      });
      toast.success("渗透测试任务创建成功");
      setShowCreateDialog(false);
      setCreateName("");
      setCreateTarget("");
      setCreateDescription("");
      loadTests();
    } catch (error: any) {
      toast.error(error.message || "创建失败");
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (test: SecurityTest) => {
    if (!confirm(`确定要删除渗透测试 "${test.name}" 吗？`)) return;
    try {
      await deleteSecurityTest(projectId, test.id);
      toast.success("删除成功");
      loadTests();
    } catch (error: any) {
      toast.error(error.message || "删除失败");
    }
  };

  const handleEdit = (test: SecurityTest) => {
    setEditingTest(test);
    setEditName(test.name);
    setEditTarget(test.target);
    setEditDescription(test.description || "");
    setShowEditDialog(true);
  };

  const handleUpdate = async () => {
    if (!editingTest) return;
    if (!editName.trim() || !editTarget.trim()) {
      toast.error("请填写名称和目标");
      return;
    }
    setUpdating(true);
    try {
      await updateSecurityTest(projectId, editingTest.id, {
        name: editName,
        target: editTarget,
        description: editDescription || undefined,
      });
      toast.success("渗透测试任务更新成功");
      setShowEditDialog(false);
      setEditingTest(null);
      loadTests();
      // 如果当前选中的测试被编辑了，更新选中状态
      if (selectedTest?.id === editingTest.id) {
        setSelectedTest({
          ...selectedTest,
          name: editName,
          target: editTarget,
          description: editDescription,
        });
      }
    } catch (error: any) {
      toast.error(error.message || "更新失败");
    } finally {
      setUpdating(false);
    }
  };

  const handleCancelTest = async (test: SecurityTest) => {
    if (!confirm(`确定要终止渗透测试 "${test.name}" 吗？`)) return;
    try {
      await updateSecurityTestStatus(projectId, test.id, "cancelled");
      toast.success("渗透测试已终止");
      loadTests();
    } catch (error: any) {
      toast.error(error.message || "终止测试失败");
    }
  };

  const handleStartTest = async (test: SecurityTest) => {
    try {
      // 1. 先选中该测试（确保 assistant config 中的 target 正确）
      setSelectedTest(test);

      // 2. 如果任务已有历史 thread_id，清除它（强制创建新对话，避免复用已完成的对话）
      if (test.thread_id) {
        await updateSecurityTestThreadId(projectId, test.id, "");
        setSelectedTest({ ...test, thread_id: undefined });
      }

      // 3. 更新数据库状态为 running
      await updateSecurityTestStatus(projectId, test.id, "running");
      toast.success("渗透测试已启动");
      loadTests();

      // 4. 构建自动触发的初始提示词，打开 AI 聊天面板自动执行
      const autoPrompt = `请对目标 ${test.target} 执行完整的渗透测试。测试名称：${test.name}。${test.description ? "测试描述：" + test.description : ""}`;

      // 5. 打开 AI 聊天面板并设置初始提示词
      setAiChatInitialPrompt(autoPrompt);
      setAiChatKey((prev) => prev + 1);
      setAiChatOpen(true);
    } catch (error: any) {
      toast.error(error.message || "启动测试失败");
    }
  };

  const handleOpenAIChat = async (test: SecurityTest) => {
    // 选中该测试
    setSelectedTest(test);

    // 如果有历史对话 thread_id，打开历史对话（不触发新指令）
    // 如果没有历史对话，打开空对话面板等待用户输入
    setAiChatInitialPrompt("");
    setAiChatKey((prev) => prev + 1);
    setAiChatOpen(true);
  };

  const handleViewReport = (reportId: string) => {
    // 通过后端代理查看报告，不暴露 MinIO 直链地址
    window.open(`/api/v2/projects/${projectId}/security-tests/reports/${reportId}/view`, "_blank");
  };

  const handleDownloadReport = async (reportId: string) => {
    try {
      const result = await downloadReport(projectId, reportId);
      // 使用预签名 URL 下载文件
      const link = document.createElement("a");
      link.href = result.download_url;
      link.target = "_blank";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } catch (error: any) {
      toast.error(error.message || "下载报告失败");
    }
  };

  const getStatusBadge = (status: string) => {
    const styles: Record<string, string> = {
      pending: "bg-yellow-100 text-yellow-800",
      running: "bg-blue-100 text-blue-800",
      completed: "bg-green-100 text-green-800",
      failed: "bg-red-100 text-red-800",
      cancelled: "bg-gray-100 text-gray-800",
    };
    const labels: Record<string, string> = {
      pending: "待处理",
      running: "运行中",
      completed: "已完成",
      failed: "失败",
      cancelled: "已取消",
    };
    return (
      <Badge className={cn("text-xs", styles[status] || "bg-gray-100")}>
        {labels[status] || status}
      </Badge>
    );
  };

  const getSeverityBadge = (count: number, label: string, color: string, isZeroVisible: boolean) => {
    if (count === 0 && !isZeroVisible) return null;
    return (
      <span className={cn("inline-flex items-center gap-1 text-xs font-medium", color)}>
        <AlertTriangle className="h-3 w-3" />
        {label}: {count === 0 ? "暂无" : count}
      </span>
    );
  };

  const getReportTypeLabel = (reportType: string) => {
    const labels: Record<string, string> = {
      full: "完整报告",
      executive: "执行摘要",
      technical: "技术报告",
    };
    return labels[reportType] || reportType;
  };

  return (
    <MainLayout>
      <div className="flex h-full flex-col relative">
        {/* Header */}
        <div className="flex items-center justify-between border-b px-6 py-4">
          <div className="flex items-center gap-3">
            <Shield className="h-6 w-6 text-primary" />
            <div>
              <h1 className="text-lg font-semibold">渗透测试</h1>
              <p className="text-sm text-muted-foreground">
                渗透测试任务管理与报告查看
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                if (selectedTest) {
                  handleOpenAIChat(selectedTest);
                } else {
                  toast.info("请先选择一个渗透测试任务");
                }
              }}
            >
              <MessageSquare className="mr-2 h-4 w-4" />
              AI 助手
            </Button>
            <Button size="sm" onClick={() => setShowCreateDialog(true)}>
              <Plus className="mr-2 h-4 w-4" />
              新建任务
            </Button>
          </div>
        </div>

        {/* Search and filters */}
        <div className="flex items-center gap-4 border-b px-6 py-3">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="搜索渗透测试..."
              className="pl-9"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && loadTests()}
            />
          </div>
          <Button variant="ghost" size="sm" onClick={() => { loadTests(); }}>
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          </Button>
        </div>

        {/* Content - Two column layout */}
        <div className="flex-1 overflow-auto p-6">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Left: Pentest Task List */}
            <div>
              <h2 className="text-sm font-semibold text-muted-foreground mb-3 flex items-center gap-2">
                <Shield className="h-4 w-4" />
                渗透测试任务
              </h2>
              {tests.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 border-2 border-dashed rounded-lg bg-muted/10">
                  <Shield className="h-12 w-12 text-muted-foreground/30" />
                  <p className="mt-2 text-sm text-muted-foreground">
                    暂无渗透测试任务
                  </p>
                </div>
              ) : (
                <div className="space-y-3">
                  {tests.map((test) => {
                    const isActive = test.status === "pending" || test.status === "running";
                    return (
                    <Card
                      key={test.id}
                      className={cn(
                        "cursor-pointer transition-colors",
                        selectedTest?.id === test.id
                          ? "border-primary bg-primary/5"
                          : "hover:bg-accent/50"
                      )}
                      onClick={() => setSelectedTest(test)}
                    >
                      <CardContent className="p-4">
                        <div className="flex items-start justify-between">
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <h3 className="font-semibold text-sm truncate">{test.name}</h3>
                              {getStatusBadge(test.status)}
                            </div>
                            <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                              <Target className="h-3 w-3" />
                              <span className="truncate">{test.target}</span>
                            </div>
                            <div className="mt-2 flex items-center gap-3 flex-wrap">
                              {getSeverityBadge(test.critical_count, "严重", "text-red-600", isActive)}
                              {getSeverityBadge(test.high_count, "高危", "text-orange-600", isActive)}
                              {getSeverityBadge(test.medium_count, "中危", "text-yellow-600", isActive)}
                              {getSeverityBadge(test.low_count, "低危", "text-blue-600", isActive)}
                              {getSeverityBadge(test.info_count, "信息", "text-gray-500", isActive)}
                            </div>
                          </div>
                          <div className="flex items-center gap-1 ml-2">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8"
                              title="AI 助手"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleOpenAIChat(test);
                              }}
                            >
                              <MessageSquare className="h-3.5 w-3.5 text-primary" />
                            </Button>
                            {test.status !== "running" && (
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8"
                                title="开始测试"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleStartTest(test);
                                }}
                              >
                                <Shield className="h-3.5 w-3.5 text-green-600" />
                              </Button>
                            )}
                            {test.status === "running" && (
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8"
                                title="终止测试"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleCancelTest(test);
                                }}
                              >
                                <Square className="h-3.5 w-3.5 text-orange-500 fill-orange-500" />
                              </Button>
                            )}
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8"
                              title="编辑任务"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleEdit(test);
                              }}
                            >
                              <Pencil className="h-3.5 w-3.5 text-blue-600" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8"
                              title="删除任务"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleDelete(test);
                              }}
                            >
                              <Trash2 className="h-3.5 w-3.5 text-destructive" />
                            </Button>
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Right: Report List */}
            <div>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-muted-foreground flex items-center gap-2">
                  <FileText className="h-4 w-4" />
                  测试报告
                </h2>
              </div>

              {!selectedTest ? (
                <div className="flex flex-col items-center justify-center py-12 border-2 border-dashed rounded-lg bg-muted/10">
                  <FileText className="h-12 w-12 text-muted-foreground/30" />
                  <p className="mt-2 text-sm text-muted-foreground">
                    请选择左侧的渗透测试任务查看报告
                  </p>
                </div>
              ) : reports.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 border-2 border-dashed rounded-lg bg-muted/10">
                  <FileText className="h-12 w-12 text-muted-foreground/30" />
                  <p className="mt-2 text-sm text-muted-foreground">
                    暂无报告
                  </p>
                </div>
              ) : (
                <div className="space-y-3">
                  {reports.map((report) => (
                    <Card key={report.id} className="hover:bg-accent/50 transition-colors">
                      <CardContent className="p-4">
                        <div className="flex items-start justify-between">
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <FileText className="h-4 w-4 text-primary" />
                              <h3 className="font-medium text-sm">{report.name}</h3>
                              <Badge variant="outline" className="text-xs">
                                {report.format}
                              </Badge>
                            </div>
                            <div className="mt-1 text-xs text-muted-foreground">
                              类型: {getReportTypeLabel(report.report_type)} · 风险评分: {report.risk_score?.toFixed(1) ?? "N/A"}
                            </div>
                            <div className="mt-1 text-xs text-muted-foreground">
                              创建于 {new Date(report.created_at).toLocaleString()}
                            </div>
                          </div>
                          <div className="flex items-center gap-1 ml-2">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8"
                              onClick={() => {
                                setSelectedReport(report);
                                setShowReportDialog(true);
                              }}
                            >
                              <Eye className="h-3.5 w-3.5" />
                            </Button>
                            {report.file_path && (
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8"
                                title="下载报告"
                                onClick={() => handleDownloadReport(report.id)}
                              >
                                <Download className="h-3.5 w-3.5" />
                              </Button>
                            )}
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Create Dialog */}
        <Dialog open={showCreateDialog} onOpenChange={setShowCreateDialog}>
          <DialogContent className="sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>新建渗透测试任务</DialogTitle>
              <DialogDescription>
                创建一个新的渗透测试任务
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label>任务名称 *</Label>
                <Input
                  placeholder="例如：example.com 安全评估"
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label>测试目标 *</Label>
                <Input
                  placeholder="例如：https://example.com"
                  value={createTarget}
                  onChange={(e) => setCreateTarget(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label>描述</Label>
                <Textarea
                  placeholder="测试范围、目标描述..."
                  value={createDescription}
                  onChange={(e) => setCreateDescription(e.target.value)}
                  rows={3}
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setShowCreateDialog(false)}>
                取消
              </Button>
              <Button onClick={handleCreate} disabled={creating}>
                {creating ? "创建中..." : "创建"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Edit Dialog */}
        <Dialog open={showEditDialog} onOpenChange={setShowEditDialog}>
          <DialogContent className="sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>编辑渗透测试任务</DialogTitle>
              <DialogDescription>
                修改渗透测试任务的基本信息
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label>任务名称 *</Label>
                <Input
                  placeholder="例如：example.com 安全评估"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label>测试目标 *</Label>
                <Input
                  placeholder="例如：https://example.com"
                  value={editTarget}
                  onChange={(e) => setEditTarget(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label>描述</Label>
                <Textarea
                  placeholder="测试范围、目标描述..."
                  value={editDescription}
                  onChange={(e) => setEditDescription(e.target.value)}
                  rows={3}
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setShowEditDialog(false)}>
                取消
              </Button>
              <Button onClick={handleUpdate} disabled={updating}>
                {updating ? "更新中..." : "更新"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Report Detail Dialog */}
        <Dialog open={showReportDialog} onOpenChange={setShowReportDialog}>
          <DialogContent className="sm:max-w-4xl max-h-[80vh] overflow-auto">
            {selectedReport && (
              <>
                <DialogHeader>
                  <DialogTitle className="flex items-center gap-2">
                    <FileText className="h-5 w-5" />
                    {selectedReport.name}
                  </DialogTitle>
                  <DialogDescription>
                    类型: {getReportTypeLabel(selectedReport.report_type)} · 格式: {selectedReport.format}
                  </DialogDescription>
                </DialogHeader>
                <div className="py-4">
                  {selectedReport.content ? (
                    <div className="prose prose-sm max-w-none dark:prose-invert">
                      {selectedReport.format === "html" ? (
                        <div ref={reportContentRef} dangerouslySetInnerHTML={{ __html: selectedReport.content }} />
                      ) : (
                        <pre className="whitespace-pre-wrap text-sm bg-muted p-4 rounded overflow-auto max-h-[60vh]">
                          {selectedReport.content}
                        </pre>
                      )}
                    </div>
                  ) : (
                    <div className="text-center py-8 text-muted-foreground">
                      报告内容为空
                    </div>
                  )}
                </div>
                <DialogFooter>
                  <Button variant="outline" onClick={() => setShowReportDialog(false)}>
                    关闭
                  </Button>
                  {selectedReport.file_path && (
                    <>
                      <Button
                        variant="outline"
                        onClick={() => {
                          window.open(`/api/v2/projects/${projectId}/security-tests/reports/${selectedReport.id}/view`, "_blank");
                        }}
                      >
                        <Eye className="mr-2 h-4 w-4" />
                        查看报告
                      </Button>
                      <Button
                        onClick={() => {
                          if (selectedReport) {
                            handleDownloadReport(selectedReport.id);
                          }
                        }}
                      >
                        <Download className="mr-2 h-4 w-4" />
                        下载报告
                      </Button>
                    </>
                  )}
                </DialogFooter>
              </>
            )}
          </DialogContent>
        </Dialog>

        {/* AI Chat Panel */}
        {assistant && (
          <div
            key={aiChatKey}
            className={cn(
              "absolute right-0 top-0 z-50 h-full w-[900px] bg-background transition-transform duration-300 ease-in-out",
              aiChatOpen ? "translate-x-0 border-l shadow-2xl" : "translate-x-full invisible"
            )}
          >
            <div className="flex items-center justify-between p-3 border-b">
              <h3 className="font-semibold flex items-center gap-2">
                <MessageSquare className="h-5 w-5" />
                渗透测试 AI 助手
              </h3>
              <Button variant="ghost" size="icon" onClick={() => setAiChatOpen(false)}>
                <X className="h-4 w-4" />
              </Button>
            </div>
            <div className="h-[calc(100%-3rem)]">
              <ClientProvider
                deploymentUrl="/lg"
                apiKey={process.env.NEXT_PUBLIC_LANGSMITH_API_KEY || ""}
              >
                <AIChatContainer
                  assistant={assistant}
                  initialPrompt={aiChatInitialPrompt}
                  onClose={() => setAiChatOpen(false)}
                  createNewThread={aiChatKey > 0}
                  hideThreadControls={true}
                  externalThreadId={selectedTest?.thread_id || null}
                  onThreadIdChange={async (newThreadId) => {
                    // 当新对话创建时，保存 thread_id 到后端
                    if (newThreadId && selectedTest && !selectedTest.thread_id) {
                      try {
                        await updateSecurityTestThreadId(projectId, selectedTest.id, newThreadId);
                        // 更新本地状态
                        setSelectedTest({ ...selectedTest, thread_id: newThreadId });
                        loadTests();
                      } catch (error: any) {
                        console.error("保存对话线程 ID 失败:", error);
                      }
                    }
                  }}
                  onTestCreated={() => {
                    loadTests();
                    if (selectedTest) {
                      loadReports(selectedTest.id);
                    }
                  }}
                />
              </ClientProvider>
            </div>
          </div>
        )}
      </div>
    </MainLayout>
  );
}
