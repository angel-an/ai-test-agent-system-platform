// TODO  MC80OmFIVnBZMlhscm9ua3VMazZiVEJUTUE9PTplZWRiZTlmNw==

/**
 * 增强版 Web 子功能测试成果物展示面板
 *
 * 支持默认显示、代码编辑和执行功能
 * P0: 收口执行入口，统一运行态展示
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
  Terminal,
  Clock,
  FileCode,
  Globe,
  FileArchive,
  ShieldAlert,
  ShieldCheck,
  Shield,
  Hash,
  AlertTriangle,
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

/**
 * 解析测试报告文件名，提取功能名、结果状态、时间戳
 * 文件名格式: 功能名-测试报告-结果-YYYYMMDD_HHMMSS.zip
 */
function parseReportFileName(fileName: string): {
  displayName: string;
  resultTag: string | null;
  timestamp: string | null;
  isHtml: boolean;
} {
  const isHtml = fileName.endsWith('.html');

  // 匹配新格式: 功能名-测试报告-结果-时间戳.zip
  const newFormatMatch = fileName.match(/^(.+?)-测试报告-(通过|失败|执行)-(\d{8}_\d{6})\.(zip|html)$/);
  if (newFormatMatch) {
    return {
      displayName: newFormatMatch[1],
      resultTag: newFormatMatch[2],
      timestamp: newFormatMatch[3],
      isHtml,
    };
  }

  // 旧格式: web-test-report-YYYYMMDD_HHMMSS.zip
  const oldFormatMatch = fileName.match(/^web-test-report-(\d{8}_\d{6})\.(zip|html)$/);
  if (oldFormatMatch) {
    return {
      displayName: "测试报告",
      resultTag: null,
      timestamp: oldFormatMatch[1],
      isHtml,
    };
  }

  // 其他格式，直接返回文件名（去掉扩展名）
  const baseName = fileName.replace(/\.(zip|html)$/, '');
  return {
    displayName: baseName,
    resultTag: null,
    timestamp: null,
    isHtml,
  };
}

/**
 * 格式化时间戳 YYYYMMDD_HHMMSS -> YYYY/MM/DD HH:MM:SS
 */
function formatTimestamp(timestamp: string | null): string {
  if (!timestamp || timestamp.length !== 15) return "";
  const year = timestamp.slice(0, 4);
  const month = timestamp.slice(4, 6);
  const day = timestamp.slice(6, 8);
  const hour = timestamp.slice(9, 11);
  const minute = timestamp.slice(11, 13);
  const second = timestamp.slice(13, 15);
  return `${year}/${month}/${day} ${hour}:${minute}:${second}`;
}

/**
 * 从描述中解析测试结果统计
 */
function parseResultStats(description: string): { passed?: number; failed?: number; duration?: string } {
  const stats: { passed?: number; failed?: number; duration?: string } = {};

  // 解析通过/失败数
  const match = description.match(/通过:\s*(\d+)\s*\|\s*失败:\s*(\d+)/);
  if (match) {
    stats.passed = parseInt(match[1], 10);
    stats.failed = parseInt(match[2], 10);
  }

  // 解析执行时间
  const durationMatch = description.match(/执行时间:\s*([\d.]+)秒/);
  if (durationMatch) {
    stats.duration = `${durationMatch[1]}s`;
  }

  return stats;
}

interface EnhancedTestArtifactsPanelProps {
  subFunctionId: string;
  projectId: string;
  onRefresh?: () => void;
  onTestCasesCountChange?: (count: number, subFunctionId?: string) => void;
  onExecuteScript?: (artifactId: string, fileName: string) => void;
  refreshTrigger?: number;
  // P0: 统一运行态
  runState?: {
    runId: string | null;
    artifactId: string | null;
    artifactVersion: string | null;
    mode: "safe" | "live";
    preflightStatus: "idle" | "passed" | "blocked";
    preflightResult: { reason_code?: string; message?: string; expected_sha256?: string; actual_sha256?: string } | null;
    isPublished: boolean;
  };
  onRunStateChange?: (state: {
    runId: string | null;
    artifactId: string | null;
    artifactVersion: string | null;
    mode: "safe" | "live";
    preflightStatus: "idle" | "passed" | "blocked";
    preflightResult: { reason_code?: string; message?: string; expected_sha256?: string; actual_sha256?: string } | null;
    isPublished: boolean;
  }) => void;
}

// 成果物类型映射
const artifactTypeConfig = {
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
    color: "text-green-500",
    bgColor: "bg-green-50 dark:bg-green-950",
    borderColor: "border-green-200 dark:border-green-800",
  },
  WEB_TEST_SCRIPT: {
    icon: Code,
    label: "测试脚本",
    color: "text-purple-500",
    bgColor: "bg-purple-50 dark:bg-purple-950",
    borderColor: "border-purple-200 dark:border-purple-800",
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
    color: "text-pink-500",
    bgColor: "bg-pink-50 dark:bg-pink-950",
    borderColor: "border-pink-200 dark:border-pink-800",
  },
  // 兜底配置：未知类型
  UNKNOWN: {
    icon: FileText,
    label: "其他",
    color: "text-gray-500",
    bgColor: "bg-gray-50 dark:bg-gray-950",
    borderColor: "border-gray-200 dark:border-gray-800",
  },
};
// TODO  My80OmFIVnBZMlhscm9ua3VMazZiVEJUTUE9PTplZWRiZTlmNw==

