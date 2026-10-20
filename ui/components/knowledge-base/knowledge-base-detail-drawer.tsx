"use client";

import * as React from "react";
import { FileText, Trash2, RefreshCw, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Drawer,
  DrawerContent,
  DrawerHeader,
  DrawerTitle,
  DrawerDescription,
} from "@/components/ui/drawer";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { KnowledgeSpaceInfo, KnowledgeDocumentInfo } from "@/lib/api/types";

interface KnowledgeBaseDetailDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  space: KnowledgeSpaceInfo | null;
  documents: KnowledgeDocumentInfo[];
  loading: boolean;
  onUpload: () => void;
  onDeleteDocument: (doc: KnowledgeDocumentInfo) => void;
  onRebuildIndex: () => void;
}

function getStatusBadge(status: string) {
  switch (status) {
    case "indexed":
      return <Badge variant="default" className="bg-green-500">已索引</Badge>;
    case "processing":
      return <Badge variant="secondary">处理中</Badge>;
    case "pending":
      return <Badge variant="outline">待处理</Badge>;
    case "failed":
      return <Badge variant="destructive">失败</Badge>;
    default:
      return <Badge variant="outline">{status}</Badge>;
  }
}

export function KnowledgeBaseDetailDrawer({
  open,
  onOpenChange,
  space,
  documents,
  loading,
  onUpload,
  onDeleteDocument,
  onRebuildIndex,
}: KnowledgeBaseDetailDrawerProps) {
  if (!space) return null;

  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent className="w-full sm:max-w-2xl ml-auto">
        <DrawerHeader className="border-b pb-4 pr-12">
          <div className="flex flex-col gap-1">
            <DrawerTitle className="text-lg">{space.name}</DrawerTitle>
            <DrawerDescription>
              {space.description || "暂无描述"}
            </DrawerDescription>
          </div>
          <div className="flex items-center gap-2 mt-2">
            {space.business_line && (
              <Badge variant="secondary">{space.business_line}</Badge>
            )}
            <span className="text-sm text-muted-foreground">
              {space.document_count} 个文档
            </span>
          </div>
        </DrawerHeader>

        <div className="p-4 space-y-4">
          {/* 操作按钮 */}
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={onUpload}>
              <FileText className="mr-2 h-4 w-4" />
              上传文档
            </Button>
            <Button size="sm" variant="outline" onClick={onRebuildIndex}>
              <RefreshCw className="mr-2 h-4 w-4" />
              重建索引
            </Button>
          </div>

          {/* 文档列表 */}
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          ) : documents.length === 0 ? (
            <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-12">
              <FileText className="h-10 w-10 text-muted-foreground" />
              <p className="mt-2 text-sm text-muted-foreground">暂无文档</p>
              <Button size="sm" variant="outline" className="mt-4" onClick={onUpload}>
                上传第一个文档
              </Button>
            </div>
          ) : (
            <ScrollArea className="h-[calc(100vh-280px)]">
              <div className="space-y-2">
                {documents.map((doc) => (
                  <div
                    key={doc.id}
                    className="flex items-center justify-between rounded-lg border p-3 hover:bg-muted/50"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <FileText className="h-5 w-5 text-muted-foreground shrink-0" />
                      <div className="min-w-0">
                        <p className="text-sm font-medium truncate">{doc.file_name}</p>
                        <div className="flex items-center gap-2 mt-1">
                          {getStatusBadge(doc.status)}
                          <span className="text-xs text-muted-foreground">
                            {doc.chunk_count} 切片 · {formatFileSize(doc.file_size)}
                          </span>
                        </div>
                      </div>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 p-0 text-destructive shrink-0"
                      onClick={() => onDeleteDocument(doc)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
              </div>
            </ScrollArea>
          )}
        </div>
      </DrawerContent>
    </Drawer>
  );
}

function formatFileSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}
