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
import {
  discoverSpecAPI,
  type SpecDiscoveryResult,
} from "@/lib/api/spec-discovery";

// ============================================================================
// 引导内容配置
// ============================================================================

interface GuideStep {
  title: string;
  description: string;
}

interface SourceGuide {
  urlPlaceholder: string;
  urlHint: React.ReactNode;
  fileHint: string;
  steps?: GuideStep[];
}

const SOURCE_GUIDES: Record<SourceCategory, SourceGuide> = {
  swagger: {
  urlPlaceholder: "https://api.example.com/openapi.json 或 https://api.example.com/doc.html",
    urlHint: "支持 OpenAPI JSON/YAML URL，也支持 Swagger UI / Knife4j 页面地址（将自动发现文档地址）",
    fileHint: "支持 JSON / YAML，最大 15MB",
  },
  apifox: {
    urlPlaceholder: "https://api.apifox.com/api/v1/share/xxx",
    urlHint: (
      <>
        <AlertCircle className="inline h-3 w-3 mr-1" />
        Apifox 需先导出为 OpenAPI/Swagger 格式，或分享为在线 OpenAPI 链接
      </>
    ),
    fileHint: "从 Apifox 导出 OpenAPI 3.0 JSON/YAML 后上传",
    steps: [
      {
        title: "在 Apifox 中导出",
        description: "项目设置 → 导出 → 选择 OpenAPI 3.0 (JSON 或 YAML)",
      },
      {
        title: "或分享在线链接",
        description: "项目 → 分享 → 开启在线链接 → 复制 OpenAPI 格式链接",
      },
      {
        title: "粘贴到此处",
        description: "上传导出的文件，或粘贴在线分享链接",
      },
    ],
  },
  manual: {
    urlPlaceholder: "",
    urlHint: "",
    fileHint: "支持 TXT / DOCX，最大 15MB",
  },
};

// URL 示例对照
const URL_EXAMPLES = [
  {
    label: "OpenAPI JSON / Swagger UI",
    correct: "https://petstore.swagger.io/v2/swagger.json",
    incorrect: "https://petstore.swagger.io/ (Swagger UI 页面 — 也支持自动发现)",
  },
  {
    label: "Knife4j 页面",
    correct: "https://api.example.com/doc.html (将自动发现 /v3/api-docs)",
    incorrect: "",
  },
  {
    label: "Apifox 分享",
    correct: "https://api.apifox.com/api/v1/share/xxx/openapi.json",
    incorrect: "https://app.apifox.com/main/project/xxx (项目页面)",
  },
];

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
type Step = "input" | "discovering" | "parsing" | "preview" | "success" | "error";

interface ImportState {
  // 第一层：来源类别
  sourceCategory: SourceCategory;
  // 第二层：输入方式
  inputMode: ImportInputMode;
  // 第三层：具体输入
  url: string;
  selectedFile: File | null;
  textContent: string;
  // 认证 Header
  authHeader: string;
  // 状态
  step: Step;
  result: UnifiedImportResult | null;
  errorMessage: string;
  // 自动发现结果
  discoveryResult: SpecDiscoveryResult | null;
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
    authHeader: "",
    step: "input",
    result: null,
    errorMessage: "",
    discoveryResult: null,
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
      authHeader: "",
      step: "input",
      result: null,
      errorMessage: "",
      discoveryResult: null,
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
      authHeader: "",
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

  // 执行导入（支持自动发现）
  const handleImport = async () => {
    const validationError = getValidationError();
    if (validationError) {
      toast.error(validationError);
      return;
    }

    // 保留输入（用于重试）
    const preserved = {
      url: state.inputMode === "url" ? state.url : undefined,
      fileName: state.inputMode === "file" ? state.selectedFile?.name : undefined,
      text: state.inputMode === "text" ? state.textContent : undefined,
    };

    // URL 模式下，先尝试自动发现
    if (state.inputMode === "url" && state.sourceCategory === "swagger") {
      setState((prev) => ({
        ...prev,
        step: "discovering",
        preservedInput: preserved,
      }));

      try {
        const discoveryResult = await discoverSpecAPI({
          url: state.url.trim(),
        });

        // 如果是直接 JSON/YAML，直接进入导入流程
        if (discoveryResult.source_type === "direct_json" || discoveryResult.source_type === "direct_yaml") {
          // 直接导入
          await doImport(state.url.trim(), preserved);
          return;
        }

        // 如果发现到 Spec 地址
        if (discoveryResult.spec_url) {
          setState((prev) => ({
            ...prev,
            discoveryResult,
            step: "preview",
          }));
          return;
        }

        // 没有发现到 Spec 地址
        if (discoveryResult.message) {
          setState((prev) => ({
            ...prev,
            discoveryResult,
            step: "error",
            errorMessage: discoveryResult.message!,
          }));
          return;
        }

        // 其他情况，直接尝试导入原 URL
        await doImport(state.url.trim(), preserved);
      } catch (error: any) {
        console.error("发现失败:", error);
        // 发现失败，直接尝试导入原 URL
        await doImport(state.url.trim(), preserved);
      }
      return;
    }

    // 非 URL 模式或 Apifox/manual，直接导入
    await doImport(state.url.trim(), preserved);
  };

