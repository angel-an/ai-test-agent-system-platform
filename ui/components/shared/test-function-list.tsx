// FIXME  MC80OmFIVnBZMlhscm9ua3VMazZRVFZLWmc9PTo5MmE1ZGNlMg==

"use client";
// NOTE  MS80OmFIVnBZMlhscm9ua3VMazZRVFZLWmc9PTo5MmE1ZGNlMg==

import * as React from "react";
import {
  Search,
  Plus,
  MoreVertical,
  Pencil,
  Trash2,
  ChevronLeft,
  ChevronRight,
  GripVertical,
  Globe,
  FileText,
  LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
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
// NOTE  Mi80OmFIVnBZMlhscm9ua3VMazZRVFZLWmc9PTo5MmE1ZGNlMg==

// 通用功能项接口（兼容 Web/Android/iOS）
export interface TestFunctionItem {
  id: string;
  identifier: string;
  display_name: string;
  business_module?: string | null;
  total_sub_functions: number;
  total_test_cases: number;
  created_at: string;
}

export interface TestFunctionListProps {
  items: TestFunctionItem[];
  loading: boolean;
  selectedIds: Set<string>;
  onSelectIds: (ids: Set<string>) => void;
  onBulkDelete?: () => void;
  onViewItem: (item: TestFunctionItem) => void;
  onDeleteItem?: (item: TestFunctionItem) => void;
  folderName?: string;
  pagination?: {
    page: number;
    pageSize: number;
    total: number;
    onPageChange: (page: number) => void;
  };
  // 自定义空状态文本
  emptyTitle?: string;
  emptyDescription?: string;
  itemTypeLabel?: string;
  // 平台相关配置
  platformConfig: {
    icon: LucideIcon;
    iconColorClass: string;
    viewItemLabel: string;
  };
}
// FIXME  My80OmFIVnBZMlhscm9ua3VMazZRVFZLWmc9PTo5MmE1ZGNlMg==

export function TestFunctionList({
  items,
  loading,
  selectedIds,
  onSelectIds,
  onBulkDelete,
  onViewItem,
  onDeleteItem,
  folderName,
  pagination,
  emptyTitle,
  emptyDescription,
  itemTypeLabel,
  platformConfig,
}: TestFunctionListProps) {
  const [activeId, setActiveId] = React.useState<string | null>(null);
  const [localItems, setLocalItems] = React.useState<TestFunctionItem[]>(
    items || []
  );

  const { icon: PlatformIcon, iconColorClass, viewItemLabel } = platformConfig;

  // 同步外部 items 到本地状态
  React.useEffect(() => {
    setLocalItems(items || []);
  }, [items]);

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

    setLocalItems((items) => {
      const oldIndex = items.findIndex((item) => item.id === active.id);
      const newIndex = items.findIndex((item) => item.id === over.id);
      const newItems = arrayMove(items, oldIndex, newIndex);

      toast.success((itemTypeLabel || "功能") + "顺序已更新");

      return newItems;
    });
  };

  // 全选/取消全选
  const handleSelectAll = (checked: boolean) => {
    if (checked) {
      onSelectIds(new Set(localItems.map((item) => item.id)));
    } else {
      onSelectIds(new Set());
    }
  };

  // 单选
  const handleSelect = (id: string, checked: boolean) => {
    const newIds = new Set(selectedIds);
    if (checked) {
      newIds.add(id);
    } else {
      newIds.delete(id);
    }
    onSelectIds(newIds);
  };

  const isAllSelected =
    localItems.length > 0 && selectedIds.size === localItems.length;
  const isPartialSelected =
    selectedIds.size > 0 && selectedIds.size < localItems.length;

  // 可拖动的功能行组件
  const DraggableFunctionRow = ({
    item,
  }: {
    item: TestFunctionItem;
  }) => {
    const {
      attributes,
      listeners,
      setNodeRef,
      transform,
      transition,
      isDragging,
    } = useSortable({ id: item.id });

    const style = {
      transform: CSS.Transform.toString(transform),
      transition,
      opacity: isDragging ? 0.5 : 1,
    };

    return (
      <tr
        ref={setNodeRef}
        style={style}
        className={cn(
          "group border-b hover:bg-muted/50 transition-colors",
          selectedIds.has(item.id) && "bg-muted/30"
        )}
      >
        <td className="p-3">
          <div className="flex items-center gap-1">
            <div
              {...attributes}
              {...listeners}
              className="cursor-grab active:cursor-grabbing opacity-0 group-hover:opacity-100"
            >
              <GripVertical className="h-4 w-4 text-muted-foreground" />
            </div>
            <Checkbox
              checked={selectedIds.has(item.id)}
              onCheckedChange={(checked) =>
                handleSelect(item.id, checked as boolean)
              }
              aria-label={`选择 ${item.display_name}`}
            />
          </div>
        </td>
        <td className="p-3 overflow-hidden">
          <button
            onClick={() => onViewItem(item)}
            className="truncate text-left text-sm font-medium hover:text-primary block w-full"
          >
            {item.display_name}
          </button>
        </td>
        <td className="p-3 overflow-hidden">
          <span className="text-sm text-muted-foreground truncate block">
            {item.identifier}
          </span>
        </td>
        <td className="p-3 overflow-hidden">
          {item.business_module && (
            <Badge variant="outline" className="truncate">
              {item.business_module}
            </Badge>
          )}
        </td>
        <td className="p-3 overflow-hidden">
          <div className="flex items-center gap-1 text-sm">
            <FileText className="h-3 w-3 text-muted-foreground" />
            <span>{item.total_sub_functions}</span>
          </div>
        </td>
        <td className="p-3 overflow-hidden">
          <div className="flex items-center gap-1 text-sm">
            <PlatformIcon className={cn("h-3 w-3", iconColorClass)} />
            <span>{item.total_test_cases}</span>
          </div>
        </td>
        <td className="p-3">
          <span className="text-sm text-muted-foreground whitespace-nowrap">
            {new Date(item.created_at).toLocaleDateString("zh-CN", {
              year: "numeric",
              month: "2-digit",
              day: "2-digit",
            })}
          </span>
        </td>
        <td className="p-3">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 opacity-0 group-hover:opacity-100"
              >
                <MoreVertical className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => onViewItem(item)}>
                <PlatformIcon className={cn("mr-2 h-4 w-4", iconColorClass)} />
                {viewItemLabel}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                className="text-destructive focus:text-destructive"
                onClick={() => onDeleteItem && onDeleteItem(item)}
              >
                <Trash2 className="mr-2 h-4 w-4" />
                删除
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </td>
      </tr>
    );
  };

  return (
    <div className="flex flex-col">
      {/* 批量操作栏 */}
      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 border-b bg-muted/50 px-4 py-2">
          <span className="text-sm text-muted-foreground">
            已选择 {selectedIds.size} 项
          </span>
          <Button variant="outline" size="sm">
            批量运行
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="text-destructive hover:bg-destructive hover:text-destructive-foreground"
            onClick={onBulkDelete}
          >
            <Trash2 className="mr-2 h-4 w-4" />
            批量删除
          </Button>
        </div>
      )}

      {/* 列表 */}
      <ScrollArea className="min-h-0">
        {loading ? (
          <div className="flex h-64 items-center justify-center">
            <div className="text-muted-foreground">加载中...</div>
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-6 py-12">
            {/* 图标 */}
            <div className="flex h-16 w-16 items-center justify-center rounded-lg border-2 border-dashed border-muted-foreground/30">
              <PlatformIcon className={cn("h-8 w-8", iconColorClass, "opacity-50")} />
            </div>

            {/* 标题和描述 */}
            <div className="text-center">
              <h3 className="text-lg font-semibold">{emptyTitle || "添加功能"}</h3>
              <p className="text-sm text-muted-foreground">
                {emptyDescription || '您可以通过上方工具栏中的"新建"或"AI生成"按钮创建功能'}
              </p>
            </div>

            {/* 操作提示 */}
            <div className="text-sm text-muted-foreground mt-4">
              提示：选择文件夹后可以筛选显示的功能
            </div>
          </div>
        ) : (
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragStart={handleDragStart}
            onDragEnd={handleDragEnd}
          >
            <table className="w-full table-fixed">
              <thead className="sticky top-0 bg-card">
                <tr className="border-b text-left text-xs font-medium uppercase text-muted-foreground">
                  <th className="w-16 p-3">
                    <Checkbox
                      checked={isAllSelected}
                      onCheckedChange={handleSelectAll}
                      aria-label="全选"
                    />
                  </th>
                  <th className="w-48 p-3">显示名称</th>
                  <th className="w-32 p-3">标识符</th>
                  <th className="w-28 p-3">业务模块</th>
                  <th className="w-20 p-3">子功能数</th>
                  <th className="w-20 p-3">用例数</th>
                  <th className="w-36 p-3">创建时间</th>
                  <th className="w-16 p-3"></th>
                </tr>
              </thead>
              <SortableContext
                items={localItems.map((item) => item.id)}
                strategy={verticalListSortingStrategy}
              >
                <tbody>
                  {localItems.map((item) => (
                    <DraggableFunctionRow
                      key={item.id}
                      item={item}
                    />
                  ))}
                </tbody>
              </SortableContext>
            </table>

            {/* 拖动覆盖层 */}
            <DragOverlay>
              {activeId ? (
                <div className="rounded-md bg-accent p-3 shadow-lg">
                  <span className="text-sm font-medium">
                    {
                      localItems.find((item) => item.id === activeId)
                        ?.display_name
                    }
                  </span>
                </div>
              ) : null}
            </DragOverlay>
          </DndContext>
        )}
      </ScrollArea>

      {/* 分页 */}
      {pagination && pagination.total > pagination.pageSize && (
        <div className="flex items-center justify-end border-t px-4 py-3 gap-2">
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
