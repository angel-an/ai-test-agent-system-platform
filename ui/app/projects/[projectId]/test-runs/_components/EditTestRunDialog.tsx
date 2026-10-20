"use client";

import * as React from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Loader2 } from "lucide-react";
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

interface EditTestRunDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  identifier: string;
  name: string;
  runState: TestRunState;
  onSave: (data: { name: string; run_state: TestRunState }) => Promise<void>;
  saving: boolean;
}

export function EditTestRunDialog({
  open,
  onOpenChange,
  identifier,
  name: initialName,
  runState: initialRunState,
  onSave,
  saving,
}: EditTestRunDialogProps) {
  const [name, setName] = React.useState(initialName);
  const [runState, setRunState] = React.useState<TestRunState>(initialRunState);

  React.useEffect(() => {
    if (open) {
      setName(initialName);
      setRunState(initialRunState);
    }
  }, [open, initialName, initialRunState]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>编辑测试运行</DialogTitle>
          <DialogDescription>
            修改 {identifier} 的基础信息
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label htmlFor="edit-name">名称</Label>
            <Input
              id="edit-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="edit-state">运行状态</Label>
            <Select
              value={runState}
              onValueChange={(v) => setRunState(v as TestRunState)}
            >
              <SelectTrigger id="edit-state">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {RUN_STATE_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={saving}
          >
            取消
          </Button>
          <Button
            onClick={() => onSave({ name, run_state: runState })}
            disabled={saving || !name.trim()}
          >
            {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
