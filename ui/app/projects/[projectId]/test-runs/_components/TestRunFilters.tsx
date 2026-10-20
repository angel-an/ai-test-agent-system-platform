"use client";

import * as React from "react";
import {
  Search,
  RefreshCw,
  CalendarClock,
  Plus,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { TestRunState } from "@/lib/api";

const RUN_STATE_OPTIONS: { value: TestRunState; label: string }[] = [
  { value: "new_run", label: "新建" },
  { value: "in_progress", label: "进行中" },
  { value: "under_review", label: "评审中" },
  { value: "rejected", label: "执行失败" },
  { value: "approved", label: "已批准" },
  { value: "done", label: "已完成" },
  { value: "closed", label: "已关闭" },
];

interface TestRunFiltersProps {
  searchInput: string;
  onSearchChange: (value: string) => void;
  runStateFilter: TestRunState | "all";
  onRunStateChange: (value: TestRunState | "all") => void;
  includeClosed: boolean;
  onIncludeClosedChange: (value: boolean) => void;
  loading: boolean;
  onRefresh: () => void;
  onCreate: () => void;
  onNavigateSchedules: () => void;
}

export function TestRunFilters({
  searchInput,
  onSearchChange,
  runStateFilter,
  onRunStateChange,
  includeClosed,
  onIncludeClosedChange,
  loading,
  onRefresh,
  onCreate,
  onNavigateSchedules,
}: TestRunFiltersProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="搜索名称或标识符..."
            value={searchInput}
            onChange={(e) => onSearchChange(e.target.value)}
            className="w-64 pl-9"
          />
        </div>
        <Select
          value={runStateFilter}
          onValueChange={(v) => onRunStateChange(v as TestRunState | "all")}
        >
          <SelectTrigger className="w-40">
            <SelectValue placeholder="运行状态" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部状态</SelectItem>
            {RUN_STATE_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={includeClosed}
            onCheckedChange={(checked) => onIncludeClosedChange(checked === true)}
          />
          <span>包含已关闭</span>
        </label>
        <Button variant="ghost" size="icon" onClick={onRefresh} disabled={loading} title="刷新">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </Button>
      </div>
      <div className="flex items-center gap-2">
        <Button variant="outline" onClick={onNavigateSchedules}>
          <CalendarClock className="mr-2 h-4 w-4" />
          测试调度
        </Button>
        <Button onClick={onCreate}>
          <Plus className="mr-2 h-4 w-4" />
          新建测试运行
        </Button>
      </div>
    </div>
  );
}
