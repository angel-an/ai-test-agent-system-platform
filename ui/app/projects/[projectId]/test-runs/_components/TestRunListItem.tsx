"use client";

import * as React from "react";
import { useParams, useRouter } from "next/navigation";
import {
  PlayCircle,
  CheckCircle2,
  XCircle,
  Clock,
  Lock,
  MoreHorizontal,
  Pencil,
  Trash2,
  Eye,
  Play,
  Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { TestRunListInfo, TestRunState, ExecutionMode, TriggerType } from "@/lib/api";

interface TestRunListItemProps {
  run: TestRunListInfo;
  onExecute: (run: TestRunListInfo) => void;
  onEdit: (run: TestRunListInfo) => void;
  onClose: (run: TestRunListInfo) => void;
  onDelete: (run: TestRunListInfo) => void;
}

const RUN_STATE_BADGE: Record<
  TestRunState,
  { label: string; variant: "default" | "secondary" | "destructive" | "outline" }
> = {
  new_run: { label: "新建", variant: "secondary" },
  in_progress: { label: "进行中", variant: "default" },
  under_review: { label: "评审中", variant: "outline" },
  rejected: { label: "执行失败", variant: "destructive" },
  approved: { label: "已批准", variant: "default" },
  done: { label: "已完成", variant: "default" },
  closed: { label: "已关闭", variant: "secondary" },
};

const EXECUTION_MODE_BADGE: Record<ExecutionMode, { label: string; icon: React.ReactNode }> = {
  sequential: { label: "顺序", icon: <Clock className="mr-1 h-3 w-3" /> },
  parallel: { label: "并行", icon: <Clock className="mr-1 h-3 w-3" /> },
};

const TRIGGER_TYPE_BADGE: Record<TriggerType, { label: string; icon: React.ReactNode; variant: "default" | "secondary" | "outline" | "destructive" }> = {
  manual: { label: "手动", icon: <Play className="mr-1 h-3 w-3" />, variant: "secondary" },
  scheduled: { label: "定时", icon: <Clock className="mr-1 h-3 w-3" />, variant: "outline" },
  api: { label: "API", icon: <Clock className="mr-1 h-3 w-3" />, variant: "default" },
};

export function progressDoneRatio(run: TestRunListInfo): number {
  const total = run.test_cases_count;
  if (!total) return 0;
  const p = run.overall_progress;
  const finished = (p.passed ?? 0) + (p.failed ?? 0) + (p.blocked ?? 0) + (p.skipped ?? 0) + (p.not_executed ?? 0);
  return Math.min(Math.round((finished / total) * 100), 100);
}

export function TestRunListItem({ run, onExecute, onEdit, onClose, onDelete }: TestRunListItemProps) {
  const params = useParams();
  const router = useRouter();
  const projectId = params.projectId as string;

  const p = run.overall_progress;
  const progress = progressDoneRatio(run);
  const closed = run.active_state === "closed";
  const stateInfo = RUN_STATE_BADGE[run.run_state];
  const execMode = run.execution_mode ? EXECUTION_MODE_BADGE[run.execution_mode] : null;
  const trigger = run.trigger_type ? TRIGGER_TYPE_BADGE[run.trigger_type] : null;

  return (
    <div className="flex items-center justify-between p-4 hover:bg-muted/50 transition-colors">
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <PlayCircle className="h-5 w-5 text-primary" />
          <h3 className="font-medium truncate">{run.name}</h3>
          <Badge variant="outline" className="font-mono text-xs">
            {run.identifier}
          </Badge>
          <Badge variant={stateInfo.variant}>{stateInfo.label}</Badge>
          {execMode && (
            <Badge variant="outline" className="text-xs">
              {execMode.icon}
              {execMode.label}
            </Badge>
          )}
          {trigger && (
            <Badge variant={trigger.variant} className="text-xs">
              {trigger.icon}
              {trigger.label}
            </Badge>
          )}
          {closed && (
            <Badge variant="secondary">
              <Lock className="mr-1 h-3 w-3" />
              已关闭
            </Badge>
          )}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2">
          <div className="flex flex-wrap items-center gap-4 text-sm">
            <span className="flex items-center gap-1 text-green-600">
              <CheckCircle2 className="h-4 w-4" />
              {p.passed ?? 0} 通过
            </span>
            <span className="flex items-center gap-1 text-red-600">
              <XCircle className="h-4 w-4" />
              {p.failed ?? 0} 失败
            </span>
            <span className="flex items-center gap-1 text-amber-600">
              <Clock className="h-4 w-4" />
              {p.not_executed ?? p.untested ?? 0} 未测
            </span>
            <span className="text-muted-foreground">
              {p.blocked ?? 0} 阻塞 / {p.skipped ?? 0} 跳过
            </span>
            <span className="text-muted-foreground">共 {run.test_cases_count}</span>
          </div>
          <div className="flex items-center gap-2">
            <Progress value={progress} className="w-32" />
            <span className="text-sm text-muted-foreground">{progress}%</span>
          </div>
        </div>
      </div>
      <div className="ml-4 flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => router.push(`/projects/${projectId}/test-runs/${run.identifier}`)}
        >
          <Eye className="mr-2 h-4 w-4" />
          查看
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon">
              <MoreHorizontal className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              onClick={() => onExecute(run)}
              disabled={closed || run.run_state === "in_progress"}
            >
              <Play className="mr-2 h-4 w-4" />
              执行
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => onEdit(run)} disabled={closed}>
              <Pencil className="mr-2 h-4 w-4" />
              编辑
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => onClose(run)} disabled={closed}>
              <Lock className="mr-2 h-4 w-4" />
              关闭
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="text-destructive" onClick={() => onDelete(run)}>
              <Trash2 className="mr-2 h-4 w-4" />
              删除
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}
