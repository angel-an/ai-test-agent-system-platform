// FIXME  MC80OmFIVnBZMlhscm9ua3VMazZPSFEwV0E9PTo3MWU1Nzg2OA==

"use client";
// TODO  MS80OmFIVnBZMlhscm9ua3VMazZPSFEwV0E9PTo3MWU1Nzg2OA==

import * as React from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import {
  Zap,
  FileCode,
  Sparkles,
  MessageSquare,
  RefreshCw,
  Workflow,
  Layers,
  Plus,
  ChevronRight,
  ChevronDown,
  Play,
  Globe,
  Filter,
  X,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Hash,
  FolderGit,
} from "lucide-react";
import { MainLayout } from "@/components/layout";
import { useLanguage } from "@/providers/LanguageProvider";
import { WebSubFunctionSidebar } from "@/components/web-tests/web-function-sidebar";
import { WebFunctionFolderTree } from "@/components/web-tests/folder-tree";
import type { WebFunctionFolderTreeRef } from "@/components/web-tests/folder-tree";
import { WebFunctionList, WebSubFunctionList } from "@/components/web-tests";
import { CreateWebFunctionDialog, AIGenerateDialog } from "@/components/web-tests";
import { EnhancedTestArtifactsPanel } from "@/components/web-tests/test-artifacts-panel-enhanced";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerHeader,
  DrawerTitle as DrawerTitleComp,
} from "@/components/ui/drawer";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { AIChatContainer } from "@/components/langgraph/AIChatContainer";
import { ClientProvider } from "@/providers/ClientProvider";
import { Assistant } from "@langchain/langgraph-sdk";
import { cn } from "@/lib/utils";
import {
  listWebFunctions,
  listWebSubFunctions,
  createWebFunction,
  updateWebFunction,
  deleteWebFunction,
  deleteWebSubFunction,
  type WebFunction,
  type WebSubFunction,
  type CreateWebFunctionRequest,
} from "@/lib/api/web-functions";
import {
  createFolder,
  updateFolder,
  deleteFolder,
} from "@/lib/api/folders";
import type {
  FolderInfo,
  FolderCreate,
} from "@/lib/api/types";
// FIXME  Mi80OmFIVnBZMlhscm9ua3VMazZPSFEwV0E9PTo3MWU1Nzg2OA==

// Simplified to only function mode
type TestMode = "function";

const RUN_STATUS_LABELS: Record<string, string> = {
  passed: "通过",
  failed: "失败",
  timeout: "超时",
  error: "错误",
  skipped: "跳过",
  running: "执行中",
  pending: "待执行",
};

function formatRunStatus(status?: string | null) {
  return status ? RUN_STATUS_LABELS[status] || status : "未执行";
}

