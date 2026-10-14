// NOTE  MC80OmFIVnBZMlhscm9ua3VMazZPVGhGZWc9PTplOTBiZWU4OA==

"use client";
// eslint-disable-next-line MS80OmFIVnBZMlhscm9ua3VMazZPVGhGZWc9PTplOTBiZWU4OA==

import * as React from "react";
import { useParams, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { MainLayout } from "@/components/layout";
import { useLanguage } from "@/providers/LanguageProvider";
import {
  FolderTree,
  TestCaseList,
  TestCaseDialog,
  MoveFolderDialog,
  AIGenerateDialog,
  AIGenerateFromDocumentDialog,
  ExportDialog,
} from "@/components/test-cases";
import type { FolderTreeRef } from "@/components/test-cases";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { TestCaseFilters } from "@/components/test-cases";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { AIChatContainer } from "@/components/langgraph/AIChatContainer";
import { ClientProvider } from "@/providers/ClientProvider";
import { Assistant } from "@langchain/langgraph-sdk";
import { ChevronLeft } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  getTestCases,
  getFolderTestCases,
  createTestCase,
  updateTestCase,
  deleteTestCase,
  bulkDeleteTestCases,
  exportTestCases,
  getRequirementReports,
  exportRequirementReport,
  importTestCasesExcel,
} from "@/lib/api/testCases";
import {
  createFolder,
  updateFolder,
  deleteFolder,
} from "@/lib/api/folders";
import type {
  TestCaseInfo,
  TestCaseCreate,
  FolderInfo,
  FolderCreate,
  Priority,
  TestCaseState,
} from "@/lib/api/types";
// TODO  Mi80OmFIVnBZMlhscm9ua3VMazZPVGhGZWc9PTplOTBiZWU4OA==

