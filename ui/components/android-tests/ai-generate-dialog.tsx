// eslint-disable  MC80OmFIVnBZMlhscm9ua3VMazZSVzgyZEE9PTo0ZmRlZmI0Nw==

/**
 * AI 生成 Android App 测试对话框
 * 用于通过 AI 智能体生成 Android App 功能和测试
 */
"use client";
// TODO  MS80OmFIVnBZMlhscm9ua3VMazZSVzgyZEE9PTo0ZmRlZmI0Nw==

import * as React from "react";
import { useState } from "react";
import { Sparkles, Loader2, Smartphone } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";
// FIXME  Mi80OmFIVnBZMlhscm9ua3VMazZSVzgyZEE9PTo0ZmRlZmI0Nw==

interface AndroidAIGenerateDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onGenerate: (description: string) => void;
  folderName?: string;
}

export function AndroidAIGenerateDialog({
  open,
  onOpenChange,
  onGenerate,
  folderName,
}: AndroidAIGenerateDialogProps) {
  const [submitting, setSubmitting] = useState(false);
  const [description, setDescription] = useState("");
  const [appPackage, setAppPackage] = useState("");
  const [deviceUdid, setDeviceUdid] = useState("");

  // 重置表单
  const resetForm = () => {
    setDescription("");
    setAppPackage("");
    setDeviceUdid("");
  };

  // 处理对话框关闭
  const handleOpenChange = (open: boolean) => {
    if (!open && !submitting) {
      resetForm();
    }
    onOpenChange(open);
  };

  // 处理生成
  const handleGenerate = () => {
    // 验证必填字段
    if (!description.trim()) {
      toast.error("请输入 App 功能描述信息");
      return;
    }

    let prompt = folderName
      ? '请帮我在"' + folderName + '"文件夹下创建 Android App 测试：' + description.trim()
      : '请帮我创建 Android App 测试：' + description.trim();

    // 添加 App 包名和设备信息
    if (appPackage.trim()) {
      prompt += '\n\nApp 包名: ' + appPackage.trim();
    }
    if (deviceUdid.trim()) {
      prompt += '\n设备 UDID: ' + deviceUdid.trim();
    }

    // 添加 Android 测试特定的上下文
    prompt += "\n\n请按以下步骤执行：\n" +
      "1. 使用 check_android_device 检查设备连接状态\n" +
      "2. 使用 get_app_info 获取 App 基本信息（包名、版本、主 Activity）\n" +
      "3. 分析 App 功能并生成测试计划\n" +
      "4. 使用 save_android_test_plan 保存测试计划\n" +
      "5. 生成测试用例并使用 save_android_test_cases 保存\n" +
      "6. 生成 Midscene Android 测试脚本（TypeScript + Vitest + @midscene/android）\n" +
      "7. 使用 save_android_test_script 保存测试脚本\n" +
      "8. 使用 download_android_script 下载脚本到本地\n" +
      "9. 使用 execute_android_test 执行测试\n" +
      "10. 使用 collect_android_report 收集测试报告\n" +
      "\n注意事项：\n" +
      "- 测试脚本使用 aiAct / aiQuery / aiAssert / aiWaitFor 等 Midscene API\n" +
      "- 每个 it 对应一个完整的原子操作+验证逻辑\n" +
      "- 所有 aiWaitFor 设置 timeoutMs: 15000 以上\n" +
      "- 设备屏幕需保持唤醒状态";

    onGenerate(prompt);
    resetForm();
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[650px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-green-500" />
            AI 生成 Android App 测试
          </DialogTitle>
          <DialogDescription>
            描述您想要测试的 Android App 功能，AI 将自动生成功能定义、测试计划、测试用例和测试脚本
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {/* App 包名 */}
          <div className="space-y-2">
            <Label htmlFor="appPackage">
              <Smartphone className="inline h-4 w-4 mr-1" />
              App 包名 <span className="text-muted-foreground">(可选)</span>
            </Label>
            <Input
              id="appPackage"
              value={appPackage}
              onChange={(e) => setAppPackage(e.target.value)}
              placeholder="例如：com.example.app"
              disabled={submitting}
            />
            <p className="text-xs text-muted-foreground">
              Android App 的包名，如 com.dongchedi.app。如果不填，AI 会尝试自动检测
            </p>
          </div>

          {/* 设备 UDID */}
          <div className="space-y-2">
            <Label htmlFor="deviceUdid">
              设备 UDID <span className="text-muted-foreground">(可选)</span>
            </Label>
            <Input
              id="deviceUdid"
              value={deviceUdid}
              onChange={(e) => setDeviceUdid(e.target.value)}
              placeholder="例如：abc123def456"
              disabled={submitting}
            />
            <p className="text-xs text-muted-foreground">
              指定测试设备的序列号。如果不填，将使用第一个已连接的设备
            </p>
          </div>

          {/* 功能描述 */}
          <div className="space-y-2">
            <Label htmlFor="description">
              功能描述 <span className="text-destructive">*</span>
            </Label>
            <Textarea
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="例如：懂车帝 App 的查看车型参数功能。打开 App → 搜索小米SU7 → 进入车型详情页 → 查看参数配置。需要测试搜索、页面跳转、参数展示等功能。"
              className="min-h-[150px] resize-none"
              disabled={submitting}
            />
            <p className="text-xs text-muted-foreground">
              请详细描述要测试的 Android App 功能，包括主要功能点、业务场景、测试要求等
            </p>
          </div>

          {/* 示例提示 */}
          <div className="rounded-lg border border-muted bg-muted/30 p-3">
            <p className="text-xs font-medium text-muted-foreground mb-2">
              💡 描述示例：
            </p>
            <p className="text-xs text-muted-foreground">
              电商 App 的购物车功能，包括商品加入购物车、购物车数量修改、购物车结算、优惠券使用等子功能。
              需要测试商品添加、数量变更、价格计算、结算流程、空购物车状态等场景。
            </p>
          </div>

          {/* Midscene 提示 */}
          <div className="rounded-lg border border-green-200 bg-green-50 dark:bg-green-950/30 p-3">
            <p className="text-xs font-medium text-green-700 dark:text-green-400 mb-1">
              🤖 AI 测试流程
            </p>
            <p className="text-xs text-muted-foreground">
              AI 将使用 Midscene + AI 视觉模型进行自动化测试，支持 aiAct（执行操作）、aiQuery（提取数据）、
              aiAssert（断言验证）、aiWaitFor（等待条件）等 API。测试脚本使用 TypeScript + Vitest 框架。
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            取消
          </Button>
          <Button
            onClick={handleGenerate}
            disabled={submitting || !description.trim()}
            className="bg-gradient-to-r from-green-500 to-emerald-500 hover:from-green-600 hover:to-emerald-600 text-white border-0"
          >
            {submitting ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                生成中...
              </>
            ) : (
              <>
                <Sparkles className="mr-2 h-4 w-4" />
                开始生成
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
// NOTE  My80OmFIVnBZMlhscm9ua3VMazZSVzgyZEE9PTo0ZmRlZmI0Nw==