  // 使用发现的 Spec URL 导入
  const handleImportWithDiscoveredUrl = async (specUrl: string) => {
    await doImport(specUrl, state.preservedInput);
  };

  // 实际导入逻辑
  const doImport = async (url: string, preserved: any) => {
    const sourceType = resolveSourceType(state.sourceCategory, state.inputMode);

    setState((prev) => ({
      ...prev,
      step: "parsing",
      preservedInput: preserved,
    }));

    try {
      let result: UnifiedImportResult;

      if (state.inputMode === "file") {
        const formData = new FormData();
        formData.append("project_identifier", projectIdentifier);
        formData.append("source_type", sourceType);
        if (parentFolderId) formData.append("parent_folder_id", parentFolderId);
        formData.append("create_structure", "true");
        formData.append("file", state.selectedFile!);

        result = await unifiedImportFileAPI(formData);
      } else {
        const requestBody: Record<string, unknown> = {
          project_identifier: projectIdentifier,
          parent_folder_id: parentFolderId ?? null,
          source_type: sourceType,
          create_structure: true,
        };

        if (state.inputMode === "url") {
          requestBody.url = url;
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
      discoveryResult: null,
      // 恢复保留的输入
      url: prev.preservedInput?.url || prev.url,
      textContent: prev.preservedInput?.text || prev.textContent,
      // 文件无法恢复，需要重新选择
      selectedFile: null,
    }));
  };

  // 当前类别配置和引导
  const currentCategory = SOURCE_CATEGORIES.find(
    (c) => c.key === state.sourceCategory
  );
  const guide = SOURCE_GUIDES[state.sourceCategory];

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
            {state.step === "input" && "支持 Swagger/OpenAPI、Apifox 和手工输入三种方式导入"}
            {state.step === "discovering" && "正在自动发现 OpenAPI 文档地址，请稍候..."}
            {state.step === "parsing" && "正在解析文档并创建文件夹结构，请稍候..."}
            {state.step === "preview" && "发现到以下文档地址，请确认后导入"}
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
                <div className="space-y-3">
                  <Input
                    value={state.url}
                    onChange={(e) =>
                      setState((prev) => ({ ...prev, url: e.target.value }))
                    }
                    placeholder={guide.urlPlaceholder}
                  />
                  <p className="text-xs text-muted-foreground">
                    {guide.urlHint}
                  </p>

                  {/* Apifox 导出步骤引导 */}
                  {state.sourceCategory === "apifox" && guide.steps && (
                    <div className="rounded-lg bg-blue-50 p-3 space-y-2">
                      <p className="text-xs font-medium text-blue-700 flex items-center gap-1">
                        <BookOpen className="h-3 w-3" />
                        Apifox 导出指引
                      </p>
                      <ol className="space-y-1.5">
                        {guide.steps.map((step, i) => (
                          <li key={i} className="flex gap-2 text-xs text-blue-600">
                            <span className="shrink-0 w-4 h-4 rounded-full bg-blue-200 text-blue-700 flex items-center justify-center text-[10px] font-medium">
                              {i + 1}
                            </span>
                            <div>
                              <span className="font-medium">{step.title}</span>
                              <span className="text-blue-500"> — {step.description}</span>
                            </div>
                          </li>
                        ))}
                      </ol>
                    </div>
                  )}

