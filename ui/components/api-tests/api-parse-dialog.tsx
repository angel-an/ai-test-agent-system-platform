// NOTE: 统一 API 文档导入对话框（D1-D5）
// 三层结构：来源选择 → 输入方式 → 具体输入

"use client";

import * as React from "react";
import { useState } from "react";
import {
  Upload,
  FileCode,
  Loader2,
  CheckCircle2,
  FolderOpen,
  Link as LinkIcon,
  FileText,
  Globe,
  BookOpen,
  AlertCircle,
  RefreshCw,
  ChevronRight,
  Shield,
  Code,
  Type,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import {
  unifiedImportAPI,
  unifiedImportFileAPI,
  type ImportSourceType,
  type ImportInputMode,
  type UnifiedImportResult,
} from "@/lib/api/api-endpoints";

interface APIParseDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectIdentifier: string;
  parentFolderId?: string | null;
  parentFolderName?: string | null;
  onSuccess?: () => void;
}

// ============================================================================
// 类型定义
// ============================================================================

type SourceCategory = "swagger" | "apifox" | "manual";
type Step = "input" | "parsing" | "preview" | "success" | "error";

interface ImportState {
  // 第一层：来源类别
  sourceCategory: SourceCategory;
  // 第二层：输入方式
  inputMode: ImportInputMode;
  // 第三层：具体输入
  url: string;
  selectedFile: File | null;
  textContent: string;
  // 状态
  step: Step;
  result: UnifiedImportResult | null;
  errorMessage: string;
  // 保留输入（失败后重试用）
  preservedInput: {
    url?: string;
    fileName?: string;
    text?: string;
  } | null;
}

// ============================================================================
// 常量配置
// ============================================================================

const SOURCE_CATEGORIES: {
  key: SourceCategory;
  label: string;
  icon: React.ReactNode;
  description: string;
  supportedModes: ImportInputMode[];
}[] = [
  {
    key: "swagger",
    label: "Swagger / OpenAPI",
    icon: <Globe className="h-4 w-4" />,
    description: "标准 OpenAPI 2.0/3.0/3.1 文档",
    supportedModes: ["url", "file"],
  },
  {
    key: "apifox",
    label: "Apifox",
    icon: <BookOpen className="h-4 w-4" />,
    description: "Apifox 导出或在线分享",
    supportedModes: ["url", "file"],
  },
  {
    key: "manual",
    label: "手工输入",
    icon: <Type className="h-4 w-4" />,
    description: "TXT / DOCX / curl 粘贴",
    supportedModes: ["text", "file"],
  },
];

const ACCEPT_FILE_TYPES = ".json,.yaml,.yml,.txt,.docx";

// 根据来源类别和输入方式确定 source_type
function resolveSourceType(
  category: SourceCategory,
  mode: ImportInputMode
): ImportSourceType {
  const map: Record<string, ImportSourceType> = {
    "swagger:url": "swagger_online",
    "swagger:file": "swagger_offline",
    "apifox:url": "apifox_online",
    "apifox:file": "apifox_offline",
    "manual:text": "manual_text",
    "manual:file": "manual_file",
  };
  return map[`${category}:${mode}`] || "swagger_online";
}

// ============================================================================
// 组件
// ============================================================================

