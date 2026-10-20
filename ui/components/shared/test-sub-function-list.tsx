// eslint-disable  MC80OmFIVnBZMlhscm9ua3VMazZjbXhVZUE9PTo0MDk0ZTEwYw==

/**
 * 通用子功能列表组件
 *
 * 支持 Web/Android/iOS 测试的子功能列表展示
 */
"use client";
// NOTE  MS80OmFIVnBZMlhscm9ua3VMazZjbXhVZUE9PTo0MDk0ZTEwYw==

import * as React from "react";
import {
  FileCode,
  CheckCircle2,
  Clock,
  AlertCircle,
  MoreVertical,
  ChevronLeft,
  ChevronRight,
  GripVertical,
  Eye,
  Play,
  LucideIcon,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DndContext,
  DragOverlay,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  DragStartEvent,
  DragEndEvent,
} from "@dnd-kit/core";
import {
  useSortable,
  SortableContext,
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
// NOTE  Mi80OmFIVnBZMlhscm9ua3VMazZjbXhVZUE9PTo0MDk0ZTEwYw==

// 通用子功能项接口
export interface TestSubFunctionItem {
  id: string;
  identifier: string;
  display_name: string;
  description: string | null;
  test_type: string;
  priority: string;
  total_test_cases: number;
  total_test_runs: number;
  last_run_status: string | null;
}

export interface TestSubFunctionListProps {
  subFunctions: TestSubFunctionItem[];
  loading: boolean;
  selectedId?: string | null;
  onSelectSubFunction: (subFunctionId: string) => void;
  onViewDetail?: (subFunctionId: string) => void;
  onViewTestCases?: (subFunctionId: string) => void;
  onRunTest?: (subFunctionId: string) => void;
  onDelete?: (subFunctionId: string) => void;
  pagination?: {
    page: number;
    pageSize: number;
    total: number;
    onPageChange: (page: number) => void;
  };
  showHeader?: boolean;
  // 平台相关配置
  platformConfig: {
    themeColorClass: string; // 选中时的边框/背景色，如 "border-l-blue-500", "bg-blue-50"
    selectedBgClass: string;
    darkSelectedBgClass: string;
  };
  // 自定义空状态文本
  emptyTitle?: string;
  emptyDescription?: string;
}

export function TestSubFunctionList({
  subFunctions,
  loading,
  selectedId,
  onSelectSubFunction,
  onViewDetail,
  onViewTestCases,
  onRunTest,
  onDelete,
  pagination,
  showHeader = true,
  platformConfig,
  emptyTitle,
  emptyDescription,
}: TestSubFunctionListProps) {
  const [activeId, setActiveId] = React.useState<string | null>(null);
  const [localSubFunctions, setLocalSubFunctions] = React.useState<TestSubFunctionItem[]>(
    subFunctions || []
  );
  const [deleteDialogOpen, setDeleteDialogOpen] = React.useState(false);
  const [deleteTarget, setDeleteTarget] = React.useState<TestSubFunctionItem | null>(null);
  const [deleting, setDeleting] = React.useState(false);

  const { themeColorClass, selectedBgClass, darkSelectedBgClass } = platformConfig;

  // 同步外部 subFunctions 到本地状态
  React.useEffect(() => {
    setLocalSubFunctions(subFunctions || []);
  }, [subFunctions]);

  // 配置拖动传感器
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    })
  );

  // 拖动开始
  const handleDragStart = (event: DragStartEvent) => {
    setActiveId(event.active.id as string);
  };

  // 拖动结束
  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    setActiveId(null);

    if (!over || active.id === over.id) {
      return;
    }

    setLocalSubFunctions((items) => {
      const oldIndex = items.findIndex((item) => item.id === active.id);
      const newIndex = items.findIndex((item) => item.id === over.id);
      return arrayMove(items, oldIndex, newIndex);
    });
  };

  // 处理删除确认
  const handleDeleteClick = React.useCallback((subFunctionId: string) => {
    const target = subFunctions.find((sf) => sf.id === subFunctionId);
    if (target) {
      setDeleteTarget(target);
      setDeleteDialogOpen(true);
    }
  }, [subFunctions]);

  const handleConfirmDelete = React.useCallback(async () => {
    if (!deleteTarget || !onDelete) return;
    setDeleting(true);
    try {
      await onDelete(deleteTarget.id);
    } finally {
      setDeleting(false);
      setDeleteDialogOpen(false);
      setDeleteTarget(null);
    }
  }, [deleteTarget, onDelete]);

  const handleCancelDelete = React.useCallback(() => {
    setDeleteDialogOpen(false);
    setDeleteTarget(null);
  }, []);

  // 获取优先级颜色
  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case "critical":
        return "bg-red-500 text-white";
      case "high":
        return "bg-orange-500 text-white";
      case "medium":
        return "bg-yellow-500 text-white";
      case "low":
        return "bg-gray-500 text-white";
      default:
        return "bg-gray-500 text-white";
    }
  };

  // 获取测试类型颜色
  const getTestTypeColor = (testType: string) => {
    switch (testType) {
      case "functional":
        return "bg-blue-500 text-white";
      case "validation":
        return "bg-green-500 text-white";
      case "ui":
        return "bg-purple-500 text-white";
      default:
        return "bg-gray-500 text-white";
    }
  };

  // 可拖动的子功能行组件
  const DraggableSubFunctionRow = ({
    subFunction,
  }: {
    subFunction: TestSubFunctionItem;
  }) => {
    const {
      attributes,
      listeners,
      setNodeRef,
      transform,
      transition,
      isDragging,
    } = useSortable({ id: subFunction.id });

    const style = {
      transform: CSS.Transform.toString(transform),
      transition,
      opacity: isDragging ? 0.5 : 1,
    };

    const isSelected = selectedId === subFunction.id;

    return (
      <div
        ref={setNodeRef}
        style={style}
        className={cn(
          "group border-b transition-colors cursor-pointer",
          isSelected
            ? cn(selectedBgClass, darkSelectedBgClass, "border-l-4", themeColorClass)
            : "hover:bg-muted/50 border-l-4 border-l-transparent",
          isDragging && "opacity-50"
        )}
        onClick={() => onSelectSubFunction(subFunction.id)}
      >
        <div className="flex items-center gap-3 p-4">
          {/* 拖动手柄 */}
          <div
            {...attributes}
            {...listeners}
            className="cursor-grab active:cursor-grabbing opacity-0 group-hover:opacity-100"
          >
            <GripVertical className="h-4 w-4 text-muted-foreground" />
          </div>

          {/* 状态指示器 */}
          <div className="flex-shrink-0">
            {subFunction.total_test_cases > 0 ? (
              <CheckCircle2 className="h-5 w-5 text-green-500" />
            ) : (
              <Clock className="h-5 w-5 text-muted-foreground" />
            )}
          </div>

          {/* 主要信息 */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <h4 className="text-sm font-medium truncate">
                {subFunction.display_name}
              </h4>
              <Badge
                className={cn("text-xs", getTestTypeColor(subFunction.test_type))}
              >
                {subFunction.test_type}
              </Badge>
              <Badge
                className={cn("text-xs", getPriorityColor(subFunction.priority))}
              >
                {subFunction.priority}
              </Badge>
            </div>
            <div className="flex items-start gap-3 text-xs text-muted-foreground">
              <span className="font-mono shrink-0">{subFunction.identifier}</span>
              <span className="shrink-0">•</span>
              <span className="line-clamp-2">{subFunction.description}</span>
            </div>
          </div>

          {/* 测试用例数 */}
          <div className="flex-shrink-0 text-right">
            <div className="flex items-center gap-1 text-sm">
              <FileCode className="h-3 w-3 text-muted-foreground" />
              <span>{subFunction.total_test_cases}</span>
            </div>
            <div className="text-xs text-muted-foreground">测试用例</div>
          </div>

          {/* 操作菜单 */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild onClick={(e) => e.stopPropagation()}>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 opacity-0 group-hover:opacity-100"
              >
                <MoreVertical className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {onViewDetail && (
                <DropdownMenuItem onClick={(e) => { e.stopPropagation(); onViewDetail(subFunction.id); }}>
                  <Eye className="mr-2 h-4 w-4" />
                  查看详情
                </DropdownMenuItem>
              )}
              {onViewDetail && (onViewTestCases || onRunTest) && <DropdownMenuSeparator />}
              {onViewTestCases && (
                <DropdownMenuItem onClick={(e) => { e.stopPropagation(); onViewTestCases(subFunction.id); }}>
                  <FileCode className="mr-2 h-4 w-4" />
                  查看测试用例
                </DropdownMenuItem>
              )}
              {onRunTest && (
                <DropdownMenuItem onClick={(e) => { e.stopPropagation(); onRunTest(subFunction.id); }}>
                  <Play className="mr-2 h-4 w-4" />
                  运行测试
                </DropdownMenuItem>
              )}
              {onDelete && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onClick={(e) => { e.stopPropagation(); handleDeleteClick(subFunction.id); }}
                    className="text-red-600 focus:text-red-600 focus:bg-red-50"
                  >
                    <Trash2 className="mr-2 h-4 w-4" />
                    删除
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    );
  };

  return (
    <div className="flex h-full flex-col">
      {/* 顶部标题栏 */}
      {showHeader && (
        <div className="flex items-center justify-between border-b px-4 py-3 bg-muted/20">
          <div className="flex items-center gap-2">
            <FileCode className="h-5 w-5 text-blue-500" />
            <h2 className="text-lg font-semibold">子功能列表</h2>
            <span className="text-sm text-muted-foreground">
              ({pagination?.total || subFunctions.length})
            </span>
          </div>
        </div>
      )}

      {/* 列表 */}
      <ScrollArea className="flex-1">
        {loading ? (
          <div className="flex h-64 items-center justify-center">
            <div className="text-muted-foreground">加载中...</div>
          </div>
        ) : subFunctions.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-4 py-16">
            <FileCode className="h-16 w-16 text-muted-foreground/30" />
            <div className="text-center">
              <h3 className="text-lg font-semibold mb-2">{emptyTitle || "暂无子功能"}</h3>
              <p className="text-sm text-muted-foreground">
                {emptyDescription || "请先创建功能，然后为其添加子功能"}
              </p>
            </div>
          </div>
        ) : (
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragStart={handleDragStart}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={localSubFunctions.map((sf) => sf.id)}
              strategy={verticalListSortingStrategy}
            >
              {localSubFunctions.map((subFunction) => (
                <DraggableSubFunctionRow
                  key={subFunction.id}
                  subFunction={subFunction}
                />
              ))}
            </SortableContext>

            {/* 拖动覆盖层 */}
            <DragOverlay>
              {activeId ? (
                <div className="rounded-md bg-accent p-3 shadow-lg">
                  <span className="text-sm font-medium">
                    {localSubFunctions.find((sf) => sf.id === activeId)
                      ?.display_name}
                  </span>
                </div>
              ) : null}
            </DragOverlay>
          </DndContext>
        )}
      </ScrollArea>

      {/* 删除确认对话框 */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除子功能？</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget && (
                <>
                  即将删除子功能 <strong>{deleteTarget.display_name}</strong> (
                  {deleteTarget.identifier})
                  {deleteTarget.total_test_cases > 0 && (
                    <span className="block mt-1 text-red-500">
                      该子功能下包含 {deleteTarget.total_test_cases} 个测试用例和 {deleteTarget.total_test_runs} 个测试运行，
                      删除后将一并清除。
                    </span>
                  )}
                  <span className="block mt-2">此操作不可撤销，请确认是否继续？</span>
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={handleCancelDelete} disabled={deleting}>
              取消
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmDelete}
              disabled={deleting}
              className="bg-red-600 hover:bg-red-700 focus:ring-red-600"
            >
              {deleting ? "删除中..." : "确认删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 分页 */}
      {pagination && pagination.total > pagination.pageSize && (
        <div className="flex items-center justify-between border-t px-4 py-3 bg-muted/20">
          <div className="text-sm text-muted-foreground">
            显示 {(pagination.page - 1) * pagination.pageSize + 1} -{" "}
            {Math.min(pagination.page * pagination.pageSize, pagination.total)}{" "}
            / {pagination.total} 条
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={pagination.page === 1}
              onClick={() => pagination.onPageChange(pagination.page - 1)}
            >
              <ChevronLeft className="h-4 w-4" />
              上一页
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={
                pagination.page >=
                Math.ceil(pagination.total / pagination.pageSize)
              }
              onClick={() => pagination.onPageChange(pagination.page + 1)}
            >
              下一页
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
// NOTE  My80OmFIVnBZMlhscm9ua3VMazZjbXhVZUE9PTo0MDk0ZTEwYw==
