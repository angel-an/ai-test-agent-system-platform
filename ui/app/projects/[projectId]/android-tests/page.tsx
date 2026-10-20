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
  Smartphone,
  Filter,
  X,
  Bug,
  Eye,
  Aperture,
} from "lucide-react";
import { MainLayout } from "@/components/layout";
import { useLanguage } from "@/providers/LanguageProvider";
import {
  TestFolderTree,
  TestFolderTreeRef,
  TestFunctionList,
  TestSubFunctionList,
  TestSubFunctionSidebar,
} from "@/components/shared";
import { AndroidTestArtifactsPanel, AndroidAIGenerateDialog, CreateAndroidAppDialog } from "@/components/android-tests";
import {
  getAndroidSubFunction,
  updateAndroidSubFunction,
  deleteAndroidSubFunction,
} from "@/lib/api/android-apps";
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
  listAndroidApps,
  listAndroidSubFunctions,
  createAndroidApp,
  updateAndroidApp,
  deleteAndroidApp,
  type AndroidApp,
  type AndroidSubFunction,
  type CreateAndroidAppRequest,
} from "@/lib/api/android-apps";
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

export default function AndroidTestsPage() {
  const params = useParams();
  const projectId = params.projectId as string;
  const { t } = useLanguage();

  // 文件夹树 ref
  const folderTreeRef = React.useRef<TestFolderTreeRef>(null);

  // 模式切换状态 - hardcoded to "function" for now
  const [testMode, setTestMode] = React.useState<TestMode>("function");

  // Android App 测试相关状态
  const [androidApps, setAndroidApps] = React.useState<AndroidApp[]>([]);
  const [androidSubFunctions, setAndroidSubFunctions] = React.useState<AndroidSubFunction[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [actualTestCasesCounts, setActualTestCasesCounts] = React.useState<Record<string, number>>({});
  const [selectedFolderId, setSelectedFolderId] = React.useState<string | null>(null);
  const [selectedIds, setSelectedIds] = React.useState<Set<string>>(new Set());
  const [selectedSubFunctionId, setSelectedSubFunctionId] = React.useState<string | null>(null);
  const [showSubFunctionSidebar, setShowSubFunctionSidebar] = React.useState(false);
  const [subFunctionDrawerOpen, setSubFunctionDrawerOpen] = React.useState(false);
  const [selectedAndroidApp, setSelectedAndroidApp] = React.useState<AndroidApp | null>(null);
  const [artifactsRefreshTrigger, setArtifactsRefreshTrigger] = React.useState(0);

  // 设备连接状态
  const [deviceConnected, setDeviceConnected] = React.useState<boolean | null>(null);
  const [deviceInfo, setDeviceInfo] = React.useState<any>(null);

  // 分页和筛选
  const [page, setPage] = React.useState(1);
  const [pageSize, setPageSize] = React.useState(20);
  const [total, setTotal] = React.useState(0);
  const [searchQuery, setSearchQuery] = React.useState("");
  const [formatFilter, setFormatFilter] = React.useState("");

  // 对话框状态
  const [editingAndroidApp, setEditingAndroidApp] = React.useState<AndroidApp | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = React.useState(false);
  const [deletingAndroidApp, setDeletingAndroidApp] = React.useState<AndroidApp | null>(null);
  const [folderDialogOpen, setFolderDialogOpen] = React.useState(false);
  const [editingFolder, setEditingFolder] = React.useState<FolderInfo | null>(null);
  const [folderParentId, setFolderParentId] = React.useState<string | undefined>();
  const [folderFormData, setFolderFormData] = React.useState<FolderCreate>({
    name: "",
    description: "",
  });
  const [deleteFolderDialogOpen, setDeleteFolderDialogOpen] = React.useState(false);
  const [deletingFolder, setDeletingFolder] = React.useState<FolderInfo | null>(null);
  const [createAndroidAppFolder, setCreateAndroidAppFolder] = React.useState<FolderInfo | null>(null);
  const [selectedFolderName, setSelectedFolderName] = React.useState<string | undefined>();
  const [createAppDialogOpen, setCreateAppDialogOpen] = React.useState(false);
  const [aiGenerateDialogOpen, setAiGenerateDialogOpen] = React.useState(false);

  // AI 聊天状态
  const [aiChatOpen, setAiChatOpen] = React.useState(false);
  const [aiChatInitialPrompt, setAiChatInitialPrompt] = React.useState<string>("");
  const [aiChatKey, setAiChatKey] = React.useState<number>(0);
  const [assistant, setAssistant] = React.useState<Assistant | null>(null);
  const [extraMcpServers, setExtraMcpServers] = React.useState<string>("");
  const [mcpConfigOpen, setMcpConfigOpen] = React.useState(false);

  // 初始化 Assistant
  React.useEffect(() => {
    const initAssistant = async () => {
      try {
        // Android agent 暂未部署到 LangGraph，使用 api_agent 作为回退
        // TODO: 部署 android_agent 后更新为正确的 UUID
        const assistantId = "263fd67c-6e42-5938-8fb6-efa21b5262c8"; // api_agent UUID
        let parsedMcpServers: Record<string, unknown> = {};
        if (extraMcpServers.trim()) {
          try { parsedMcpServers = JSON.parse(extraMcpServers); } catch { /* ignore invalid JSON */ }
        }
        const mockAssistant: Assistant = {
          assistant_id: assistantId,
          graph_id: "api_agent", // 回退到 api_agent
          config: {
            configurable: {
              project_identifier: projectId,
              folder_id: selectedFolderId || "",
              template_type: "android_test",
              ...(Object.keys(parsedMcpServers).length > 0 && { extra_mcp_servers: parsedMcpServers }),
            }
          },
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          metadata: {},
          version: 1,
          name: t("androidTests.androidAppAssistant"),
          context: {},
        };
        setAssistant(mockAssistant);
      } catch (error) {
        console.error("Failed to initialize assistant:", error);
      }
    };
    initAssistant();
  }, [projectId, selectedFolderId, extraMcpServers]);

  // 加载 Android App 测试数据
  const loadAndroidApps = React.useCallback(async () => {
    if (testMode !== "function") return;

    try {
      setLoading(true);

      // 先加载 App 列表
      const params = {
        p: page,
        page_size: pageSize,
        search: searchQuery || undefined,
      };

      let response;
      if (selectedFolderId) {
        response = await listAndroidApps(projectId, { ...params, folder_id: selectedFolderId });
      } else {
        response = await listAndroidApps(projectId, params);
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

      setAndroidApps(items);
      setTotal(total);
    } catch (error) {
      console.error("Failed to load android apps:", error);
      toast.error(t("androidTests.loadAndroidAppsFailed"));
    } finally {
      setLoading(false);
    }
  }, [projectId, selectedFolderId, page, pageSize, searchQuery, formatFilter, testMode]);

  // 根据模式加载数据
  React.useEffect(() => {
    if (projectId) {
      loadAndroidApps();
    }
  }, [projectId, testMode, loadAndroidApps]);

  // 当 App 列表加载完成后，自动加载第一个 App 的子功能并选中第一个
  React.useEffect(() => {
    if (androidApps.length > 0 && androidSubFunctions.length === 0) {
      const firstAppId = androidApps[0].id;
      listAndroidSubFunctions(projectId, { function_id: firstAppId, p: 1, page_size: 100 })
        .then((result) => {
          const items = result?.items || (result as any)?.data?.items || [];
          setAndroidSubFunctions(items);
          if (items.length > 0) {
            setSelectedSubFunctionId(items[0].id);
          }
        })
        .catch((e) => console.error("Auto-load sub-functions failed:", e));
    }
  }, [androidApps, projectId]);

  const handleSelectFolder = (folder: FolderInfo | null) => {
    setSelectedFolderId(folder?.id || null);
    setSelectedFolderName(folder?.name);
    setPage(1);
    setSelectedIds(new Set());
    // 切换文件夹时清除子功能选择，避免显示错误的测试成果物
    setSelectedSubFunctionId(null);
  };

  // 处理创建 App
  const [createAppFolderId, setCreateAppFolderId] = React.useState<string | null>(null);
  const handleCreateAndroidApp = (folderId?: string | null) => {
    setCreateAppFolderId(folderId || null);
    setCreateAppDialogOpen(true);
  };

  // 处理 App 创建成功
  const handleAppCreated = () => {
    loadAndroidApps();
    folderTreeRef.current?.refresh();
  };

  const handleTestCasesCountChange = React.useCallback(async (count: number, subFunctionId?: string) => {
    const targetSubFunctionId = subFunctionId || selectedSubFunctionId || androidSubFunctions[0]?.id;
    if (!targetSubFunctionId) return;

    setActualTestCasesCounts(prev => ({
      ...prev,
      [targetSubFunctionId]: count,
    }));

    setAndroidSubFunctions(prev => prev.map(sf => {
      if (sf.id === targetSubFunctionId) {
        return { ...sf, total_test_cases: count };
      }
      return sf;
    }));
  }, [selectedSubFunctionId, androidSubFunctions]);

  // 加载特定 App 的子功能
  const loadSubFunctionsForApp = React.useCallback(async (appId: string) => {
    try {
      const subFunctions = await listAndroidSubFunctions(projectId, {
        function_id: appId,
        p: 1,
        page_size: 100,
      });
      setAndroidSubFunctions(subFunctions.items || []);
    } catch (error) {
      console.error("Failed to load sub-functions:", error);
      toast.error("加载子功能失败");
    }
  }, [projectId]);

  // 执行脚本
  const handleExecuteScript = (artifactId: string, fileName: string) => {
    const appPackage = selectedAndroidApp?.app_package || "";
    const deviceUdid = selectedAndroidApp?.device_udid || "";
    const prompt = `${t("androidTests.executeTestPrompt")}:

**Script ID**: ${artifactId}
**Script File**: ${fileName}
**Project ID**: ${projectId}
**Sub Function ID**: ${selectedSubFunctionId || "N/A"}
**App Package**: ${appPackage}
**Device UDID**: ${deviceUdid}

请按以下步骤执行测试：
1. 使用 \`download_android_script\` 工具下载脚本到测试工作目录（参数：script_id="${artifactId}"）
2. 使用 \`execute_android_test\` 工具执行测试（参数：local_script_path=从步骤1获取的路径、device_udid="${deviceUdid}"、project_identifier="${projectId}"）
3. 解析执行结果并报告测试状态
4. （可选）使用 \`collect_android_report\` 收集测试报告
5. （可选）使用 \`delete_android_script\` 清理临时脚本`;

    setAiChatInitialPrompt(prompt);
    setAiChatKey(prev => prev + 1);
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
    const subFunction = androidSubFunctions.find(sf => sf.id === subFunctionId);
    const appPackage = selectedAndroidApp?.app_package || "";
    const prompt = `请为子功能「${subFunction?.display_name || ''}」执行测试。

**Project ID**: ${projectId}
**Sub Function ID**: ${subFunctionId}
**App Package**: ${appPackage}

请按以下步骤执行：
1. 检查设备连接状态（\`check_android_device\`）
2. 查询该子功能关联的测试脚本
3. 下载脚本到测试工作目录
4. 使用 \`execute_android_test\` 执行测试（device_udid、project_identifier="${projectId}"）
5. 收集测试报告（\`collect_android_report\`）
6. 解析结果并报告状态

如果找不到脚本，请先生成测试脚本再执行。`;
    setAiChatInitialPrompt(prompt);
    setAiChatKey(prev => prev + 1);
    setSubFunctionDrawerOpen(false);
    setShowSubFunctionSidebar(false);
    setAiChatOpen(true);
  };

  // 处理提交文件夹
  const handleSubmitFolder = async () => {
    if (!folderFormData.name.trim()) {
      toast.error(t("androidTests.folderNameRequired"));
      return;
    }
    try {
      setSubmitting(true);
      if (editingFolder) {
        // 编辑文件夹 - 使用本地更新
        const response = await updateFolder(projectId, editingFolder.id, folderFormData);
        toast.success(t("androidTests.folderUpdateSuccess"));
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
          folder_type: "android_test",  // 指定为 Android 测试类型文件夹
        });
        toast.success(t("androidTests.folderCreateSuccess"));
        setFolderDialogOpen(false);
        // 本地添加文件夹
        if (response.success && response.data) {
          folderTreeRef.current?.addFolderLocally(response.data, folderParentId || null);
        }
      }
    } catch (error) {
      console.error("Failed to save folder:", error);
      toast.error(editingFolder ? t("androidTests.folderUpdateFailed") : t("androidTests.folderCreateFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  // 处理删除文件夹
  const handleDeleteFolder = async () => {
    if (!deletingFolder) return;

    try {
      await deleteFolder(projectId, deletingFolder.id);
      toast.success(t("androidTests.folderDeleteSuccess"));
      setDeleteFolderDialogOpen(false);
      setDeletingFolder(null);

      // 如果删除的是当前选中的文件夹，清空选中状态
      if (selectedFolderId === deletingFolder.id) {
        setSelectedFolderId(null);
        setSelectedFolderName(undefined);
        setAndroidSubFunctions([]);
        setAndroidApps([]);
      }

      // 刷新文件夹树
      folderTreeRef.current?.refresh();
    } catch (error) {
      console.error("Failed to delete folder:", error);
      toast.error(t("androidTests.folderDeleteFailed"));
    }
  };

  // 处理 AI 生成
  const handleAIGenerate = (prompt: string) => {
    setAiChatInitialPrompt(prompt);
    setAiChatKey(prev => prev + 1);
    setAiChatOpen(true);
  };

  // 处理删除 Android App - 打开确认对话框
  const handleDeleteAndroidApp = (androidApp: AndroidApp) => {
    setDeletingAndroidApp(androidApp);
    setDeleteDialogOpen(true);
  };

  // 实际执行删除
  const confirmDeleteAndroidApp = async () => {
    if (!deletingAndroidApp) return;

    try {
      console.log("Deleting android app:", deletingAndroidApp.id, deletingAndroidApp);
      const result = await deleteAndroidApp(projectId, deletingAndroidApp.id);
      console.log("Delete result:", result);
      toast.success(t("androidTests.appDeleteSuccess"));
      setDeleteDialogOpen(false);
      setDeletingAndroidApp(null);
      await loadAndroidApps();
      // 刷新文件夹树
      folderTreeRef.current?.refresh();
    } catch (error: any) {
      console.error("Failed to delete android app:", error);
      console.error("Error details:", {
        message: error.message,
        response: error.response?.data,
        status: error.response?.status
      });
      toast.error(`${t("androidTests.appDeleteFailed")}: ${error.message || "Unknown error"}`);
    }
  };

  // 处理批量删除 Android App
  const handleBulkDelete = async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;

    try {
      console.log("Bulk deleting android apps:", ids);
      for (const id of ids) {
        console.log("Deleting app:", id);
        const result = await deleteAndroidApp(projectId, id);
        console.log("Delete result for", id, ":", result);
      }
      toast.success(t("androidTests.bulkDeleteSuccess", { count: String(ids.length) }));
      setSelectedIds(new Set());
      await loadAndroidApps();
      // 刷新文件夹树
      folderTreeRef.current?.refresh();
    } catch (error: any) {
      console.error("Failed to bulk delete android apps:", error);
      console.error("Error details:", {
        message: error.message,
        response: error.response?.data,
        status: error.response?.status
      });
      toast.error(`${t("androidTests.bulkDeleteFailed")}: ${error.message || "Unknown error"}`);
    }
  };

  // 检查设备连接
  const handleCheckDevice = async () => {
    try {
      const response = await fetch(`/api/v2/projects/${projectId}/android-tests/check-device`, {
        method: "POST",
      });
      const data = await response.json();
      if (data.success) {
        setDeviceConnected(true);
        setDeviceInfo(data);
        toast.success(`已连接 ${data.authorized_devices} 台设备`);
      } else {
        setDeviceConnected(false);
        setDeviceInfo(data);
        toast.error(data.error || "设备连接检查失败");
      }
    } catch (error) {
      console.error("Failed to check device:", error);
      toast.error("设备连接检查失败");
      setDeviceConnected(false);
    }
  };

  return (
    <MainLayout title={t("androidTests.title")}>
      <div className="relative flex h-[calc(100vh-8rem)] rounded-lg border bg-card overflow-hidden">
        <div className="flex h-full w-full">
          {/* 左侧面板 (320px) */}
          <div className="w-80 shrink-0 border-r bg-muted/10 flex flex-col">
            {/* 标题 */}
            <div className="p-3 border-b bg-background">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold flex items-center gap-2">
                  <Smartphone className="h-4 w-4 text-green-500" />
                  {t("androidTests.testManagement")}
                </h3>
              </div>
            </div>

            {/* 设备状态卡片 */}
            <div className="p-3 border-b">
              <div className={cn(
                "rounded-lg border p-2 flex items-center gap-2 cursor-pointer transition-all hover:shadow-md",
                deviceConnected === true ? "bg-green-50 border-green-200 dark:bg-green-950/30 dark:border-green-800" :
                deviceConnected === false ? "bg-red-50 border-red-200 dark:bg-red-950/30 dark:border-red-800" :
                "bg-muted/30 border-muted"
              )} onClick={handleCheckDevice}>
                <Smartphone className={cn(
                  "h-4 w-4",
                  deviceConnected === true ? "text-green-500" :
                  deviceConnected === false ? "text-red-500" :
                  "text-muted-foreground"
                )} />
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium">
                    {deviceConnected === true ? "设备已连接" :
                     deviceConnected === false ? "设备未连接" :
                     "点击检查设备"}
                  </div>
                  {deviceInfo?.devices?.[0] && (
                    <div className="text-[10px] text-muted-foreground truncate">
                      {deviceInfo.devices[0].model} ({deviceInfo.devices[0].udid})
                    </div>
                  )}
                </div>
                <Button variant="ghost" size="sm" className="h-6 px-2 text-xs">
                  检查
                </Button>
              </div>
            </div>

            {/* 文件夹树 */}
            <div className="flex-1 overflow-hidden">
              <TestFolderTree
                ref={folderTreeRef}
                projectId={projectId}
                folderType="android_test"
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
                onCreateItem={() => {
                  setCreateAppDialogOpen(true);
                }}
                onSelectItem={(itemId) => {
                  setSelectedSubFunctionId(itemId);
                }}
                selectedItemId={selectedSubFunctionId}
                platformConfig={{
                  createItemLabel: "创建 App",
                  allItemsLabel: "全部 App",
                  itemIconColorClass: "text-green-500",
                  folderIcon: Smartphone,
                  folderIconColorClass: "text-green-500",
                }}
              />
            </div>
          </div>

          {/* 中间主区域 */}
          <div className="flex-1 flex flex-col min-h-0 bg-background">
            {/* 工具栏 */}
            <div className="flex items-center justify-between border-b px-4 py-3 bg-muted/20">
              <div className="flex items-center gap-2">
                <Layers className="h-5 w-5 text-green-500" />
                <div>
                  <h2 className="text-lg font-semibold">
                    {selectedFolderName || t("androidTests.allApps")}
                  </h2>
                  <p className="text-xs text-muted-foreground">
                    {androidSubFunctions.length}{t("androidTests.subFunctionsCount")}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setAiGenerateDialogOpen(true)}
                  className="bg-gradient-to-r from-green-500 to-emerald-500 hover:from-green-600 hover:to-emerald-600 text-white border-0 shadow-md hover:shadow-lg transition-all"
                >
                  <Sparkles className="mr-2 h-4 w-4" />
                  AI 生成
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setCreateAppDialogOpen(true)}
                  className="bg-background hover:bg-accent"
                >
                  <Plus className="mr-2 h-4 w-4" />
                  新建
                </Button>
                <Button
                  size="sm"
                  onClick={() => setAiChatOpen(true)}
                  className="bg-gradient-to-r from-green-500 to-emerald-500 hover:from-green-600 hover:to-emerald-600 text-white border-0 shadow-md hover:shadow-lg transition-all"
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
                  onClick={() => loadAndroidApps()}
                >
                  <RefreshCw className="h-4 w-4" />
                </Button>
              </div>
            </div>

            {/* 模式内容区域 */}
            <div className="flex-1 overflow-hidden relative flex flex-col">
              {/* Android App 列表 */}
              <div className="overflow-y-auto">
                <TestFunctionList
                  items={androidApps as any}
                  loading={loading}
                  selectedIds={selectedIds}
                  onSelectIds={setSelectedIds}
                  onBulkDelete={handleBulkDelete}
                  onDeleteItem={handleDeleteAndroidApp as any}
                  onViewItem={async (androidApp: any) => {
                    // 设置选中的 App，用于显示标题
                    setSelectedAndroidApp(androidApp);
                    // 加载该 App 下的子功能
                    await loadSubFunctionsForApp(androidApp.id);
                    // 打开抽屉
                    setSubFunctionDrawerOpen(true);
                  }}
                  folderName={selectedFolderName}
                  pagination={{
                    page,
                    pageSize,
                    total,
                    onPageChange: setPage,
                  }}
                  emptyTitle="添加 Android App"
                  emptyDescription='您可以通过上方工具栏中的"新建"或"AI生成"按钮创建 Android App'
                  itemTypeLabel="App"
                  platformConfig={{
                    icon: Smartphone,
                    iconColorClass: "text-green-500",
                    viewItemLabel: "查看子功能",
                  }}
                />
              </div>

              {/* 测试成果物 */}
              <div data-testid="test-artifacts-section" className="flex-1 min-h-0 overflow-y-auto bg-gradient-to-b from-muted/20 to-background p-6">
                <div className="max-w-7xl mx-auto">
                  {/* 当前选中的子功能信息卡片 */}
                  {selectedSubFunctionId && (() => {
                    const currentSubFunction = androidSubFunctions.find(
                      sf => sf.id === selectedSubFunctionId
                    );
                    if (!currentSubFunction) return null;
                    return (
                      <div className="mb-6 rounded-xl border-2 bg-gradient-to-r from-green-50 to-emerald-50 dark:from-green-950/30 dark:to-emerald-950/30 p-4 shadow-sm">
                        <div className="flex items-start justify-between">
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-3 mb-2">
                              <FileCode className="h-5 w-5 text-green-500 shrink-0" />
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
                    );
                  })()}

                  <div className="flex items-center justify-between mb-6">
                    <div>
                      <h2 className="text-xl font-bold flex items-center gap-2">
                        <Zap className="h-5 w-5 text-purple-500" />
                        {t("androidTests.testArtifacts")}
                      </h2>
                      <p className="text-sm text-muted-foreground mt-1">
                        {selectedSubFunctionId
                          ? t("androidTests.testArtifactsDesc")
                          : androidSubFunctions.length > 0
                          ? "请在抽屉中选择一个子功能以查看其测试成果物"
                          : t("androidTests.noAppData")
                        }
                      </p>
                    </div>
                  </div>

                  {androidSubFunctions.length === 0 && (
                    <div className="text-center py-12 border-2 border-dashed rounded-lg bg-muted/10">
                      <Smartphone className="h-16 w-16 text-muted-foreground mx-auto mb-4" />
                      <p className="text-lg font-medium mb-2">{t("androidTests.noAppData")}</p>
                      <p className="text-sm text-muted-foreground mb-4">
                        {t("androidTests.selectFolderOrImportApp")}
                      </p>
                    </div>
                  )}

                  {androidSubFunctions.length > 0 && !selectedSubFunctionId && (
                    <div className="text-center py-12 border-2 border-dashed rounded-xl bg-gradient-to-br from-green-50/50 to-emerald-50/50 dark:from-green-950/20 dark:to-emerald-950/20">
                      <Smartphone className="h-16 w-16 text-muted-foreground mx-auto mb-4" />
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
                    <AndroidTestArtifactsPanel
                      key={`artifacts-${selectedSubFunctionId}`}
                      subFunctionId={selectedSubFunctionId}
                      projectId={projectId}
                      appPackage={selectedAndroidApp?.app_package}
                      onRefresh={loadAndroidApps}
                      onTestCasesCountChange={handleTestCasesCountChange}
                      onExecuteScript={handleExecuteScript}
                      refreshTrigger={artifactsRefreshTrigger}
                    />
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* 右侧悬浮 AI 聊天面板 */}
          {assistant && (
            <div
              key={aiChatKey}
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
                  onTestCreated={() => {
                    loadAndroidApps();
                    folderTreeRef.current?.refresh();
                    setArtifactsRefreshTrigger(prev => prev + 1);
                  }}
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
              <TestSubFunctionSidebar
                subFunctionId={selectedSubFunctionId}
                projectId={projectId}
                onClose={() => {
                  setShowSubFunctionSidebar(false);
                  setSelectedSubFunctionId(null);
                  loadAndroidApps();
                }}
                onGenerateTest={() => {
                  setShowSubFunctionSidebar(false);
                  setAiChatOpen(true);
                  setAiChatInitialPrompt(t("androidTests.generateTestsForFunction", { id: selectedSubFunctionId }));
                }}
                onOpenAIChat={(prompt) => {
                  setAiChatInitialPrompt(prompt);
                  setAiChatKey(prev => prev + 1);
                  setShowSubFunctionSidebar(false);
                  setAiChatOpen(true);
                }}
                onRefresh={() => {
                  loadAndroidApps();
                  folderTreeRef.current?.refresh();
                }}
                apiFunctions={{
                  getSubFunction: getAndroidSubFunction,
                  updateSubFunction: updateAndroidSubFunction,
                  deleteSubFunction: deleteAndroidSubFunction,
                }}
                platformConfig={{
                  headerGradientClass: "bg-gradient-to-r from-green-50 to-emerald-50",
                  darkHeaderGradientClass: "dark:from-green-950/30 dark:to-emerald-950/30",
                  titleGradientClass: "from-green-600 to-emerald-600",
                  descriptionCardClass: "border-green-200 bg-gradient-to-br from-green-50/50 to-emerald-50/50",
                  darkDescriptionCardClass: "dark:from-green-950/20 dark:to-emerald-950/20",
                  icon: Smartphone,
                  iconColorClass: "text-green-500",
                }}
              />
            </div>
          )}
        </div>
      </div>

      {/* 各种对话框 */}
      {/* 创建/编辑 App 对话框 */}
      <CreateAndroidAppDialog
          open={createAppDialogOpen}
          onOpenChange={setCreateAppDialogOpen}
          projectId={projectId}
          folderId={createAppFolderId !== null ? createAppFolderId : selectedFolderId}
          editingApp={editingAndroidApp}
          onSuccess={handleAppCreated}
        />

        {/* AI 生成对话框 */}
        <AndroidAIGenerateDialog
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
                {editingFolder ? t("androidTests.editFolder") : t("androidTests.createFolder")}
              </DialogTitle>
              <DialogDescription>
                {editingFolder ? t("androidTests.editFolderInfo") : t("androidTests.createNewFolder")}
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="folder-name">{t("androidTests.folderNameLabel")}</Label>
                <Input
                  id="folder-name"
                  value={folderFormData.name}
                  onChange={(e) =>
                    setFolderFormData({ ...folderFormData, name: e.target.value })
                  }
                  placeholder={t("androidTests.enterFolderName")}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="folder-description">{t("androidTests.descriptionLabel")}</Label>
                <Textarea
                  id="folder-description"
                  value={folderFormData.description}
                  onChange={(e) =>
                    setFolderFormData({
                      ...folderFormData,
                      description: e.target.value,
                    })
                  }
                  placeholder={t("androidTests.enterDescription")}
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
              <DialogTitle>{t("androidTests.deleteFolderTitle")}</DialogTitle>
              <DialogDescription>
                {t("androidTests.deleteFolderMessage", { name: deletingFolder?.name || "" })}
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

        {/* 删除 Android App 确认对话框 */}
        <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("androidTests.deleteAppTitle")}</DialogTitle>
              <DialogDescription>
                {t("androidTests.deleteAppMessage", { name: deletingAndroidApp?.display_name || "" })}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => {
                  setDeleteDialogOpen(false);
                  setDeletingAndroidApp(null);
                }}
              >
                {t("common.cancel")}
              </Button>
              <Button
                variant="destructive"
                onClick={confirmDeleteAndroidApp}
              >
                {t("common.delete")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* 子功能列表抽屉 */}
        <Drawer open={subFunctionDrawerOpen} onOpenChange={(open) => {
          setSubFunctionDrawerOpen(open);
          // 关闭抽屉时，恢复所有子功能列表
          if (!open) {
            loadAndroidApps();
            setSelectedAndroidApp(null);
          }
        }}>
          <DrawerContent direction="right" showCloseButton={false} className="h-full w-[560px] border-l rounded-none">
            <DrawerHeader className="flex flex-row items-center justify-between space-y-0 pb-4 border-b">
              <div className="flex items-center gap-2">
                <FileCode className="h-5 w-5 text-green-500" />
                <DrawerTitleComp>子功能列表</DrawerTitleComp>
                <span className="text-sm text-muted-foreground">
                  ({selectedAndroidApp ? `${selectedAndroidApp.display_name} - ` : ""}{androidSubFunctions.length})
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
              <TestSubFunctionList
                subFunctions={androidSubFunctions}
                loading={loading}
                selectedId={selectedSubFunctionId}
                onSelectSubFunction={(subFunctionId) => {
                  setSelectedSubFunctionId(subFunctionId);
                  setSubFunctionDrawerOpen(false);
                  // 延迟滚动到测试成果物区域
                  setTimeout(() => {
                    const artifactsSection = document.querySelector('[data-testid="test-artifacts-section"]');
                    if (artifactsSection) {
                      artifactsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    }
                  }, 100);
                }}
                onViewDetail={handleViewSubFunctionDetail}
                onViewTestCases={handleViewSubFunctionTestCases}
                onRunTest={handleRunSubFunctionTest}
                pagination={{
                  page,
                  pageSize,
                  total: androidSubFunctions.length,
                  onPageChange: setPage,
                }}
                showHeader={false}
                platformConfig={{
                  themeColorClass: "border-l-green-500",
                  selectedBgClass: "bg-green-50/80",
                  darkSelectedBgClass: "dark:bg-green-950/20",
                }}
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
              <Button variant="outline" onClick={() => setMcpConfigOpen(false)}>关闭</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </MainLayout>
    );
  }
// FIXME  My80OmFIVnBZMlhscm9ua3VMazZPSFEwV0E9PTo3MWU1Nzg2OA==
