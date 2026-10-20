// FIXME  MC80OmFIVnBZMlhscm9ua3VMazZXbkZ0ZWc9PTo0NTM5Njg1Ng==

"use client";
// eslint-disable  MS80OmFIVnBZMlhscm9ua3VMazZXbkZ0ZWc9PTo0NTM5Njg1Ng==

import * as React from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import {
  Zap,
  FileCode,
  FileSpreadsheet,
  Sparkles,
  MessageSquare,
  RefreshCw,
  Workflow,
  Layers,
  Plus,
  ChevronRight,
  ChevronDown,
  Play,
  CheckCircle2,
  Eye,
  Loader2,
  AlertCircle,
} from "lucide-react";
import { MainLayout } from "@/components/layout";
import { useLanguage } from "@/providers/LanguageProvider";
import { APITestList, APITestDialog } from "@/components/api-tests";
import { APIEndpointSidebar } from "@/components/api-tests/api-endpoint-sidebar";
import { APIParseDialog } from "@/components/api-tests/api-parse-dialog";
import { ExcelParsePreview } from "@/components/api-tests/excel-parse-preview";
import { UnifiedImportDialog } from "@/components/api-tests/unified-import-dialog";
import { APIEndpointList } from "@/components/api-tests/APIEndpointList";
import { EnhancedTestArtifactsPanel } from "@/components/api-tests/test-artifacts-panel-enhanced";
import { EndpointEnvConfig } from "@/components/api-tests/endpoint-env-config";
import { CreateEndpointDialog } from "@/components/api-tests/create-endpoint-dialog";
import { APIFolderTree } from "@/components/api-tests/folder-tree";
import { APILayerPanel } from "@/components/api-tests/api-layer-panel";
import { ApiIntentPanel } from "@/components/api-tests/api-intent-panel";
import type { APIFolderTreeRef } from "@/components/api-tests/folder-tree";
import { ScenarioListPanel } from "@/components/scenario-tests/scenario-list-panel";
import { ScenarioOrchestrationView } from "@/components/scenario-tests/scenario-orchestration-view";
import { ScenarioExecutionMonitor } from "@/components/scenario-tests/scenario-execution-monitor";
import { ScenarioCreateDialog } from "@/components/scenario-tests/scenario-create-dialog";
import { AIGenerateScenarioDialog } from "@/components/scenario-tests/ai-generate-scenario-dialog";
import { ScenarioDetailSidebar } from "@/components/scenario-tests/scenario-detail-sidebar";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AIChatContainer } from "@/components/langgraph/AIChatContainer";
import { ClientProvider } from "@/providers/ClientProvider";
import { Assistant } from "@langchain/langgraph-sdk";
import { cn } from "@/lib/utils";
import {
  getFolderAPITests,
  createAPITest,
  updateAPITest,
  deleteAPITest,
  runAPITest,
} from "@/lib/api/api-tests";
import {
  listAPIEndpoints,
  type APIEndpoint,
} from "@/lib/api/api-endpoints";
import {
  createFolder,
  updateFolder,
  deleteFolder,
} from "@/lib/api/folders";
import { listScenarios, executeScenario } from "@/lib/api/scenarios";
import type {
  FolderInfo,
  FolderCreate,
} from "@/lib/api/types";
import type { APITest, CreateAPITestRequest } from "@/lib/api/api-tests";
import type { Scenario } from "@/types/scenario";

type TestMode = "endpoint" | "scenario" | "layers";
type ScenarioViewMode = "orchestrate" | "monitor";