                  {/* URL 示例对照 */}
                  {state.sourceCategory !== "manual" && (
                    <div className="rounded-lg border p-3 space-y-2">
                      <p className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                        <LinkIcon className="h-3 w-3" />
                        URL 示例对照
                      </p>
                      <div className="space-y-1.5">
                        {URL_EXAMPLES.map((ex) => (
                          <div key={ex.label} className="text-xs space-y-0.5">
                            <div className="flex items-center gap-1.5">
                              <span className="text-green-600">✓</span>
                              <code className="text-green-700 bg-green-50 px-1 rounded">{ex.correct}</code>
                            </div>
                            <div className="flex items-center gap-1.5">
                              <span className="text-red-500">✗</span>
                              <span className="text-muted-foreground">{ex.incorrect}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
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
                                {guide.fileHint}
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

        {/* ========== 发现中 ========== */}
        {state.step === "discovering" && (
          <div className="flex flex-col items-center justify-center py-16 space-y-6">
            <div className="relative">
              <div className="absolute inset-0 bg-primary/20 rounded-full animate-ping" />
              <Loader2 className="relative h-16 w-16 animate-spin text-primary" />
            </div>
            <div className="text-center space-y-2">
              <h3 className="text-lg font-semibold">正在发现文档地址...</h3>
              <p className="text-sm text-muted-foreground">
                正在分析页面并探测 OpenAPI/Swagger 文档接口
              </p>
            </div>
          </div>
        )}

        {/* ========== 发现结果预览 ========== */}
        {state.step === "preview" && state.discoveryResult && (
          <div className="space-y-6 py-4">
            {/* 发现结果图标 */}
            <div className="flex justify-center">
              <div className="rounded-full bg-blue-100 p-4">
                <Globe className="h-12 w-12 text-blue-600" />
              </div>
            </div>

            {/* 发现消息 */}
            <div className="text-center space-y-2">
              <h3 className="text-lg font-semibold">
                检测到 {state.discoveryResult.source_type === "swagger_ui" ? "Swagger UI" : state.discoveryResult.source_type === "knife4j" ? "Knife4j" : "UI"} 页面
              </h3>
              <p className="text-sm text-muted-foreground">
                已发现可访问的 OpenAPI 文档地址
              </p>
            </div>

            {/* 发现的 Spec 地址 */}
            {state.discoveryResult.spec_url && (
              <div className="rounded-lg border border-green-200 bg-green-50 p-4 space-y-3">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="h-5 w-5 text-green-600" />
                  <span className="font-medium text-green-800">发现有效的文档地址</span>
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between text-sm">
                    <span className="text-muted-foreground">文档地址：</span>
                    <code className="text-green-700 bg-green-100 px-2 py-0.5 rounded text-xs">
                      {state.discoveryResult.spec_url}
                    </code>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-muted-foreground">接口数量：</span>
                    <span className="font-medium">{state.discoveryResult.endpoint_count} 个</span>
                  </div>
                  {state.discoveryResult.version && (
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">版本：</span>
                      <span className="font-medium">{state.discoveryResult.version}</span>
                    </div>
                  )}
                </div>
                <Button
                  className="w-full"
                  onClick={() => handleImportWithDiscoveredUrl(state.discoveryResult!.spec_url!)}
                >
                  <CheckCircle2 className="mr-2 h-4 w-4" />
                  使用发现的地址导入
                </Button>
              </div>
            )}

            {/* 候选地址列表 */}
            {state.discoveryResult.candidates.length > 0 && (
              <div className="space-y-2">
                <Label className="text-sm font-medium">探测的候选地址：</Label>
                <div className="rounded-lg border p-3 space-y-2 max-h-48 overflow-y-auto">
                  {state.discoveryResult.candidates.map((candidate, index) => (
                    <div
                      key={index}
                      className={`flex items-center justify-between text-sm p-2 rounded ${
                        candidate.valid ? "bg-green-50" : "bg-red-50"
                      }`}
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        {candidate.valid ? (
                          <CheckCircle2 className="h-4 w-4 text-green-600 shrink-0" />
                        ) : (
                          <AlertCircle className="h-4 w-4 text-red-500 shrink-0" />
                        )}
                        <code className="text-xs truncate">{candidate.url}</code>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        {candidate.valid && (
                          <span className="text-xs text-green-600">
                            {candidate.endpoint_count} 个接口
                          </span>
                        )}
                        {!candidate.valid && candidate.error && (
                          <span className="text-xs text-red-500">{candidate.error}</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 手动输入选项 */}
            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={handleRetry}>
                <RefreshCw className="mr-2 h-4 w-4" />
                重新输入
              </Button>
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

          {state.step === "preview" && (
            <Button variant="outline" onClick={handleRetry}>
              <RefreshCw className="mr-2 h-4 w-4" />
              重新输入
            </Button>
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
