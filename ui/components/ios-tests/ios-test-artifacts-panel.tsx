// TODO  MC80OmFIVnBZMlhscm9ua3VMazZiVEJUTUE9PTplZWRiZTlmNw==

/**
 * iOS 测试成果物展示面板
 *
 * 支持测试计划、测试用例、测试脚本、测试报告、截图的展示和管理
 * 针对 iOS 测试特点优化：支持截图查看、设备信息展示、模拟器支持
 */

"use client";
// eslint-disable  MS80OmFIVnBZMlhscm9ua3VMazZiVEJUTUE9PTplZWRiZTlmNw==

import * as React from "react";
import { useState, useEffect, useCallback, useRef } from "react";
import {
  FileText,
  Code,
  ScrollText,
  Download,
  Eye,
  Edit,
  Play,
  Loader2,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  XCircle,
  RefreshCw,
  Save,
  Clock,
  FileCode,
  Smartphone,
  Image,
  Monitor,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { MonacoCodeEditor } from "@/components/editor/MonacoCodeEditor";

interface TestArtifact {
  id: string;
  type: string;
  file_name: string;
  description: string;
  file_size: number;
  content_type: string;
  object_name: string;
  created_at: string;
}
// NOTE  Mi80OmFIVnBZMlhscm9ua3VMazZiVEJUTUE9PTplZWRiZTlmNw==

interface IOSTestArtifactsPanelProps {
  subFunctionId: string;
  projectId: string;
  bundleId?: string;
  onRefresh?: () => void;
  onTestCasesCountChange?: (count: number, subFunctionId?: string) => void;
  onExecuteScript?: (artifactId: string, fileName: string) => void;
  refreshTrigger?: number;
}

// iOS 成果物类型映射 - 针对 iOS 测试特点优化
const artifactTypeConfig = {
  IOS_TEST_PLAN: {
    icon: FileText,
    label: "测试计划",
    color: "text-blue-500",
    bgColor: "bg-blue-50 dark:bg-blue-950",
    borderColor: "border-blue-200 dark:border-blue-800",
  },
  IOS_TEST_CASE: {
    icon: ScrollText,
    label: "测试用例",
    color: "text-sky-500",
    bgColor: "bg-sky-50 dark:bg-sky-950",
    borderColor: "border-sky-200 dark:border-sky-800",
  },
  IOS_TEST_SCRIPT: {
    icon: Code,
    label: "测试脚本",
    color: "text-indigo-500",
    bgColor: "bg-indigo-50 dark:bg-indigo-950",
    borderColor: "border-indigo-200 dark:border-indigo-800",
  },
  IOS_TEST_REPORT: {
    icon: FileCode,
    label: "测试报告",
    color: "text-violet-500",
    bgColor: "bg-violet-50 dark:bg-violet-950",
    borderColor: "border-violet-200 dark:border-violet-800",
  },
  IOS_DEVICE_INFO: {
    icon: Smartphone,
    label: "设备信息",
    color: "text-cyan-500",
    bgColor: "bg-cyan-50 dark:bg-cyan-950",
    borderColor: "border-cyan-200 dark:border-cyan-800",
  },
  IOS_SCREENSHOT: {
    icon: Image,
    label: "截图",
    color: "text-orange-500",
    bgColor: "bg-orange-50 dark:bg-orange-950",
    borderColor: "border-orange-200 dark:border-orange-800",
  },
  // 兼容旧类型
  WEB_TEST_PLAN: {
    icon: FileText,
    label: "测试计划",
    color: "text-blue-500",
    bgColor: "bg-blue-50 dark:bg-blue-950",
    borderColor: "border-blue-200 dark:border-blue-800",
  },
  WEB_TEST_CASE: {
    icon: ScrollText,
    label: "测试用例",
    color: "text-sky-500",
    bgColor: "bg-sky-50 dark:bg-sky-950",
    borderColor: "border-sky-200 dark:border-sky-800",
  },
  WEB_TEST_SCRIPT: {
    icon: Code,
    label: "测试脚本",
    color: "text-indigo-500",
    bgColor: "bg-indigo-50 dark:bg-indigo-950",
    borderColor: "border-indigo-200 dark:border-indigo-800",
  },
  WEB_TEST_RESULT: {
    icon: CheckCircle2,
    label: "执行结果",
    color: "text-orange-500",
    bgColor: "bg-orange-50 dark:bg-orange-950",
    borderColor: "border-orange-200 dark:border-orange-800",
  },
  WEB_TEST_REPORT: {
    icon: FileCode,
    label: "测试报告",
    color: "text-violet-500",
    bgColor: "bg-violet-50 dark:bg-violet-950",
    borderColor: "border-violet-200 dark:border-violet-800",
  },
};
// TODO  My80OmFIVnBZMlhscm9ua3VMazZiVEJUTUE9PTplZWRiZTlmNw==

export function IOSTestArtifactsPanel({
  subFunctionId,
  projectId,
  bundleId,
  onRefresh,
  onTestCasesCountChange,
  onExecuteScript,
  refreshTrigger,
}: IOSTestArtifactsPanelProps) {
  const [artifacts, setArtifacts] = useState<TestArtifact[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedTypes, setExpandedTypes] = useState<Set<string>>(
    new Set([
      "IOS_TEST_PLAN",
      "IOS_TEST_CASE",
      "IOS_TEST_SCRIPT",
      "IOS_TEST_REPORT",
      "IOS_DEVICE_INFO",
      "IOS_SCREENSHOT",
    ])
  );

  // 编辑状态
  const [editingArtifact, setEditingArtifact] = useState<TestArtifact | null>(
    null
  );
  const [scriptContent, setScriptContent] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [loadingScript, setLoadingScript] = useState(false);

  // 截图查看状态
  const [viewingImage, setViewingImage] = useState<TestArtifact | null>(null);
  const [imageContent, setImageContent] = useState<string>("");

  // 执行结果状态
  const [executionResult, setExecutionResult] = useState<{
    status: "success" | "error" | "running" | null;
    output: string;
    duration?: number;
  }>({ status: null, output: "" });

  // 使用 ref 存储回调，避免函数引用变化导致无限循环
  const onTestCasesCountChangeRef = useRef(onTestCasesCountChange);
  useEffect(() => {
    onTestCasesCountChangeRef.current = onTestCasesCountChange;
  }, [onTestCasesCountChange]);

  // 加载成果物列表
  const loadArtifacts = useCallback(async () => {
    try {
      setLoading(true);
      console.log(
        "[iOS Panel] ===== Loading artifacts for sub-function ====="
      );
      console.log("[iOS Panel] Sub-function ID:", subFunctionId);
      console.log("[iOS Panel] Project ID:", projectId);

      // iOS 使用 bundle_id 作为查询参数
      const apiUrl = bundleId
        ? `/api/v2/projects/${projectId}/ios-apps/artifacts?bundle_id=${encodeURIComponent(bundleId)}&sub_function_id=${subFunctionId}`
        : `/api/v2/projects/${projectId}/ios-apps/sub-functions/${subFunctionId}/artifacts`;
      console.log("[iOS Panel] Fetching from:", apiUrl);

      const response = await fetch(apiUrl);
      console.log("[iOS Panel] Response status:", response.status);
      console.log("[iOS Panel] Response OK:", response.ok);

      if (!response.ok) {
        const errorText = await response.text();
        console.error(
          "[iOS Panel] Failed to load artifacts. Status:",
          response.status
        );
        console.error("[iOS Panel] Error response:", errorText);
        toast.error(`加载测试成果物失败 (${response.status})`);
        setArtifacts([]);
        return;
      }

      const data = await response.json();
      console.log("[iOS Panel] Response data keys:", Object.keys(data));
      console.log("[iOS Panel] Full response data:", data);

      // 修复：API 返回格式为 { success: true, data: { artifacts: [...], total: N } }
      const artifacts = data.data?.artifacts || data.artifacts || [];
      console.log("[iOS Panel] Total artifacts found:", artifacts.length);
      console.log("[iOS Panel] Artifacts:", artifacts);

      if (artifacts.length > 0) {
        console.log(
          "[iOS Panel] Artifact types:",
          artifacts.map((a: any) => a.type)
        );
      }

      setArtifacts(artifacts);

      // 查找test-cases artifact并解析测试用例数量
      // 修复：使用小写比较，因为 API 返回的是小写类型
      const testCasesArtifact = artifacts.find(
        (a: TestArtifact) =>
          a.type === "ios_test_case" || a.type === "web_test_case"
      );
      if (testCasesArtifact) {
        console.log(
          "[iOS Panel] Found test-cases artifact:",
          testCasesArtifact
        );

        if (onTestCasesCountChange) {
          try {
            const contentResponse = await fetch(
              `/api/v2/attachments/${testCasesArtifact.id}/content`
            );
            console.log(
              "[iOS Panel] Content response status:",
              contentResponse.status
            );

            if (contentResponse.ok) {
              const contentData = await contentResponse.json();
              console.log(
                "[iOS Panel] Content data type:",
                typeof contentData.content
              );
              console.log(
                "[iOS Panel] Content preview:",
                contentData.content?.substring(0, 300)
              );

              const testCasesData = JSON.parse(contentData.content);
              console.log(
                "[iOS Panel] Parsed testCasesData type:",
                typeof testCasesData
              );
              console.log("[iOS Panel] Is array?", Array.isArray(testCasesData));

              // 处理不同的数据格式
              let count = 0;
              if (Array.isArray(testCasesData)) {
                count = testCasesData.length;
              } else if (typeof testCasesData === "object" && testCasesData !== null) {
                if (
                  testCasesData.test_cases &&
                  Array.isArray(testCasesData.test_cases)
                ) {
                  count = testCasesData.test_cases.length;
                } else if (
                  testCasesData.cases &&
                  Array.isArray(testCasesData.cases)
                ) {
                  count = testCasesData.cases.length;
                } else {
                  count = Object.keys(testCasesData).length;
                }
              }

              console.log("[iOS Panel] Final test cases count:", count);
              // 使用 ref 调用回调，避免函数引用变化导致无限循环
              onTestCasesCountChangeRef.current?.(count, subFunctionId);
            } else {
              console.error(
                "[iOS Panel] Failed to load content:",
                contentResponse.status
              );
            }
          } catch (error) {
            console.error(
              "[iOS Panel] Failed to parse test cases count:",
              error
            );
          }
        }
      }
    } catch (error) {
      console.error("[iOS Panel] Failed to load artifacts:", error);
      setArtifacts([]);
    } finally {
      setLoading(false);
    }
  }, [subFunctionId, projectId, bundleId]);

  useEffect(() => {
    if (subFunctionId) {
      console.log(
        "[iOS Panel] Loading artifacts for sub-function:",
        subFunctionId
      );
      loadArtifacts();
    }
  }, [subFunctionId, refreshTrigger, loadArtifacts]);

  // 加载脚本内容
  const loadScriptContent = async (artifact: TestArtifact) => {
    try {
      // 立即打开弹窗并显示loading
      setEditingArtifact(artifact);
      setScriptContent(""); // 清空之前的内容
      setLoadingScript(true);

      console.log("[iOS Panel] Loading script content for:", artifact.id);
      const response = await fetch(`/api/v2/attachments/${artifact.id}/content`);
      console.log("[iOS Panel] Content response status:", response.status);

      if (response.ok) {
        const data = await response.json();
        console.log(
          "[iOS Panel] Content data type:",
          typeof data.content
        );
        console.log(
          "[iOS Panel] Content preview:",
          data.content?.substring(0, 300)
        );

        if (data.content) {
          setScriptContent(data.content);
        } else {
          console.error("[iOS Panel] No content in response:", data);
          toast.error("脚本内容为空");
        }
      } else {
        console.error(
          "[iOS Panel] Failed to load script content:",
          response.status
        );
        toast.error("加载脚本内容失败");
      }
    } catch (error) {
      console.error("[iOS Panel] Error loading script content:", error);
      toast.error("加载脚本内容出错");
    } finally {
      setLoadingScript(false);
    }
  };

  // 查看截图
  const viewScreenshot = async (artifact: TestArtifact) => {
    try {
      setViewingImage(artifact);
      setImageContent("");

      const response = await fetch(`/api/v2/attachments/${artifact.id}/content`);
      if (response.ok) {
        const data = await response.json();
        // 截图可能是 base64 编码或 URL
        if (data.content?.startsWith("data:image")) {
          setImageContent(data.content);
        } else if (data.object_name) {
          // 从 MinIO 获取预签名 URL（正确路由需带 projectId）
          const url = await fetchAttachmentUrl(artifact.id);
          if (url) {
            setImageContent(url);
          }
        }
      }
    } catch (error) {
      console.error("[iOS Panel] Error loading screenshot:", error);
      toast.error("加载截图失败");
    }
  };

  // 保存脚本
  const saveScript = async () => {
    if (!editingArtifact) return;

    try {
      setSaving(true);
      console.log(
        "[iOS Panel] Saving script for artifact:",
        editingArtifact.id
      );

      const response = await fetch(
        `/api/v2/attachments/${editingArtifact.id}/content`,
        {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ content: scriptContent }),
        }
      );

      if (response.ok) {
        console.log("[iOS Panel] Script saved successfully");
        toast.success("脚本保存成功");
        // 重新加载成果物列表
        loadArtifacts();
      } else {
        console.error("[iOS Panel] Failed to save script:", response.status);
        toast.error("保存脚本失败");
      }
    } catch (error) {
      console.error("[iOS Panel] Error saving script:", error);
      toast.error("保存脚本出错");
    } finally {
      setSaving(false);
    }
  };

  // 执行脚本
  const executeScript = async (artifact: TestArtifact) => {
    try {
      setExecuting(true);
      setExecutionResult({ status: "running", output: "正在执行测试脚本..." });

      console.log("[iOS Panel] Executing script:", artifact.id);

      // 调用执行 API
      const response = await fetch(
        `/api/v2/projects/${projectId}/ios-apps/execute`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            script_id: artifact.id,
            sub_function_id: subFunctionId,
            bundle_id: bundleId,
          }),
        }
      );

      const data = await response.json();
      console.log("[iOS Panel] Execution response:", data);

      if (response.ok) {
        setExecutionResult({
          status: "success",
          output: data.output || "测试执行成功",
          duration: data.duration,
        });
        toast.success("测试执行成功");
        // 刷新成果物列表
        loadArtifacts();
        // 通知父组件
        onRefresh?.();
      } else {
        setExecutionResult({
          status: "error",
          output: data.error || "测试执行失败",
        });
        toast.error(data.error || "测试执行失败");
      }
    } catch (error) {
      console.error("[iOS Panel] Error executing script:", error);
      setExecutionResult({
        status: "error",
        output: `执行出错: ${error}`,
      });
      toast.error("执行脚本出错");
    } finally {
      setExecuting(false);
    }
  };

  // 切换展开/折叠
  const toggleExpand = (type: string) => {
    setExpandedTypes((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(type)) {
        newSet.delete(type);
      } else {
        newSet.add(type);
      }
      return newSet;
    });
  };

  // 按类型分组
  const groupedArtifacts = artifacts.reduce((acc, artifact) => {
    const type = artifact.type.toUpperCase();
    if (!acc[type]) {
      acc[type] = [];
    }
    acc[type].push(artifact);
    return acc;
  }, {} as Record<string, TestArtifact[]>);

  // 统计各类型数量
  const typeCounts = Object.keys(groupedArtifacts).reduce((acc, type) => {
    acc[type] = groupedArtifacts[type].length;
    return acc;
  }, {} as Record<string, number>);

  // 获取类型配置
  const getTypeConfig = (type: string) => {
    const upperType = type.toUpperCase();
    return (
      artifactTypeConfig[upperType as keyof typeof artifactTypeConfig] || {
        icon: FileCode,
        label: type,
        color: "text-gray-500",
        bgColor: "bg-gray-50 dark:bg-gray-950",
        borderColor: "border-gray-200 dark:border-gray-800",
      }
    );
  };

  /**
   * 获取附件的预签名下载 URL。
   *
   * 正确路由是 `/api/v2/projects/{projectId}/attachments/{id}`，返回
   * `SuccessResponse{ data: { download_url, name, expires_in } }`。
   *
   * 此前调用的 `/api/v2/attachments/{id}/download-url` **在应用路由表中并不存在**，
   * 请求必然 404；且旧代码读取的是 `data.url`，与真实字段名 `data.download_url` 不符。
   */
  const fetchAttachmentUrl = async (artifactId: string): Promise<string | null> => {
    if (!projectId) {
      toast.error("缺少项目标识，无法获取附件链接");
      return null;
    }
    try {
      const response = await fetch(
        `/api/v2/projects/${projectId}/attachments/${artifactId}`
      );
      if (!response.ok) return null;
      const payload = await response.json();
      return payload?.data?.download_url ?? null;
    } catch (error) {
      console.error("[iOS Panel] Error fetching attachment url:", error);
      return null;
    }
  };

  // 下载文件
  const downloadFile = async (artifact: TestArtifact) => {
    const url = await fetchAttachmentUrl(artifact.id);
    if (url) {
      window.open(url, "_blank", "noopener,noreferrer");
    } else {
      toast.error("获取下载链接失败");
    }
  };

  // 查看报告（HTML）
  const viewReport = async (artifact: TestArtifact) => {
    const url = await fetchAttachmentUrl(artifact.id);
    if (url) {
      window.open(url, "_blank", "noopener,noreferrer");
    } else {
      toast.error("获取报告链接失败");
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        <span className="ml-2 text-muted-foreground">加载测试成果物中...</span>
      </div>
    );
  }

  if (artifacts.length === 0) {
    return (
      <div className="text-center py-12 border-2 border-dashed rounded-lg bg-muted/10">
        <Smartphone className="h-16 w-16 text-muted-foreground mx-auto mb-4" />
        <p className="text-lg font-medium mb-2">暂无测试成果物</p>
        <p className="text-sm text-muted-foreground mb-4">
          使用 AI 助手生成测试计划、测试用例和测试脚本
        </p>
        <div className="flex gap-2 justify-center">
          <Badge variant="outline" className="text-xs">
            <FileText className="h-3 w-3 mr-1" />
            测试计划
          </Badge>
          <Badge variant="outline" className="text-xs">
            <ScrollText className="h-3 w-3 mr-1" />
            测试用例
          </Badge>
          <Badge variant="outline" className="text-xs">
            <Code className="h-3 w-3 mr-1" />
            测试脚本
          </Badge>
          <Badge variant="outline" className="text-xs">
            <Image className="h-3 w-3 mr-1" />
            截图
          </Badge>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* 统计卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        {Object.entries(typeCounts).map(([type, count]) => {
          const config = getTypeConfig(type);
          const Icon = config.icon;
          return (
            <div
              key={type}
              className={cn(
                "rounded-lg border p-3 cursor-pointer transition-all hover:shadow-md",
                config.bgColor,
                config.borderColor,
                expandedTypes.has(type) && "ring-2 ring-primary"
              )}
              onClick={() => toggleExpand(type)}
            >
              <div className="flex items-center gap-2">
                <Icon className={cn("h-4 w-4", config.color)} />
                <span className="text-sm font-medium">{config.label}</span>
              </div>
              <div className="mt-1 text-2xl font-bold">{count}</div>
            </div>
          );
        })}
      </div>

      {/* 按类型分组的成果物列表 */}
      {Object.entries(groupedArtifacts).map(([type, typeArtifacts]) => {
        const config = getTypeConfig(type);
        const Icon = config.icon;
        const isExpanded = expandedTypes.has(type);

        return (
          <div
            key={type}
            className={cn(
              "rounded-lg border overflow-hidden",
              config.borderColor
            )}
          >
            {/* 类型标题栏 */}
            <div
              className={cn(
                "flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-accent/50 transition-colors",
                config.bgColor
              )}
              onClick={() => toggleExpand(type)}
            >
              <div className="flex items-center gap-2">
                {isExpanded ? (
                  <ChevronDown className="h-4 w-4 text-muted-foreground" />
                ) : (
                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                )}
                <Icon className={cn("h-5 w-5", config.color)} />
                <span className="font-semibold">{config.label}</span>
                <Badge variant="secondary" className="text-xs">
                  {typeArtifacts.length}
                </Badge>
              </div>
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    loadArtifacts();
                  }}
                >
                  <RefreshCw className="h-3 w-3" />
                </Button>
              </div>
            </div>

            {/* 成果物列表 */}
            {isExpanded && (
              <div className="divide-y">
                {typeArtifacts.map((artifact) => (
                  <div
                    key={artifact.id}
                    className="flex items-center justify-between px-4 py-3 hover:bg-accent/30 transition-colors"
                  >
                    <div className="flex items-center gap-3 min-w-0 flex-1">
                      <Icon className={cn("h-4 w-4 shrink-0", config.color)} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-sm truncate">
                            {artifact.file_name}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            ({(artifact.file_size / 1024).toFixed(1)} KB)
                          </span>
                        </div>
                        {artifact.description && (
                          <p className="text-xs text-muted-foreground truncate">
                            {artifact.description}
                          </p>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-1 shrink-0">
                      {/* 截图类型：查看图片 */}
                      {(type === "IOS_SCREENSHOT" || type === "WEB_TEST_RESULT") && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => viewScreenshot(artifact)}
                          title="查看截图"
                        >
                          <Eye className="h-4 w-4" />
                        </Button>
                      )}

                      {/* 脚本类型：编辑和执行 */}
                      {(type === "IOS_TEST_SCRIPT" || type === "WEB_TEST_SCRIPT") && (
                        <>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => loadScriptContent(artifact)}
                            title="编辑脚本"
                          >
                            <Edit className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                              if (onExecuteScript) {
                                onExecuteScript(artifact.id, artifact.file_name);
                              } else {
                                executeScript(artifact);
                              }
                            }}
                            disabled={executing}
                            title="执行脚本"
                          >
                            {executing ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <Play className="h-4 w-4" />
                            )}
                          </Button>
                        </>
                      )}

                      {/* 报告类型：查看报告 */}
                      {(type === "IOS_TEST_REPORT" || type === "WEB_TEST_REPORT") && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => viewReport(artifact)}
                          title="查看报告"
                        >
                          <Eye className="h-4 w-4" />
                        </Button>
                      )}

                      {/* 所有类型：下载 */}
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => downloadFile(artifact)}
                        title="下载文件"
                      >
                        <Download className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}

      {/* 执行结果展示 */}
      {executionResult.status && (
        <div
          className={cn(
            "rounded-lg border p-4",
            executionResult.status === "success" &&
              "border-green-200 bg-green-50 dark:bg-green-950/30",
            executionResult.status === "error" &&
              "border-red-200 bg-red-50 dark:bg-red-950/30",
            executionResult.status === "running" &&
              "border-blue-200 bg-blue-50 dark:bg-blue-950/30"
          )}
        >
          <div className="flex items-center gap-2 mb-2">
            {executionResult.status === "success" && (
              <CheckCircle2 className="h-5 w-5 text-green-500" />
            )}
            {executionResult.status === "error" && (
              <XCircle className="h-5 w-5 text-red-500" />
            )}
            {executionResult.status === "running" && (
              <Loader2 className="h-5 w-5 animate-spin text-blue-500" />
            )}
            <span className="font-semibold">
              {executionResult.status === "success" && "执行成功"}
              {executionResult.status === "error" && "执行失败"}
              {executionResult.status === "running" && "执行中..."}
            </span>
            {executionResult.duration && (
              <span className="text-sm text-muted-foreground flex items-center gap-1">
                <Clock className="h-3 w-3" />
                {executionResult.duration}s
              </span>
            )}
          </div>
          {executionResult.output && (
            <pre className="text-sm bg-background rounded p-3 overflow-auto max-h-60 border">
              <code>{executionResult.output}</code>
            </pre>
          )}
        </div>
      )}

      {/* 脚本编辑弹窗 */}
      {editingArtifact && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-background rounded-lg shadow-xl w-[90vw] h-[90vh] flex flex-col">
            {/* 弹窗标题栏 */}
            <div className="flex items-center justify-between px-4 py-3 border-b">
              <div className="flex items-center gap-2">
                <Code className="h-5 w-5 text-indigo-500" />
                <span className="font-semibold">
                  编辑脚本: {editingArtifact.file_name}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={saveScript}
                  disabled={saving}
                >
                  {saving ? (
                    <Loader2 className="h-4 w-4 animate-spin mr-1" />
                  ) : (
                    <Save className="h-4 w-4 mr-1" />
                  )}
                  保存
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => executeScript(editingArtifact)}
                  disabled={executing}
                >
                  {executing ? (
                    <Loader2 className="h-4 w-4 animate-spin mr-1" />
                  ) : (
                    <Play className="h-4 w-4 mr-1" />
                  )}
                  执行
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setEditingArtifact(null);
                    setScriptContent("");
                  }}
                >
                  关闭
                </Button>
              </div>
            </div>

            {/* 代码编辑器 */}
            <div className="flex-1 overflow-hidden">
              {loadingScript ? (
                <div className="flex items-center justify-center h-full">
                  <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                  <span className="ml-2 text-muted-foreground">
                    加载脚本内容...
                  </span>
                </div>
              ) : (
                <MonacoCodeEditor
                  value={scriptContent}
                  onChange={setScriptContent}
                  language="typescript"
                  height="100%"
                />
              )}
            </div>
          </div>
        </div>
      )}

      {/* 截图查看弹窗 */}
      {viewingImage && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
          onClick={() => {
            setViewingImage(null);
            setImageContent("");
          }}
        >
          <div
            className="bg-background rounded-lg shadow-xl p-4 max-w-[90vw] max-h-[90vh]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-2">
              <span className="font-semibold">{viewingImage.file_name}</span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setViewingImage(null);
                  setImageContent("");
                }}
              >
                关闭
              </Button>
            </div>
            {imageContent ? (
              <img
                src={imageContent}
                alt={viewingImage.file_name}
                className="max-w-full max-h-[80vh] rounded"
              />
            ) : (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                <span className="ml-2 text-muted-foreground">加载截图中...</span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
