// NOTE  MC80OmFIVnBZMlhscm9ua3VMazZPR1E0Ymc9PToxMjJiYzk1ZA==

/**
 * 创建/编辑 Android App 对话框
 * 用于手工创建或编辑 Android App 测试对象
 */
"use client";
// NOTE  MS80OmFIVnBZMlhscm9ua3VMazZPR1E0Ymc9PToxMjJiYzk1ZA==

import * as React from "react";
import { useState } from "react";
import { Loader2, Smartphone } from "lucide-react";
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
import type { AndroidApp } from "@/lib/api/android-apps";
import { createAndroidApp, updateAndroidApp } from "@/lib/api/android-apps";
// NOTE  Mi80OmFIVnBZMlhscm9ua3VMazZPR1E0Ymc9PToxMjJiYzk1ZA==

interface CreateAndroidAppDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  folderId?: string | null;
  editingApp?: AndroidApp | null;
  onSuccess?: () => void;
}
// NOTE  My80OmFIVnBZMlhscm9ua3VMazZPR1E0Ymc9PToxMjJiYzk1ZA==

export function CreateAndroidAppDialog({
  open,
  onOpenChange,
  projectId,
  folderId,
  editingApp,
  onSuccess,
}: CreateAndroidAppDialogProps) {
  const [submitting, setSubmitting] = useState(false);

  // 判断是否为编辑模式
  const isEditMode = !!editingApp;

  // 表单数据
  const [displayName, setDisplayName] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [appPackage, setAppPackage] = useState("");
  const [mainActivity, setMainActivity] = useState("");
  const [version, setVersion] = useState("");
  const [deviceUdid, setDeviceUdid] = useState("");
  const [businessModule, setBusinessModule] = useState("");

  // 重置表单
  const resetForm = () => {
    setDisplayName("");
    setName("");
    setDescription("");
    setAppPackage("");
    setMainActivity("");
    setVersion("");
    setDeviceUdid("");
    setBusinessModule("");
  };

  // 当对话框打开或编辑 App 变化时，初始化表单
  React.useEffect(() => {
    if (open && editingApp) {
      // 编辑模式：填充现有数据
      setDisplayName(editingApp.display_name || "");
      setName(editingApp.name || "");
      setDescription(editingApp.description || "");
      setAppPackage(editingApp.app_package || "");
      setMainActivity(editingApp.main_activity || "");
      setVersion(editingApp.version || "");
      setDeviceUdid(editingApp.device_udid || "");
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
    console.log("[CreateAndroidApp] handleSave 被调用");
    // 验证必填字段
    if (!displayName.trim()) {
      toast.error("请输入显示名称");
      return;
    }
    if (!name.trim()) {
      toast.error("请输入英文名称");
      return;
    }
    if (!appPackage.trim()) {
      toast.error("请输入 App 包名");
      return;
    }

    // 验证英文名称格式（只允许字母、数字、连字符和下划线）
    const nameRegex = /^[a-zA-Z0-9-_]+$/;
    if (!nameRegex.test(name.trim())) {
      toast.error("英文名称只能包含字母、数字、连字符和下划线");
      return;
    }

    // 验证包名格式
    const packageRegex = /^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+[0-9a-z_]$/i;
    if (!packageRegex.test(appPackage.trim())) {
      toast.error("App 包名格式不正确，应为 com.example.app 格式");
      return;
    }

    try {
      setSubmitting(true);
      console.log("[CreateAndroidApp] submitting 已设置为 true");

      const requestBody: any = {
        display_name: displayName.trim(),
        name: name.trim(),
        app_package: appPackage.trim(),
      };

      // 添加所有非空的字段到请求中
      if (description.trim()) requestBody.description = description.trim();
      if (mainActivity.trim()) requestBody.main_activity = mainActivity.trim();
      if (version.trim()) requestBody.version = version.trim();
      if (deviceUdid.trim()) requestBody.device_udid = deviceUdid.trim();
      requestBody.business_module = businessModule.trim();
      if (folderId) requestBody.folder_id = folderId;

      console.log("[CreateAndroidApp] 请求体:", requestBody);
      console.log("[CreateAndroidApp] projectId:", projectId, "isEditMode:", isEditMode);

      if (isEditMode && editingApp) {
        // 编辑模式：使用 PATCH 更新
        console.log("[CreateAndroidApp] 调用 updateAndroidApp");
        await updateAndroidApp(projectId, editingApp.id, requestBody);
        toast.success("Android App 更新成功");
      } else {
        // 创建模式：使用 POST 创建
        console.log("[CreateAndroidApp] 调用 createAndroidApp");
        await createAndroidApp(projectId, requestBody);
        toast.success("Android App 创建成功");
      }

      console.log("[CreateAndroidApp] API 调用成功");
      resetForm();
      onOpenChange(false);
      onSuccess?.();
    } catch (error: any) {
      console.error(isEditMode ? "更新 Android App 失败:" : "创建 Android App 失败:", error);
      toast.error(error.message || (isEditMode ? "更新 Android App 失败" : "创建 Android App 失败"));
    } finally {
      console.log("[CreateAndroidApp] finally 块执行，设置 submitting=false");
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[600px] max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Smartphone className="h-5 w-5 text-green-500" />
            {isEditMode ? "编辑 Android App" : "创建 Android App"}
          </DialogTitle>
          <DialogDescription>
            {isEditMode ? "编辑现有的 Android App 配置" : "手工创建 Android App 测试对象，填写 App 的完整信息"}
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
              placeholder="例如：懂车帝 App"
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
              placeholder="例如：dongchedi"
              disabled={submitting}
            />
            <p className="text-xs text-muted-foreground">
              App 的英文标识符，只能包含字母、数字、连字符和下划线
            </p>
          </div>

          {/* App 包名 */}
          <div className="space-y-2">
            <Label htmlFor="appPackage">
              <Smartphone className="inline h-4 w-4 mr-1" />
              App 包名 <span className="text-destructive">*</span>
            </Label>
            <Input
              id="appPackage"
              value={appPackage}
              onChange={(e) => setAppPackage(e.target.value)}
              placeholder="例如：com.dongchedi.app"
              disabled={submitting}
            />
            <p className="text-xs text-muted-foreground">
              Android App 的包名，如 com.example.app
            </p>
          </div>

          {/* 主 Activity */}
          <div className="space-y-2">
            <Label htmlFor="mainActivity">主 Activity</Label>
            <Input
              id="mainActivity"
              value={mainActivity}
              onChange={(e) => setMainActivity(e.target.value)}
              placeholder="例如：com.dongchedi.app/.MainActivity"
              disabled={submitting}
            />
            <p className="text-xs text-muted-foreground">
              App 的主入口 Activity，用于启动测试
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
              指定测试设备的序列号，如果不填将使用第一个已连接设备
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
