// eslint-disable  MC80OmFIVnBZMlhscm9ua3VMazZSVzgyZEE9PTo0ZmRlZmI0Nw==

/**
 * AI 生成 iOS App 测试对话框
 * 用于通过 AI 智能体生成 iOS App 功能和测试
 */
"use client";
// TODO  MS80OmFIVnBZMlhscm9ua3VMazZSVzgyZEE9PTo0ZmRlZmI0Nw==

import * as React from "react";
import { useState } from "react";
import { Sparkles, Loader2, Smartphone, Monitor } from "lucide-react";
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

interface IOSAIGenerateDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onGenerate: (description: string) => void;
  folderName?: string;
}

export function IOSAIGenerateDialog({
  open,
  onOpenChange,
  onGenerate,
  folderName,
}: IOSAIGenerateDialogProps) {
  const [submitting, setSubmitting] = useState(false);
  const [description, setDescription] = useState("");
  const [bundleId, setBundleId] = useState("");
  const [deviceUdid, setDeviceUdid] = useState("");
  const [deviceType, setDeviceType] = useState<"physical" | "simulator">("physical");
  const [wdaUrl, setWdaUrl] = useState("");

  // 重置表单
  const resetForm = () => {
    setDescription("");
    setBundleId("");
    setDeviceUdid("");
    setDeviceType("physical");
    setWdaUrl("");
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
      ? '请帮我在"' + folderName + '"文件夹下创建 iOS App 测试：' + description.trim()
      : '请帮我创建 iOS App 测试：' + description.trim();

    // 添加 iOS 特有信息
    if (bundleId.trim()) {
      prompt += '\n\nBundle ID: ' + bundleId.trim();
    }
    if (deviceUdid.trim()) {
      prompt += '\n设备 UDID: ' + deviceUdid.trim();
    }
    prompt += '\n设备类型: ' + (deviceType === "physical" ? "物理设备" : "模拟器");
    if (wdaUrl.trim()) {
      prompt += '\nWebDriverAgent URL: ' + wdaUrl.trim();
    }

    // 添加 iOS 测试特定的上下文
    prompt += "\n\n请按以下步骤执行：\n" +
      "1. 使用 check_ios_device 检查设备连接状态（支持物理设备和模拟器）\n" +
      "2. 使用 get_ios_app_info 获取 App 基本信息（Bundle ID、版本、构建号）\n" +
      "3. 分析 App 功能并生成测试计划\n" +
      "4. 使用 save_ios_test_plan 保存测试计划\n" +
      "5. 生成测试用例并使用 save_ios_test_cases 保存\n" +
      "6. 生成 Midscene iOS 测试脚本（TypeScript + Vitest + @midscene/ios）\n" +
      "7. 使用 save_ios_test_script 保存测试脚本\n" +
      "8. 使用 download_ios_script 下载脚本到本地\n" +
      "9. 使用 execute_ios_test 执行测试\n" +
      "10. 使用 collect_ios_report 收集测试报告\n" +
      "\niOS 特有注意事项：\n" +
      "- 测试脚本使用 @midscene/ios 包\n" +
      "- 支持 pinch/zoom 手势操作（地图缩放、图片预览等）\n" +
      "- 支持 Home/Back/RecentApp 系统导航操作\n" +
      "- 支持通过 bundleId 终止 App 重置状态\n" +
      "- 使用 aiAct / aiQuery / aiAssert / aiWaitFor 等 Midscene API\n" +
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
            <Sparkles className="h-5 w-5 text-blue-500" />
            AI 生成 iOS App 测试
          </DialogTitle>
          <DialogDescription>
            描述您想要测试的 iOS App 功能，AI 将自动生成功能定义、测试计划、测试用例和测试脚本
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {/* Bundle ID */}
          <div className="space-y-2">
            <Label htmlFor="bundleId">
              <Smartphone className="inline h-4 w-4 mr-1" />
              Bundle ID <span className="text-muted-foreground">(可选)</span>
            </Label>
            <Input
              id="bundleId"
              value={bundleId}
              onChange={(e) => setBundleId(e.target.value)}
              placeholder="例如：com.example.app"
              disabled={submitting}
            />
            <p className="text-xs text-muted-foreground">
              iOS App 的 Bundle Identifier，如 com.meituan.itakeaway。如果不填，AI 会尝试自动检测
            </p>
          </div>

          {/* 设备类型 */}
          <div className="space-y-2">
            <Label>设备类型</Label>
            <div className="flex gap-2">
              <Button
                variant={deviceType === "physical" ? "default" : "outline"}
                size="sm"
                className="flex-1"
                onClick={() => setDeviceType("physical")}
                disabled={submitting}
              >
                <Smartphone className="h-4 w-4 mr-1" />
                物理设备
              </Button>
              <Button
                variant={deviceType === "simulator" ? "default" : "outline"}
                size="sm"
                className="flex-1"
                onClick={() => setDeviceType("simulator")}
                disabled={submitting}
              >
                <Monitor className="h-4 w-4 mr-1" />
                模拟器
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              选择测试设备类型，AI 将针对对应环境生成测试脚本
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
              指定测试设备的 UDID。如果不填，将使用第一个可用设备
            </p>
          </div>

          {/* WebDriverAgent URL */}
          <div className="space-y-2">
            <Label htmlFor="wdaUrl">
              WebDriverAgent URL <span className="text-muted-foreground">(可选)</span>
            </Label>
            <Input
              id="wdaUrl"
              value={wdaUrl}
              onChange={(e) => setWdaUrl(e.target.value)}
              placeholder="例如：http://localhost:8100"
              disabled={submitting}
            />
            <p className="text-xs text-muted-foreground">
              WebDriverAgent 服务地址，用于连接 iOS 设备
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
              placeholder="例如：美团外卖 App 的下单功能。打开 App → 搜索餐厅 → 选择商品 → 加入购物车 → 提交订单。需要测试搜索、商品选择、购物车、订单提交等功能。"
              className="min-h-[150px] resize-none"
              disabled={submitting}
            />
            <p className="text-xs text-muted-foreground">
              请详细描述要测试的 iOS App 功能，包括主要功能点、业务场景、测试要求等
            </p>
          </div>

          {/* 示例提示 */}
          <div className="rounded-lg border border-muted bg-muted/30 p-3">
            <p className="text-xs font-medium text-muted-foreground mb-2">
              💡 描述示例：
            </p>
            <p className="text-xs text-muted-foreground">
              社交 App 的发布动态功能，包括拍照上传、文字编辑、位置标记、@好友、权限设置等子功能。
              需要测试图片上传、文字输入、位置获取、好友选择、隐私设置等场景。
            </p>
          </div>

          {/* Midscene iOS 提示 */}
          <div className="rounded-lg border border-blue-200 bg-blue-50 dark:bg-blue-950/30 p-3">
            <p className="text-xs font-medium text-blue-700 dark:text-blue-400 mb-1">
              🤖 AI 测试流程
            </p>
            <p className="text-xs text-muted-foreground">
              AI 将使用 Midscene iOS + AI 视觉模型进行自动化测试，支持 aiAct（执行操作）、aiQuery（提取数据）、
              aiAssert（断言验证）、aiWaitFor（等待条件）等 API。支持 pinch/zoom 手势、Home/Back 导航、
              bundleId 终止 App 等 iOS 特有功能。测试脚本使用 TypeScript + Vitest 框架。
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
            className="bg-gradient-to-r from-blue-500 to-sky-500 hover:from-blue-600 hover:to-sky-600 text-white border-0"
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