export function APIParseDialog({
  open,
  onOpenChange,
  projectIdentifier,
  parentFolderId,
  parentFolderName,
  onSuccess,
}: APIParseDialogProps) {
  const [state, setState] = useState<ImportState>({
    sourceCategory: "swagger",
    inputMode: "url",
    url: "",
    selectedFile: null,
    textContent: "",
    step: "input",
    result: null,
    errorMessage: "",
    preservedInput: null,
  });

  // 重置表单
  const resetForm = () => {
    setState({
      sourceCategory: "swagger",
      inputMode: "url",
      url: "",
      selectedFile: null,
      textContent: "",
      step: "input",
      result: null,
      errorMessage: "",
      preservedInput: null,
    });
  };

  const handleOpenChange = (open: boolean) => {
    if (!open) resetForm();
    onOpenChange(open);
  };

  // 选择来源类别
  const handleSelectCategory = (category: SourceCategory) => {
    const categoryConfig = SOURCE_CATEGORIES.find((c) => c.key === category);
    setState((prev) => ({
      ...prev,
      sourceCategory: category,
      inputMode: categoryConfig?.supportedModes[0] || "url",
      // 清空之前输入
      url: "",
      selectedFile: null,
      textContent: "",
    }));
  };

  // 选择文件
  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (file.size > 15 * 1024 * 1024) {
      toast.error("文件大小不能超过 15MB");
      return;
    }

    // 检查扩展名
    const ext = file.name.toLowerCase().split(".").pop() || "";
    const allowedExts = ["json", "yaml", "yml", "txt", "docx"];
    if (!allowedExts.includes(ext)) {
      toast.error(`不支持的文件类型 .${ext}，请上传 ${ACCEPT_FILE_TYPES} 文件`);
      return;
    }

    setState((prev) => ({ ...prev, selectedFile: file }));
  };

  // 获取输入验证状态
  const getValidationError = (): string | null => {
    if (state.inputMode === "url" && !state.url.trim()) {
      return "请输入 URL 地址";
    }
    if (state.inputMode === "file" && !state.selectedFile) {
      return "请选择文件";
    }
    if (state.inputMode === "text" && !state.textContent.trim()) {
      return "请输入文本内容";
    }
    return null;
  };

  // 执行导入
  const handleImport = async () => {
    const validationError = getValidationError();
    if (validationError) {
      toast.error(validationError);
      return;
    }

    const sourceType = resolveSourceType(state.sourceCategory, state.inputMode);

    // 保留输入（用于重试）
    const preserved = {
      url: state.inputMode === "url" ? state.url : undefined,
      fileName: state.inputMode === "file" ? state.selectedFile?.name : undefined,
      text: state.inputMode === "text" ? state.textContent : undefined,
    };

    setState((prev) => ({
      ...prev,
      step: "parsing",
      preservedInput: preserved,
    }));

    try {
      let result: UnifiedImportResult;

      // 根据输入方式调用对应 API
      if (state.inputMode === "file") {
        // 文件上传：使用 apiClient.form 继承统一超时和错误处理
        const formData = new FormData();
        formData.append("project_identifier", projectIdentifier);
        formData.append("source_type", sourceType);
        if (parentFolderId) formData.append("parent_folder_id", parentFolderId);
        formData.append("create_structure", "true");
        formData.append("file", state.selectedFile!);

        result = await unifiedImportFileAPI(formData);
      } else {
        // URL / 文本：使用 JSON 接口
        const requestBody: Record<string, unknown> = {
          project_identifier: projectIdentifier,
          parent_folder_id: parentFolderId ?? null,
          source_type: sourceType,
          create_structure: true,
        };

        if (state.inputMode === "url") {
          requestBody.url = state.url.trim();
        } else if (state.inputMode === "text") {
          requestBody.text_content = state.textContent;
        }

        result = await unifiedImportAPI(requestBody as any);
      }

      setState((prev) => ({
        ...prev,
        step: result.errors.length > 0 ? "error" : "success",
        result,
        errorMessage: result.errors[0] || "",
      }));

      if (!result.errors.length) {
        onSuccess?.();
        toast.success("API 文档导入成功！");
      }
    } catch (error: any) {
      console.error("导入失败:", error);
      const msg = error.message || "导入失败，请检查输入内容或稍后重试";
      setState((prev) => ({
        ...prev,
        step: "error",
        errorMessage: msg,
      }));
      toast.error(msg);
    }
  };

  // 重试
  const handleRetry = () => {
    setState((prev) => ({
      ...prev,
      step: "input",
      errorMessage: "",
      // 恢复保留的输入
      url: prev.preservedInput?.url || prev.url,
      textContent: prev.preservedInput?.text || prev.textContent,
      // 文件无法恢复，需要重新选择
      selectedFile: null,
    }));
  };

  // 当前类别配置
  const currentCategory = SOURCE_CATEGORIES.find(
    (c) => c.key === state.sourceCategory
  );

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-xl">
            <FileCode className="h-5 w-5 text-primary" />
            {state.step === "input" && "导入 API 文档"}
            {state.step === "parsing" && "正在解析..."}
            {state.step === "success" && "导入成功！"}
            {state.step === "error" && "导入失败"}
          </DialogTitle>
          <DialogDescription className="text-base">
            {state.step === "input" &&
              "支持 Swagger/OpenAPI、Apifox 和手工输入三种方式导入"}
            {state.step === "parsing" && "正在解析文档并创建文件夹结构，请稍候..."}
            {state.step === "success" && "文档解析成功，文件夹结构已创建！"}
            {state.step === "error" && state.errorMessage}
          </DialogDescription>
        </DialogHeader>

        {/* ========== 输入步骤 ========== */}
        {state.step === "input" && (
          <div className="space-y-6 py-4">
            {/* 目标文件夹提示 */}
            <div className="flex items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2 text-sm">
              <FolderOpen className="h-4 w-4 shrink-0 text-primary" />
              {parentFolderId ? (
                <span>
                  将解析到文件夹：
                  <span className="font-medium text-foreground">
                    {parentFolderName || parentFolderId}
                  </span>
                </span>
              ) : (
                <span className="text-muted-foreground">
                  未选中文件夹，将解析到项目根目录（全部接口）
                </span>
              )}
            </div>

            {/* 第一层：来源类别选择 */}
            <div className="space-y-3">
              <Label className="text-sm font-medium">1. 选择文档来源</Label>
              <div className="grid grid-cols-3 gap-3">
                {SOURCE_CATEGORIES.map((cat) => (
                  <button
                    key={cat.key}
                    type="button"
                    onClick={() => handleSelectCategory(cat.key)}
                    className={`flex flex-col items-center gap-2 rounded-lg border p-4 text-sm transition-all ${
                      state.sourceCategory === cat.key
                        ? "border-primary bg-primary/5 text-primary"
                        : "border-border hover:border-primary/50 hover:bg-muted/50"
                    }`}
                  >
                    {cat.icon}
                    <span className="font-medium">{cat.label}</span>
                    <span className="text-xs text-muted-foreground">
                      {cat.description}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {/* 第二层：输入方式选择 */}
            <div className="space-y-3">
              <Label className="text-sm font-medium">2. 选择输入方式</Label>
              <div className="flex gap-3">
                {currentCategory?.supportedModes.map((mode) => (
                  <Button
                    key={mode}
                    type="button"
                    variant={state.inputMode === mode ? "default" : "outline"}
                    onClick={() =>
                      setState((prev) => ({ ...prev, inputMode: mode }))
                    }
                    className="flex-1 gap-2"
                  >
                    {mode === "url" && <LinkIcon className="h-4 w-4" />}
                    {mode === "file" && <Upload className="h-4 w-4" />}
                    {mode === "text" && <FileText className="h-4 w-4" />}
                    {mode === "url" && "URL 地址"}
                    {mode === "file" && "上传文件"}
                    {mode === "text" && "粘贴文本"}
                  </Button>
                ))}
              </div>
            </div>

            {/* 第三层：具体输入 */}
            <div className="space-y-3">
              <Label className="text-sm font-medium">3. 输入文档内容</Label>

              {/* URL 输入 */}
              {state.inputMode === "url" && (
                <div className="space-y-2">
                  <Input
                    value={state.url}
                    onChange={(e) =>
                      setState((prev) => ({ ...prev, url: e.target.value }))
                    }
                    placeholder={
                      state.sourceCategory === "apifox"
                        ? "https://app.apifox.com/...（需为 OpenAPI 格式）"
                        : "https://api.example.com/openapi.json"
                    }
                  />
                  <p className="text-xs text-muted-foreground">
                    {state.sourceCategory === "apifox" ? (
                      <>
                        <AlertCircle className="inline h-3 w-3 mr-1" />
                        Apifox 需先导出为 OpenAPI/Swagger 格式，或分享为在线 OpenAPI 链接
                      </>
                    ) : (
                      "支持公开的 OpenAPI/Swagger JSON 或 YAML URL"
                    )}
                  </p>
                </div>
              )}

              {/* 文件上传 */}
              {state.inputMode === "file" && (
                <div className="space-y-2">
                  <div className="border-2 border-dashed rounded-lg p-8 text-center hover:border-primary transition-colors">
                    <input
                      type="file"
                      id="importFile"
                      accept={ACCEPT_FILE_TYPES}
                      className="hidden"
                      onChange={handleFileSelect}
                    />
                    <label htmlFor="importFile" className="cursor-pointer">
                      <div className="flex flex-col items-center gap-3">
                        {state.selectedFile ? (
                          <>
                            <FileCode className="h-12 w-12 text-primary" />
                            <div>
                              <p className="text-sm font-medium">
                                {state.selectedFile.name}
                              </p>
                              <p className="text-xs text-muted-foreground mt-1">
                                {(state.selectedFile.size / 1024).toFixed(2)} KB
                              </p>
                            </div>
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={(e) => {
                                e.preventDefault();
                                setState((prev) => ({
                                  ...prev,
                                  selectedFile: null,
                                }));
                              }}
                            >
                              重新选择
                            </Button>
                          </>
                        ) : (
                          <>
                            <Upload className="h-12 w-12 text-muted-foreground" />
                            <div>
                              <p className="text-sm font-medium">点击选择文件</p>
                              <p className="text-xs text-muted-foreground mt-1">
                                支持 JSON / YAML / TXT / DOCX，最大 15MB
                              </p>
                            </div>
                          </>
                        )}
                      </div>
                    </label>
                  </div>
                </div>
              )}

              {/* 文本粘贴 */}
              {state.inputMode === "text" && (
                <div className="space-y-2">
                  <Textarea
                    value={state.textContent}
                    onChange={(e) =>
                      setState((prev) => ({
                        ...prev,
                        textContent: e.target.value,
                      }))
                    }
                    placeholder={`-- 用户管理
GET /api/v1/users
请求参数：page, size

POST /api/v1/users
请求体：{"name": "张三"}

或粘贴 curl 命令...`}
                    rows={8}
                    className="font-mono text-sm"
                  />
                  <p className="text-xs text-muted-foreground">
                    支持 TXT 格式 API 文档、curl 命令粘贴
                  </p>
                </div>
              )}
            </div>

            {/* 格式说明 */}
            <div className="rounded-lg border p-4 text-sm space-y-2">
              <p className="font-medium flex items-center gap-2">
                <Code className="h-4 w-4" />
                支持的格式
              </p>
              <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                <div className="flex items-center gap-1">
                  <Badge variant="outline" className="text-[10px]">JSON</Badge>
                  OpenAPI 2.0/3.0/3.1
                </div>
                <div className="flex items-center gap-1">
                  <Badge variant="outline" className="text-[10px]">YAML</Badge>
                  OpenAPI 3.0/3.1
                </div>
                <div className="flex items-center gap-1">
                  <Badge variant="outline" className="text-[10px]">TXT</Badge>
                  API 文档文本
                </div>
                <div className="flex items-center gap-1">
                  <Badge variant="outline" className="text-[10px]">DOCX</Badge>
                  Word 文档
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ========== 解析中 ========== */}
        {state.step === "parsing" && (
          <div className="flex flex-col items-center justify-center py-16 space-y-6">
            <div className="relative">
              <div className="absolute inset-0 bg-primary/20 rounded-full animate-ping" />
              <Loader2 className="relative h-16 w-16 animate-spin text-primary" />
            </div>
            <div className="text-center space-y-2">
              <h3 className="text-lg font-semibold">正在解析文档...</h3>
              <p className="text-sm text-muted-foreground">
                {state.inputMode === "url" && "从远程地址获取文档内容..."}
                {state.inputMode === "file" && "读取文件并解析..."}
                {state.inputMode === "text" && "解析文本内容..."}
              </p>
            </div>
          </div>
        )}

        {/* ========== 成功结果 ========== */}
        {state.step === "success" && state.result && (
          <div className="space-y-6 py-4">
            {/* 成功图标 */}
            <div className="flex justify-center">
              <div className="rounded-full bg-green-100 p-4">
                <CheckCircle2 className="h-12 w-12 text-green-600" />
              </div>
            </div>

            {/* 成功消息 */}
            <div className="text-center space-y-2">
              <h3 className="text-lg font-semibold">
                {state.result.schema_title || "API 文档"} 导入成功
              </h3>
              <p className="text-sm text-muted-foreground">
                共识别 {state.result.total_endpoints} 个接口，
                {state.result.total_tags} 个分组
              </p>
            </div>

            {/* 统计信息 */}
            <div className="grid grid-cols-3 gap-4">
              <div className="rounded-lg border p-4 text-center">
                <div className="text-2xl font-bold text-primary">
                  {state.result.total_tags}
                </div>
                <div className="text-xs text-muted-foreground mt-1">标签分组</div>
              </div>
              <div className="rounded-lg border p-4 text-center">
                <div className="text-2xl font-bold text-primary">
                  {state.result.total_endpoints}
                </div>
                <div className="text-xs text-muted-foreground mt-1">接口总数</div>
              </div>
              <div className="rounded-lg border p-4 text-center">
                <div className="text-2xl font-bold text-green-600">
                  {state.result.tag_folders.length}
                </div>
                <div className="text-xs text-muted-foreground mt-1">已创建文件夹</div>
              </div>
            </div>

            {/* 预览摘要 */}
            {state.result.preview && (
              <div className="rounded-lg border p-4 space-y-3">
                <p className="font-medium text-sm">接口概览</p>
                {/* 方法统计 */}
                {Object.keys(state.result.preview.method_summary).length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(state.result.preview.method_summary).map(
                      ([method, count]) => (
                        <Badge key={method} variant="secondary" className="text-xs">
                          {method}: {count}
                        </Badge>
                      )
                    )}
                  </div>
                )}
                {/* 路径前缀 */}
                {state.result.preview.path_prefixes.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {state.result.preview.path_prefixes.map((prefix) => (
                      <Badge
                        key={prefix}
                        variant="outline"
                        className="text-[10px]"
                      >
                        {prefix}
                      </Badge>
                    ))}
                  </div>
                )}
                {/* 告警 */}
                {state.result.warnings.length > 0 && (
                  <div className="space-y-1">
                    {state.result.warnings.map((w, i) => (
                      <p key={i} className="text-xs text-amber-600 flex items-center gap-1">
                        <AlertCircle className="h-3 w-3" />
                        {w}
                      </p>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* 文件夹列表 */}
            {state.result.tag_folders.length > 0 && (
              <div className="space-y-2">
                <Label className="text-sm font-medium">已创建的文件夹：</Label>
                <div className="rounded-lg border p-4 space-y-2 max-h-48 overflow-y-auto">
                  {state.result.tag_folders.map((folder, index) => (
                    <div
                      key={index}
                      className="flex items-center gap-2 text-sm"
                    >
                      <FolderOpen className="h-4 w-4 text-primary" />
                      <span className="font-medium">{folder.folder_name}</span>
                      <span className="text-muted-foreground">
                        ({folder.endpoint_count} 个接口)
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ========== 错误状态 ========== */}
        {state.step === "error" && (
          <div className="flex flex-col items-center justify-center py-12 space-y-4">
            <div className="rounded-full bg-red-100 p-4">
              <AlertCircle className="h-12 w-12 text-red-600" />
            </div>
            <div className="text-center space-y-2">
              <h3 className="text-lg font-semibold">导入失败</h3>
              <p className="text-sm text-muted-foreground max-w-md">
                {state.errorMessage || "未知错误，请检查输入内容或稍后重试"}
              </p>
            </div>
            <Button variant="outline" onClick={handleRetry} className="gap-2">
              <RefreshCw className="h-4 w-4" />
              重试
            </Button>
          </div>
        )}

        {/* ========== 底部按钮 ========== */}
        <DialogFooter className="gap-2">
          {state.step === "input" && (
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                取消
              </Button>
              <Button
                onClick={handleImport}
                disabled={!!getValidationError()}
              >
                <FileCode className="mr-2 h-4 w-4" />
                开始导入
              </Button>
            </>
          )}

          {state.step === "success" && (
            <>
              <Button variant="outline" onClick={resetForm}>
                再导入一个
              </Button>
              <Button onClick={() => onOpenChange(false)}>
                <CheckCircle2 className="mr-2 h-4 w-4" />
                完成，查看接口
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
