"use client";

import * as React from "react";
import { BookOpen, Plus, MoreHorizontal, Trash2, RefreshCw, FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Badge } from "@/components/ui/badge";
import type { KnowledgeSpaceInfo } from "@/lib/api/types";

interface KnowledgeBaseListProps {
  spaces: KnowledgeSpaceInfo[];
  loading: boolean;
  onSelectSpace: (space: KnowledgeSpaceInfo) => void;
  onDeleteSpace: (space: KnowledgeSpaceInfo) => void;
  onRebuildIndex: (space: KnowledgeSpaceInfo) => void;
  onCreateSpace: () => void;
}

export function KnowledgeBaseList({
  spaces,
  loading,
  onSelectSpace,
  onDeleteSpace,
  onRebuildIndex,
  onCreateSpace,
}: KnowledgeBaseListProps) {
  if (loading) {
    return (
      <div className="space-y-4">
        {Array.from({ length: 3 }).map((_, i) => (
          <Card key={i} className="animate-pulse">
            <CardHeader className="pb-3">
              <div className="h-5 w-1/3 rounded bg-muted" />
              <div className="h-4 w-2/3 rounded bg-muted" />
            </CardHeader>
          </Card>
        ))}
      </div>
    );
  }

  if (spaces.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-lg border border-dashed p-12">
        <BookOpen className="h-12 w-12 text-muted-foreground" />
        <h3 className="mt-4 text-lg font-semibold">暂无知识空间</h3>
        <p className="mt-2 text-sm text-muted-foreground">
          创建知识空间来组织和管理您的知识文档
        </p>
        <Button className="mt-4" onClick={onCreateSpace}>
          <Plus className="mr-2 h-4 w-4" />
          创建知识空间
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">知识空间</h2>
        <Button size="sm" onClick={onCreateSpace}>
          <Plus className="mr-2 h-4 w-4" />
          新建空间
        </Button>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {spaces.map((space) => (
          <Card
            key={space.id}
            className="cursor-pointer transition-colors hover:bg-accent/50"
            onClick={() => onSelectSpace(space)}
          >
            <CardHeader className="pb-3">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-2">
                  <BookOpen className="h-5 w-5 text-primary" />
                  <CardTitle className="text-base">{space.name}</CardTitle>
                </div>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 p-0"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <MoreHorizontal className="h-4 w-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem
                      onClick={(e) => {
                        e.stopPropagation();
                        onRebuildIndex(space);
                      }}
                    >
                      <RefreshCw className="mr-2 h-4 w-4" />
                      重建索引
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      className="text-destructive"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteSpace(space);
                      }}
                    >
                      <Trash2 className="mr-2 h-4 w-4" />
                      删除空间
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
              {space.description && (
                <CardDescription className="line-clamp-2">
                  {space.description}
                </CardDescription>
              )}
            </CardHeader>
            <CardContent className="pt-0">
              <div className="flex items-center gap-2">
                {space.business_line && (
                  <Badge variant="secondary">{space.business_line}</Badge>
                )}
                <div className="flex items-center gap-1 text-sm text-muted-foreground">
                  <FileText className="h-3.5 w-3.5" />
                  <span>{space.document_count} 个文档</span>
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