export default function WebTestsPage() {
  const params = useParams();
  const projectId = params.projectId as string;
  const { t } = useLanguage();

  // 文件夹树 ref
  const folderTreeRef = React.useRef<WebFunctionFolderTreeRef>(null);

  // 模式切换状态 - hardcoded to "function" for now
  const [testMode, setTestMode] = React.useState<TestMode>("function");

  // Web函数测试相关状态
  const [webFunctions, setWebFunctions] = React.useState<WebFunction[]>([]);
  const [webSubFunctions, setWebSubFunctions] = React.useState<WebSubFunction[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [actualTestCasesCounts, setActualTestCasesCounts] = React.useState<Record<string, number>>({});
  const [selectedFolderId, setSelectedFolderId] = React.useState<string | null>(null);
  const [selectedIds, setSelectedIds] = React.useState<Set<string>>(new Set());
  const [selectedSubFunctionId, setSelectedSubFunctionId] = React.useState<string | null>(null);
  const [showSubFunctionSidebar, setShowSubFunctionSidebar] = React.useState(false);
  const [subFunctionDrawerOpen, setSubFunctionDrawerOpen] = React.useState(false);
  const [selectedWebFunction, setSelectedWebFunction] = React.useState<WebFunction | null>(null);
  const [artifactsRefreshTrigger, setArtifactsRefreshTrigger] = React.useState(0);
  const [sidebarRefreshTrigger, setSidebarRefreshTrigger] = React.useState(0);

  // 分页和筛选
  const [page, setPage] = React.useState(1);
  const [pageSize, setPageSize] = React.useState(10);
  const [total, setTotal] = React.useState(0);
  const [searchQuery, setSearchQuery] = React.useState("");
  const [formatFilter, setFormatFilter] = React.useState("");

  // 抽屉独立分页状态（避免与主列表分页互相影响）
  const [drawerPage, setDrawerPage] = React.useState(1);
  const drawerPageSize = 20;

  // 对话框状态
  const [editingWebFunction, setEditingWebFunction] = React.useState<WebFunction | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = React.useState(false);
  const [deletingWebFunction, setDeletingWebFunction] = React.useState<WebFunction | null>(null);
  const [folderDialogOpen, setFolderDialogOpen] = React.useState(false);
  const [editingFolder, setEditingFolder] = React.useState<FolderInfo | null>(null);
  const [folderParentId, setFolderParentId] = React.useState<string | undefined>();
  const [folderFormData, setFolderFormData] = React.useState<FolderCreate>({
    name: "",
    description: "",
  });
  const [deleteFolderDialogOpen, setDeleteFolderDialogOpen] = React.useState(false);
  const [deletingFolder, setDeletingFolder] = React.useState<FolderInfo | null>(null);
  const [createWebFunctionFolder, setCreateWebFunctionFolder] = React.useState<FolderInfo | null>(null);
  const [selectedFolderName, setSelectedFolderName] = React.useState<string | undefined>();
  const [createFunctionDialogOpen, setCreateFunctionDialogOpen] = React.useState(false);
  const [aiGenerateDialogOpen, setAiGenerateDialogOpen] = React.useState(false);

  // AI 聊天状态
  const [aiChatOpen, setAiChatOpen] = React.useState(false);
  const [aiChatInitialPrompt, setAiChatInitialPrompt] = React.useState<string>("");
  const [aiChatKey, setAiChatKey] = React.useState<number>(0);
  const [assistant, setAssistant] = React.useState<Assistant | null>(null);
  const [extraMcpServers, setExtraMcpServers] = React.useState<string>("");
  const [mcpConfigOpen, setMcpConfigOpen] = React.useState(false);
  // 新增：专门用于控制是否创建新对话的 state
  const [shouldCreateNewThread, setShouldCreateNewThread] = React.useState(false);

  // === P0: 统一页面状态源 ===
  // 当前运行态：run_id / artifact_id / artifact_version / mode / preflight_status
  const [currentRunState, setCurrentRunState] = React.useState<{
    runId: string | null;
    artifactId: string | null;
    artifactVersion: string | null;
    mode: "safe" | "live";
    preflightStatus: "idle" | "passed" | "blocked";
    preflightResult: { reason_code?: string; message?: string; expected_sha256?: string; actual_sha256?: string } | null;
    isPublished: boolean;
  }>({
    runId: null,
    artifactId: null,
    artifactVersion: null,
    mode: "safe",
    preflightStatus: "idle",
    preflightResult: null,
    isPublished: false,
  });

  // 生成 run_id 的便捷函数
  const generateRunId = React.useCallback(() => {
    const now = new Date();
    const timestamp = now.toISOString().replace(/[-:T.Z]/g, "").slice(0, 14);
    return `run_${timestamp}`;
  }, []);

  // 当选择子功能时，初始化运行状态
  React.useEffect(() => {
    if (selectedSubFunctionId) {
      setCurrentRunState(prev => ({
        ...prev,
        runId: prev.runId || generateRunId(),
        artifactId: null,
        artifactVersion: null,
        preflightStatus: "idle",
        preflightResult: null,
        isPublished: false,
      }));
    }
  }, [selectedSubFunctionId, generateRunId]);

  // 初始化 Assistant
  React.useEffect(() => {
    const initAssistant = async () => {
      try {
        // 使用正确的 LangGraph assistant UUID（不是 graph_id）
        const assistantId = "0f14872d-c3cd-5218-a92f-429d67ef35ff";
        let parsedMcpServers: Record<string, unknown> = {};
        if (extraMcpServers.trim()) {
          try { parsedMcpServers = JSON.parse(extraMcpServers); } catch { /* ignore invalid JSON */ }
        }
        const mockAssistant: Assistant = {
          assistant_id: assistantId,
          graph_id: "web_agent",
          config: {
            configurable: {
              project_identifier: projectId,
              folder_id: selectedFolderId || "",
              template_type: "web_test",
              ...(Object.keys(parsedMcpServers).length > 0 && { extra_mcp_servers: parsedMcpServers }),
            }
          },
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          metadata: {},
          version: 1,
          name: t("webTests.webFunctionAssistant"),
          context: {},
        };
        setAssistant(mockAssistant);
      } catch (error) {
        console.error("Failed to initialize assistant:", error);
      }
    };
    initAssistant();
  }, [projectId, selectedFolderId, extraMcpServers]);

  // 加载 Web 函数测试数据
  const loadWebFunctions = React.useCallback(async () => {
    if (testMode !== "function") return;

    try {
      setLoading(true);

      // 先加载功能列表
      const params = {
        p: page,
        page_size: pageSize,
        search: searchQuery || undefined,
      };

      let response;
      if (selectedFolderId) {
        response = await listWebFunctions(projectId, { ...params, folder_id: selectedFolderId });
      } else {
        response = await listWebFunctions(projectId, params);
      }

      let items: any[] = [];
      let total = 0;
      const rawResponse = response as any;
      // 处理多种可能的返回格式
      if (rawResponse?.data?.items) {
        items = rawResponse.data.items;
        total = rawResponse.data.total || 0;
      } else if (rawResponse?.items) {
        items = rawResponse.items;
        total = rawResponse.total || 0;
      } else if (Array.isArray(rawResponse?.data)) {
        items = rawResponse.data;
        total = rawResponse.data.length;
      } else if (Array.isArray(rawResponse)) {
        items = rawResponse;
        total = rawResponse.length;
      }

      setWebFunctions(items);
      setTotal(total);

      // 如果功能列表为空，清空子功能列表和选中状态
      if (items.length === 0) {
        setWebSubFunctions([]);
        setSelectedSubFunctionId(null);
      }
    } catch (error) {
      console.error("Failed to load web functions:", error);
      toast.error(t("webTests.loadWebFunctionsFailed"));
    } finally {
      setLoading(false);
    }
  }, [projectId, selectedFolderId, page, pageSize, searchQuery, testMode]);

  // 根据模式加载数据
  React.useEffect(() => {
    if (projectId) {
      loadWebFunctions();
    }
  }, [projectId, testMode, loadWebFunctions]);

  // 当功能列表加载完成后，自动加载第一个功能的子功能并选中第一个
  React.useEffect(() => {
    if (webFunctions.length > 0 && webSubFunctions.length === 0) {
      const firstFunction = webFunctions[0];
      const firstFunctionId = firstFunction.id;
      setSelectedWebFunction(firstFunction);
      listWebSubFunctions(projectId, { function_id: firstFunctionId, p: 1, page_size: 100 })
        .then((result) => {
          const items = result?.items || (result as any)?.data?.items || [];
          setWebSubFunctions(items);
          if (items.length > 0) {
            setSelectedSubFunctionId(items[0].id);
          } else {
            setSelectedSubFunctionId(null);
          }
        })
        .catch((e) => console.error("Auto-load sub-functions failed:", e));
    }
  }, [webFunctions, projectId]);

  const handleSelectFolder = React.useCallback((folder: FolderInfo | null) => {
    setSelectedFolderId(folder?.id || null);
    setSelectedFolderName(folder?.name);
    setPage(1);
    setDrawerPage(1); // 重置抽屉分页
    setSelectedIds(new Set());
    // 切换文件夹时清除子功能选择，避免显示错误的测试成果物
    setSelectedSubFunctionId(null);
  }, []);

  // 处理创建函数
  const [createFunctionFolderId, setCreateFunctionFolderId] = React.useState<string | null>(null);
  const handleCreateWebFunction = React.useCallback((folderId?: string | null) => {
    setCreateFunctionFolderId(folderId || null);
    setCreateFunctionDialogOpen(true);
  }, []);

  // 处理函数创建成功
  const handleFunctionCreated = React.useCallback(() => {
    loadWebFunctions();
    folderTreeRef.current?.refresh();
  }, [loadWebFunctions]);

  // 处理文件夹操作
  const handleCreateFolder = React.useCallback((parentId?: string | null) => {
    setEditingFolder(null);
    setFolderParentId(parentId ?? undefined);
    setFolderFormData({ name: "", description: "" });
    setFolderDialogOpen(true);
  }, []);

  const handleEditFolder = React.useCallback((folder: FolderInfo) => {
    setEditingFolder(folder);
    setFolderParentId(undefined);
    setFolderFormData({
      name: folder.name,
      description: folder.description || "",
    });
    setFolderDialogOpen(true);
  }, []);

  const handleDeleteFolderClick = React.useCallback((folder: FolderInfo) => {
    setDeletingFolder(folder);
    setDeleteFolderDialogOpen(true);
  }, []);

  const handleMoveFolder = React.useCallback((_folder: FolderInfo) => {
    // TODO: 实现移动文件夹
  }, []);

  const handleSelectWebSubFunctionCallback = React.useCallback((subFunctionId: string) => {
    setSelectedSubFunctionId(subFunctionId);
    setShowSubFunctionSidebar(true);
  }, []);

  const handleTestCasesCountChange = React.useCallback(async (count: number, subFunctionId?: string) => {
    const targetSubFunctionId = subFunctionId || selectedSubFunctionId || webSubFunctions[0]?.id;
    if (!targetSubFunctionId) return;

    setActualTestCasesCounts(prev => ({
      ...prev,
      [targetSubFunctionId]: count,
    }));

    setWebSubFunctions(prev => prev.map(sf => {
      if (sf.id === targetSubFunctionId) {
        return { ...sf, total_test_cases: count };
      }
      return sf;
    }));
  }, [selectedSubFunctionId, webSubFunctions]);

  // 加载特定功能的子功能
  const loadSubFunctionsForFunction = React.useCallback(async (functionId: string): Promise<WebSubFunction[]> => {
    try {
      const subFunctions = await listWebSubFunctions(projectId, {
        function_id: functionId,
        p: 1,
        page_size: 100,
      });
      const items = subFunctions.items || (subFunctions as any)?.data?.items || [];
      setWebSubFunctions(items);
      return items;
    } catch (error) {
      console.error("Failed to load sub-functions:", error);
      toast.error("加载子功能失败");
      setWebSubFunctions([]);
      return [];
    }
  }, [projectId]);

  // 处理查看 Web 功能（需要在 loadSubFunctionsForFunction 之后定义）
  const handleViewWebFunction = React.useCallback(async (webFunction: WebFunction) => {
    setSelectedWebFunction(webFunction);
    setDrawerPage(1); // 打开抽屉时重置分页到第一页
    const items = await loadSubFunctionsForFunction(webFunction.id);
    setSelectedSubFunctionId((prev) =>
      items.some((sf) => sf.id === prev) ? prev : items[0]?.id || null
    );
    setSubFunctionDrawerOpen(true);
  }, [loadSubFunctionsForFunction]);

  const handleSelectWebFunctionCallback = React.useCallback(async (functionId: string) => {
    const webFunction = webFunctions.find((wf) => wf.id === functionId) || null;
    setSelectedWebFunction(webFunction);
    setDrawerPage(1);
    const items = await loadSubFunctionsForFunction(functionId);
    setSelectedSubFunctionId(items[0]?.id || null);
    setSubFunctionDrawerOpen(items.length > 0);
    if (items.length === 0) {
      toast.info("该功能暂无子功能");
    }
  }, [loadSubFunctionsForFunction, webFunctions]);

  // 处理 AI 聊天关闭
  const handleAIChatClose = React.useCallback(() => {
    setAiChatOpen(false);
  }, []);

  // 使用 ref 存储 webFunctions，避免闭包陷阱
  const webFunctionsRef = React.useRef(webFunctions);
  React.useEffect(() => {
    webFunctionsRef.current = webFunctions;
  }, [webFunctions]);

  // 使用 ref 存储 selectedWebFunction，避免闭包陷阱
  const selectedWebFunctionRef = React.useRef(selectedWebFunction);
  React.useEffect(() => {
    selectedWebFunctionRef.current = selectedWebFunction;
  }, [selectedWebFunction]);

  // 使用 ref 存储 selectedSubFunctionId，避免延迟刷新读取到旧值
  const selectedSubFunctionIdRef = React.useRef(selectedSubFunctionId);
  React.useEffect(() => {
    selectedSubFunctionIdRef.current = selectedSubFunctionId;
  }, [selectedSubFunctionId]);

  const scrollToArtifactsSection = React.useCallback(() => {
    const artifactsSection = document.querySelector('[data-testid="test-artifacts-section"]');
    if (artifactsSection) {
      artifactsSection.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, []);

  // 处理测试创建成功（也用于测试执行完成后刷新数据）
  const handleTestCreated = React.useCallback(() => {
    // 立即刷新一次
    loadWebFunctions();
    folderTreeRef.current?.refresh();
    setArtifactsRefreshTrigger(prev => prev + 1);
    scrollToArtifactsSection();
    // 刷新子功能列表以更新执行次数和最后状态
    if (selectedSubFunctionIdRef.current) {
      // 使用 ref 获取最新的 webFunctions，避免闭包陷阱
      const latestWebFunctions = webFunctionsRef.current;
      const latestSelectedWebFunction = selectedWebFunctionRef.current;
      const selectedFunc = latestWebFunctions.find(wf =>
        wf.id === latestSelectedWebFunction?.id
      );
      if (selectedFunc) {
        loadSubFunctionsForFunction(selectedFunc.id);
      }
      // 触发侧边栏刷新以更新详情面板中的执行次数和最后状态
      setSidebarRefreshTrigger(prev => prev + 1);
    }

    // 延迟刷新：后端数据库更新可能需要时间，3秒后再次刷新
    setTimeout(() => {
      console.log("[WebTests] Delayed refresh after test execution");
      loadWebFunctions();
      setArtifactsRefreshTrigger(prev => prev + 1);
      scrollToArtifactsSection();
      if (selectedSubFunctionIdRef.current) {
        const latestWebFunctions = webFunctionsRef.current;
        const latestSelectedWebFunction = selectedWebFunctionRef.current;
        const selectedFunc = latestWebFunctions.find(wf =>
          wf.id === latestSelectedWebFunction?.id
        );
        if (selectedFunc) {
          loadSubFunctionsForFunction(selectedFunc.id);
        }
        setSidebarRefreshTrigger(prev => prev + 1);
      }
    }, 3000);

    // 再次延迟刷新：6秒后最终刷新
    setTimeout(() => {
      console.log("[WebTests] Final delayed refresh after test execution");
      loadWebFunctions();
      setArtifactsRefreshTrigger(prev => prev + 1);
      scrollToArtifactsSection();
      if (selectedSubFunctionIdRef.current) {
        const latestWebFunctions = webFunctionsRef.current;
        const latestSelectedWebFunction = selectedWebFunctionRef.current;
        const selectedFunc = latestWebFunctions.find(wf =>
          wf.id === latestSelectedWebFunction?.id
        );
        if (selectedFunc) {
          loadSubFunctionsForFunction(selectedFunc.id);
        }
        setSidebarRefreshTrigger(prev => prev + 1);
      }
    }, 6000);

    // 轮询刷新：stream 被取消时，后端可能仍在执行，需要轮询检查状态
    let pollCount = 0;
    const maxPolls = 5;
    const pollInterval = setInterval(() => {
      pollCount++;
      console.log(`[WebTests] Polling refresh ${pollCount}/${maxPolls}`);
      loadWebFunctions();
      setArtifactsRefreshTrigger(prev => prev + 1);
      scrollToArtifactsSection();
      if (selectedSubFunctionIdRef.current) {
        const latestWebFunctions = webFunctionsRef.current;
        const latestSelectedWebFunction = selectedWebFunctionRef.current;
        const selectedFunc = latestWebFunctions.find(wf =>
          wf.id === latestSelectedWebFunction?.id
        );
        if (selectedFunc) {
          loadSubFunctionsForFunction(selectedFunc.id);
        }
        setSidebarRefreshTrigger(prev => prev + 1);
      }
      if (pollCount >= maxPolls) {
        clearInterval(pollInterval);
        console.log("[WebTests] Polling refresh completed");
      }
    }, 10000); // 每10秒轮询一次
  }, [loadWebFunctions, loadSubFunctionsForFunction, scrollToArtifactsSection]);

  // 处理侧边栏关闭
  const handleSidebarClose = React.useCallback(() => {
    setShowSubFunctionSidebar(false);
    // 关闭侧边栏时不应清除 selectedSubFunctionId，否则测试成果物会消失
    // 查看详情是纯读操作，关闭后无需刷新数据；写操作（编辑/删除/创建）已有各自的刷新回调
  }, []);

  // 处理侧边栏生成测试
  const handleSidebarGenerateTest = React.useCallback(() => {
    setShowSubFunctionSidebar(false);
    setAiChatOpen(true);
    setShouldCreateNewThread(true);
    setAiChatInitialPrompt(t("webTests.generateTestsForFunction", { id: selectedSubFunctionId || "" }));
  }, [selectedSubFunctionId, t]);

  // 处理侧边栏打开 AI 聊天
  const handleSidebarOpenAIChat = React.useCallback((prompt: string) => {
    setAiChatInitialPrompt(prompt);
    setAiChatKey(prev => prev + 1);
    setShouldCreateNewThread(true);
    setShowSubFunctionSidebar(false);
    setAiChatOpen(true);
    scrollToArtifactsSection();
  }, [scrollToArtifactsSection]);

  // 处理侧边栏刷新
  const handleSidebarRefresh = React.useCallback(() => {
    loadWebFunctions();
    folderTreeRef.current?.refresh();
  }, [loadWebFunctions]);

  // 处理抽屉关闭
  const handleDrawerClose = React.useCallback((open: boolean) => {
    setSubFunctionDrawerOpen(open);
    if (!open) {
      // 关闭抽屉时无需刷新数据，纯查看/选择操作不修改数据
      setSelectedWebFunction(null);
    }
  }, []);

  // 处理子功能选择（从抽屉）
  const handleSelectSubFunctionFromDrawer = React.useCallback((subFunctionId: string) => {
    setSelectedSubFunctionId(subFunctionId);
    setSubFunctionDrawerOpen(false);
    setTimeout(() => {
      const artifactsSection = document.querySelector('[data-testid="test-artifacts-section"]');
      if (artifactsSection) {
        artifactsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }, 100);
  }, []);

  // 处理取消删除文件夹
  const handleCancelDeleteFolder = React.useCallback(() => {
    setDeleteFolderDialogOpen(false);
    setDeletingFolder(null);
  }, []);

  // 处理取消删除功能
  const handleCancelDeleteFunction = React.useCallback(() => {
    setDeleteDialogOpen(false);
    setDeletingWebFunction(null);
  }, []);

  // 处理 MCP 配置关闭
  const handleMcpConfigClose = React.useCallback(() => {
    setMcpConfigOpen(false);
  }, []);

  const handleExecuteScript = (artifactId: string, fileName: string) => {
    const prompt = `请执行测试脚本「${fileName}」。

关键参数：
- script_id: ${artifactId}
- project_identifier: ${projectId}
- sub_function_id: ${selectedSubFunctionId || "未指定"}
- web_function_id: ${selectedWebFunction?.id || "未指定"}
- web_function_name: ${selectedWebFunction?.display_name || "未指定"}

执行步骤：
1. 调用 download_web_script(script_id="${artifactId}", project_identifier="${projectId}") 下载脚本
2. 使用步骤1返回的 local_path 调用 execute_web_script(local_script_path=local_path, framework="auto", reporter="html", project_identifier="${projectId}", sub_function_id="${selectedSubFunctionId || ""}")
3. 保存执行报告和执行结果到当前子功能成果物
4. 返回 run_id、报告入口、脚本来源授权结果、执行状态和失败原因（如有）`;
    setAiChatInitialPrompt(prompt);
    setAiChatKey(prev => prev + 1);
    setShouldCreateNewThread(true);
    setShowSubFunctionSidebar(false);
    setAiChatOpen(true);
  };

  // 查看子功能详情
  const handleViewSubFunctionDetail = (subFunctionId: string) => {
    setSelectedSubFunctionId(subFunctionId);
    setShowSubFunctionSidebar(true);
    setSubFunctionDrawerOpen(false);
  };

  // 查看子功能测试用例
  const handleViewSubFunctionTestCases = (subFunctionId: string) => {
    setSelectedSubFunctionId(subFunctionId);
    setSubFunctionDrawerOpen(false);
    setTimeout(() => {
      const artifactsSection = document.querySelector('[data-testid="test-artifacts-section"]');
      if (artifactsSection) {
        artifactsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }, 100);
  };

  // 运行子功能测试
  const handleRunSubFunctionTest = (subFunctionId: string) => {
    const subFunction = webSubFunctions.find(sf => sf.id === subFunctionId);
    const prompt = `请为子功能「${subFunction?.display_name || ''}」执行测试。

关键参数：
- project_identifier: ${projectId}
- sub_function_id: ${subFunctionId}
- web_function_id: ${selectedWebFunction?.id || "未指定"}

执行步骤：
1. 调用 get_web_sub_function_artifacts(sub_function_id="${subFunctionId}") 查询该子功能关联的测试脚本
2. 找到类型为 web_test_script 的脚本，调用 download_web_script(script_id=脚本ID, project_identifier="${projectId}") 下载脚本
3. 使用步骤2返回的 local_path 调用 execute_web_script(local_script_path=local_path, framework="auto", reporter="html", project_identifier="${projectId}", sub_function_id="${subFunctionId}")
4. 保存执行报告和执行结果到当前子功能成果物
5. 返回 run_id、报告入口、脚本来源授权结果、执行状态和失败原因（如有）`;
    setAiChatInitialPrompt(prompt);
    setAiChatKey(prev => prev + 1);
    setShouldCreateNewThread(true);
    setSubFunctionDrawerOpen(false);
    setShowSubFunctionSidebar(false);
    setAiChatOpen(true);
  };

  // 处理提交文件夹
  const handleSubmitFolder = async () => {
    if (!folderFormData.name.trim()) {
      toast.error(t("webTests.folderNameRequired"));
      return;
    }
    try {
      setSubmitting(true);
      if (editingFolder) {
        // 编辑文件夹 - 使用本地更新
        const response = await updateFolder(projectId, editingFolder.id, folderFormData);
        toast.success(t("webTests.folderUpdateSuccess"));
        setFolderDialogOpen(false);
        // 本地更新文件夹
        if (response.success && response.data) {
          folderTreeRef.current?.updateFolderLocally(editingFolder.id, response.data);
        }
      } else {
        // 创建文件夹 - 使用本地添加
        const response = await createFolder(projectId, {
          ...folderFormData,
          parent_id: folderParentId,
          folder_type: "web_test",  // 指定为 Web 测试类型文件夹
        });
        toast.success(t("webTests.folderCreateSuccess"));
        setFolderDialogOpen(false);
        // 本地添加文件夹
        if (response.success && response.data) {
          folderTreeRef.current?.addFolderLocally(response.data, folderParentId || null);
        }
      }
    } catch (error) {
      console.error("Failed to save folder:", error);
      toast.error(editingFolder ? t("webTests.folderUpdateFailed") : t("webTests.folderCreateFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  // 处理删除文件夹
  const handleDeleteFolder = async () => {
    if (!deletingFolder) return;

    try {
      await deleteFolder(projectId, deletingFolder.id);
      toast.success(t("webTests.folderDeleteSuccess"));
      setDeleteFolderDialogOpen(false);
      setDeletingFolder(null);

      // 如果删除的是当前选中的文件夹，清空选中状态
      if (selectedFolderId === deletingFolder.id) {
        setSelectedFolderId(null);
        setSelectedFolderName(undefined);
        setWebSubFunctions([]);
        setWebFunctions([]);
      }

      // 刷新文件夹树
      folderTreeRef.current?.refresh();
    } catch (error) {
      console.error("Failed to delete folder:", error);
      toast.error(t("webTests.folderDeleteFailed"));
    }
  };

  // 处理 AI 生成
  const handleAIGenerate = (prompt: string) => {
    setAiChatInitialPrompt(prompt);
    setAiChatKey(prev => prev + 1);
    setShouldCreateNewThread(true);
    setAiChatOpen(true);
  };

  // 处理删除 Web 功能 - 打开确认对话框
  const handleDeleteWebFunction = (webFunction: WebFunction) => {
    setDeletingWebFunction(webFunction);
    setDeleteDialogOpen(true);
  };

  // 实际执行删除
  const confirmDeleteWebFunction = async () => {
    if (!deletingWebFunction) return;

    try {
      console.log("Deleting web function:", deletingWebFunction.id, deletingWebFunction);
      const result = await deleteWebFunction(projectId, deletingWebFunction.id);
      console.log("Delete result:", result);
      toast.success(t("webTests.functionDeleteSuccess"));
      setDeleteDialogOpen(false);
      setDeletingWebFunction(null);
      await loadWebFunctions();
      // 刷新文件夹树
      folderTreeRef.current?.refresh();
    } catch (error: any) {
      console.error("Failed to delete web function:", error);
      console.error("Error details:", {
        message: error.message,
        response: error.response?.data,
        status: error.response?.status
      });
      toast.error(`${t("webTests.functionDeleteFailed")}: ${error.message || "Unknown error"}`);
    }
  };

  // 处理删除子功能
  const handleDeleteSubFunction = React.useCallback(async (subFunctionId: string) => {
    try {
      await deleteWebSubFunction(projectId, subFunctionId);
      toast.success("子功能已删除");
      // 如果删除的是当前选中的子功能，清空选中状态
      if (selectedSubFunctionId === subFunctionId) {
        setSelectedSubFunctionId(null);
      }
      // 刷新子功能列表
      if (selectedWebFunction) {
        const response = await listWebSubFunctions(projectId, {
          function_id: selectedWebFunction.id,
          p: 1,
          page_size: 100,
        });
        setWebSubFunctions(response.items || []);
      }
      // 刷新功能列表以更新计数
      await loadWebFunctions();
    } catch (error: any) {
      console.error("Failed to delete sub function:", error);
      toast.error(`删除子功能失败: ${error.message || "未知错误"}`);
    }
  }, [projectId, selectedSubFunctionId, selectedWebFunction, loadWebFunctions]);

  // 处理批量运行 Web 功能测试
  const handleBulkRun = React.useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;

    // 获取选中功能的所有子功能ID
    const selectedFunctions = webFunctions.filter(wf => selectedIds.has(wf.id));
    const subFunctionIds: string[] = [];

    for (const func of selectedFunctions) {
      try {
        const subFunctions = await listWebSubFunctions(projectId, {
          function_id: func.id,
          p: 1,
          page_size: 100,
        });
        const items = subFunctions.items || (subFunctions as any)?.data?.items || [];
        for (const sf of items) {
          subFunctionIds.push(sf.id);
        }
      } catch (error) {
        console.error(`Failed to load sub-functions for ${func.id}:`, error);
      }
    }

    if (subFunctionIds.length === 0) {
      toast.error("选中的功能没有子功能，无法运行测试");
      return;
    }

    const functionNames = selectedFunctions.map(wf => wf.display_name).join("、");

    const prompt = `请为以下 Web 功能批量执行测试：${functionNames}（共 ${subFunctionIds.length} 个子功能）。

关键参数：
- project_identifier: ${projectId}
- sub_function_ids: ${subFunctionIds.join(",")}

执行步骤（对每个子功能循环执行）：
1. 调用 get_web_sub_function_artifacts 查询子功能关联的测试脚本
2. 下载脚本到测试工作目录（download_web_script）
3. 使用 execute_web_script 执行测试（framework="auto", reporter="html", project_identifier="${projectId}", sub_function_id=子功能ID）
4. 汇总所有子功能的执行结果并报告`;

    setAiChatInitialPrompt(prompt);
    setAiChatKey(prev => prev + 1);
    setAiChatOpen(true);
  }, [selectedIds, webFunctions, projectId]);

  const handleBulkDelete = async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;

    try {
      console.log("Bulk deleting web functions:", ids);
      for (const id of ids) {
        console.log("Deleting function:", id);
        const result = await deleteWebFunction(projectId, id);
        console.log("Delete result for", id, ":", result);
      }
      toast.success(t("webTests.bulkDeleteSuccess", { count: String(ids.length) }));
      setSelectedIds(new Set());
      await loadWebFunctions();
      // 刷新文件夹树
      folderTreeRef.current?.refresh();
    } catch (error: any) {
      console.error("Failed to bulk delete web functions:", error);
      console.error("Error details:", {
        message: error.message,
        response: error.response?.data,
        status: error.response?.status
      });
      toast.error(`${t("webTests.bulkDeleteFailed")}: ${error.message || "Unknown error"}`);
    }
  };

  const currentSubFunction = selectedSubFunctionId
    ? webSubFunctions.find(sf => sf.id === selectedSubFunctionId) || null
    : null;

  return (
    <MainLayout title={t("webTests.title")}>
      <div className="relative flex h-[calc(100vh-8rem)] rounded-lg border bg-card overflow-hidden">
        <div className="flex h-full w-full">
          {/* 左侧面板 (320px) */}
          <div className="w-80 shrink-0 border-r bg-muted/10 flex flex-col">
            {/* 标题 */}
            <div className="p-3 border-b bg-background">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold flex items-center gap-2">
                  <Globe className="h-4 w-4 text-blue-500" />
                  {t("webTests.testManagement")}
                </h3>
              </div>
            </div>

            {/* 文件夹树 */}
            <div className="flex-1 overflow-hidden">
              <WebFunctionFolderTree
                ref={folderTreeRef}
                projectId={projectId}
                selectedFolderId={selectedFolderId}
                onSelectFolder={handleSelectFolder}
                onCreateFolder={handleCreateFolder}
                onEditFolder={handleEditFolder}
                onDeleteFolder={handleDeleteFolderClick}
                onMoveFolder={handleMoveFolder}
                onCreateWebFunction={handleCreateWebFunction}
                onSelectWebFunction={handleSelectWebFunctionCallback}
                selectedWebFunctionId={selectedWebFunction?.id || null}
                onSelectWebSubFunction={handleSelectWebSubFunctionCallback}
                selectedWebSubFunctionId={selectedSubFunctionId}
              />
            </div>
          </div>

          {/* 中间主区域 */}
          <div className="flex-1 flex flex-col min-h-0 bg-background">
            {/* 工具栏 */}
            <div className="flex items-center justify-between border-b px-4 py-3 bg-muted/20">
              <div className="flex items-center gap-2">
                <Layers className="h-5 w-5 text-blue-500" />
                <div>
                  <h2 className="text-lg font-semibold">
                    {selectedFolderName || t("webTests.allFunctions")}
                  </h2>
                  <p className="text-xs text-muted-foreground">
                    {(() => {
                      const totalSubFunctions = webFunctions.reduce((sum, wf) => sum + (wf.total_sub_functions || 0), 0);
                      return `${totalSubFunctions}${t("webTests.subFunctionsCount")}`;
                    })()}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setAiGenerateDialogOpen(true)}
                  className="bg-gradient-to-r from-blue-500 to-cyan-500 hover:from-blue-600 hover:to-cyan-600 text-white border-0 shadow-md hover:shadow-lg transition-all"
                >
                  <Sparkles className="mr-2 h-4 w-4" />
                  AI 生成
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setCreateFunctionDialogOpen(true)}
                  className="bg-background hover:bg-accent"
                >
                  <Plus className="mr-2 h-4 w-4" />
                  新建
                </Button>
                <Button
                  size="sm"
                  onClick={() => setAiChatOpen(true)}
                  className="bg-gradient-to-r from-blue-500 to-cyan-500 hover:from-blue-600 hover:to-cyan-600 text-white border-0 shadow-md hover:shadow-lg transition-all"
                >
                  <MessageSquare className="mr-2 h-4 w-4" />
                  AI 助手
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setMcpConfigOpen(true)}
                  title="配置外部 MCP Server"
                >
                  MCP
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => loadWebFunctions()}
                >
                  <RefreshCw className="h-4 w-4" />
                </Button>
              </div>
            </div>

            {/* 模式内容区域 */}
            <div className="flex-1 overflow-hidden relative flex flex-col">
              {/* Web函数列表 - 固定高度，内部滚动 */}
              <div className="shrink-0 flex flex-col border-b" style={{ height: '50%' }}>
                <div className="flex-1 overflow-y-auto">
                  <WebFunctionList
                    webFunctions={webFunctions}
                    loading={loading}
                    selectedIds={selectedIds}
                    onSelectIds={setSelectedIds}
                    onBulkDelete={handleBulkDelete}
                    onBulkRun={handleBulkRun}
                    onDeleteWebFunction={handleDeleteWebFunction}
                    onViewWebFunction={handleViewWebFunction}
                    folderName={selectedFolderName}
                    pagination={{
                      page,
                      pageSize,
                      total,
                      onPageChange: setPage,
                    }}
                  />
                </div>
              </div>

              {/* 测试成果物 - 占据剩余空间 */}
              <div data-testid="test-artifacts-section" className="flex-1 min-h-0 overflow-y-auto bg-gradient-to-b from-muted/20 to-background p-6">
                <div className="max-w-7xl mx-auto">
                  {/* 当前选中的子功能信息卡片 */}
                  {currentSubFunction && (
                    <div data-testid="web-test-loop-context" className="mb-6 rounded-xl border-2 bg-gradient-to-r from-blue-50 to-purple-50 dark:from-blue-950/30 dark:to-purple-950/30 p-4 shadow-sm">
                      <div className="flex items-start justify-between">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-3 mb-2">
                            <FileCode className="h-5 w-5 text-blue-500 shrink-0" />
                            <h3 className="text-lg font-semibold truncate">
                              {currentSubFunction.display_name}
                            </h3>
                            <Badge variant="outline" className="shrink-0 text-xs">
                              {currentSubFunction.identifier}
                            </Badge>
                            <Badge
                              className={cn(
                                "shrink-0 text-xs",
                                currentSubFunction.test_type === "functional" ? "bg-blue-500 text-white" :
                                currentSubFunction.test_type === "validation" ? "bg-green-500 text-white" :
                                currentSubFunction.test_type === "ui" ? "bg-purple-500 text-white" :
                                "bg-gray-500 text-white"
                              )}
                            >
                              {currentSubFunction.test_type}
                            </Badge>
                            <Badge
                              className={cn(
                                "shrink-0 text-xs",
                                currentSubFunction.priority === "critical" ? "bg-red-500 text-white" :
                                currentSubFunction.priority === "high" ? "bg-orange-500 text-white" :
                                currentSubFunction.priority === "medium" ? "bg-yellow-500 text-white" :
                                "bg-gray-500 text-white"
                              )}
                            >
                              {currentSubFunction.priority}
                            </Badge>
                          </div>
                          {currentSubFunction.description && (
                            <p className="text-sm text-muted-foreground line-clamp-2 ml-8">
                              {currentSubFunction.description}
                            </p>
                          )}
                          <div className="mt-3 ml-8 flex flex-wrap items-center gap-2" data-testid="web-test-loop-status">
                            <Badge variant="secondary" className="gap-1 text-xs">
                              <Workflow className="h-3 w-3" />
                              {projectId}
                            </Badge>
                            <Badge variant="outline" className="gap-1 text-xs">
                              <FileCode className="h-3 w-3" />
                              用例 {actualTestCasesCounts[currentSubFunction.id] ?? currentSubFunction.total_test_cases ?? 0}
                            </Badge>
                            <Badge variant="outline" className="gap-1 text-xs">
                              <Play className="h-3 w-3" />
                              执行 {currentSubFunction.total_test_runs ?? 0}
                            </Badge>
                            <Badge
                              variant={currentSubFunction.last_run_status === "failed" ? "destructive" : "outline"}
                              className={cn(
                                "gap-1 text-xs",
                                currentSubFunction.last_run_status === "passed" && "border-green-200 bg-green-50 text-green-700"
                              )}
                            >
                              <RefreshCw className="h-3 w-3" />
                              {formatRunStatus(currentSubFunction.last_run_status)}
                            </Badge>
                          </div>
                        </div>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setSubFunctionDrawerOpen(true)}
                          className="shrink-0 gap-2"
                        >
                          <span className="text-xs">查看子功能列表</span>
                          <ChevronRight className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  )}

                  {/* === P0: 执行摘要块 === */}
                  {selectedSubFunctionId && currentRunState.runId && (
                    <div className="mb-4 rounded-lg border bg-card shadow-sm overflow-hidden">
                      <div className="px-4 py-2 bg-muted/50 border-b flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Shield className="h-4 w-4 text-blue-500" />
                          <span className="text-sm font-medium">执行上下文</span>
                        </div>
                        <div className="flex items-center gap-2">
                          {currentRunState.preflightStatus === "passed" ? (
                            <Badge variant="outline" className="gap-1 text-xs bg-green-50 text-green-700 border-green-200">
                              <ShieldCheck className="h-3 w-3" />
                              预检通过
                            </Badge>
                          ) : currentRunState.preflightStatus === "blocked" ? (
                            <Badge variant="outline" className="gap-1 text-xs bg-red-50 text-red-700 border-red-200">
                              <ShieldAlert className="h-3 w-3" />
                              已阻断
                            </Badge>
                          ) : (
                            <Badge variant="outline" className="gap-1 text-xs text-muted-foreground">
                              <Shield className="h-3 w-3" />
                              未预检
                            </Badge>
                          )}
                          <Badge
                            variant="outline"
                            className={cn(
                              "gap-1 text-xs",
                              currentRunState.mode === "live"
                                ? "bg-orange-50 text-orange-700 border-orange-200"
                                : "bg-blue-50 text-blue-700 border-blue-200"
                            )}
                          >
                            {currentRunState.mode === "live" ? "🔴 Live" : "🛡️ Safe"}
                          </Badge>
                        </div>
                      </div>
                      <div className="px-4 py-3 grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                        <div>
                          <span className="text-muted-foreground block mb-1">run_id</span>
                          <code className="bg-muted px-1.5 py-0.5 rounded font-mono">{currentRunState.runId}</code>
                        </div>
                        <div>
                          <span className="text-muted-foreground block mb-1">产物版本</span>
                          <span className="font-medium">{currentRunState.artifactVersion || "-"}</span>
                        </div>
                        <div>
                          <span className="text-muted-foreground block mb-1">expected SHA256</span>
                          <code className="bg-muted px-1.5 py-0.5 rounded font-mono text-[10px]">
                            {currentRunState.preflightResult?.expected_sha256
                              ? `${currentRunState.preflightResult.expected_sha256.slice(0, 16)}...`
                              : "-"}
                          </code>
                        </div>
                        <div>
                          <span className="text-muted-foreground block mb-1">actual SHA256</span>
                          <code className="bg-muted px-1.5 py-0.5 rounded font-mono text-[10px]">
                            {currentRunState.preflightResult?.actual_sha256
                              ? `${currentRunState.preflightResult.actual_sha256.slice(0, 16)}...`
                              : "-"}
                          </code>
                        </div>
                      </div>
                      {/* 阻断原因展示 */}
                      {currentRunState.preflightStatus === "blocked" && currentRunState.preflightResult && (
                        <div className="px-4 py-2 bg-red-50 border-t border-red-100">
                          <div className="flex items-start gap-2">
                            <ShieldAlert className="h-4 w-4 text-red-500 shrink-0 mt-0.5" />
                            <div>
                              <p className="text-sm font-medium text-red-700">
                                阻断原因: {currentRunState.preflightResult.reason_code}
                              </p>
                              <p className="text-xs text-red-600 mt-0.5">
                                {currentRunState.preflightResult.message}
                              </p>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  <div className="flex items-center justify-between mb-6">
                    <div>
                      <h2 className="text-xl font-bold flex items-center gap-2">
                        <Zap className="h-5 w-5 text-purple-500" />
                        {t("webTests.testArtifacts")}
                      </h2>
                      <p className="text-sm text-muted-foreground mt-1">
                        {selectedSubFunctionId
                          ? t("webTests.testArtifactsDesc")
                          : webSubFunctions.length > 0
                          ? "请在抽屉中选择一个子功能以查看其测试成果物"
                          : t("webTests.noFunctionData")
                        }
                      </p>
                    </div>
                  </div>

                  {webSubFunctions.length === 0 && (
                    <div className="text-center py-12 border-2 border-dashed rounded-lg bg-muted/10">
                      <FileCode className="h-16 w-16 text-muted-foreground mx-auto mb-4" />
                      <p className="text-lg font-medium mb-2">{t("webTests.noFunctionData")}</p>
                      <p className="text-sm text-muted-foreground mb-4">
                        {t("webTests.selectFolderOrImportWeb")}
                      </p>
                    </div>
                  )}

                  {webSubFunctions.length > 0 && !selectedSubFunctionId && (
                    <div className="text-center py-12 border-2 border-dashed rounded-xl bg-gradient-to-br from-blue-50/50 to-purple-50/50 dark:from-blue-950/20 dark:to-purple-950/20">
                      <FileCode className="h-16 w-16 text-muted-foreground mx-auto mb-4" />
                      <p className="text-lg font-semibold mb-2">请在抽屉中选择一个子功能</p>
                      <p className="text-sm text-muted-foreground mb-4">
                        点击右上角的按钮打开子功能列表抽屉，选择一个子功能后即可查看其测试成果物
                      </p>
                      <Button
                        variant="outline"
                        onClick={() => setSubFunctionDrawerOpen(true)}
                        className="gap-2"
                      >
                        <FileCode className="h-4 w-4" />
                        打开子功能列表
                      </Button>
                    </div>
                  )}

                  {selectedSubFunctionId && (
                    <EnhancedTestArtifactsPanel
                      key={`artifacts-${selectedSubFunctionId}`}
                      subFunctionId={selectedSubFunctionId}
                      projectId={projectId}
                      onRefresh={loadWebFunctions}
                      onTestCasesCountChange={handleTestCasesCountChange}
                      onExecuteScript={handleExecuteScript}
                      refreshTrigger={artifactsRefreshTrigger}
                      runState={currentRunState}
                      onRunStateChange={setCurrentRunState}
                    />
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* 右侧悬浮 AI 聊天面板 */}
          {assistant && (
            <div
              className={cn(
                "absolute right-0 top-0 z-50 h-full w-[1200px] bg-background transition-transform duration-300 ease-in-out",
                aiChatOpen ? "translate-x-0 border-l shadow-2xl" : "translate-x-full"
              )}
            >
              <ClientProvider
                deploymentUrl="/lg"
                apiKey={process.env.NEXT_PUBLIC_LANGSMITH_API_KEY || ""}
              >
                <AIChatContainer
                  key={aiChatKey}
                  assistant={assistant}
                  initialPrompt={aiChatInitialPrompt}
                  onClose={handleAIChatClose}
                  createNewThread={shouldCreateNewThread}
                  onCreateNewThreadConsumed={() => setShouldCreateNewThread(false)}
                  onTestCreated={handleTestCreated}
                />
              </ClientProvider>
            </div>
          )}

          {/* 右侧悬浮详情侧边栏 */}
          {testMode === "function" && showSubFunctionSidebar && selectedSubFunctionId && (
            <div
              className={cn(
                "absolute right-0 top-0 z-30 h-full w-[600px] bg-background transition-transform duration-300 ease-in-out border-l shadow-xl",
                showSubFunctionSidebar ? "translate-x-0" : "translate-x-full"
              )}
            >
              <WebSubFunctionSidebar
                key={`sidebar-${selectedSubFunctionId}-${sidebarRefreshTrigger}`}
                subFunctionId={selectedSubFunctionId}
                projectId={projectId}
                onClose={handleSidebarClose}
                onGenerateTest={handleSidebarGenerateTest}
                onOpenAIChat={handleSidebarOpenAIChat}
                onRefresh={handleSidebarRefresh}
              />
            </div>
          )}
        </div>
      </div>

      {/* 各种对话框 */}
        {/* 创建/编辑函数对话框 */}
        <CreateWebFunctionDialog
          open={createFunctionDialogOpen}
          onOpenChange={setCreateFunctionDialogOpen}
          projectId={projectId}
          folderId={createFunctionFolderId !== null ? createFunctionFolderId : selectedFolderId}
          editingFunction={editingWebFunction}
          onSuccess={handleFunctionCreated}
        />

        {/* AI 生成对话框 */}
        <AIGenerateDialog
          open={aiGenerateDialogOpen}
          onOpenChange={setAiGenerateDialogOpen}
          onGenerate={handleAIGenerate}
          folderName={selectedFolderName}
        />

        {/* 文件夹对话框 */}
        <Dialog open={folderDialogOpen} onOpenChange={setFolderDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>
                {editingFolder ? t("webTests.editFolder") : t("webTests.createFolder")}
              </DialogTitle>
              <DialogDescription>
                {editingFolder ? t("webTests.editFolderInfo") : t("webTests.createNewFolder")}
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="folder-name">{t("webTests.folderNameLabel")}</Label>
                <Input
                  id="folder-name"
                  value={folderFormData.name}
                  onChange={(e) =>
                    setFolderFormData({ ...folderFormData, name: e.target.value })
                  }
                  placeholder={t("webTests.enterFolderName")}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="folder-description">{t("webTests.descriptionLabel")}</Label>
                <Textarea
                  id="folder-description"
                  value={folderFormData.description}
                  onChange={(e) =>
                    setFolderFormData({
                      ...folderFormData,
                      description: e.target.value,
                    })
                  }
                  placeholder={t("webTests.enterDescription")}
                  rows={3}
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setFolderDialogOpen(false)}>
                {t("common.cancel")}
              </Button>
              <Button onClick={handleSubmitFolder} disabled={submitting}>
                {submitting ? t("common.saving") : editingFolder ? t("common.save") : t("common.create")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* 删除文件夹确认对话框 */}
        <Dialog open={deleteFolderDialogOpen} onOpenChange={setDeleteFolderDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("webTests.deleteFolderTitle")}</DialogTitle>
              <DialogDescription>
                {t("webTests.deleteFolderMessage", { name: deletingFolder?.name || "" })}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={handleCancelDeleteFolder}
              >
                {t("common.cancel")}
              </Button>
              <Button
                variant="destructive"
                onClick={handleDeleteFolder}
              >
                {t("common.delete")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* 删除 Web 功能确认对话框 */}
        <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("webTests.deleteFunctionTitle")}</DialogTitle>
              <DialogDescription>
                {t("webTests.deleteFunctionMessage", { name: deletingWebFunction?.display_name || "" })}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={handleCancelDeleteFunction}
              >
                {t("common.cancel")}
              </Button>
              <Button
                variant="destructive"
                onClick={confirmDeleteWebFunction}
              >
                {t("common.delete")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* 子功能列表抽屉 */}
        <Drawer open={subFunctionDrawerOpen} onOpenChange={handleDrawerClose}>
          <DrawerContent direction="right" showCloseButton={false} className="h-full w-[560px] border-l rounded-none">
            <DrawerHeader className="flex flex-row items-center justify-between space-y-0 pb-4 border-b">
              <div className="flex items-center gap-2">
                <FileCode className="h-5 w-5 text-blue-500" />
                <DrawerTitleComp>子功能列表</DrawerTitleComp>
                <span className="text-sm text-muted-foreground">
                  ({selectedWebFunction ? `${selectedWebFunction.display_name} - ` : ""}{webSubFunctions.length})
                </span>
              </div>
              <DrawerClose asChild>
                <Button variant="ghost" size="icon" className="h-8 w-8">
                  <X className="h-4 w-4" />
                  <span className="sr-only">关闭</span>
                </Button>
              </DrawerClose>
            </DrawerHeader>
            <div className="flex-1 overflow-y-auto p-0">
              <WebSubFunctionList
                subFunctions={webSubFunctions}
                loading={loading}
                selectedId={selectedSubFunctionId}
                onSelectSubFunction={handleSelectSubFunctionFromDrawer}
                onViewDetail={handleViewSubFunctionDetail}
                onViewTestCases={handleViewSubFunctionTestCases}
                onRunTest={handleRunSubFunctionTest}
                onDelete={handleDeleteSubFunction}
                pagination={{
                  page: drawerPage,
                  pageSize: drawerPageSize,
                  total: webSubFunctions.length,
                  onPageChange: setDrawerPage,
                }}
                showHeader={false}
              />
            </div>
          </DrawerContent>
        </Drawer>

        {/* MCP Server 配置对话框 */}
        <Dialog open={mcpConfigOpen} onOpenChange={setMcpConfigOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>外部 MCP Server 配置</DialogTitle>
              <DialogDescription>
                配置额外的 MCP Server，AI 助手将在执行时连接这些服务。留空则不使用外部 MCP。
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-2 py-4">
              <Label htmlFor="mcp-servers">MCP Server 配置 (JSON)</Label>
              <Textarea
                id="mcp-servers"
                value={extraMcpServers}
                onChange={(e) => setExtraMcpServers(e.target.value)}
                placeholder={`{\n  "my_tool": {\n    "transport": "sse",\n    "url": "http://localhost:8080/sse"\n  }\n}`}
                rows={8}
                className="font-mono text-sm"
              />
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={handleMcpConfigClose}>关闭</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </MainLayout>
    );
}
// FIXME  My80OmFIVnBZMlhscm9ua3VMazZPSFEwV0E9PTo3MWU1Nzg2OA==
