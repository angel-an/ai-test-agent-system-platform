// NOTE  MC80OmFIVnBZMlhscm9ua3VMazZPR1E0Ymc9PToxMjJiYzk1ZA==

/**
 * 创建/编辑 iOS App 对话框
 * 用于手工创建或编辑 iOS App 测试对象
 */
"use client";
// NOTE  MS80OmFIVnBZMlhscm9ua3VMazZPR1E0Ymc9PToxMjJiYzk1ZA==

import * as React from "react";
import { useState } from "react";
import { Loader2, Smartphone, Monitor } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import type { IOSApp } from "@/lib/api/ios-apps";
import { createIOSApp, updateIOSApp } from "@/lib/api/ios-apps";
// NOTE  Mi80OmFIVnBZMlhscm9ua3VMazZPR1E0Ymc9PToxMjJiYzk1ZA==

interface CreateIOSAppDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  folderId?: string | null;
  editingApp?: IOSApp | null;
  onSuccess?: () => void;
}
// NOTE  My80OmFIVnBZMlhscm9ua3VMazZPR1E0Ymc9PToxMjJiYzk1ZA==

export function CreateIOSAppDialog({
  open,
  onOpenChange,
  projectId,
  folderId,
  editingApp,
  onSuccess,
}: CreateIOSAppDialogProps) {
  const [submitting, setSubmitting] = useState(false);

  // 判断是否为编辑模式
  const isEditMode = !!editingApp;

  // 表单数据
  const [displayName, setDisplayName] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [bundleId, setBundleId] = useState("");
  const [mainActivity, setMainActivity] = useState("");
  const [version, setVersion] = useState("");
  const [deviceUdid, setDeviceUdid] = useState("");
  const [deviceType, setDeviceType] = useState<"physical" | "simulator">("physical");
  const [wdaUrl, setWdaUrl] = useState("");
  const [businessModule, setBusinessModule] = useState("");

  // 重置表单
  const resetForm = () => {
    setDisplayName("");
    setName("");
    setDescription("");
    setBundleId("");
    setMainActivity("");
    setVersion("");
    setDeviceUdid("");
    setDeviceType("physical");
    setWdaUrl("");
    setBusinessModule("");
  };

  // 当对话框打开或编辑 App 变化时，初始化表单
  React.useEffect(() => {
    if (open && editingApp) {
      // 编辑模式：填充现有数据
      setDisplayName(editingApp.display_name || "");
      setName(editingApp.name || "");
      setDescription(editingApp.description || "");
      setBundleId(editingApp.bundle_id || "");
      setMainActivity(editingApp.main_activity || "");
      setVersion(editingApp.version || "");
      setDeviceUdid(editingApp.device_udid || "");
      setDeviceType(editingApp.device_type || "physical");
      setWdaUrl(editingApp.wda_url || "");
      setBusinessModule(editingApp.business_module || "");
    } else if (open && !editingApp) {
      // 创建模式：重置表单
      resetForm();
    }
  }, [open, editingApp]);

  // 处理对话框关闭
  const handleOpenChange = (open: boolean) => {
    if (!open && !submitting) {
      resetForm();
    }
    onOpenChange(open);
  };

  // 处理保存
  const handleSave = async () => {
    console.log("[CreateIOSApp] handleSave 被调用");
    // 验证必填字段
    if (!displayName.trim()) {
      toast.error("请输入显示名称");
      return;
    }
    if (!name.trim()) {
      toast.error("请输入英文名称");
      return;
    }
    if (!bundleId.trim()) {
      toast.error("请输入 Bundle ID");
      return;
    }

    // 验证英文名称格式（只允许字母、数字、连字符和下划线）
    const nameRegex = /^[a-zA-Z0-9-_]+$/;
    if (!nameRegex.test(name.trim())) {
      toast.error("英文名称只能包含字母、数字、连字符和下划线");
      return;
    }

    // 验证 Bundle ID 格式
    const bundleRegex = /^[a-zA-Z][a-zA-Z0-9]*(\.[a-zA-Z0-9]+)+$/;
    if (!bundleRegex.test(bundleId.trim())) {
      toast.error("Bundle ID 格式不正确，应为 com.example.app 格式");
      return;
    }

    try {
      setSubmitting(true);
      console.log("[CreateIOSApp] submitting 已设置为 true");

      const requestBody: any = {
        display_name: displayName.trim(),
        name: name.trim(),
        bundle_id: bundleId.trim(),
      };

      // 添加所有非空的字段到请求中
      if (description.trim()) requestBody.description = description.trim();
      if (mainActivity.trim()) requestBody.main_activity = mainActivity.trim();
      if (version.trim()) requestBody.version = version.trim();
      if (deviceUdid.trim()) requestBody.device_udid = deviceUdid.trim();
      requestBody.device_type = deviceType;
      if (wdaUrl.trim()) requestBody.wda_url = wdaUrl.trim();
      requestBody.business_module = businessModule.trim();
      if (folderId) requestBody.folder_id = folderId;

      console.log("[CreateIOSApp] 请求体:", requestBody);
      console.log("[CreateIOSApp] projectId:", projectId, "isEditMode:", isEditMode);

      if (isEditMode && editingApp) {
        // 编辑模式：使用 PATCH 更新
        console.log("[CreateIOSApp] 调用 updateIOSApp");
        await updateIOSApp(projectId, editingApp.id, requestBody);
        toast.success("iOS App 更新成功");
      } else {
        // 创建模式：使用 POST 创建
        console.log("[CreateIOSApp] 调用 createIOSApp");
        await createIOSApp(projectId, requestBody);
        toast.success("iOS App 创建成功");
      }

      console.log("[CreateIOSApp] API 调用成功");
      resetForm();
      onOpenChange(false);
      onSuccess?.();
    } catch (error: any) {
      console.error(isEditMode ? "更新 iOS App 失败:" : "创建 iOS App 失败:", error);
      toast.error(error.message || (isEditMode ? "更新 iOS App 失败" : "创建 iOS App 失败"));
    } finally {
      console.log("[CreateIOSApp] finally 块执行，设置 submitting=false");
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[600px] max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Smartphone className="h-5 w-5 text-blue-500" />
            {isEditMode ? "编辑 iOS App" : "创建 iOS App"}
          </DialogTitle>
          <DialogDescription>
            {isEditMode ? "编辑现有的 iOS App 配置" : "手工创建 iOS App 测试对象，填写 App 的完整信息"}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {/* 显示名称 */}
          <div className="space-y-2">
            <Label htmlFor="displayName">
              显示名称 <span className="text-destructive">*</span>
            </Label>
            <Input
              id="displayName"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="例如：美团 App"
              disabled={submitting}
            />
            <p className="text-xs text-muted-foreground">
              App 的中文名称，用于界面显示
            </p>
          </div>

          {/* 英文名称 */}
          <div className="space-y-2">
            <Label htmlFor="name">
              英文名称 <span className="text-destructive">*</span>
            </Label>
            <Input
              id="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：meituan"
              disabled={submitting}
            />
            <p className="text-xs text-muted-foreground">
              App 的英文标识符，只能包含字母、数字、连字符和下划线
            </p>
          </div>

          {/* Bundle ID */}
          <div className="space-y-2">
            <Label htmlFor="bundleId">
              <Smartphone className="inline h-4 w-4 mr-1" />
              Bundle ID <span className="text-destructive">*</span>
            </Label>
            <Input
              id="bundleId"
              value={bundleId}
              onChange={(e) => setBundleId(e.target.value)}
              placeholder="例如：com.meituan.itakeaway"
              disabled={submitting}
            />
            <p className="text-xs text-muted-foreground">
              iOS App 的 Bundle Identifier，如 com.example.app
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
              选择测试设备类型，物理设备需要连接真机，模拟器使用 Xcode Simulator
            </p>
          </div>

          {/* 主 Activity */}
          <div className="space-y-2">
            <Label htmlFor="mainActivity">主 Activity</Label>
            <Input
              id="mainActivity"
              value={mainActivity}
              onChange={(e) => setMainActivity(e.target.value)}
              placeholder="例如：MTMainViewController"
              disabled={submitting}
            />
            <p className="text-xs text-muted-foreground">
              App 的主入口 ViewController，用于启动测试
            </p>
          </div>

          {/* 版本号 */}
          <div className="space-y-2">
            <Label htmlFor="version">版本号</Label>
            <Input
              id="version"
              value={version}
              onChange={(e) => setVersion(e.target.value)}
              placeholder="例如：1.0.0"
              disabled={submitting}
            />
            <p className="text-xs text-muted-foreground">
              App 当前版本号
            </p>
          </div>

          {/* 设备 UDID */}
          <div className="space-y-2">
            <Label htmlFor="deviceUdid">设备 UDID</Label>
            <Input
              id="deviceUdid"
              value={deviceUdid}
              onChange={(e) => setDeviceUdid(e.target.value)}
              placeholder="例如：abc123def456"
              disabled={submitting}
            />
            <p className="text-xs text-muted-foreground">
              指定测试设备的 UDID，如果不填将使用第一个可用设备
            </p>
          </div>

          {/* WebDriverAgent URL */}
          <div className="space-y-2">
            <Label htmlFor="wdaUrl">WebDriverAgent URL</Label>
            <Input
              id="wdaUrl"
              value={wdaUrl}
              onChange={(e) => setWdaUrl(e.target.value)}
              placeholder="例如：http://localhost:8100"
              disabled={submitting}
            />
            <p className="text-xs text-muted-foreground">
              WebDriverAgent 服务地址，用于连接 iOS 设备。如果不填将使用默认地址
            </p>
          </div>

          {/* 业务模块 */}
          <div className="space-y-2">
            <Label htmlFor="businessModule">业务模块</Label>
            <Input
              id="businessModule"
              value={businessModule}
              onChange={(e) => setBusinessModule(e.target.value)}
              placeholder="例如：电商、社交、工具等"
              disabled={submitting}
            />
            <p className="text-xs text-muted-foreground">
              用于 App 分类和模块化管理
            </p>
          </div>

          {/* 详细描述 */}
          <div className="space-y-2">
            <Label htmlFor="description">详细描述</Label>
            <Textarea
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="详细描述 App 的功能、测试要点、主要页面等"
              className="min-h-[100px] resize-none"
              disabled={submitting}
            />
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
          <Button onClick={handleSave} disabled={submitting}>
            {submitting ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                {isEditMode ? "保存中..." : "创建中..."}
              </>
            ) : (
              isEditMode ? "保存更改" : "创建 App"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