export function EnhancedTestArtifactsPanel({
  subFunctionId,
  projectId,
  onRefresh,
  onTestCasesCountChange,
  onExecuteScript,
  refreshTrigger,
  runState,
  onRunStateChange,
}: EnhancedTestArtifactsPanelProps) {
  const [artifacts, setArtifacts] = useState<TestArtifact[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedTypes, setExpandedTypes] = useState<Set<string>>(
    new Set([
      "WEB_TEST_PLAN",
      "WEB_TEST_CASE",
      "WEB_TEST_SCRIPT",
      "WEB_TEST_RESULT",
      "WEB_TEST_REPORT",
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

  // 使用 ref 防止并发加载请求
  const loadingRef = useRef(false);

  // 执行结果状态
  const [executionResult, setExecutionResult] = useState<{
    status: "success" | "error" | "running" | null;
    output: string;
    duration?: number;
  }>({ status: null, output: "" });

  // P1: 预检状态（本地 UI 状态，与 runState 同步）
  const [preflightChecking, setPreflightChecking] = useState(false);

  // P1: 预检签名 — 绑定 run_id + artifact_id + artifact_version + mode
  // 当这些关键参数变化时，预检状态自动失效
  const preflightSignatureRef = useRef<string>("");

  // P1: 检查预检签名是否仍然有效
  const isPreflightValid = useCallback(() => {
    if (!runState) return false;
    if (runState.preflightStatus !== "passed") return false;

    const currentSig = `${runState.runId}:${runState.artifactId}:${runState.artifactVersion}:${runState.mode}`;
    return preflightSignatureRef.current === currentSig;
  }, [runState]);

  // P1: 当关键参数变化时，自动失效预检状态
  useEffect(() => {
    if (runState && runState.preflightStatus === "passed") {
      const currentSig = `${runState.runId}:${runState.artifactId}:${runState.artifactVersion}:${runState.mode}`;
      if (preflightSignatureRef.current && preflightSignatureRef.current !== currentSig) {
        // 签名不匹配，预检失效
        console.log("[Preflight] 签名变化，预检失效:", {
          old: preflightSignatureRef.current,
          new: currentSig,
        });
        onRunStateChange?.({
          ...runState,
          preflightStatus: "idle",
          preflightResult: null,
        });
        preflightSignatureRef.current = "";
      }
    }
  }, [runState?.runId, runState?.artifactId, runState?.artifactVersion, runState?.mode]);

  // 使用 ref 存储回调，避免函数引用变化导致无限循环
  const onTestCasesCountChangeRef = useRef(onTestCasesCountChange);
  useEffect(() => {
    onTestCasesCountChangeRef.current = onTestCasesCountChange;
  }, [onTestCasesCountChange]);

  // 加载成果物列表
  const loadArtifacts = useCallback(async () => {
    // 防止并发请求：如果正在加载中，跳过本次请求
    if (loadingRef.current) return;

    try {
      loadingRef.current = true;
      setLoading(true);
      console.log(
        "[Enhanced Panel] ===== Loading artifacts for sub-function ====="
      );
      console.log("[Enhanced Panel] Sub-function ID:", subFunctionId);
      console.log("[Enhanced Panel] Project ID:", projectId);

      const apiUrl = `/api/v2/projects/${projectId}/web-functions/sub-functions/${subFunctionId}/artifacts`;
      console.log("[Enhanced Panel] Fetching from:", apiUrl);

      const response = await fetch(apiUrl);
      console.log("[Enhanced Panel] Response status:", response.status);
      console.log("[Enhanced Panel] Response OK:", response.ok);

      if (!response.ok) {
        const errorText = await response.text();
        console.error(
          "[Enhanced Panel] Failed to load artifacts. Status:",
          response.status
        );
        console.error("[Enhanced Panel] Error response:", errorText);
        toast.error(`加载测试成果物失败 (${response.status})`);
        setArtifacts([]);
        return;
      }

      const data = await response.json();
      console.log("[Enhanced Panel] Response data keys:", Object.keys(data));
      console.log("[Enhanced Panel] Full response data:", data);

      // 修复：API 返回格式为 { success: true, data: { artifacts: [...], total: N } }
      const artifacts = data.data?.artifacts || data.artifacts || [];
      console.log("[Enhanced Panel] Total artifacts found:", artifacts.length);
      console.log("[Enhanced Panel] Artifacts:", artifacts);

      if (artifacts.length > 0) {
        console.log(
          "[Enhanced Panel] Artifact types:",
          artifacts.map((a: any) => a.type)
        );
      }

      // 对 artifacts 按类型分组，并对 WEB_TEST_REPORT 按 created_at 倒序排列
      const sortedArtifacts = [...artifacts].sort((a: TestArtifact, b: TestArtifact) => {
        // 报告类型按时间倒序排列（最新的在前面）
        if (a.type === "web_test_report" && b.type === "web_test_report") {
          return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
        }
        // 其他类型保持原有顺序
        return 0;
      });

      setArtifacts(sortedArtifacts);

      // 查找test-cases artifact并解析测试用例数量
      // 修复：使用小写比较，因为 API 返回的是小写类型
      const testCasesArtifact = artifacts.find(
        (a: TestArtifact) => a.type === "web_test_case"
      );
      if (testCasesArtifact) {
        console.log(
          "[Enhanced Panel] Found test-cases artifact:",
          testCasesArtifact
        );

        if (onTestCasesCountChange) {
          try {
            const contentResponse = await fetch(
              `/api/v2/attachments/${testCasesArtifact.id}/content`
            );
            console.log(
              "[Enhanced Panel] Content response status:",
              contentResponse.status
            );

            if (contentResponse.ok) {
              const contentData = await contentResponse.json();
              console.log(
                "[Enhanced Panel] Content data type:",
                typeof contentData.content
              );
              console.log(
                "[Enhanced Panel] Content preview:",
                contentData.content?.substring(0, 300)
              );

              const testCasesData = JSON.parse(contentData.content);
              console.log(
                "[Enhanced Panel] Parsed testCasesData type:",
                typeof testCasesData
              );
              console.log("[Enhanced Panel] Is array?", Array.isArray(testCasesData));

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

              console.log("[Enhanced Panel] Final test cases count:", count);
              // 使用 ref 调用回调，避免函数引用变化导致无限循环
              onTestCasesCountChangeRef.current?.(count, subFunctionId);
            } else {
              console.error(
                "[Enhanced Panel] Failed to load content:",
                contentResponse.status
              );
            }
          } catch (error) {
            console.error(
              "[Enhanced Panel] Failed to parse test cases count:",
              error
            );
          }
        }
      }

      // 移除 onRefresh 调用以避免无限循环
      // 加载成果物不应该触发父组件的全面刷新
      // if (artifacts.length > 0 && onRefresh) {
      //   onRefresh();
      // }
    } catch (error) {
      console.error("[Enhanced Panel] Failed to load artifacts:", error);
      setArtifacts([]);
    } finally {
      setLoading(false);
      loadingRef.current = false;
    }
  }, [subFunctionId, projectId, refreshTrigger]);

  useEffect(() => {
    if (subFunctionId) {
      console.log(
        "[Enhanced Panel] Loading artifacts for sub-function:",
        subFunctionId
      );
      loadArtifacts();
    }
  }, [subFunctionId, refreshTrigger, loadArtifacts]);

  // 轮询刷新：当检测到可能有执行中任务时启用智能轮询（30秒间隔，减少服务器压力）
  // 轮询条件：存在 WEB_TEST_RESULT 类型成果物（表示有执行记录）
  useEffect(() => {
    if (!subFunctionId) return;

    // 检查是否存在执行结果类型的成果物，判断是否需要轮询
    const hasExecutionResults = artifacts.some(
      (a) => a.type.toUpperCase() === "WEB_TEST_RESULT"
    );

    // 如果没有执行结果，不需要轮询（新创建子功能或从未执行过）
    if (!hasExecutionResults) return;

    const pollInterval = setInterval(() => {
      console.log("[Enhanced Panel] Smart polling artifacts for sub-function:", subFunctionId);
      loadArtifacts();
    }, 30000); // 30秒间隔，平衡实时性和服务器压力

    return () => clearInterval(pollInterval);
  }, [subFunctionId, loadArtifacts, artifacts]);

  /**
   * 下载成果物：统一走已有的直连下载接口。
   *
   * 使用 `/api/v2/attachments/{id}/download`（无额外参数），
   * 与报告分支、「下载 ZIP」按钮保持一致，不再各自占位。
   */
  const downloadArtifact = (artifact: TestArtifact) => {
    if (!artifact?.id) {
      toast.error("该成果物缺少 ID，无法下载");
      return;
    }
    window.open(
      `/api/v2/attachments/${artifact.id}/download`,
      "_blank",
      "noopener,noreferrer"
    );
  };

  // 加载脚本内容
  const loadScriptContent = async (artifact: TestArtifact) => {
    try {
      // 立即打开弹窗并显示loading
      setEditingArtifact(artifact);
      setScriptContent(""); // 清空之前的内容
      setLoadingScript(true);

      console.log(
        "[Enhanced Panel] Loading script content for artifact:",
        artifact.id
      );

      const response = await fetch(
        `/api/v2/attachments/${artifact.id}/content`
      );
      console.log(
        "[Enhanced Panel] Script content response status:",
        response.status
      );

      if (!response.ok) {
        const errorText = await response.text();
        console.error("[Enhanced Panel] Failed to load script:", errorText);
        toast.error("加载脚本内容失败");
        setEditingArtifact(null); // 关闭弹窗
        return;
      }

      const data = await response.json();
      console.log(
        "[Enhanced Panel] Script content loaded, length:",
        data.content?.length
      );
      console.log(
        "[Enhanced Panel] Script content preview:",
        data.content?.substring(0, 200)
      );

      setScriptContent(data.content || "");
    } catch (error) {
      console.error("[Enhanced Panel] Failed to load script content:", error);
      toast.error("加载脚本内容失败");
      setEditingArtifact(null); // 关闭弹窗
    } finally {
      setLoadingScript(false);
    }
  };

  // 保存脚本
  const saveScript = async () => {
    if (!editingArtifact) return;

    try {
      setSaving(true);
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

      if (!response.ok) {
        throw new Error("保存脚本失败");
      }

      toast.success("脚本保存成功");
      setEditingArtifact(null);
    } catch (error) {
      console.error("Failed to save script:", error);
      toast.error("保存脚本失败");
    } finally {
      setSaving(false);
    }
  };

  // P1: 执行前预检 — 调用后端 runtime/preflight 进行门禁检查
  const runPreflightCheck = async (
    targetArtifact: TestArtifact,
    mode: "safe" | "live"
  ): Promise<{ passed: boolean; result?: any }> => {
    const runId = runState?.runId || `run_${Date.now()}`;

    try {
      setPreflightChecking(true);
      console.log("[Preflight] 开始预检:", { runId, artifactId: targetArtifact.id, mode });

      const response = await fetch(
        `/api/v2/projects/${projectId}/runtime/preflight`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            run_id: runId,
            artifact_id: targetArtifact.id,
            artifact_version: runState?.artifactVersion || null,
            track: "web",
            mode: mode,
          }),
        }
      );

      const data = await response.json();
      console.log("[Preflight] 预检结果:", data);

      if (!response.ok || !data.success) {
        // 预检被阻断
        const blockedResult = {
          reason_code: data.data?.reason_code || "PREFLIGHT_FAILED",
          message: data.data?.message || "预检请求失败",
          expected_sha256: data.data?.expected_sha256,
          actual_sha256: data.data?.actual_sha256,
        };

        // 回写状态到父组件
        onRunStateChange?.({
          ...runState!,
          runId,
          artifactId: targetArtifact.id,
          preflightStatus: "blocked",
          preflightResult: blockedResult,
          isPublished: data.data?.is_published ?? false,
        });

        toast.error(`执行被阻断: ${blockedResult.message}`);
        return { passed: false, result: data.data };
      }

      // 预检通过
      const passedResult = {
        reason_code: undefined,
        message: data.data?.message || "预检通过",
        expected_sha256: data.data?.expected_sha256,
        actual_sha256: data.data?.actual_sha256,
      };

      // P1: 设置预检签名，绑定 run_id + artifact_id + artifact_version + mode
      const signature = `${runId}:${targetArtifact.id}:${runState?.artifactVersion || ""}:${mode}`;
      preflightSignatureRef.current = signature;
      console.log("[Preflight] 签名设置:", signature);

      // 回写状态到父组件
      onRunStateChange?.({
        ...runState!,
        runId,
        artifactId: targetArtifact.id,
        preflightStatus: "passed",
        preflightResult: passedResult,
        isPublished: true,
      });

      toast.success("预检通过，准备执行");
      return { passed: true, result: data.data };
    } catch (error) {
      console.error("[Preflight] 预检异常:", error);
      const errMsg = error instanceof Error ? error.message : "预检请求异常";

      onRunStateChange?.({
        ...runState!,
        runId,
        artifactId: targetArtifact.id,
        preflightStatus: "blocked",
        preflightResult: { reason_code: "PREFLIGHT_ERROR", message: errMsg },
        isPublished: false,
      });

      toast.error(`预检失败: ${errMsg}`);
      return { passed: false };
    } finally {
      setPreflightChecking(false);
    }
  };

  // 执行脚本 - P0: 收口执行入口，统一从当前选中的脚本/成果物发起
  // P1: 增加预检门禁，阻断不通过时禁止执行
  const executeScript = async (artifact?: TestArtifact, mode?: "safe" | "live") => {
    const targetArtifact = artifact || editingArtifact;
    const execMode = mode || runState?.mode || "safe";

    if (!targetArtifact) {
      console.error("[测试成果物] 没有找到目标 artifact");
      return;
    }

    // P1: 步骤1 — 执行前预检（门禁检查）
    const preflight = await runPreflightCheck(targetArtifact, execMode);
    if (!preflight.passed) {
      console.log("[测试成果物] 预检未通过，中止执行");
      // 展示阻断详情在 executionResult 中
      setExecutionResult({
        status: "error",
        output: `【执行被阻断】\n原因代码: ${runState?.preflightResult?.reason_code || "UNKNOWN"}\n详情: ${runState?.preflightResult?.message || "预检未通过"}\n\n请检查:\n1. 产物是否已发布（publish）\n2. 脚本哈希是否一致\n3. 执行门控是否允许当前模式（safe/live）`,
      });
      return;
    }

    // P1: 步骤2 — 预检通过，更新运行态并继续执行
    if (onRunStateChange && runState) {
      onRunStateChange({
        ...runState,
        artifactId: targetArtifact.id,
        mode: execMode,
        preflightStatus: "passed",
      });
    }

    // 如果提供了执行回调，调用它打开 AI 对话助手
    if (onExecuteScript) {
      console.log("[测试成果物] 打开 AI 助手执行脚本");
      console.log("[测试成果物] Artifact ID:", targetArtifact.id);
      console.log("[测试成果物] Artifact 文件名:", targetArtifact.file_name);
      // P0: 在 prompt 中注入 run_id 和 artifact_version，确保后端使用统一上下文
      const runId = runState?.runId || `run_${Date.now()}`;
      const prompt = `请执行测试脚本「${targetArtifact.file_name}」。

关键参数：
- script_id: ${targetArtifact.id}
- project_identifier: ${projectId}
- sub_function_id: ${subFunctionId}
- run_id: ${runId}
- mode: ${execMode}

执行步骤：
1. 调用 download_web_script(script_id="${targetArtifact.id}", project_identifier="${projectId}") 下载脚本
2. 使用步骤1返回的 local_path 调用 execute_web_script(
     local_script_path=local_path,
     framework="auto",
     reporter="html",
     project_identifier="${projectId}",
     sub_function_id="${subFunctionId}",
     run_id="${runId}"
   )
3. 保存执行报告和执行结果到当前子功能成果物
4. 返回 run_id、报告入口、脚本来源授权结果、执行状态和失败原因（如有）`;
      onExecuteScript(targetArtifact.id, targetArtifact.file_name);
      // 关闭编辑器弹窗
      setEditingArtifact(null);
      setExecutionResult({ status: null, output: "" });
      return;
    }

    // 如果没有提供回调，使用模拟执行（向后兼容）
    try {
      setExecuting(true);
      setExecutionResult({ status: "running", output: "正在执行测试脚本...\n" });

      const startTime = Date.now();

      // 模拟执行
      await new Promise((resolve) => setTimeout(resolve, 2000));

      const duration = (Date.now() - startTime) / 1000;

      // 模拟执行结果
      const mockOutput = `
✓ 测试执行完成

执行摘要:
  总测试数: 5
  通过: 4
  失败: 1
  跳过: 0

执行时间: ${duration.toFixed(2)}s

详细结果:
  ✓ 打开登录页面 - 成功 (125ms)
  ✓ 输入用户名 - 成功 (234ms)
  ✓ 输入密码 - 成功 (156ms)
  ✗ 点击登录按钮 - 失败 (89ms)
    Expected: 200, Received: 404
  ✓ 验证登录成功 - 成功 (98ms)
`;

      setExecutionResult({
        status: "success",
        output: mockOutput,
        duration,
      });

      toast.success("脚本执行完成");
    } catch (error) {
      console.error("Failed to execute script:", error);
      setExecutionResult({
        status: "error",
        output: `执行失败: ${error instanceof Error ? error.message : "未知错误"}`,
      });
      toast.error("执行脚本失败");
    } finally {
      setExecuting(false);
    }
  };

  // 切换展开/折叠
  const toggleExpand = (type: string) => {
    setExpandedTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) {
        next.delete(type);
      } else {
        next.add(type);
      }
      return next;
    });
  };

  // 按类型分组成果物，每个类型内按创建时间降序排序（最新的在前）
  // 修复：将小写类型转换为大写，以匹配 artifactTypeConfig
  const groupedArtifacts = artifacts.reduce(
    (acc, artifact) => {
      // 将小写类型转换为大写（web_test_script -> WEB_TEST_SCRIPT）
      let normalizedType = artifact.type.toUpperCase();
      // 如果类型不在配置中，使用 UNKNOWN 兜底
      if (!artifactTypeConfig[normalizedType as keyof typeof artifactTypeConfig]) {
        normalizedType = "UNKNOWN";
      }
      if (!acc[normalizedType]) {
        acc[normalizedType] = [];
      }
      // 创建新对象，修改 type 为大写，以便后续比较
      acc[normalizedType].push({
        ...artifact,
        type: normalizedType
      });
      return acc;
    },
    {} as Record<string, TestArtifact[]>
  );

  // 对每个分组内的 artifacts 按创建时间降序排序
  Object.keys(groupedArtifacts).forEach((type) => {
    groupedArtifacts[type].sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    );
  });

  // Debug: Log grouped artifacts
  console.log("[Enhanced Panel] ===== Rendering artifacts =====");
  console.log("[Enhanced Panel] Total artifacts:", artifacts.length);
  console.log("[Enhanced Panel] Grouped artifacts:", Object.keys(groupedArtifacts));
  console.log("[Enhanced Panel] Expanded types:", Array.from(expandedTypes));
  Object.entries(groupedArtifacts).forEach(([type, items]) => {
    console.log(
      `[Enhanced Panel] Type ${type}: ${items.length} items, expanded: ${expandedTypes.has(type)}`
    );
  });

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <Loader2 className="h-8 w-8 animate-spin text-primary mb-3" />
        <p className="text-sm text-muted-foreground">加载测试成果物中...</p>
      </div>
    );
  }

  if (artifacts.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 px-4 text-center border-2 border-dashed rounded-xl bg-gradient-to-br from-blue-50/50 to-purple-50/50 dark:from-blue-950/20 dark:to-purple-950/20">
        <div className="relative">
          <div className="absolute inset-0 bg-gradient-to-r from-blue-400 to-purple-400 rounded-full blur-xl opacity-20 animate-pulse"></div>
          <FileText className="relative h-20 w-20 text-muted-foreground mb-4" />
        </div>
        <p className="text-lg font-semibold mb-2 bg-gradient-to-r from-blue-600 to-purple-600 bg-clip-text text-transparent">
          暂无测试成果物
        </p>
        <p className="text-sm text-muted-foreground max-w-md mb-4">
          该子功能尚未生成测试成果物。点击下方按钮开始生成测试计划、测试用例和测试脚本。
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={loadArtifacts}
          className="gap-2 hover:bg-gradient-to-r hover:from-blue-50 hover:to-purple-50"
        >
          <RefreshCw className="h-4 w-4" />
          刷新
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* 成果物统计卡片 - 自适应网格布局 */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2 mb-4">
        {Object.entries(groupedArtifacts).map(([type, items]) => {
          const config = artifactTypeConfig[type as keyof typeof artifactTypeConfig] || artifactTypeConfig.UNKNOWN;
          const Icon = config.icon;
          return (
            <div
              key={`stat-${type}`}
              className={cn(
                "rounded-lg p-3 border-2 cursor-pointer transition-all hover:shadow-md",
                config.borderColor,
                config.bgColor,
                expandedTypes.has(type) && "ring-2 ring-offset-2 ring-primary/50"
              )}
              onClick={() => toggleExpand(type)}
            >
              <div className="flex items-center gap-2 mb-1">
                <Icon className={cn("h-4 w-4", config.color)} />
                <span className="text-xs font-medium text-muted-foreground truncate">
                  {config.label}
                </span>
              </div>
              <div className="text-2xl font-bold">{items.length}</div>
            </div>
          );
        })}
      </div>

      {/* 成果物列表 */}
      {Object.entries(groupedArtifacts).map(([type, items]) => {
        const config = artifactTypeConfig[type as keyof typeof artifactTypeConfig] || artifactTypeConfig.UNKNOWN;

        const Icon = config.icon;
        const isExpanded = expandedTypes.has(type);

        return (
          <div
            key={type}
            className={cn(
              "rounded-xl border-2 bg-card shadow-sm overflow-hidden transition-all",
              config.borderColor,
              isExpanded && "shadow-lg"
            )}
          >
            {/* 标题栏 */}
            <button
              onClick={() => toggleExpand(type)}
              className={cn(
                "w-full px-4 py-3 transition-all flex items-center justify-between",
                "hover:bg-gradient-to-r",
                type === "WEB_TEST_PLAN" && "hover:from-blue-50/50 hover:to-blue-100/50",
                type === "WEB_TEST_CASE" && "hover:from-green-50/50 hover:to-green-100/50",
                type === "WEB_TEST_SCRIPT" && "hover:from-purple-50/50 hover:to-purple-100/50"
              )}
            >
              <div className="flex items-center gap-3">
                <div className={cn("p-2 rounded-lg", config.bgColor)}>
                  <Icon className={cn("h-5 w-5", config.color)} />
                </div>
                <div className="text-left">
                  <span className="font-semibold text-sm block">
                    {config.label}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {items.length} 个文件
                  </span>
                </div>
              </div>
              {isExpanded ? (
                <ChevronDown className="h-5 w-5 text-muted-foreground transition-transform" />
              ) : (
                <ChevronRight className="h-5 w-5 text-muted-foreground transition-transform" />
              )}
            </button>

            {/* 内容区域 */}
            {isExpanded && (
              <div className="border-t border-border/50 p-3 space-y-2 bg-gradient-to-br from-background to-muted/20">
                {items.map((artifact) => {
                  // 解析报告文件名信息
                  const reportInfo = artifact.type === "WEB_TEST_REPORT"
                    ? parseReportFileName(artifact.file_name)
                    : null;
                  const resultStats = artifact.type === "WEB_TEST_REPORT"
                    ? parseResultStats(artifact.description)
                    : null;

                  return (
                  <div
                    key={artifact.id}
                    className={cn(
                      "group rounded-lg border bg-background p-4 hover:shadow-lg transition-all cursor-pointer",
                      "hover:border-primary/50"
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-2">
                          {artifact.type === "WEB_TEST_REPORT" && reportInfo ? (
                            <>
                              {/* 功能名称 */}
                              <p className="text-sm font-semibold truncate group-hover:text-primary transition-colors">
                                {reportInfo.displayName}
                              </p>
                              {/* 结果状态标签 */}
                              {reportInfo.resultTag && (
                                <Badge
                                  variant="outline"
                                  className={cn(
                                    "text-xs shrink-0 font-medium",
                                    reportInfo.resultTag === "通过" && "bg-green-50 text-green-700 border-green-200",
                                    reportInfo.resultTag === "失败" && "bg-red-50 text-red-700 border-red-200",
                                    reportInfo.resultTag === "执行" && "bg-blue-50 text-blue-700 border-blue-200"
                                  )}
                                >
                                  {reportInfo.resultTag === "通过" && <CheckCircle2 className="h-3 w-3 mr-1" />}
                                  {reportInfo.resultTag === "失败" && <XCircle className="h-3 w-3 mr-1" />}
                                  {reportInfo.resultTag === "执行" && <Terminal className="h-3 w-3 mr-1" />}
                                  {reportInfo.resultTag}
                                </Badge>
                              )}
                              {/* 文件类型标签 */}
                              <Badge variant="outline" className="text-xs shrink-0 text-muted-foreground">
                                {reportInfo.isHtml ? "HTML" : "ZIP"}
                              </Badge>
                              {/* 文件大小 */}
                              <Badge variant="outline" className="text-xs shrink-0 text-muted-foreground">
                                {(artifact.file_size / 1024).toFixed(1)} KB
                              </Badge>
                            </>
                          ) : (
                            <>
                              <p className="text-sm font-semibold truncate group-hover:text-primary transition-colors">
                                {artifact.file_name}
                              </p>
                              <Badge variant="outline" className="text-xs shrink-0">
                                {(artifact.file_size / 1024).toFixed(1)} KB
                              </Badge>
                            </>
                          )}
                        </div>
                        {/* 描述/统计信息行 */}
                        <div className="flex items-center gap-2 mb-2">
                          {artifact.type === "WEB_TEST_REPORT" && resultStats ? (
                            <div className="flex items-center gap-3 text-xs">
                              {resultStats.passed !== undefined && (
                                <span className="text-green-600 font-medium">
                                  ✓ 通过 {resultStats.passed}
                                </span>
                              )}
                              {resultStats.failed !== undefined && resultStats.failed > 0 && (
                                <span className="text-red-600 font-medium">
                                  ✗ 失败 {resultStats.failed}
                                </span>
                              )}
                              {resultStats.duration && (
                                <span className="text-muted-foreground">
                                  ⏱ {resultStats.duration}
                                </span>
                              )}
                              {/* 时间戳 */}
                              {reportInfo?.timestamp && (
                                <span className="text-muted-foreground">
                                  {formatTimestamp(reportInfo.timestamp)}
                                </span>
                              )}
                            </div>
                          ) : (
                            <p className="text-xs text-muted-foreground line-clamp-2">
                              {artifact.description}
                            </p>
                          )}
                        </div>
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Clock className="h-3 w-3" />
                          <span>
                            {new Date(artifact.created_at).toLocaleString("zh-CN")}
                          </span>
                        </div>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        {artifact.type === "WEB_TEST_SCRIPT" ? (
                          <>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-9 px-3 gap-2 hover:bg-blue-50 hover:text-blue-600"
                              onClick={() => loadScriptContent(artifact)}
                              title="编辑脚本"
                            >
                              <Edit className="h-4 w-4" />
                              <span className="text-xs">编辑</span>
                            </Button>
                            {/* P1: 执行按钮组 — 带模式切换和预检门禁 */}
                            <div className="flex items-center gap-1">
                              <Button
                                variant="ghost"
                                size="sm"
                                className={cn(
                                  "h-9 px-2 gap-1 hover:bg-green-50 hover:text-green-600",
                                  (runState?.preflightStatus === "blocked" || preflightChecking) && "opacity-50 cursor-not-allowed"
                                )}
                                onClick={() => {
                                  // Safe 模式执行（默认）
                                  executeScript(artifact, "safe");
                                }}
                                disabled={preflightChecking || runState?.preflightStatus === "blocked"}
                                title="Safe 模式执行（只读/沙箱）"
                              >
                                {preflightChecking ? (
                                  <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                  <Play className="h-4 w-4" />
                                )}
                                <span className="text-xs">执行</span>
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                className={cn(
                                  "h-9 px-2 gap-1 hover:bg-orange-50 hover:text-orange-600",
                                  (runState?.preflightStatus === "blocked" || preflightChecking) && "opacity-50 cursor-not-allowed",
                                  runState?.mode === "live" && "bg-orange-50 text-orange-600"
                                )}
                                onClick={() => {
                                  // Live 模式执行（真实环境）
                                  // 先切换模式，再执行
                                  if (onRunStateChange && runState) {
                                    onRunStateChange({ ...runState, mode: "live" });
                                  }
                                  executeScript(artifact, "live");
                                }}
                                disabled={preflightChecking || runState?.preflightStatus === "blocked"}
                                title="🔴 Live 模式执行（真实环境，需谨慎）"
                              >
                                <Globe className="h-3.5 w-3.5" />
                                <span className="text-[10px]">Live</span>
                              </Button>
                            </div>
                          </>
                        ) : artifact.type === "WEB_TEST_RESULT" ? (
                          <>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-9 px-3 gap-2 hover:bg-orange-50 hover:text-orange-600"
                              onClick={() => loadScriptContent(artifact)}
                              title="查看详情"
                            >
                              <Eye className="h-4 w-4" />
                              <span className="text-xs">查看</span>
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-9 px-3 gap-2 hover:bg-slate-50 hover:text-slate-600"
                              onClick={() => downloadArtifact(artifact)}
                              title="下载报告"
                            >
                              <Download className="h-4 w-4" />
                            </Button>
                          </>
                        ) : artifact.type === "WEB_TEST_REPORT" ? (
                          <>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-9 px-3 gap-2 hover:bg-pink-50 hover:text-pink-600"
                              onClick={async () => {
                                try {
                                  // 判断报告类型：HTML 可视化报告 vs ZIP 原生报告
                                  const isHtml = artifact.content_type === "text/html" || artifact.file_name.endsWith(".html");

                                  if (isHtml) {
                                    // HTML 报告：直接打开可视化报告
                                    window.open(
                                      `/api/v2/attachments/${artifact.id}/report-html`,
                                      "_blank",
                                      "noopener,noreferrer"
                                    );
                                  } else {
                                    // ZIP 报告：获取查看器 URL（原生报告，用于失败排查）
                                    const response = await fetch(
                                      `/api/v2/attachments/${artifact.id}/report-viewer`
                                    );
                                    if (!response.ok) {
                                      toast.error("无法打开测试报告");
                                      return;
                                    }
                                    const data = await response.json();
                                    window.open(data.index_url, "_blank", "noopener,noreferrer");
                                  }
                                } catch (error) {
                                  console.error("打开报告失败:", error);
                                  toast.error("打开报告失败");
                                }
                              }}
                              title="查看报告"
                            >
                              <Eye className="h-4 w-4" />
                              <span className="text-xs">查看</span>
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-9 px-3 gap-2 hover:bg-slate-50 hover:text-slate-600"
                              onClick={() => {
                                // 下载原始文件
                                window.open(
                                  `/api/v2/attachments/${artifact.id}/download`,
                                  "_blank"
                                );
                              }}
                              title="下载文件"
                            >
                              <Download className="h-4 w-4" />
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-9 px-3 gap-2 hover:bg-purple-50 hover:text-purple-600"
                              onClick={() => loadScriptContent(artifact)}
                              title="查看内容"
                            >
                              <Eye className="h-4 w-4" />
                              <span className="text-xs">查看</span>
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-9 px-3 gap-2 hover:bg-slate-50 hover:text-slate-600"
                              onClick={() => downloadArtifact(artifact)}
                              title="下载文件"
                            >
                              <Download className="h-4 w-4" />
                            </Button>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}

      {/* 代码编辑器弹窗 */}
      {editingArtifact && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="bg-background rounded-xl shadow-2xl w-full max-w-7xl h-[90vh] flex flex-col border-2 border-border">
            {/* 头部 */}
            <div
              className={cn(
                "flex items-center justify-between px-6 py-4 border-b shrink-0",
                editingArtifact.type === "WEB_TEST_SCRIPT" &&
                  "bg-gradient-to-r from-blue-50/50 to-purple-50/50 dark:from-blue-950/20 dark:to-purple-950/20",
                editingArtifact.type === "WEB_TEST_RESULT" &&
                  "bg-gradient-to-r from-orange-50/50 to-amber-50/50 dark:from-orange-950/20 dark:to-amber-950/20",
                editingArtifact.type === "WEB_TEST_REPORT" &&
                  "bg-gradient-to-r from-pink-50/50 to-rose-50/50 dark:from-pink-950/20 dark:to-rose-950/20",
                (editingArtifact.type === "WEB_TEST_PLAN" ||
                  editingArtifact.type === "WEB_TEST_CASE") &&
                  "bg-gradient-to-r from-green-50/50 to-emerald-50/50 dark:from-green-950/20 dark:to-emerald-950/20"
              )}
            >
              <div className="flex items-center gap-3">
                <div
                  className={cn(
                    "p-2 rounded-lg",
                    editingArtifact.type === "WEB_TEST_SCRIPT" &&
                      "bg-purple-100 dark:bg-purple-900",
                    editingArtifact.type === "WEB_TEST_RESULT" &&
                      "bg-orange-100 dark:bg-orange-900",
                    editingArtifact.type === "WEB_TEST_REPORT" &&
                      "bg-pink-100 dark:bg-pink-900",
                    (editingArtifact.type === "WEB_TEST_PLAN" ||
                      editingArtifact.type === "WEB_TEST_CASE") &&
                      "bg-green-100 dark:bg-green-900"
                  )}
                >
                  {editingArtifact.type === "WEB_TEST_SCRIPT" && (
                    <Code className="h-5 w-5 text-purple-600 dark:text-purple-400" />
                  )}
                  {editingArtifact.type === "WEB_TEST_RESULT" && (
                    <CheckCircle2 className="h-5 w-5 text-orange-600 dark:text-orange-400" />
                  )}
                  {editingArtifact.type === "WEB_TEST_REPORT" && (
                    <FileCode className="h-5 w-5 text-pink-600 dark:text-pink-400" />
                  )}
                  {editingArtifact.type === "WEB_TEST_PLAN" && (
                    <FileText className="h-5 w-5 text-blue-600 dark:text-blue-400" />
                  )}
                  {editingArtifact.type === "WEB_TEST_CASE" && (
                    <ScrollText className="h-5 w-5 text-green-600 dark:text-green-400" />
                  )}
                </div>
                <div>
                  <h3 className="text-lg font-bold">
                    {editingArtifact.type === "WEB_TEST_SCRIPT" && "编辑测试脚本"}
                    {editingArtifact.type === "WEB_TEST_RESULT" && "查看执行结果"}
                    {editingArtifact.type === "WEB_TEST_REPORT" && "查看测试报告"}
                    {editingArtifact.type === "WEB_TEST_PLAN" && "查看测试计划"}
                    {editingArtifact.type === "WEB_TEST_CASE" && "查看测试用例"}
                  </h3>
                  <p className="text-sm text-muted-foreground flex items-center gap-2">
                    <span>{editingArtifact.file_name}</span>
                    <Badge variant="outline" className="text-xs">
                      {(editingArtifact.file_size / 1024).toFixed(1)} KB
                    </Badge>
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    loadScriptContent(editingArtifact);
                    setExecutionResult({ status: null, output: "" });
                    toast.success("已重置为原始内容");
                  }}
                  disabled={loadingScript || executing}
                  className="gap-2"
                >
                  <RefreshCw className="h-4 w-4" />
                  重置
                </Button>
                {editingArtifact.type === "WEB_TEST_SCRIPT" && (
                  <>
                    <Button
                      variant="default"
                      size="sm"
                      onClick={saveScript}
                      disabled={saving || loadingScript || executing || preflightChecking}
                      className="gap-2"
                    >
                      {saving ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Save className="h-4 w-4" />
                      )}
                      保存
                    </Button>
                    {/* P1: 弹窗内执行按钮组 — 带预检门禁 */}
                    <div className="flex items-center gap-1">
                      <Button
                        variant="default"
                        size="sm"
                        onClick={() => executeScript(editingArtifact, "safe")}
                        disabled={executing || loadingScript || preflightChecking || runState?.preflightStatus === "blocked"}
                        className="gap-2 bg-gradient-to-r from-green-600 to-emerald-600 hover:from-green-700 hover:to-emerald-700"
                        title="Safe 模式执行（只读/沙箱）"
                      >
                        {preflightChecking ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : executing ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Play className="h-4 w-4" />
                        )}
                        执行测试
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          if (onRunStateChange && runState) {
                            onRunStateChange({ ...runState, mode: "live" });
                          }
                          executeScript(editingArtifact, "live");
                        }}
                        disabled={executing || loadingScript || preflightChecking || runState?.preflightStatus === "blocked"}
                        className={cn(
                          "gap-1 border-orange-300 text-orange-700 hover:bg-orange-50 hover:text-orange-800",
                          runState?.mode === "live" && "bg-orange-50 text-orange-700"
                        )}
                        title="🔴 Live 模式执行（真实环境，需谨慎）"
                      >
                        <Globe className="h-3.5 w-3.5" />
                        <span className="text-xs">Live</span>
                      </Button>
                    </div>
                  </>
                )}
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => {
                    setEditingArtifact(null);
                    setExecutionResult({ status: null, output: "" });
                  }}
                  disabled={loadingScript || executing}
                  className="hover:bg-red-50 hover:text-red-600"
                >
                  <XCircle className="h-5 w-5" />
                </Button>
              </div>
            </div>

            {/* 主内容区域 - 分为编辑器和执行结果 */}
            <div className="flex-1 min-h-0 flex flex-col lg:flex-row overflow-hidden">
              {/* 编辑器区域 */}
              <div
                className={cn(
                  "flex-1 min-h-0 overflow-hidden bg-background relative border-r",
                  executionResult.status && "lg:w-1/2"
                )}
              >
                {loadingScript || scriptContent === "" ? (
                  <div className="absolute inset-0 z-10 flex items-center justify-center bg-background">
                    <div className="flex flex-col items-center gap-3">
                      <Loader2 className="h-8 w-8 animate-spin text-primary" />
                      <p className="text-sm text-muted-foreground">
                        {loadingScript ? "加载脚本内容中..." : "准备编辑器..."}
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="h-full w-full">
                    <MonacoCodeEditor
                      key={`editor-${editingArtifact.id}`}
                      value={scriptContent}
                      onChange={setScriptContent}
                      language={
                        editingArtifact.file_name.endsWith(".json")
                          ? "json"
                          : editingArtifact.file_name.endsWith(".py")
                            ? "python"
                            : "typescript"
                      }
                      readOnly={loadingScript || editingArtifact.type !== "WEB_TEST_SCRIPT"}
                      height="100%"
                      minimap={true}
                      fontSize={14}
                      onSave={saveScript}
                    />
                  </div>
                )}
              </div>

              {/* 执行结果区域 */}
              {executionResult.status && (
                <div
                  className={cn(
                    "flex-1 min-h-0 flex flex-col bg-slate-50 dark:bg-slate-950",
                    "lg:w-1/2"
                  )}
                >
                  {/* 结果头部 */}
                  <div
                    className={cn(
                      "px-4 py-3 border-b flex items-center justify-between",
                      executionResult.status === "success" &&
                        "bg-green-50 dark:bg-green-950/20 border-green-200",
                      executionResult.status === "error" &&
                        "bg-red-50 dark:bg-red-950/20 border-red-200",
                      executionResult.status === "running" &&
                        "bg-blue-50 dark:bg-blue-950/20 border-blue-200"
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <Terminal
                        className={cn(
                          "h-4 w-4",
                          executionResult.status === "success" && "text-green-600",
                          executionResult.status === "error" && "text-red-600",
                          executionResult.status === "running" && "text-blue-600"
                        )}
                      />
                      <span className="font-semibold text-sm">
                        {executionResult.status === "success" && "执行成功"}
                        {executionResult.status === "error" && "执行失败"}
                        {executionResult.status === "running" && "执行中..."}
                      </span>
                      {executionResult.duration && (
                        <Badge variant="outline" className="text-xs">
                          {executionResult.duration.toFixed(2)}s
                        </Badge>
                      )}
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setExecutionResult({ status: null, output: "" })}
                      className="h-7 px-2"
                    >
                      <XCircle className="h-4 w-4" />
                    </Button>
                  </div>

                  {/* 结果内容 */}
                  <ScrollArea className="flex-1 p-4">
                    <pre
                      className={cn(
                        "text-xs font-mono whitespace-pre-wrap",
                        executionResult.status === "success" &&
                          "text-green-800 dark:text-green-200",
                        executionResult.status === "error" &&
                          "text-red-800 dark:text-red-200",
                        executionResult.status === "running" &&
                          "text-blue-800 dark:text-blue-200"
                      )}
                    >
                      {executionResult.output}
                    </pre>
                  </ScrollArea>
                </div>
              )}
            </div>

            {/* 底部状态栏 */}
            <div className="px-6 py-3 border-t bg-muted/30 shrink-0 flex items-center justify-between">
              <div className="flex items-center gap-4 text-xs text-muted-foreground">
                <div className="flex items-center gap-1">
                  <FileCode className="h-3 w-3" />
                  <span>
                    {editingArtifact.file_name.endsWith(".json")
                      ? "JSON"
                      : editingArtifact.file_name.endsWith(".py")
                        ? "Python"
                        : "TypeScript"}
                  </span>
                </div>
                <div className="flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  <span>
                    创建于{" "}
                    {new Date(editingArtifact.created_at).toLocaleString("zh-CN")}
                  </span>
                </div>
                {/* P1: 预检状态指示器 */}
                {runState && (
                  <div className="flex items-center gap-1.5">
                    {runState.preflightStatus === "passed" ? (
                      <>
                        <ShieldCheck className="h-3 w-3 text-green-500" />
                        <span className="text-green-600 font-medium">预检通过</span>
                      </>
                    ) : runState.preflightStatus === "blocked" ? (
                      <>
                        <ShieldAlert className="h-3 w-3 text-red-500" />
                        <span className="text-red-600 font-medium">已阻断</span>
                      </>
                    ) : (
                      <>
                        <Shield className="h-3 w-3 text-muted-foreground" />
                        <span>未预检</span>
                      </>
                    )}
                    <span className="text-muted-foreground">|</span>
                    <span className={cn(
                      runState.mode === "live" ? "text-orange-600 font-medium" : "text-blue-600"
                    )}>
                      {runState.mode === "live" ? "🔴 Live" : "🛡️ Safe"}
                    </span>
                  </div>
                )}
              </div>
              <div className="text-xs text-muted-foreground">
                {editingArtifact.type === "WEB_TEST_SCRIPT" ? (
                  <span>按 Ctrl+S 保存 • 点击&quot;执行测试&quot;运行脚本 • 预检通过后方可执行</span>
                ) : (
                  <span>只读模式</span>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