export default function APITestsPage() {
  const params = useParams();
  const projectId = params.projectId as string;
  const { t } = useLanguage();

  // 文件夹树 ref
  const folderTreeRef = React.useRef<APIFolderTreeRef>(null);

  // 模式切换状态
  const [testMode, setTestMode] = React.useState<TestMode>("endpoint");

  // 接口测试相关状态
  const [apiTests, setApiTests] = React.useState<APITest[]>([]);
  const [apiEndpoints, setApiEndpoints] = React.useState<APIEndpoint[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [actualTestCasesCounts, setActualTestCasesCounts] = React.useState<Record<string, number>>({});
  const [selectedFolderId, setSelectedFolderId] = React.useState<string | null>(null);
  const [selectedIds, setSelectedIds] = React.useState<Set<string>>(new Set());
  const [selectedEndpointId, setSelectedEndpointId] = React.useState<string | null>(null);
  const [showEndpointSidebar, setShowEndpointSidebar] = React.useState(false);
  const [artifactsRefreshTrigger, setArtifactsRefreshTrigger] = React.useState(0);

  // 环境与执行配置对话框状态
  const [envConfigOpen, setEnvConfigOpen] = React.useState(false);
  const [envConfigEndpoint, setEnvConfigEndpoint] = React.useState<{ id: string; name: string } | null>(null);
  // 环境是否已通过配置（执行后视为就绪，用于流程条 env/execute 步骤）
  const [envConfigured, setEnvConfigured] = React.useState(false);

  // AI 面板打开状态/失败（用于"AI 不可用"反馈与重试）
  const [aiOpening, setAiOpening] = React.useState(false);
  const [aiError, setAiError] = React.useState(false);

  // 当前选中的接口对象（用于流程指引显示名称）
  const selectedEndpoint = React.useMemo(
    () => apiEndpoints.find((ep) => ep.id === selectedEndpointId) || null,
    [apiEndpoints, selectedEndpointId]
  );

  // 流程步骤推导：select → detail → generated → env → execute
  // env/execute 依据环境配置与执行状态（envConfigured 在环境配置提交或执行后置位）
  const flowStep = React.useMemo<"select" | "detail" | "generated" | "env" | "execute">(() => {
    if (!selectedEndpointId) return "select";
    const caseCount =
      (selectedEndpointId && actualTestCasesCounts[selectedEndpointId]) ||
      selectedEndpoint?.total_test_cases ||
      0;
    if (caseCount === 0) return "detail";
    if (!envConfigured) return "generated";
    // 有成果物且环境已配置：最近执行为 running 则 execute，否则停在 env 待执行
    if (selectedEndpoint?.last_run_status === "running") return "execute";
    return "env";
  }, [selectedEndpointId, actualTestCasesCounts, selectedEndpoint, envConfigured]);

  // 场景测试相关状态
  const [scenarios, setScenarios] = React.useState<Scenario[]>([]);
  const [selectedScenarioId, setSelectedScenarioId] = React.useState<string | null>(null);
  const [scenarioViewMode, setScenarioViewMode] = React.useState<ScenarioViewMode>("orchestrate");
  const [showScenarioSidebar, setShowScenarioSidebar] = React.useState(false);
  const [scenarioDialogOpen, setScenarioDialogOpen] = React.useState(false);
  const [scenarioRefreshTrigger, setScenarioRefreshTrigger] = React.useState(0);

  // 分页和筛选
  const [page, setPage] = React.useState(1);
  const [pageSize, setPageSize] = React.useState(20);
  const [total, setTotal] = React.useState(0);
  const [searchQuery, setSearchQuery] = React.useState("");
  const [formatFilter, setFormatFilter] = React.useState("");

  // 对话框状态
  const [apiTestDialogOpen, setApiTestDialogOpen] = React.useState(false);
  const [editingAPITest, setEditingAPITest] = React.useState<APITest | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = React.useState(false);
  const [deletingAPITest, setDeletingAPITest] = React.useState<APITest | null>(null);
  const [folderDialogOpen, setFolderDialogOpen] = React.useState(false);
  const [editingFolder, setEditingFolder] = React.useState<FolderInfo | null>(null);
  const [folderParentId, setFolderParentId] = React.useState<string | undefined>();
  const [folderFormData, setFolderFormData] = React.useState<FolderCreate>({
    name: "",
    description: "",
  });
  const [deleteFolderDialogOpen, setDeleteFolderDialogOpen] = React.useState(false);
  const [deletingFolder, setDeletingFolder] = React.useState<FolderInfo | null>(null);
  const [createAPITestFolder, setCreateAPITestFolder] = React.useState<FolderInfo | null>(null);
  const [selectedFolderName, setSelectedFolderName] = React.useState<string | undefined>();
  const [apiParseDialogOpen, setApiParseDialogOpen] = React.useState(false);
  const [excelParseDialogOpen, setExcelParseDialogOpen] = React.useState(false);
  const [unifiedImportOpen, setUnifiedImportOpen] = React.useState(false);
  const [aiGenerateScenarioDialogOpen, setAiGenerateScenarioDialogOpen] = React.useState(false);
  const [createEndpointDialogOpen, setCreateEndpointDialogOpen] = React.useState(false);

  // 意图面板状态
  const [intentPanelOpen, setIntentPanelOpen] = React.useState(false);

  // AI 聊天状态
  const [aiChatOpen, setAiChatOpen] = React.useState(false);
  const [aiChatInitialPrompt, setAiChatInitialPrompt] = React.useState<string>("");
  const [aiChatKey, setAiChatKey] = React.useState<number>(0);
  const [assistant, setAssistant] = React.useState<Assistant | null>(null);

  // 初始化 Assistant
  React.useEffect(() => {
    const initAssistant = async () => {
      try {
        // 本地 LangGraph 使用稳定 graph_id；内存模式重启后 UUID assistant 不可用。
        const assistantId = "api_agent";
        const mockAssistant: Assistant = {
          assistant_id: assistantId,
          graph_id: "api_agent",
          config: {
            configurable: {
              project_identifier: projectId,
              folder_id: selectedFolderId || "",
              template_type: testMode === "endpoint" ? "api_test" : "scenario_test",
            }
          },
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          metadata: {},
          version: 1,
          name: testMode === "endpoint" ? t("apiTests.apiTestAssistant") : t("apiTests.scenarioTestAssistant"),
          context: {},
        };
        setAssistant(mockAssistant);
      } catch (error) {
        console.error("Failed to initialize assistant:", error);
      }
    };
    initAssistant();
  }, [projectId, selectedFolderId, testMode]);

  // 加载 API 测试数据
  const loadAPITests = React.useCallback(async () => {
    if (testMode !== "endpoint") return;

    try {
      setLoading(true);

      // 加载端点列表（无论是根目录还是子文件夹，都加载对应的 endpoints）
      const endpoints = await listAPIEndpoints(projectId, {
        folder_id: selectedFolderId || undefined,  // undefined 表示加载所有根目录的端点
      });
      setApiEndpoints(endpoints);

      const params = {
        page,
        page_size: pageSize,
        search: searchQuery || undefined,
        script_format: formatFilter && formatFilter !== "all" ? formatFilter : undefined,
      };

      let response;
      if (selectedFolderId) {
        response = await getFolderAPITests(projectId, selectedFolderId, params);
      } else {
        const { listAPITests } = await import("@/lib/api/api-tests");
        response = await listAPITests(projectId, params);
      }

      let items, total;
      if ((response as any).data) {
        const data = (response as any).data;
        items = data.items || data.data || [];
        total = data.total || 0;
      } else {
        items = (response as any).items || (response as any).data || [];
        total = (response as any).total || 0;
      }

      setApiTests(items);
      setTotal(total);
    } catch (error) {
      console.error("Failed to load API tests:", error);
      toast.error(t("apiTests.loadAPITestsFailed"));
    } finally {
      setLoading(false);
    }
  }, [projectId, selectedFolderId, page, pageSize, searchQuery, formatFilter, testMode]);

  // 加载场景测试数据
  const loadScenarios = React.useCallback(async () => {
    if (testMode !== "scenario") return;

    try {
      setLoading(true);
      const result = await listScenarios(projectId, { page: 1, page_size: 100 });
      setScenarios(result.items);
    } catch (error) {
      console.error("Failed to load scenarios:", error);
      toast.error(t("apiTests.loadScenariosFailed"));
    } finally {
      setLoading(false);
    }
  }, [projectId, testMode]);

  // 根据模式加载数据
  React.useEffect(() => {
    if (projectId) {
      if (testMode === "endpoint") {
        loadAPITests();
      } else {
        loadScenarios();
      }
    }
  }, [projectId, testMode, loadAPITests, loadScenarios]);

  const handleSelectFolder = (folder: FolderInfo | null) => {
    setSelectedFolderId(folder?.id || null);
    setSelectedFolderName(folder?.name);
    setPage(1);
    setSelectedIds(new Set());
  };

  // 处理创建接口
  const handleCreateAPIEndpoint = (folderId?: string | null) => {
    setCreateEndpointDialogOpen(true);
  };

  // 处理接口创建成功
  const handleEndpointCreated = () => {
    loadAPITests();
    folderTreeRef.current?.refresh();
  };

  const handleTestCasesCountChange = async (count: number, endpointId?: string) => {
    const targetEndpointId = endpointId || selectedEndpointId || apiEndpoints[0]?.id;
    if (!targetEndpointId) return;

    setActualTestCasesCounts(prev => ({
      ...prev,
      [targetEndpointId]: count,
    }));

    setApiEndpoints(prev => prev.map(ep => {
      if (ep.id === targetEndpointId) {
        return { ...ep, total_test_cases: count };
      }
      return ep;
    }));
  };

  // 统一回调：打开接口详情侧栏
  const openEndpointDetail = React.useCallback((endpointId: string) => {
    setSelectedEndpointId(endpointId);
    setShowEndpointSidebar(true);
  }, []);

  // 统一回调：打开 AI 生成（带接口 ID，瞬时反馈）
  // target 为空表示未指定接口：尝试用当前选中/首个接口，都没有则提示选择
  // 返回 Promise<boolean>：true=面板已打开，false=打开失败（供"重试"复用）
  const openAIGenerateFor = React.useCallback(async (target?: string): Promise<boolean> => {
    let endpointId = target;
    if (!endpointId) {
      endpointId = selectedEndpointId || apiEndpoints[0]?.id || "";
    }
    if (!endpointId) {
      toast.warning(t("apiTests.selectEndpointFirst"));
      return false;
    }
    setSelectedEndpointId(endpointId);
    setAiError(false);
    setAiOpening(true);
    toast.info(t("apiTests.openingAIPanel"));
    setAiChatInitialPrompt(t("apiTests.generateTestsForEndpoint", { id: endpointId }));
    setAiChatKey((prev) => prev + 1);
    setShowEndpointSidebar(false);
    setAiChatOpen(true);
    // assistant 为前端 mock，同步置位；真实连接失败由 AIChatContainer 内部展示
    setAiOpening(false);
    return true;
  }, [t, selectedEndpointId, apiEndpoints]);

  // AI 打开失败重试：重新走打开流程
  const handleAiRetry = React.useCallback(() => {
    setAiError(false);
    openAIGenerateFor();
  }, [openAIGenerateFor]);

  // 统一回调：打开环境与执行配置（重新打开视为未就绪，待配置/执行后再置位）
  const openEnvConfig = React.useCallback((endpoint: APIEndpoint) => {
    setSelectedEndpointId(endpoint.id);
    setEnvConfigEndpoint({ id: endpoint.id, name: endpoint.display_name });
    setEnvConfigured(false);
    setEnvConfigOpen(true);
  }, []);

  const handleExecuteScript = (artifactId: string, fileName: string) => {
    const prompt = `请执行 API 测试脚本「${fileName}」。

关键参数：
- script_id: ${artifactId}
- project_identifier: ${projectId}

执行步骤：
1. 调用 download_api_script(script_id="${artifactId}", project_identifier="${projectId}") 下载脚本
2. 使用步骤1返回的 local_path 调用 execute_api_script(local_script_path=local_path, project_identifier="${projectId}")
3. 解析执行结果并报告测试状态`;
    setAiChatInitialPrompt(prompt);
    setAiChatKey(prev => prev + 1);
    setShowEndpointSidebar(false);
    setAiChatOpen(true);
  };

  const handleSelectScenario = (scenarioId: string) => {
    setSelectedScenarioId(scenarioId);
    setShowScenarioSidebar(false); // 不自动打开详情侧边栏，避免遮挡编辑按钮
    setScenarioViewMode("orchestrate");
  };

  const handleCloseScenarioSidebar = () => {
    setShowScenarioSidebar(false);
  };

  const handleOpenScenarioSidebar = () => {
    setShowScenarioSidebar(true);
  };

  const handleScenarioCreated = (scenarioId: string) => {
    setSelectedScenarioId(scenarioId);
    setScenarioDialogOpen(false);
    setScenarioRefreshTrigger(prev => prev + 1);
    toast.success(t("apiTests.scenarioCreated"));
  };

  // 处理提交文件夹
  const handleSubmitFolder = async () => {
    if (!folderFormData.name.trim()) {
      toast.error(t("apiTests.folderNameRequired"));
      return;
    }
    try {
      setSubmitting(true);
      if (editingFolder) {
        // 编辑文件夹 - 使用本地更新
        const response = await updateFolder(projectId, editingFolder.id, folderFormData);
        toast.success(t("apiTests.folderUpdateSuccess"));
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
          folder_type: "api_test",  // 指定为 API 测试类型文件夹
        });
        toast.success(t("apiTests.folderCreateSuccess"));
        setFolderDialogOpen(false);
        // 本地添加文件夹
        if (response.success && response.data) {
          folderTreeRef.current?.addFolderLocally(response.data, folderParentId || null);
        }
      }
    } catch (error) {
      console.error("Failed to save folder:", error);
      toast.error(editingFolder ? t("apiTests.folderUpdateFailed") : t("apiTests.folderCreateFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  // 处理删除文件夹
  const handleDeleteFolder = async () => {
    if (!deletingFolder) return;

    try {
      await deleteFolder(projectId, deletingFolder.id);
      toast.success(t("apiTests.folderDeleteSuccess"));
      setDeleteFolderDialogOpen(false);
      setDeletingFolder(null);

      // 如果删除的是当前选中的文件夹，清空选中状态
      if (selectedFolderId === deletingFolder.id) {
        setSelectedFolderId(null);
        setSelectedFolderName(undefined);
        setApiEndpoints([]);
        setApiTests([]);
      }

      // 刷新文件夹树
      folderTreeRef.current?.refresh();
    } catch (error) {
      console.error("Failed to delete folder:", error);
      toast.error(t("apiTests.folderDeleteFailed"));
    }
  };

  // 流程指引条（核心6步）：选择接口 → 查看详情 → AI生成 → 配置环境 → 执行
  const flowSteps = [
    { key: "select", label: t("apiTests.flowSelectEndpoint") },
    { key: "detail", label: t("apiTests.flowViewDetail") },
    { key: "generated", label: t("apiTests.flowAIGenerate") },
    { key: "env", label: t("apiTests.flowConfigEnv") },
    { key: "execute", label: t("apiTests.flowExecute") },
  ];
  const flowOrder = ["select", "detail", "generated", "env", "execute"];
  const currentFlowIdx = flowOrder.indexOf(flowStep);

  // 动态提示文案（依据当前流程步骤，而非弹窗开关）
  const flowHint = (() => {
    if (!selectedEndpointId) return t("apiTests.flowHintSelect");
    const caseCount =
      (selectedEndpointId && actualTestCasesCounts[selectedEndpointId]) ||
      selectedEndpoint?.total_test_cases ||
      0;
    if (caseCount === 0) return t("apiTests.flowHintSelected", { name: selectedEndpoint?.display_name || "" });
    if (!envConfigured) return t("apiTests.configureEnvFirst");
    return t("apiTests.flowHintEnvReady");
  })();

  return (
    <MainLayout title={t("apiTests.title")}>
      <div className="relative flex h-[calc(100vh-8rem)] rounded-lg border bg-card overflow-hidden">
        <div className={cn("flex h-full w-full min-w-0", testMode === "layers" && "flex-col")}>
          {/* 左侧面板 (320px) */}
          <div className={cn("shrink-0 bg-muted/10 flex flex-col", testMode === "layers" ? "w-full border-b" : "w-80 border-r")}>
            {/* 模式切换 Tab */}
            <div className="p-3 border-b bg-background">
              <Tabs className={testMode === "layers" ? "w-full max-w-sm" : undefined} value={testMode} onValueChange={(v) => {
                setTestMode(v as TestMode);
                setAiChatOpen(false);
                setIntentPanelOpen(false);
              }}>
                <TabsList className="w-full grid grid-cols-3">
                  <TabsTrigger value="endpoint" className="gap-1.5 data-[state=active]:bg-green-50 data-[state=active]:text-green-700 data-[state=active]:border-green-200">
                    <FileCode className="h-4 w-4" />
                    {t("apiTests.endpointTest")}
                  </TabsTrigger>
                  <TabsTrigger value="scenario" className="gap-1.5 data-[state=active]:bg-purple-50 data-[state=active]:text-purple-700 data-[state=active]:border-purple-200">
                    <Workflow className="h-4 w-4" />
                    {t("apiTests.scenarioTest")}
                  </TabsTrigger>
                  <TabsTrigger value="layers" className="gap-1.5 data-[state=active]:bg-blue-50 data-[state=active]:text-blue-700 data-[state=active]:border-blue-200">
                    <Layers className="h-4 w-4" />
                    分层执行
                  </TabsTrigger>
                </TabsList>
              </Tabs>
            </div>

            {/* 文件夹树 / 场景列表 / 分层面板 */}
            {testMode !== "layers" && <div className="flex-1 overflow-hidden">
              {testMode === "endpoint" ? (
                <APIFolderTree
                  ref={folderTreeRef}
                  projectId={projectId}
                  selectedFolderId={selectedFolderId}
                  onSelectFolder={handleSelectFolder}
                  onCreateFolder={(parentId) => {
                    setEditingFolder(null);
                    setFolderParentId(parentId);
                    setFolderFormData({ name: "", description: "" });
                    setFolderDialogOpen(true);
                  }}
                  onEditFolder={(folder) => {
                    setEditingFolder(folder);
                    setFolderParentId(undefined);
                    setFolderFormData({
                      name: folder.name,
                      description: folder.description || "",
                    });
                    setFolderDialogOpen(true);
                  }}
                  onDeleteFolder={(folder) => {
                    setDeletingFolder(folder);
                    setDeleteFolderDialogOpen(true);
                  }}
                  onMoveFolder={(folder) => {
                    // TODO: 实现移动文件夹
                  }}
                  onCreateAPIEndpoint={handleCreateAPIEndpoint}
                  onSelectAPIEndpoint={(endpointId) => {
                    setSelectedEndpointId(endpointId);
                    setShowEndpointSidebar(true);
                  }}
                  selectedAPIEndpointId={selectedEndpointId}
                />
              ) : testMode === "scenario" ? (
                <ScenarioListPanel
                  key={scenarioRefreshTrigger}
                  projectId={projectId}
                  selectedScenarioId={selectedScenarioId}
                  onSelectScenario={handleSelectScenario}
                />
              ) : (
                null
              )}
            </div>}

            {/* 底部操作按钮 */}
            {testMode !== "layers" && <div className="p-3 border-t bg-background">
              {testMode === "endpoint" ? (
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full justify-start gap-2"
                  disabled={aiOpening}
                  onClick={() => openAIGenerateFor()}
                >
                  {aiOpening ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Plus className="h-4 w-4" />
                  )}
                  {t("apiTests.aiGenerateTests")}
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full justify-start gap-2"
                  onClick={() => setScenarioDialogOpen(true)}
                >
                  <Plus className="h-4 w-4" />
                  {t("apiTests.manuallyCreateScenario")}
                </Button>
              )}
            </div>}
          </div>

          {/* 中间主区域 */}
          <div className="flex-1 flex flex-col min-h-0 min-w-0 bg-background">
            {/* 工具栏 */}
            {testMode === "layers" ? (
              <div className="border-b px-4 py-3 bg-muted/20">
                <h2 className="text-lg font-semibold">API 分层执行</h2>
                <p className="text-sm text-muted-foreground">选择测试分层，查看执行条件与各层结果。</p>
              </div>
            ) : <div className="flex items-center justify-between border-b px-4 py-3 bg-muted/20">
              <div className="flex items-center gap-2">
                {testMode === "endpoint" ? (
                  <>
                    <Layers className="h-5 w-5 text-blue-500" />
                    <div>
                      <h2 className="text-lg font-semibold">
                        {selectedFolderName || t("apiTests.allEndpoints")}
                      </h2>
                      <p className="text-xs text-muted-foreground">
                        {apiEndpoints.length}{t("apiTests.endpointsCount")}
                      </p>
                    </div>
                  </>
                ) : (
                  <>
                    <Workflow className="h-5 w-5 text-purple-500" />
                    <div>
                      <h2 className="text-lg font-semibold">
                        {scenarioViewMode === "orchestrate" ? t("apiTests.scenarioOrchestration") : t("apiTests.executionMonitor")}
                      </h2>
                      <p className="text-xs text-muted-foreground">
                        {scenarios.length}{t("apiTests.scenariosCount")}
                      </p>
                    </div>
                  </>
                )}
              </div>

              <div className="flex items-center gap-2">
                {testMode === "endpoint" ? (
                  <>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setIntentPanelOpen(true)}
                      className="gap-2"
                    >
                      <Sparkles className="h-4 w-4" />
                      自然语言测试
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setApiParseDialogOpen(true)}
                    >
                      <FileCode className="mr-2 h-4 w-4" />
                      {t("apiTests.parseAPI")}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setExcelParseDialogOpen(true)}
                    >
                      <FileSpreadsheet className="mr-2 h-4 w-4" />
                      Excel 导入
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => setUnifiedImportOpen(true)}>
                      <FileSpreadsheet className="mr-2 h-4 w-4" /> 联合导入
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => setAiChatOpen(true)}
                      className="bg-gradient-to-r from-blue-500 to-cyan-500 hover:from-blue-600 hover:to-cyan-600 text-white border-0 shadow-md hover:shadow-lg transition-all"
                    >
                      <MessageSquare className="mr-2 h-4 w-4" />
                      {t("apiTests.aiAssistant")}
                    </Button>
                  </>
                ) : (
                  <>
                    <Button
                      size="sm"
                      onClick={() => setAiGenerateScenarioDialogOpen(true)}
                      className="bg-gradient-to-r from-blue-500 to-cyan-500 hover:from-blue-600 hover:to-cyan-600 text-white border-0 shadow-md hover:shadow-lg transition-all gap-2"
                    >
                      <Sparkles className="h-4 w-4" />
                      {t("apiTests.aiGenerateScenarios")}
                    </Button>
                    <div className="flex bg-muted p-1 rounded-lg">
                      <Button
                        variant={scenarioViewMode === "orchestrate" ? "default" : "ghost"}
                        size="sm"
                        className="h-8 px-3"
                        onClick={() => setScenarioViewMode("orchestrate")}
                      >
                        <Layers className="h-4 w-4 mr-1" />
                        {t("apiTests.orchestrate")}
                      </Button>
                      <Button
                        variant={scenarioViewMode === "monitor" ? "default" : "ghost"}
                        size="sm"
                        className="h-8 px-3"
                        onClick={() => setScenarioViewMode("monitor")}
                      >
                        <Play className="h-4 w-4 mr-1" />
                        {t("apiTests.monitor")}
                      </Button>
                    </div>
                    <Button
                      size="sm"
                      onClick={() => setAiChatOpen(true)}
                      className="bg-gradient-to-r from-blue-500 to-cyan-500 hover:from-blue-600 hover:to-cyan-600 text-white border-0 shadow-md hover:shadow-lg transition-all"
                    >
                      <MessageSquare className="mr-2 h-4 w-4" />
                      {t("apiTests.aiAssistant")}
                    </Button>
                  </>
                )}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    if (testMode === "endpoint") {
                      loadAPITests();
                    } else {
                      setScenarioRefreshTrigger(prev => prev + 1);
                    }
                  }}
                >
                  <RefreshCw className="h-4 w-4" />
                </Button>
              </div>
            </div>}

            {/* 模式内容区域 */}
            <div className="flex-1 overflow-hidden relative flex flex-col">
              {/* 顶部流程指引条（仅接口测试模式） */}
              {testMode === "endpoint" && (
                <div className="border-b bg-gradient-to-r from-blue-50/60 via-indigo-50/40 to-purple-50/60 dark:from-blue-950/20 dark:via-indigo-950/10 dark:to-purple-950/20 px-4 py-2.5">
                  <div className="flex items-center justify-between gap-4">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      {flowSteps.map((step, idx) => {
                        const isActive = idx === currentFlowIdx;
                        const isDone = idx < currentFlowIdx;
                        return (
                          <React.Fragment key={step.key}>
                            {idx > 0 && <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/50" />}
                            <div
                              className={cn(
                                "flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium border transition-colors",
                                isActive && "bg-primary text-primary-foreground border-primary shadow-sm",
                                isDone && "bg-green-100 text-green-700 border-green-200 dark:bg-green-950 dark:text-green-300 dark:border-green-800",
                                !isActive && !isDone && "bg-muted/50 text-muted-foreground border-transparent"
                              )}
                            >
                              {isDone && <CheckCircle2 className="h-3 w-3" />}
                              {step.label}
                            </div>
                          </React.Fragment>
                        );
                      })}
                    </div>
                    <p className="text-xs text-muted-foreground shrink-0 hidden sm:block">{flowHint}</p>
                  </div>
                  {/* AI 打开失败提示条：重试 / 返回接口详情，不丢失选中接口 */}
                  {aiError && (
                    <div className="mt-2 flex items-center gap-2 rounded-md border border-red-200 bg-red-50 dark:bg-red-950/20 dark:border-red-800 px-3 py-2">
                      <AlertCircle className="h-4 w-4 text-red-600 shrink-0" />
                      <p className="text-xs text-red-700 dark:text-red-300 flex-1">
                        {t("apiTests.aiUnavailable")}
                      </p>
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        onClick={handleAiRetry}
                      >
                        {t("apiTests.aiRetry")}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        onClick={() => {
                          setAiError(false);
                          if (selectedEndpointId) setShowEndpointSidebar(true);
                        }}
                      >
                        {t("apiTests.flowViewDetail")}
                      </Button>
                    </div>
                  )}
                </div>
              )}

              {testMode === "endpoint" ? (
                <>
                  {/* 接口列表 - 上半部分 */}
                  <div className="max-h-[300px] overflow-y-auto border-b">
                    <APIEndpointList
                      endpoints={apiEndpoints}
                      loading={loading}
                      selectedEndpointId={selectedEndpointId}
                      actualTestCasesCounts={actualTestCasesCounts}
                      onSelectEndpoint={(endpointId) => {
                        setSelectedEndpointId(endpointId);
                        setShowEndpointSidebar(true);
                      }}
                      onSearch={() => {}}
                      folderName={selectedFolderName}
                      onViewDetail={(ep) => openEndpointDetail(ep.id)}
                      onAIGenerate={(ep) => openAIGenerateFor(ep.id)}
                      onEnvConfig={(ep) => openEnvConfig(ep)}
                    />
                  </div>

                  {/* 测试成果物 - 下半部分 */}
                  <div className="flex-1 min-h-0 overflow-y-auto bg-gradient-to-b from-muted/20 to-background p-6">
                    <div className="max-w-7xl mx-auto">
                      <div className="flex items-center justify-between mb-6">
                        <div>
                          <h2 className="text-xl font-bold flex items-center gap-2">
                            <Zap className="h-5 w-5 text-purple-500" />
                            {t("apiTests.testArtifacts")}
                          </h2>
                          <p className="text-sm text-muted-foreground mt-1">
                            {selectedEndpointId
                              ? t("apiTests.testArtifactsDesc")
                              : apiEndpoints.length > 0
                              ? t("apiTests.testArtifactsForEndpoint", { name: apiEndpoints[0].display_name })
                              : t("apiTests.noEndpointData")
                            }
                          </p>
                        </div>
                      </div>

                      {apiEndpoints.length === 0 && (
                        <div className="text-center py-12 border-2 border-dashed rounded-lg bg-muted/10">
                          <FileCode className="h-16 w-16 text-muted-foreground mx-auto mb-4" />
                          <p className="text-lg font-medium mb-2">{t("apiTests.noArtifactsTitle")}</p>
                          <p className="text-sm text-muted-foreground mb-4 max-w-md mx-auto">
                            {t("apiTests.noArtifactsHint")}
                          </p>
                          {apiEndpoints[0] && (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => openEndpointDetail(apiEndpoints[0].id)}
                              className="gap-2"
                            >
                              <Eye className="h-4 w-4" />
                              {t("apiTests.viewEndpointDetail")}
                            </Button>
                          )}
                        </div>
                      )}

                      {apiEndpoints.length > 0 && (
                        <EnhancedTestArtifactsPanel
                          key={`artifacts-${selectedEndpointId || apiEndpoints[0]?.id}`}
                          endpointId={selectedEndpointId || apiEndpoints[0]?.id}
                          projectId={projectId}
                          onRefresh={loadAPITests}
                          onTestCasesCountChange={handleTestCasesCountChange}
                          onExecuteScript={handleExecuteScript}
                          refreshTrigger={artifactsRefreshTrigger}
                          onViewDetail={() => {
                            const targetId = selectedEndpointId || apiEndpoints[0]?.id;
                            if (targetId) openEndpointDetail(targetId);
                          }}
                        />
                      )}
                    </div>
                  </div>
                </>
              ) : testMode === "layers" ? (
                <div className="flex-1 min-h-0 min-w-0 overflow-y-auto p-4 sm:p-6">
                  <div className="mx-auto w-full max-w-5xl">
                    <APILayerPanel projectId={projectId} />
                  </div>
                </div>
              ) : (
                <>
                  {/* Scenario test mode */}
                  {scenarioViewMode === "orchestrate" ? (
                    <div className="flex-1 min-h-0 overflow-y-auto p-6">
                      <ScenarioOrchestrationView
                        projectId={projectId}
                        scenarioId={selectedScenarioId}
                        onScenarioUpdate={() => setScenarioRefreshTrigger(prev => prev + 1)}
                        onCloseSidebar={handleCloseScenarioSidebar}
                        onOpenSidebar={handleOpenScenarioSidebar}
                      />
                    </div>
                  ) : (
                    <div className="flex-1 min-h-0 overflow-y-auto p-6">
                      <ScenarioExecutionMonitor
                        scenarioId={selectedScenarioId}
                      />
                    </div>
                  )}
                </>
              )}
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
                  assistant={assistant}
                  initialPrompt={aiChatInitialPrompt}
                  onClose={() => setAiChatOpen(false)}
                  createNewThread={aiChatKey > 0}
                  onCreateNewThreadConsumed={() => {
                    // 重置 aiChatKey，避免重复触发创建新线程
                    setAiChatKey(0);
                  }}
                  onTestCreated={() => {
                    // 无论是生成测试还是执行脚本，结束后都刷新成果物面板与端点列表，
                    // 让新产生的报告/用例立即可见；不强制关闭聊天框，便于用户查看对话结果。
                    if (testMode === "endpoint") {
                      loadAPITests();
                      folderTreeRef.current?.refresh();
                      setArtifactsRefreshTrigger(prev => prev + 1);
                    } else {
                      setScenarioRefreshTrigger(prev => prev + 1);
                    }
                  }}
                />
              </ClientProvider>
            </div>
          )}

          {/* 右侧悬浮详情侧边栏 */}
          {testMode === "endpoint" && showEndpointSidebar && selectedEndpointId && (
            <div
              className={cn(
                "absolute right-0 top-0 z-30 h-full w-[600px] bg-background transition-transform duration-300 ease-in-out border-l shadow-xl",
                showEndpointSidebar ? "translate-x-0" : "translate-x-full"
              )}
            >
              <APIEndpointSidebar
                endpointId={selectedEndpointId}
                projectId={projectId}
                onClose={() => {
                  setShowEndpointSidebar(false);
                  // 不要 setSelectedEndpointId(null)，保持接口选中状态，
                  // 否则下方成果物面板会回退到 apiEndpoints[0]，选中接口的成果物"消失"
                  loadAPITests();
                }}
                onGenerateTest={() => {
                  setShowEndpointSidebar(false);
                  setAiChatOpen(true);
                  setAiChatInitialPrompt(t("apiTests.generateTestsForEndpoint", { id: selectedEndpointId }));
                }}
                onOpenAIChat={(prompt) => {
                  setAiChatInitialPrompt(prompt);
                  setAiChatKey(prev => prev + 1);
                  setShowEndpointSidebar(false);
                  setAiChatOpen(true);
                }}
                onRefresh={() => {
                  loadAPITests();
                  folderTreeRef.current?.refresh();
                }}
              />
            </div>
          )}

          {testMode === "scenario" && showScenarioSidebar && selectedScenarioId && (
            <div
              className={cn(
                "absolute right-0 top-0 z-30 h-full w-[500px] bg-background transition-transform duration-300 ease-in-out border-l shadow-xl",
                showScenarioSidebar ? "translate-x-0" : "translate-x-full"
              )}
            >
              <ScenarioDetailSidebar
                scenarioId={selectedScenarioId}
                projectId={projectId}
                onClose={() => {
                  setShowScenarioSidebar(false);
                  // 不要 setSelectedScenarioId(null)，保持场景选中状态
                }}
                onScenarioUpdated={() => {
                  setScenarioRefreshTrigger(prev => prev + 1);
                }}
                onOpenAIChat={(prompt) => {
                  // 关闭侧边栏
                  setShowScenarioSidebar(false);
                  // 打开 AI 助手
                  setAiChatInitialPrompt(prompt);
                  setAiChatKey(prev => prev + 1);
                  setAiChatOpen(true);
                }}
              />
            </div>
          )}

          {/* 意图输入面板（右侧悬浮） */}
          {testMode === "endpoint" && (
            <div
              className={cn(
                "absolute right-0 top-0 z-40 h-full w-[600px] bg-background transition-transform duration-300 ease-in-out border-l shadow-xl",
                intentPanelOpen ? "translate-x-0" : "translate-x-full"
              )}
            >
              <div className="flex h-full flex-col">
                <div className="flex items-center justify-between border-b px-4 py-3">
                  <h3 className="font-semibold flex items-center gap-2">
                    <Sparkles className="h-4 w-4 text-primary" />
                    自然语言驱动测试
                  </h3>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setIntentPanelOpen(false)}
                  >
                    关闭
                  </Button>
                </div>
                <div className="flex-1 overflow-y-auto p-4">
                  <ApiIntentPanel
                    projectId={projectId}
                    endpointId={selectedEndpointId}
                    onIntentCompiled={(intent) => {
                      // 打开 AI 聊天并传入编译后的意图
                      setAiChatInitialPrompt(
                        `请根据以下意图配置执行 API 测试：\n\n${JSON.stringify(intent, null, 2)}`
                      );
                      setAiChatKey(prev => prev + 1);
                      setIntentPanelOpen(false);
                      setAiChatOpen(true);
                    }}
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 各种对话框 */}
        {/* API解析对话框 */}
        <APIParseDialog
          open={apiParseDialogOpen}
          onOpenChange={setApiParseDialogOpen}
          projectIdentifier={projectId}
          parentFolderId={selectedFolderId}
          parentFolderName={selectedFolderName}
          onSuccess={() => {
            toast.success(t("apiTests.apiDocParseSuccess"));
            loadAPITests();
            folderTreeRef.current?.refresh();
          }}
        />

        {/* Excel API 文档解析对话框（美心模板） */}
        <ExcelParsePreview
          open={excelParseDialogOpen}
          onOpenChange={setExcelParseDialogOpen}
          projectIdentifier={projectId}
          parentFolderId={selectedFolderId}
          onSuccess={() => {
            toast.success("Excel 接口导入成功");
            loadAPITests();
            folderTreeRef.current?.refresh();
          }}
        />
        <UnifiedImportDialog
          open={unifiedImportOpen}
          onOpenChange={setUnifiedImportOpen}
          projectIdentifier={projectId}
          onSuccess={() => { loadAPITests(); folderTreeRef.current?.refresh(); }}
        />

        {/* 环境与执行配置对话框 */}
        {envConfigEndpoint && (
          <EndpointEnvConfig
            open={envConfigOpen}
            onOpenChange={setEnvConfigOpen}
            projectIdentifier={projectId}
            endpointId={envConfigEndpoint.id}
            endpointName={envConfigEndpoint.name}
            onExecuted={(_runId, status) => {
              // 仅非 blocked（缺配置被后端拦截）才视为环境已就绪；blocked 保持未配置态
              if (status !== "blocked") {
                setEnvConfigured(true);
              }
              loadAPITests();
              setArtifactsRefreshTrigger((prev) => prev + 1);
            }}
          />
        )}

        {/* 创建接口对话框 */}
        <CreateEndpointDialog
          open={createEndpointDialogOpen}
          onOpenChange={setCreateEndpointDialogOpen}
          projectId={projectId}
          folderId={selectedFolderId}
          onSuccess={handleEndpointCreated}
        />

        {/* 场景AI生成对话框 */}
        <AIGenerateScenarioDialog
          open={aiGenerateScenarioDialogOpen}
          onOpenChange={setAiGenerateScenarioDialogOpen}
          projectIdentifier={projectId}
          onOpenChat={(prompt) => {
            setAiChatInitialPrompt(prompt);
            setAiChatKey(prev => prev + 1);
            setAiChatOpen(true);
          }}
        />

        {/* 场景创建对话框 */}
        <ScenarioCreateDialog
          open={scenarioDialogOpen}
          onOpenChange={setScenarioDialogOpen}
          projectId={projectId}
          onSuccess={handleScenarioCreated}
        />

        {/* 文件夹对话框 */}
        <Dialog open={folderDialogOpen} onOpenChange={setFolderDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>
                {editingFolder ? t("apiTests.editFolder") : t("apiTests.createFolder")}
              </DialogTitle>
              <DialogDescription>
                {editingFolder ? t("apiTests.editFolderInfo") : t("apiTests.createNewFolder")}
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="folder-name">{t("apiTests.folderNameLabel")}</Label>
                <Input
                  id="folder-name"
                  value={folderFormData.name}
                  onChange={(e) =>
                    setFolderFormData({ ...folderFormData, name: e.target.value })
                  }
                  placeholder={t("apiTests.enterFolderName")}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="folder-description">{t("apiTests.descriptionLabel")}</Label>
                <Textarea
                  id="folder-description"
                  value={folderFormData.description}
                  onChange={(e) =>
                    setFolderFormData({
                      ...folderFormData,
                      description: e.target.value,
                    })
                  }
                  placeholder={t("apiTests.enterDescription")}
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
              <DialogTitle>{t("apiTests.deleteFolderTitle")}</DialogTitle>
              <DialogDescription>
                {t("apiTests.deleteFolderMessage", { name: deletingFolder?.name || "" })}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => {
                  setDeleteFolderDialogOpen(false);
                  setDeletingFolder(null);
                }}
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
      </MainLayout>
    );
}
