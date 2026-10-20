"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { MainLayout } from "@/components/layout";
import { useLanguage } from "@/providers/LanguageProvider";
import {
  KnowledgeBaseList,
  KnowledgeBaseUploadDialog,
  KnowledgeBaseDetailDrawer,
} from "@/components/knowledge-base";
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
import {
  getKnowledgeSpaces,
  createKnowledgeSpace,
  deleteKnowledgeSpace,
  getDocuments,
  deleteDocument,
  rebuildIndex,
  uploadDocument,
} from "@/lib/api/knowledge-base";
import type {
  KnowledgeSpaceInfo,
  KnowledgeDocumentInfo,
} from "@/lib/api/types";

export default function KnowledgeBasePage() {
  const params = useParams();
  const projectId = params.projectId as string;
  const { t } = useLanguage();

  // 状态
  const [spaces, setSpaces] = React.useState<KnowledgeSpaceInfo[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [selectedSpace, setSelectedSpace] = React.useState<KnowledgeSpaceInfo | null>(null);
  const [documents, setDocuments] = React.useState<KnowledgeDocumentInfo[]>([]);
  const [documentsLoading, setDocumentsLoading] = React.useState(false);

  // 对话框状态
  const [createDialogOpen, setCreateDialogOpen] = React.useState(false);
  const [uploadDialogOpen, setUploadDialogOpen] = React.useState(false);
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const [deleteSpaceDialogOpen, setDeleteSpaceDialogOpen] = React.useState(false);
  const [deleteDocDialogOpen, setDeleteDocDialogOpen] = React.useState(false);
  const [deletingSpace, setDeletingSpace] = React.useState<KnowledgeSpaceInfo | null>(null);
  const [deletingDoc, setDeletingDoc] = React.useState<KnowledgeDocumentInfo | null>(null);

  // 表单状态
  const [spaceForm, setSpaceForm] = React.useState({
    name: "",
    description: "",
    business_line: "",
  });
  const [submitting, setSubmitting] = React.useState(false);

  // 加载知识空间列表
  const loadSpaces = React.useCallback(async () => {
    try {
      setLoading(true);
      const response = await getKnowledgeSpaces(projectId);
      if (response.success) {
        setSpaces(response.data || []);
      }
    } catch (error) {
      console.error("Failed to load knowledge spaces:", error);
      toast.error("加载知识空间失败");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  React.useEffect(() => {
    if (projectId) {
      loadSpaces();
    }
  }, [projectId, loadSpaces]);

  // 加载文档列表
  const loadDocuments = React.useCallback(async (spaceId: string) => {
    try {
      setDocumentsLoading(true);
      const response = await getDocuments(projectId, spaceId);
      if (response.success) {
        setDocuments(response.data || []);
      }
    } catch (error) {
      console.error("Failed to load documents:", error);
      toast.error("加载文档失败");
    } finally {
      setDocumentsLoading(false);
    }
  }, [projectId]);

  // 选择空间
  const handleSelectSpace = (space: KnowledgeSpaceInfo) => {
    setSelectedSpace(space);
    setDrawerOpen(true);
    loadDocuments(space.id);
  };

  // 创建空间
  const handleCreateSpace = async () => {
    if (!spaceForm.name.trim()) {
      toast.error("请输入空间名称");
      return;
    }

    try {
      setSubmitting(true);
      const response = await createKnowledgeSpace(projectId, {
        name: spaceForm.name.trim(),
        description: spaceForm.description || undefined,
        business_line: spaceForm.business_line || undefined,
      });

      if (response.success) {
        toast.success("知识空间创建成功");
        setCreateDialogOpen(false);
        setSpaceForm({ name: "", description: "", business_line: "" });
        loadSpaces();
      }
    } catch (error) {
      console.error("Failed to create space:", error);
      toast.error("创建知识空间失败");
    } finally {
      setSubmitting(false);
    }
  };

  // 删除空间
  const handleDeleteSpace = (space: KnowledgeSpaceInfo) => {
    setDeletingSpace(space);
    setDeleteSpaceDialogOpen(true);
  };

  const confirmDeleteSpace = async () => {
    if (!deletingSpace) return;

    try {
      await deleteKnowledgeSpace(projectId, deletingSpace.id);
      toast.success("知识空间删除成功");
      setDeleteSpaceDialogOpen(false);
      setDeletingSpace(null);
      loadSpaces();

      // 如果删除的是当前选中的空间，关闭抽屉
      if (selectedSpace?.id === deletingSpace.id) {
        setDrawerOpen(false);
        setSelectedSpace(null);
      }
    } catch (error) {
      console.error("Failed to delete space:", error);
      toast.error("删除知识空间失败");
    }
  };

  // 上传文档
  const handleUpload = async (file: File, title?: string) => {
    if (!selectedSpace) return;

    try {
      const response = await uploadDocument(projectId, selectedSpace.id, file, title);
      if (response.success) {
        toast.success("文档上传成功");
        loadDocuments(selectedSpace.id);
        loadSpaces(); // 刷新空间列表以更新文档计数
      } else {
        toast.error(response.data?.message || "上传失败");
        throw new Error(response.data?.message || "上传失败");
      }
    } catch (error) {
      console.error("Failed to upload document:", error);
      toast.error("文档上传失败");
      throw error;
    }
  };

  // 删除文档
  const handleDeleteDocument = (doc: KnowledgeDocumentInfo) => {
    setDeletingDoc(doc);
    setDeleteDocDialogOpen(true);
  };

  const confirmDeleteDocument = async () => {
    if (!deletingDoc || !selectedSpace) return;

    try {
      await deleteDocument(projectId, selectedSpace.id, deletingDoc.id);
      toast.success("文档删除成功");
      setDeleteDocDialogOpen(false);
      setDeletingDoc(null);
      loadDocuments(selectedSpace.id);
      loadSpaces();
    } catch (error) {
      console.error("Failed to delete document:", error);
      toast.error("删除文档失败");
    }
  };

  // 重建索引
  const handleRebuildIndex = async (space: KnowledgeSpaceInfo) => {
    try {
      toast.info("开始重建索引...");
      const response = await rebuildIndex(projectId, space.id);
      if (response.success) {
        toast.success(
          `索引重建完成：成功 ${response.data.success} 个，失败 ${response.data.failed} 个`
        );
        if (selectedSpace?.id === space.id) {
          loadDocuments(space.id);
        }
      }
    } catch (error) {
      console.error("Failed to rebuild index:", error);
      toast.error("重建索引失败");
    }
  };

  return (
    <MainLayout title="知识库">
      <div className="p-6">
        <KnowledgeBaseList
          spaces={spaces}
          loading={loading}
          onSelectSpace={handleSelectSpace}
          onDeleteSpace={handleDeleteSpace}
          onRebuildIndex={handleRebuildIndex}
          onCreateSpace={() => setCreateDialogOpen(true)}
        />
      </div>

      {/* 空间详情抽屉 */}
      <KnowledgeBaseDetailDrawer
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
        space={selectedSpace}
        documents={documents}
        loading={documentsLoading}
        onUpload={() => setUploadDialogOpen(true)}
        onDeleteDocument={handleDeleteDocument}
        onRebuildIndex={() => selectedSpace && handleRebuildIndex(selectedSpace)}
      />

      {/* 创建空间对话框 */}
      <Dialog open={createDialogOpen} onOpenChange={setCreateDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>创建知识空间</DialogTitle>
            <DialogDescription>
              创建一个新的知识空间来组织文档
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="space-name">空间名称 *</Label>
              <Input
                id="space-name"
                value={spaceForm.name}
                onChange={(e) =>
                  setSpaceForm({ ...spaceForm, name: e.target.value })
                }
                placeholder="例如：订单模块知识库"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="space-desc">描述</Label>
              <Textarea
                id="space-desc"
                value={spaceForm.description}
                onChange={(e) =>
                  setSpaceForm({ ...spaceForm, description: e.target.value })
                }
                placeholder="知识空间的描述信息"
                rows={3}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="space-business">业务线</Label>
              <Input
                id="space-business"
                value={spaceForm.business_line}
                onChange={(e) =>
                  setSpaceForm({ ...spaceForm, business_line: e.target.value })
                }
                placeholder="例如：电商业务"
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setCreateDialogOpen(false)}
              disabled={submitting}
            >
              取消
            </Button>
            <Button onClick={handleCreateSpace} disabled={submitting}>
              {submitting ? "创建中..." : "创建"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 上传文档对话框 */}
      {selectedSpace && (
        <KnowledgeBaseUploadDialog
          open={uploadDialogOpen}
          onOpenChange={setUploadDialogOpen}
          spaceId={selectedSpace.id}
          spaceName={selectedSpace.name}
          onUpload={handleUpload}
        />
      )}

      {/* 删除空间确认 */}
      <Dialog open={deleteSpaceDialogOpen} onOpenChange={setDeleteSpaceDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除知识空间</DialogTitle>
            <DialogDescription>
              确定要删除知识空间「{deletingSpace?.name}」吗？此操作将同时删除该空间下的所有文档和索引，不可恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteSpaceDialogOpen(false)}>
              取消
            </Button>
            <Button variant="destructive" onClick={confirmDeleteSpace}>
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除文档确认 */}
      <Dialog open={deleteDocDialogOpen} onOpenChange={setDeleteDocDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除文档</DialogTitle>
            <DialogDescription>
              确定要删除文档「{deletingDoc?.file_name}」吗？
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteDocDialogOpen(false)}>
              取消
            </Button>
            <Button variant="destructive" onClick={confirmDeleteDocument}>
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </MainLayout>
  );
}