export default function TestCasesPage() {
  const params = useParams();
  const projectId = params.projectId as string;
  const { t } = useLanguage();

  // 文件夹树 ref
  const folderTreeRef = React.useRef<FolderTreeRef>(null);

  // 状态
  const [testCases, setTestCases] = React.useState<TestCaseInfo[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [selectedFolderId, setSelectedFolderId] = React.useState<string | null>(
    null
  );
  const [selectedIds, setSelectedIds] = React.useState<Set<string>>(new Set());

  // 分页
  const [page, setPage] = React.useState(1);
  const [pageSize, setPageSize] = React.useState(50);
  const [total, setTotal] = React.useState(0);

  // 筛选 - 改用新的筛选接口
  const [searchQuery, setSearchQuery] = React.useState("");
  const [priorityFilter, setPriorityFilter] = React.useState<Priority | undefined>(undefined);
  const [statusFilter, setStatusFilter] = React.useState<TestCaseState | undefined>(undefined);
  const [caseKindFilter, setCaseKindFilter] = React.useState<string | undefined>(undefined);

  // 可用的标签和负责人（从测试用例列表中提取）
  const [availableTags, setAvailableTags] = React.useState<string[]>([]);
  const [availableOwners, setAvailableOwners] = React.useState<string[]>([]);

  // 导出状态
  const [exporting, setExporting] = React.useState(false);
  const [importing, setImporting] = React.useState(false);

  // 导出对话框
  const [exportDialogOpen, setExportDialogOpen] = React.useState(false);

  // 测试用例对话框
  const [testCaseDialogOpen, setTestCaseDialogOpen] = React.useState(false);
  const [editingTestCase, setEditingTestCase] =
    React.useState<TestCaseInfo | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  // 删除确认对话框
  const [deleteDialogOpen, setDeleteDialogOpen] = React.useState(false);
  const [deletingTestCase, setDeletingTestCase] =
    React.useState<TestCaseInfo | null>(null);

  // 批量删除确认对话框
  const [bulkDeleteDialogOpen, setBulkDeleteDialogOpen] = React.useState(false);

  // 文件夹对话框
  const [folderDialogOpen, setFolderDialogOpen] = React.useState(false);
  const [editingFolder, setEditingFolder] = React.useState<FolderInfo | null>(
    null
  );
  const [folderParentId, setFolderParentId] = React.useState<string | undefined>();
  const [folderFormData, setFolderFormData] = React.useState<FolderCreate>({
    name: "",
    description: "",
  });

  // 删除文件夹对话框
  const [deleteFolderDialogOpen, setDeleteFolderDialogOpen] =
    React.useState(false);
  const [deletingFolder, setDeletingFolder] = React.useState<FolderInfo | null>(
    null
  );

  // 创建测试用例时指定的文件夹
  const [createTestCaseFolder, setCreateTestCaseFolder] = React.useState<FolderInfo | null>(null);

  // 当前选中的文件夹名称
  const [selectedFolderName, setSelectedFolderName] = React.useState<string | undefined>();

  // 加载测试用例
  const loadTestCases = React.useCallback(async () => {
    try {
      setLoading(true);
      const params = {
        p: page,
        page_size: pageSize,
        search: searchQuery || undefined,
        priority: priorityFilter,
        status: statusFilter,
        case_kind: caseKindFilter || undefined,
      };

      let response;
      if (selectedFolderId) {
        response = await getFolderTestCases(projectId, selectedFolderId, params);
      } else {
        response = await getTestCases(projectId, params);
      }

      console.log('[TestCasesPage] API 响应:', {
        success: response.success,
        dataCount: response.data?.length || response.test_cases?.length || 0,
        info: response.info,
        pagination: response.pagination,
        total: response.info?.total || response.pagination?.total
      });

      if (response.success) {
        const cases = response.data || response.test_cases || [];
        setTestCases(cases);
        setTotal(response.pagination?.total || 0);

        // 提取可用的标签和负责人
        const tags = new Set<string>();
        const owners = new Set<string>();
        cases.forEach((tc: TestCaseInfo) => {
          tc.tags?.forEach((tag) => tags.add(tag));
          if (tc.owner || tc.created_by) {
            owners.add(tc.owner || tc.created_by!);
          }
        });
        setAvailableTags(Array.from(tags));
        setAvailableOwners(Array.from(owners));
      }
    } catch (error) {
      console.error("Failed to load test cases:", error);
      toast.error(t("testCases.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [projectId, selectedFolderId, page, pageSize, searchQuery, priorityFilter, statusFilter, caseKindFilter]);

  React.useEffect(() => {
    if (projectId) {
      loadTestCases();
    }
  }, [projectId, loadTestCases]);

  // 选择文件夹
  const handleSelectFolder = (folder: FolderInfo | null) => {
    setSelectedFolderId(folder?.id || null);
    setSelectedFolderName(folder?.name);
    setPage(1);
    setSelectedIds(new Set());
  };

  // 筛选变化处理
  const handleFilterChange = React.useCallback((filters: TestCaseFilters) => {
    setSearchQuery(filters.search);
    setPriorityFilter(filters.priority);
    setStatusFilter(filters.status);
    setCaseKindFilter(filters.caseKind);
    setPage(1); // 筛选变化时重置页码
    setSelectedIds(new Set()); // 清空选择
  }, []);

  // 创建/编辑测试用例
  const handleSubmitTestCase = async (data: TestCaseCreate) => {
    try {
      setSubmitting(true);
      if (editingTestCase) {
        await updateTestCase(projectId, editingTestCase.id, data);
        toast.success(t("testCases.updateSuccess"));
      } else {
        // 优先使用从文件夹菜单指定的文件夹
        const targetFolderId = createTestCaseFolder?.id || selectedFolderId;
        await createTestCase(projectId, targetFolderId, data);
        toast.success(t("testCases.createSuccess"));
      }
      setTestCaseDialogOpen(false);
      setEditingTestCase(null);
      setCreateTestCaseFolder(null);
      loadTestCases();
      // 刷新文件夹树以更新用例计数（测试用例操作需要刷新计数）
      folderTreeRef.current?.refresh();
    } catch (error) {
      console.error("Failed to save test case:", error);
      toast.error(editingTestCase ? t("testCases.updateFailed") : t("testCases.createFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  // 从文件夹菜单创建测试用例
  const handleCreateTestCaseInFolder = (folder: FolderInfo) => {
    setCreateTestCaseFolder(folder);
    setEditingTestCase(null);
    setTestCaseDialogOpen(true);
  };

  // 批量删除测试用例
  const handleBulkDelete = () => {
    if (selectedIds.size === 0) {
      toast.error(t("testCases.selectToDelete"));
      return;
    }
    setBulkDeleteDialogOpen(true);
  };

  const confirmBulkDelete = async () => {
    try {
      const idsArray = Array.from(selectedIds);
      const response = await bulkDeleteTestCases(projectId, idsArray);

      if (response.success) {
        toast.success(response.message || t("testCases.bulkDeleteSuccess", { count: response.affected_count.toString() }));
        setSelectedIds(new Set());
        setBulkDeleteDialogOpen(false);
        loadTestCases();
        folderTreeRef.current?.refresh();
      } else {
        toast.error(t("testCases.bulkDeleteFailed"));
      }
    } catch (error) {
      console.error("Failed to bulk delete test cases:", error);
      toast.error(t("testCases.bulkDeleteFailedRetry"));
    }
  };

  // 移动文件夹状态
  const [moveFolderDialogOpen, setMoveFolderDialogOpen] = React.useState(false);
  const [movingFolder, setMovingFolder] = React.useState<FolderInfo | null>(null);

  // AI生成对话框状态
  const [aiGenerateDialogOpen, setAiGenerateDialogOpen] = React.useState(false);
  const [aiGenerateFromDocDialogOpen, setAiGenerateFromDocDialogOpen] = React.useState(false);

  // 选中的知识空间ID（用于AI生成时传递）
  const [selectedKnowledgeSpaceId, setSelectedKnowledgeSpaceId] = React.useState<string>("");

  // AI聊天面板状态
  const [aiChatOpen, setAiChatOpen] = React.useState(false);
  const [aiChatInitialPrompt, setAiChatInitialPrompt] = React.useState<string>("");
  const [aiChatAttachments, setAiChatAttachments] = React.useState<Array<{
    data: string;
    mimeType: string;
    metadata: { filename: string; size: number };
  }> | undefined>(undefined);
  const [aiChatKey, setAiChatKey] = React.useState<number>(0);
  const [assistant, setAssistant] = React.useState<Assistant | null>(null);

  // 从 URL 读取 assistantId（用于外部跳转进入）
  const searchParams = useSearchParams();
  const urlAssistantId = searchParams.get("assistantId");

  // 初始化 Assistant
  React.useEffect(() => {
    const initAssistant = async () => {
      try {
        const defaultAssistantId = "ca1260c3-8d04-5c91-9c72-ba55e4181a56";
        const assistantId = urlAssistantId || defaultAssistantId;
        const mockAssistant: Assistant = {
          assistant_id: assistantId,
          graph_id: "testcase_generator_agent",
          config: {
            configurable: {
              project_identifier: projectId,
              folder_id: selectedFolderId || "",
              template_type: "test_case",
              knowledge_space_id: selectedKnowledgeSpaceId || "",
            }
          },
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          metadata: {},
          version: 1,
          name: t("testCases.aiAssistantName"),
          context: {},
        };
        setAssistant(mockAssistant);
      } catch (error) {
        console.error("Failed to initialize assistant:", error);
      }
    };
    initAssistant();
  }, [projectId, selectedFolderId, urlAssistantId, selectedKnowledgeSpaceId]); // 当 projectId、selectedFolderId 或 urlAssistantId 变化时重新初始化

  // 移动文件夹
  const handleMoveFolder = (folder: FolderInfo) => {
    setMovingFolder(folder);
    setMoveFolderDialogOpen(true);
  };

  // AI生成成功回调
  const handleAIGenerateSuccess = (
    testCases: TestCaseInfo[],
    extra?: { assertionGapWarning?: string | null }
  ) => {
    toast.success(t("testCases.aiGenerateSuccess", { count: testCases.length.toString() }));
    // 断言缺口持久提示：生成期就说明哪些用例无法自动执行、怎么补充，
    // 避免用户等到执行后才看到「需人工执行」而不知原因（sonner 默认 4s 太短）。
    if (extra?.assertionGapWarning) {
      toast.warning("部分需求点未提取到可判定断言（需人工执行）", {
        description: extra.assertionGapWarning,
        duration: 20000,
      });
    }
    loadTestCases();
    folderTreeRef.current?.refresh();
  };

  // 从对话框打开AI聊天面板
  const handleOpenAIChatFromDialog = ({
    prompt,
    attachments,
    knowledgeSpaceId,
  }: {
    prompt: string;
    attachments?: Array<{
      data: string;
      mimeType: string;
      metadata: { filename: string; size: number };
    }>;
    knowledgeSpaceId?: string;
  }) => {
    if (attachments) {
      setAiChatAttachments(attachments);
    } else {
      setAiChatAttachments(undefined);
    }
    if (knowledgeSpaceId) {
      setSelectedKnowledgeSpaceId(knowledgeSpaceId);
    }
    // 设置初始提示词，保持当前对话线程（不更新 aiChatKey，避免重新挂载创建新线程）
    setAiChatInitialPrompt(prompt);
    // 注意：不更新 aiChatKey，保持当前线程上下文，让用户可以继续之前的对话
    setAiChatOpen(true);
    // 关闭生成对话框
    setAiGenerateDialogOpen(false);
    setAiGenerateFromDocDialogOpen(false);
  };

  // 移动文件夹成功回调 - 本地更新树
  const handleMoveFolderSuccess = (folderId: string, newParentId: string | null, updatedFolder: FolderInfo) => {
    folderTreeRef.current?.moveFolderLocally(folderId, newParentId, updatedFolder);
  };

  // 快速创建测试用例
  const handleQuickCreateTestCase = async (title: string, template: "test_case" | "test_case_bdd") => {
    try {
      const data: TestCaseCreate = {
        name: title,
        priority: "medium",
        case_type: "functional",
        template,
      };

      // BDD 测试用例需要提供 feature 和 scenario
      if (template === "test_case_bdd") {
        data.feature = `Feature: ${title}`;
        data.scenario = `Scenario: 验证${title}\n  Given [前置条件]\n  When [操作]\n  Then [预期结果]`;
      }

      await createTestCase(projectId, selectedFolderId || null, data);
      toast.success(t("testCases.createSuccess"));
      loadTestCases();
      folderTreeRef.current?.refresh();
    } catch (error) {
      console.error("Failed to create test case:", error);
      toast.error(t("testCases.createFailed"));
    }
  };

  // 删除测试用例
  const handleDeleteTestCase = async () => {
    if (!deletingTestCase) return;
    try {
      setSubmitting(true);
      await deleteTestCase(projectId, deletingTestCase.id);
      toast.success(t("testCases.deleteSuccess"));
      setDeleteDialogOpen(false);
      setDeletingTestCase(null);
      loadTestCases();
      // 刷新文件夹树以更新用例计数
      folderTreeRef.current?.refresh();
    } catch (error) {
      console.error("Failed to delete test case:", error);
      toast.error(t("testCases.deleteFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  // 打开导出对话框
  const handleOpenExportDialog = () => {
    setExportDialogOpen(true);
  };

  // 导出选中的测试用例
  /** 外部 xlsx 导入：步骤/优先级/模块全保留；完成后刷新列表与文件夹树。 */
  const handleImportTestCases = async (file: File) => {
    if (!selectedFolderId) {
      toast.warning("请先在左侧选择一个目标文件夹再导入");
      return;
    }
    setImporting(true);
    try {
      const result = await importTestCasesExcel(projectId, selectedFolderId, file);
      if (result.error_count === 0) {
        toast.success(`已导入 ${result.created_count} 条测试用例`);
      } else {
        toast.warning(
          `导入 ${result.created_count} 条，失败 ${result.error_count} 条（首条：${result.errors?.[0]?.error ?? ""}）`
        );
      }
      loadTestCases();
      folderTreeRef.current?.refresh();
    } catch (error) {
      toast.error(`导入失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setImporting(false);
    }
  };

  const handleExportSelectedTestCases = async (ids: string[], requirementName?: string) => {
    try {
      setExporting(true);
      const { blob, filename } = await exportTestCases(projectId, {
        test_case_ids: ids.join(","),
        export_type: "test_cases",
        requirement_name: requirementName,
      });

      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);

      toast.success("测试用例导出成功");
    } catch (error) {
      console.error("Failed to export test cases:", error);
      toast.error("导出测试用例失败");
      throw error;
    } finally {
      setExporting(false);
    }
  };

  // 加载需求分析报告列表
  const handleLoadReports = async () => {
    const response = await getRequirementReports(projectId);
    if (response.success) {
      return response.data;
    }
    throw new Error(response.message || "加载失败");
  };

  // 导出需求分析报告
  const handleExportReqReport = async (filename: string) => {
    try {
      setExporting(true);
      const { blob, filename: downloadedFilename } = await exportRequirementReport(
        projectId,
        filename
      );

      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = downloadedFilename;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);

      toast.success("需求分析报告导出成功");
    } catch (error) {
      console.error("Failed to export requirement report:", error);
      toast.error("导出需求分析报告失败");
      throw error;
    } finally {
      setExporting(false);
    }
  };

  // 创建文件夹
  const handleCreateFolder = (parentId?: string) => {
    setEditingFolder(null);
    setFolderParentId(parentId);
    setFolderFormData({ name: "", description: "" });
    setFolderDialogOpen(true);
  };

  // 编辑文件夹
  const handleEditFolder = (folder: FolderInfo) => {
    setEditingFolder(folder);
    setFolderParentId(undefined);
    setFolderFormData({
      name: folder.name,
      description: folder.description || "",
    });
    setFolderDialogOpen(true);
  };

  // 提交文件夹
  const handleSubmitFolder = async () => {
    if (!folderFormData.name.trim()) {
      toast.error(t("testCases.folderNameRequired"));
      return;
    }
    try {
      setSubmitting(true);
      if (editingFolder) {
        // 编辑文件夹 - 使用本地更新
        const response = await updateFolder(projectId, editingFolder.id, folderFormData);
        toast.success(t("testCases.folderUpdateSuccess"));
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
          folder_type: "test_case",  // 指定为测试用例类型文件夹
        });
        toast.success(t("testCases.folderCreateSuccess"));
        setFolderDialogOpen(false);
        // 本地添加文件夹
        if (response.success && response.data) {
          folderTreeRef.current?.addFolderLocally(response.data, folderParentId || null);
        }
      }
    } catch (error) {
      console.error("Failed to save folder:", error);
      toast.error(editingFolder ? t("testCases.folderUpdateFailed") : t("testCases.folderCreateFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  // 删除文件夹
  const handleDeleteFolder = async () => {
    if (!deletingFolder) return;
    try {
      setSubmitting(true);
      await deleteFolder(projectId, deletingFolder.id);
      toast.success(t("testCases.folderDeleteSuccess"));
      setDeleteFolderDialogOpen(false);
      // 本地删除文件夹
      folderTreeRef.current?.removeFolderLocally(deletingFolder.id);
      if (selectedFolderId === deletingFolder.id) {
        setSelectedFolderId(null);
      }
      setDeletingFolder(null);
    } catch (error) {
      console.error("Failed to delete folder:", error);
      toast.error(t("testCases.folderDeleteFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <MainLayout title={t("testCases.title")}>
      <div className="relative flex h-[calc(100vh-8rem)] rounded-lg border bg-card overflow-hidden">
        <div className="flex h-full w-full">
          {/* 文件夹树 */}
          <div className="w-80 shrink-0">
            <FolderTree
              ref={folderTreeRef}
              projectId={projectId}
              folderType="test_case"
              selectedFolderId={selectedFolderId}
              onSelectFolder={handleSelectFolder}
              onCreateFolder={handleCreateFolder}
              onEditFolder={handleEditFolder}
              onDeleteFolder={(folder) => {
                setDeletingFolder(folder);
                setDeleteFolderDialogOpen(true);
              }}
              onCreateTestCase={handleCreateTestCaseInFolder}
              onMoveFolder={handleMoveFolder}
            />
          </div>

          {/* 测试用例列表 */}
          <div className="flex-1">
            <TestCaseList
              testCases={testCases}
              loading={loading}
              selectedIds={selectedIds}
              onSelectIds={setSelectedIds}
              onSearch={(query) => {
                setSearchQuery(query);
                setPage(1);
              }}
              onFilterChange={handleFilterChange}
              onCreateTestCase={() => {
                setEditingTestCase(null);
                setTestCaseDialogOpen(true);
              }}
              onEditTestCase={(tc) => {
                setEditingTestCase(tc);
                setTestCaseDialogOpen(true);
              }}
              onDeleteTestCase={(tc) => {
                setDeletingTestCase(tc);
                setDeleteDialogOpen(true);
              }}
              onBulkDelete={handleBulkDelete}
              onViewTestCase={(tc) => {
                setEditingTestCase(tc);
                setTestCaseDialogOpen(true);
              }}
              onQuickCreateTestCase={handleQuickCreateTestCase}
              onAIGenerate={() => setAiGenerateDialogOpen(true)}
              onAIGenerateFromDocument={() => setAiGenerateFromDocDialogOpen(true)}
              onOpenAIChat={() => setAiChatOpen(true)}
              aiChatOpen={aiChatOpen}
              folderName={selectedFolderName}
              pagination={{
                page,
                pageSize,
                total,
                onPageChange: (newPage) => {
                  setPage(newPage);
                  setSelectedIds(new Set()); // 页码变化时清空选择
                },
                onPageSizeChange: (newPageSize) => {
                  setPageSize(newPageSize);
                  setPage(1); // 页码重置为1
                  setSelectedIds(new Set());
                },
              }}
              availableTags={availableTags}
              availableOwners={availableOwners}
              onExportTestCases={handleOpenExportDialog}
              onImportTestCases={handleImportTestCases}
              importing={importing}
              exporting={exporting}
            />
          </div>
        </div>

        {/* 右侧悬浮 AI 聊天面板 - 始终渲染，避免条件渲染导致的状态丢失 */}
        <div
          key={aiChatKey}
          className={cn(
            "absolute right-0 top-0 z-50 h-full w-[1200px] bg-background transition-transform duration-300 ease-in-out",
            aiChatOpen ? "translate-x-0 border-l shadow-2xl" : "translate-x-full"
          )}
        >
          {assistant ? (
            <ClientProvider
              deploymentUrl="/lg"
              apiKey={process.env.NEXT_PUBLIC_LANGSMITH_API_KEY || ""}
            >
              <AIChatContainer
                assistant={assistant}
                initialPrompt={aiChatInitialPrompt}
                initialAttachments={aiChatAttachments}
                onClose={() => setAiChatOpen(false)}
                createNewThread={aiChatKey > 0}
                onTestCaseCreated={() => {
                  // AI 生成测试用例后自动刷新列表
                  // 延迟 2 秒刷新，确保数据库写入完成
                  setTimeout(() => {
                    loadTestCases();
                    folderTreeRef.current?.refresh();
                  }, 2000);
                }}
              />
            </ClientProvider>
          ) : (
            <div className="flex h-full items-center justify-center">
              <p className="text-muted-foreground">AI 助手加载中...</p>
            </div>
          )}
        </div>
      </div>

      {/* 测试用例对话框 */}
      <TestCaseDialog
        open={testCaseDialogOpen}
        onOpenChange={setTestCaseDialogOpen}
        testCase={editingTestCase}
        onSubmit={handleSubmitTestCase}
        submitting={submitting}
        folderName={createTestCaseFolder?.name || selectedFolderName}
        projectId={projectId}
      />

      {/* 删除测试用例确认 */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("testCases.deleteConfirmTitle")}</DialogTitle>
            <DialogDescription>
              {t("testCases.deleteConfirmMessage", { name: deletingTestCase?.name || "" })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteDialogOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteTestCase}
              disabled={submitting}
            >
              {submitting ? t("testCases.deleting") : t("common.delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 文件夹对话框 */}
      <Dialog open={folderDialogOpen} onOpenChange={setFolderDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {editingFolder ? t("testCases.editFolder") : t("testCases.createFolder")}
            </DialogTitle>
            <DialogDescription>
              {editingFolder ? t("testCases.editFolder") : t("testCases.createFolder")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="folder-name">{t("testCases.folderName")}</Label>
              <Input
                id="folder-name"
                value={folderFormData.name}
                onChange={(e) =>
                  setFolderFormData({ ...folderFormData, name: e.target.value })
                }
                placeholder={t("testCases.folderNamePlaceholder")}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="folder-description">{t("testCases.folderDescription")}</Label>
              <Textarea
                id="folder-description"
                value={folderFormData.description}
                onChange={(e) =>
                  setFolderFormData({
                    ...folderFormData,
                    description: e.target.value,
                  })
                }
                placeholder={t("testCases.descriptionPlaceholder")}
                rows={3}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFolderDialogOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button onClick={handleSubmitFolder} disabled={submitting}>
              {submitting ? t("testCases.saving") : editingFolder ? t("common.save") : t("common.create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除文件夹确认 */}
      <Dialog
        open={deleteFolderDialogOpen}
        onOpenChange={setDeleteFolderDialogOpen}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("testCases.folderDeleteConfirmTitle")}</DialogTitle>
            <DialogDescription>
              {t("testCases.folderDeleteConfirmMessage", { name: deletingFolder?.name || "" })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteFolderDialogOpen(false)}
            >
              {t("common.cancel")}
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteFolder}
              disabled={submitting}
            >
              {submitting ? t("testCases.deleting") : t("common.delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 批量删除确认对话框 */}
      <Dialog
        open={bulkDeleteDialogOpen}
        onOpenChange={setBulkDeleteDialogOpen}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("testCases.bulkDeleteConfirmTitle")}</DialogTitle>
            <DialogDescription>
              {t("testCases.bulkDeleteConfirmMessage", { count: selectedIds.size.toString() })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setBulkDeleteDialogOpen(false)}
            >
              {t("common.cancel")}
            </Button>
            <Button
              variant="destructive"
              onClick={confirmBulkDelete}
            >
              {t("common.delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 移动文件夹对话框 */}
      <MoveFolderDialog
        open={moveFolderDialogOpen}
        onOpenChange={setMoveFolderDialogOpen}
        projectId={projectId}
        folder={movingFolder}
        onMoveSuccess={handleMoveFolderSuccess}
      />

      {/* AI生成测试用例对话框 */}
      <AIGenerateDialog
        open={aiGenerateDialogOpen}
        onOpenChange={(open) => {
          setAiGenerateDialogOpen(open);
          if (!open) setSelectedKnowledgeSpaceId("");
        }}
        projectId={projectId}
        folderId={selectedFolderId}
        onSuccess={handleAIGenerateSuccess}
        onOpenChat={handleOpenAIChatFromDialog}
      />

      {/* AI从文档生成测试用例对话框 */}
      <AIGenerateFromDocumentDialog
        open={aiGenerateFromDocDialogOpen}
        onOpenChange={(open) => {
          setAiGenerateFromDocDialogOpen(open);
          if (!open) setSelectedKnowledgeSpaceId("");
        }}
        projectId={projectId}
        folderId={selectedFolderId}
        onSuccess={handleAIGenerateSuccess}
        onOpenChat={handleOpenAIChatFromDialog}
      />

      {/* 导出对话框 */}
      <ExportDialog
        open={exportDialogOpen}
        onOpenChange={setExportDialogOpen}
        projectId={projectId}
        testCases={testCases}
        selectedIds={selectedIds}
        onExportTestCases={handleExportSelectedTestCases}
        onExportReqReport={handleExportReqReport}
        onLoadReports={handleLoadReports}
        exporting={exporting}
      />
    </MainLayout>
  );
}

// NOTE  My80OmFIVnBZMlhscm9ua3VMazZPVGhGZWc9PTplOTBiZWU4OA==
