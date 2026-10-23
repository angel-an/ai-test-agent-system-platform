/**
 * andan
 */
// TODO  MC80OmFIVnBZMlhscm9ua3VMazZSVnBXWmc9PTo2NWRlN2UwMw==

"use client";
// NOTE  MS80OmFIVnBZMlhscm9ua3VMazZSVnBXWmc9PTo2NWRlN2UwMw==

import * as React from "react";
import { Upload, FileText, FileCode2, Image as ImageIcon, Loader2, X, Sparkles, PlayCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";
import {
  createMinimalAPILoop,
  createMinimalWebLoop,
  generateTestCasesFromDocument,
  parseAPIDocFile,
  type WebCompileReceipt,
} from "@/lib/api/ai";
import {
  analyzeRequirement,
  type RequirementAnalysisData,
} from "@/lib/api/requirement-quality";
import { RequirementQualityPanel } from "@/components/test-cases/requirement-quality-panel";
import { executeTestRun } from "@/lib/api/testRuns";
import { getKnowledgeSpaces } from "@/lib/api/knowledge-base";
import type { TestCaseInfo, TestCaseTemplate, KnowledgeSpaceInfo } from "@/lib/api/types";
import { cn } from "@/lib/utils";

interface AIGenerateFromDocumentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  folderId?: string | null;
  onSuccess?: (
    testCases: TestCaseInfo[],
    extra?: {
      assertionGapWarning?: string | null;
      /** 模糊词告警（rev183）：文档里「适当/尽量/多种」这类无阈值写法，不自动替换只点名 */
      vagueWordWarning?: string | null;
      /** 模糊词命中明细 */
      vagueWordHits?: Array<{
        word: string;
        point: string;
        clause: string;
        reason: string;
        suggestion: string;
      }> | null;
      /** 生成来源文件（成果物面板标题用） */
      sourceFileName?: string | null;
      /** 本批次 Web 冒烟编译回执（成果物面板警示用） */
      compileReceiptText?: string | null;
      webLoopError?: string | null;
    }
  ) => void;
  onOpenChat?: (params: {
    prompt: string;
    attachments?: Array<{
      data: string;
      mimeType: string;
      metadata: { filename: string; size: number };
    }>;
    knowledgeSpaceId?: string;
  }) => void;
}
// eslint-disable  Mi80OmFIVnBZMlhscm9ua3VMazZSVnBXWmc9PTo2NWRlN2UwMw==

/** 编译回执（A2/rev97）→ 持久警告文本；无异常时返回 null。
 *  回执 warnings 是后端写好的中文长句（含 [zero_executable_checks] 等机读标记），直接逐行展示。 */
function formatCompileReceipt(receipt: WebCompileReceipt | null | undefined): string | null {
  if (!receipt) return null;
  const warns = receipt.warnings ?? [];
  const summary =
    `可执行检查 ${receipt.form_executable_checks ?? 0} 条` +
    ` · 负路径场景 ${receipt.negative_scenario_count ?? 0} 个` +
    ` · 跳过断言 ${receipt.skipped_assertion_count ?? 0} 条` +
    ` · 依赖定位断言 ${receipt.locator_depended_assertion_count ?? receipt.pending_locator_count ?? 0} 条`;
  if (!warns.length && receipt.executable !== false) return null;
  return [summary, ...warns].join("\n");
}

export function AIGenerateFromDocumentDialog({
  open,
  onOpenChange,
  projectId,
  folderId,
  onSuccess,
  onOpenChat,
}: AIGenerateFromDocumentDialogProps) {
  // 开发环境自动填充放行凭证（仅本地，不写入浏览器存储）
  const devOverrideToken = process.env.NEXT_PUBLIC_REQUIREMENT_GATE_OVERRIDE_TOKEN || "";

  const [file, setFile] = React.useState<File | null>(null);
  const [apiDocFile, setApiDocFile] = React.useState<File | null>(null);
  const [additionalPrompt, setAdditionalPrompt] = React.useState("");
  const [template, setTemplate] = React.useState<TestCaseTemplate>("test_case");
  const [outputType, setOutputType] = React.useState<"requirement_analysis" | "test_case">(
    "requirement_analysis"
  );
  const [testMode, setTestMode] = React.useState<"sit" | "uat" | "smoke" | "api">("sit");
  const [generating, setGenerating] = React.useState(false);
  const [dragActive, setDragActive] = React.useState(false);
  const [createTestRun, setCreateTestRun] = React.useState(true);
  const [apiDataProfile, setApiDataProfile] = React.useState("DEFAULT");
  const [allowMutatingApiTests, setAllowMutatingApiTests] = React.useState(false);
  const [jiraLinks, setJiraLinks] = React.useState<string[]>([]);
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const apiDocInputRef = React.useRef<HTMLInputElement>(null);

  // 需求质量门控（阶段一）：文件变更后自动做一次质量判定，用于禁用生成按钮与展示阻断原因
  const [quality, setQuality] = React.useState<RequirementAnalysisData | null>(null);
  const [qualityLoading, setQualityLoading] = React.useState(false);
  const [qualityError, setQualityError] = React.useState<string | null>(null);
  // Web 冒烟资产/运行未创建时的原因：必须以**页面持久提示**呈现。
  // 原先只弹一条约 4 秒的 warning toast，采样稍慢就完全看不到失败痕迹（实测踩过）。
  const [webLoopError, setWebLoopError] = React.useState<string | null>(null);
  // 编译回执（A2/rev97）：零可执行检查/上传检查被关等「脚本在、覆盖无」的形态，
  // 必须持久提示——资产状态仍显示 completed，不提示就等于把骨架读成覆盖。
  const [webLoopWarning, setWebLoopWarning] = React.useState<string | null>(null);
  const [overrideToken, setOverrideToken] = React.useState(devOverrideToken);
  const [overrideReason, setOverrideReason] = React.useState("");
  const [showOverride, setShowOverride] = React.useState(false);
  const [overrideApplied, setOverrideApplied] = React.useState(false);
  // 生成会话标识：用于后端门控状态跟踪（同一对话框内保持稳定）
  const [sessionKey] = React.useState(
    () => `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
  );

  const qualityBlocked =
    quality !== null && quality.prd_quality.can_generate_testcases === false;
  // PDF/图片等二进制文件：前端无法预检，后端生成时才判定；此时也显示人工放行入口
  const isBinaryFile =
    file !== null &&
    !file.type.startsWith("text/") &&
    !file.name.toLowerCase().endsWith(".txt") &&
    !file.name.toLowerCase().endsWith(".md");
  const showOverrideSection = qualityBlocked || (isBinaryFile && outputType === "test_case");
  // 人工放行：勾选且填写了凭证和原因（≥10字符）后，允许生成
  const overrideReady =
    showOverride && overrideToken.trim().length > 0 && overrideReason.trim().length >= 10 &&
    new Set(Array.from(overrideReason.trim())).size > 1;
  // 需求分析报告模式下仍允许点击（只输出报告，不生成用例）
  const generateDisabled =
    generating ||
    !file ||
    (outputType === "test_case" && qualityBlocked && !overrideReady);

  // Knowledge space selection
  const [spaces, setSpaces] = React.useState<KnowledgeSpaceInfo[]>([]);
  const [selectedSpaceId, setSelectedSpaceId] = React.useState<string>("__none__");
  const [spacesLoading, setSpacesLoading] = React.useState(false);

  // Load knowledge spaces when dialog opens
  React.useEffect(() => {
    if (!open || !projectId) return;
    setSpacesLoading(true);
    getKnowledgeSpaces(projectId)
      .then((res) => {
        if (res.success) {
          setSpaces(res.data || []);
        }
      })
      .catch((err) => {
        console.error("Failed to load knowledge spaces:", err);
      })
      .finally(() => setSpacesLoading(false));
  }, [open, projectId]);

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFile(e.dataTransfer.files[0]);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      handleFile(e.target.files[0]);
    }
  };

  const handleFile = (selectedFile: File) => {
    // 验证文件类型
    const validTypes = [
      "image/jpeg",
      "image/png",
      "image/gif",
      "image/webp",
      "application/pdf",
      "application/msword",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "text/plain",
    ];

    if (!validTypes.includes(selectedFile.type)) {
      toast.error("不支持的文件类型，请上传图片、PDF 或文档文件");
      return;
    }

    // 验证文件大小（最大 15MB）
    if (selectedFile.size > 15 * 1024 * 1024) {
      toast.error("文件大小不能超过 15MB");
      return;
    }

    setFile(selectedFile);
  };

  const handleApiDocFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      handleApiDocFile(e.target.files[0]);
    }
  };

  const handleApiDocFile = (selectedFile: File) => {
    const fileName = selectedFile.name.toLowerCase();
    if (fileName.endsWith(".doc")) {
      toast.error("暂不支持旧版 .doc，请另存为 .docx 或导出 TXT 后上传");
      return;
    }

    const validTypes = [
      "text/plain",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ];
    const validExtensions = [".txt", ".md", ".docx"];

    if (
      !validTypes.includes(selectedFile.type) &&
      !validExtensions.some((ext) => fileName.endsWith(ext))
    ) {
      toast.error("接口文档暂支持 TXT、MD 或 DOCX 文件");
      return;
    }

    if (selectedFile.size > 15 * 1024 * 1024) {
      toast.error("接口文档大小不能超过 15MB");
      return;
    }

    setApiDocFile(selectedFile);
    setOutputType("test_case");
  };

  const resetForm = () => {
    setFile(null);
    setApiDocFile(null);
    setAdditionalPrompt("");
    setCreateTestRun(true);
    setApiDataProfile("DEFAULT");
    setAllowMutatingApiTests(false);
    setSelectedSpaceId("__none__");
    setQuality(null);
    setQualityError(null);
    setOverrideApplied(false);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
    if (apiDocInputRef.current) {
      apiDocInputRef.current.value = "";
    }
  };

  /**
   * 需求质量门控：文本类需求文档可直接在前端做一次结构化分析；
   * 用于展示缺失项/阻断原因并控制生成按钮；PDF/图片等二进制文档由后端在生成时判定。
   */
  const runQualityCheck = React.useCallback(
    async (target: File, prompt: string) => {
      const name = target.name.toLowerCase();
      const isText =
        target.type.startsWith("text/") ||
        name.endsWith(".txt") ||
        name.endsWith(".md");
      if (!isText) {
        setQuality(null);
        setQualityError(null);
        return;
      }

      try {
        setQualityLoading(true);
        setQualityError(null);
        const text = await target.text();
        const requirementText = prompt.trim()
          ? `${text}\n\n补充说明:\n${prompt.trim()}`
          : text;
        const response = await analyzeRequirement(projectId, {
          requirement_text: requirementText,
          source: target.name,
        });
        setQuality(response.data);
      } catch (error) {
        console.error("Failed to analyze requirement quality:", error);
        setQuality(null);
        setQualityError(
          error instanceof Error ? error.message : "需求质量校验失败"
        );
      } finally {
        setQualityLoading(false);
      }
    },
    [projectId]
  );

  React.useEffect(() => {
    if (!file || !open) {
      setQuality(null);
      setQualityError(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      if (!cancelled) {
        void runQualityCheck(file, additionalPrompt);
      }
    }, 400);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // additionalPrompt 变更由 debounce 合并，避免连续输入触发请求
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file, open, projectId, runQualityCheck]);

  const handleGenerate = async () => {
    if (!file) {
      toast.error("请先上传文件");
      return;
    }

    const shouldCreateApiLoop = Boolean(apiDocFile && outputType === "test_case");

    // 需求分析保留对话交互；测试用例直接落库，保证可进入执行闭环。
    if (onOpenChat && outputType === "requirement_analysis") {
      try {
        toast.info("正在准备文件...");

        // 读取文件为 base64
        const fileBuffer = await file.arrayBuffer();
        const base64Data = btoa(
          new Uint8Array(fileBuffer).reduce(
            (data, byte) => data + String.fromCharCode(byte),
            ""
          )
        );

        const fileType = file.type || "application/octet-stream";

        // 根据输出类型构造不同的提示词
        const modeLabels: Record<string, string> = {
          sit: "SIT全量功能测试",
          uat: "UAT业务场景验收",
          smoke: "冒烟测试",
          api: "API接口复合场景",
        };

        const chatPrompt =
          outputType === "requirement_analysis"
            ? `output_type=requirement_analysis

请仅基于上传的需求文档输出一份**需求分析报告（Markdown 格式）**，不要生成测试用例。

文件名：${file.name}
文件类型：${fileType}
文件大小：{(file.size / 1024).toFixed(2)} KB
${additionalPrompt.trim() ? `\n补充说明：\n${additionalPrompt.trim()}\n` : ""}
执行步骤：
1. 分析上传的文档内容
2. 加载 requirement_analyzer skill，按其报告结构生成 Markdown
3. 返回分析报告，并询问我是否需要进一步生成测试用例

⛔ 在我未明确确认前，不要生成测试用例。`
            : `output_type=test_case
test_mode=${testMode}

请帮我从文档中生成测试用例。

文件名：${file.name}
文件类型：${fileType}
文件大小：{(file.size / 1024).toFixed(2)} KB
模板类型：${template === "test_case" ? "标准测试用例" : template === "test_case_bdd" ? "BDD测试用例" : "其他"}
测试模式：${modeLabels[testMode]}
${folderId ? `目标文件夹ID：${folderId}` : ""}
${additionalPrompt.trim() ? `\n补充说明：\n${additionalPrompt.trim()}\n` : ""}

生成要求：
1. 每条用例必须包含 module（所属模块）字段，从需求文档的功能矩阵中提取
2. 每条用例必须包含 keyword（关键词）字段，值为：正向/反向/边界 之一
3. 每条用例必须包含 case_kind 字段，值为：${testMode}
4. 如有关联的测试点，请填写 tp_code（测试点编号）字段
5. 请根据用例重要性填写 risk_level（风险等级）字段：high/medium/low

请先分析文档内容，然后根据解析结果生成相应的测试用例。`;

        // 打开聊天对话框，并传递附件信息
        onOpenChat({
          prompt: chatPrompt,
          attachments: [{
            data: base64Data,
            mimeType: fileType,
            metadata: { filename: file.name, size: file.size }
          }],
          knowledgeSpaceId: selectedSpaceId === "__none__" ? undefined : selectedSpaceId,
        });
        resetForm();
        toast.success("文件已准备好，正在发送给AI分析...");
      } catch (error) {
        console.error("Failed to prepare document:", error);
        toast.error(error instanceof Error ? error.message : "文件处理失败");
      }
      return;
    }

    // 否则使用原来的 API 方式
    try {
      setGenerating(true);
      if (shouldCreateApiLoop) {
        toast.info("正在生成测试用例...");
      }
      // 前端已判定不满足测试设计条件时，不再发起生成请求（后端同样有硬门控兜底）
      // 例外：用户已勾选人工放行并填写凭证+原因时，允许提交由后端校验
      if (outputType === "test_case" && qualityBlocked && !overrideReady) {
        if (showOverride && overrideReason.trim().length < 10) {
          toast.warning("人工放行原因至少需要填写 10 个字符");
          return;
        }
        toast.warning(
          "需求质量不满足测试设计条件，已阻止生成；请先补齐缺失项（见需求质量面板）"
        );
        return;
      }

      const response = await generateTestCasesFromDocument(projectId, {
        file,
        folder_id: folderId,
        additional_prompt: additionalPrompt.trim() || undefined,
        template,
        test_mode: testMode,
        knowledge_space_id: selectedSpaceId === "__none__" ? undefined : selectedSpaceId,
        override_token: showOverride ? overrideToken.trim() || undefined : undefined,
        override_reason: showOverride ? overrideReason.trim() || undefined : undefined,
        // 门控与追踪字段（阶段一/二）
        output_type: outputType,
        requirement_id: sessionKey,
        session_key: sessionKey,
      });

      // ---- 需求质量门控结果回显（阶段一）----
      // 后端响应结构可能随版本变化，这里做防御性读取，避免缺少字段时抛运行时错误。
      const gated = response;
      const qualitySummary = gated?.prd_quality;
      const analysisReport = gated?.analysis_report;

      if (qualitySummary && analysisReport) {
        setQuality({
          prd_quality: qualitySummary,
          analysis_report: analysisReport as RequirementAnalysisData["analysis_report"],
          testcase_generation:
            gated?.testcase_generation ?? {
              status: "blocked",
              reasons: [],
              created_count: 0,
            },
          analysis_report_markdown: gated?.analysis_report_markdown,
        });
      }

      if (qualitySummary && qualitySummary.can_generate_testcases === false) {
        const reasons = analysisReport?.blocked_reasons || [];
        toast.warning(
          `需求质量判定为 ${qualitySummary.status_label ?? qualitySummary.status}，已暂停测试用例生成${
            reasons.length ? `：${reasons.slice(0, 2).join("；")}` : ""
          }`
        );
        return;
      }

      // 防御：后端未按契约返回 test_cases 时，给出明确提示而不是崩溃
      const generatedCases = Array.isArray(gated?.test_cases) ? gated.test_cases : [];

      if (response.success && generatedCases.length > 0) {
        if (showOverride && overrideToken) {
          setOverrideApplied(true);
        }
        if (apiDocFile && outputType === "test_case") {
          toast.info("正在解析接口文档并创建可执行闭环...");
          const apiDocResponse = await parseAPIDocFile(projectId, {
            file: apiDocFile,
            title: apiDocFile.name,
            create_structure: true,
          });
          const endpoints = apiDocResponse.data.created?.endpoints || [];
          const endpointIds = endpoints
            .map((endpoint) => endpoint.endpoint_id || endpoint.id)
            .filter((id): id is string => Boolean(id));

          if (endpointIds.length === 0) {
            throw new Error("接口文档已解析，但没有创建可关联的 API 端点");
          }

          const loopResponse = await createMinimalAPILoop(projectId, {
            endpoint_ids: endpointIds,
            folder_id: folderId,
            case_kind: testMode,
            base_url: apiDocResponse.data.draft?.base_url || undefined,
            create_test_run: createTestRun,
            execution_config: {
              data_profile: apiDataProfile,
              ...(allowMutatingApiTests ? { allow_mutating_api_tests: true } : {}),
            },
          });
          const testRunIdentifier = loopResponse.data.test_run?.identifier;
          let webLoopMessage = "";
          if (createTestRun && testRunIdentifier) {
            const generatedCaseIds = generatedCases
              .map((testCase) => testCase.id)
              .filter((id): id is string => Boolean(id));
            if (generatedCaseIds.length > 0) {
              toast.info("正在追加 Web 冒烟自动化...");
              const webLoopResponse = await createMinimalWebLoop(projectId, {
                test_case_ids: generatedCaseIds,
                folder_id: folderId,
                test_run_identifier: testRunIdentifier,
                // 刻意**不**下发 base_url：入口一律由项目「路径配置」解析
                // （后端 web_case_link_service 会写 web_path_base_url_source=template）。
                // 早期这里写死小杨生煎 UAT 入口，导致任何非小杨生煎项目都把执行指向小杨生煎。
                execution_config: {},
              });
              const webTestCount = webLoopResponse.data.web_tests.length;
              webLoopMessage = webTestCount > 0 ? `，追加 ${webTestCount} 条 Web 冒烟` : "";
              const receiptText1 = formatCompileReceipt(webLoopResponse.data.compile_receipt);
              setWebLoopWarning(receiptText1);
              if (receiptText1) {
                toast.warning("Web 冒烟编译回执需关注", {
                  description: receiptText1.split("\n")[0],
                });
              }
            }
          }
          let executionMessage = "";
          if (createTestRun && testRunIdentifier) {
            toast.info(`正在启动测试运行 ${testRunIdentifier}...`);
            await executeTestRun(projectId, testRunIdentifier);
            executionMessage = "，已启动执行";
          }
          toast.success(
            `已生成 ${generatedCases.length} 条需求用例，关联 ${endpointIds.length} 个接口${webLoopMessage}${
              testRunIdentifier ? `，创建测试运行 ${testRunIdentifier}${executionMessage}` : ""
            }`
          );
        } else if (outputType === "test_case" && createTestRun) {
          const generatedCaseIds = generatedCases
            .map((testCase) => testCase.id)
            .filter((id): id is string => Boolean(id));
          // Web 资产/运行未创建（如未绑定子功能、base_url 仍为占位地址）时，
          // 不影响"用例已生成"的事实——两者分开提示，避免误导。
          let webLoopMessage = "";
          let webLoopErrorText = "";
          try {
            const webLoopResponse = await createMinimalWebLoop(projectId, {
              test_case_ids: generatedCaseIds,
              folder_id: folderId,
              create_test_run: true,
              // 同上：不写死入口，交给项目「路径配置」；未配置时后端会返回带错误码的 400，
              // 由下方 setWebLoopError 持久展示（而不是 4 秒 toast 一闪而过）。
              execution_config: {},
            });
            const testRunIdentifier = webLoopResponse.data.test_run?.identifier;
            webLoopMessage = `，追加 ${webLoopResponse.data.web_tests.length} 条 Web 冒烟`;
            const receiptText2 = formatCompileReceipt(webLoopResponse.data.compile_receipt);
            setWebLoopWarning(receiptText2);
            if (receiptText2) {
              toast.warning("Web 冒烟编译回执需关注", {
                description: receiptText2.split("\n")[0],
              });
            }
            if (testRunIdentifier) {
              webLoopMessage += `，已启动测试运行 ${testRunIdentifier}`;
              toast.info(`正在启动 Web 测试运行 ${testRunIdentifier}...`);
              await executeTestRun(projectId, testRunIdentifier);
            }
          } catch (error) {
            webLoopErrorText = error instanceof Error ? error.message : String(error);
          }
          toast.success(`已生成 ${generatedCases.length} 条需求用例${webLoopMessage}`);
          if (webLoopErrorText) {
            // 持久提示 + toast 双通道：toast 易错过，页面提示可用于排查与复现
            setWebLoopError(webLoopErrorText);
            setWebLoopWarning(null);
            toast.warning(`Web 执行未创建：${webLoopErrorText}`);
          } else {
            setWebLoopError(null);
          }
        } else {
          toast.success(`成功生成 ${generatedCases.length} 个测试用例`);
        }
        // 断言缺口说明（生成期即点名）：哪些需求点提取不出可判定断言、
        // 对应用例将标记「需人工执行」、怎么补。
        // 本弹窗成功后会立即关闭，所以这些上下文一并交给父页面，
        // 由「生成成果物」面板承接展示（标题/断言/编译回执/为何需人工执行）。
        const assertionGapWarning = gated?.assertion_gap_warning ?? null;
        if (assertionGapWarning) {
          toast.warning("部分用例无法提取可判定断言，已标记「需人工执行」", {
            description: assertionGapWarning,
            duration: 15000,
          });
        }
        // 模糊词告警（rev183）：不自动替换（替换=凭空造阈值，跑绿也是假通过），
        // 只在生成期点名，把文档写法问题退回给写文档的人。
        const vagueWordWarning = gated?.vague_word_warning ?? null;
        const vagueWordHits = gated?.vague_word_hits ?? null;
        if (vagueWordWarning) {
          toast.warning(`需求文本有 ${vagueWordHits?.length ?? 0} 处模糊表述`, {
            description: vagueWordWarning,
            duration: 15000,
          });
        }
        onSuccess?.(generatedCases, {
          assertionGapWarning,
          vagueWordWarning,
          vagueWordHits,
          sourceFileName: file?.name ?? null,
          compileReceiptText: webLoopWarning,
          webLoopError,
        });
        onOpenChange(false);
        resetForm();
      } else {
        toast.error(response.message || "生成测试用例失败");
      }
    } catch (error) {
      console.error("Failed to generate test cases from document:", error);
      toast.error(error instanceof Error ? error.message : "生成测试用例失败，请稍后重试");
    } finally {
      setGenerating(false);
    }
  };

  const getFileIcon = () => {
    if (!file) return <Upload className="h-8 w-8" />;
    if (file.type.startsWith("image/")) return <ImageIcon className="h-8 w-8" />;
    return <FileText className="h-8 w-8" />;
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-xl">
            <Sparkles className="h-5 w-5 text-primary" />
            从文档生成测试用例与执行闭环
          </DialogTitle>
          <DialogDescription className="text-base">
            上传需求文档生成测试用例，可选上传接口文档同步创建 API 可执行资产和测试运行
          </DialogDescription>

          {/* 安全提示 */}
          <div className="flex items-center gap-2 rounded-lg bg-green-50 px-3 py-2 text-sm text-green-700 mt-3">
            <svg className="h-4 w-4 shrink-0" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
            </svg>
            <span>您的数据是安全的，不会用于 AI 训练</span>
          </div>
        </DialogHeader>

        <div className="space-y-5 py-4">
          {/* 文件上传区域 */}
          <div className="space-y-2">
            <Label className="text-sm font-medium">
              上传文件 <span className="text-destructive">*</span>
            </Label>
            <div
              className={cn(
                "relative flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-10 transition-all cursor-pointer",
                dragActive
                  ? "border-primary bg-primary/10 scale-[1.01]"
                  : file
                  ? "border-primary/50 bg-primary/5"
                  : "border-muted-foreground/25 hover:border-primary/50 hover:bg-muted/30"
              )}
              onDragEnter={handleDrag}
              onDragLeave={handleDrag}
              onDragOver={handleDrag}
              onDrop={handleDrop}
              onClick={() => !file && fileInputRef.current?.click()}
            >
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                accept="image/*,.pdf,.doc,.docx,.txt"
                onChange={handleFileChange}
              />

              {file ? (
                <div className="flex w-full items-center justify-between gap-4">
                  <div className="flex items-center gap-4 flex-1 min-w-0">
                    <div className="flex h-14 w-14 items-center justify-center rounded-lg bg-primary/10 text-primary shrink-0">
                      {getFileIcon()}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="font-medium truncate text-base">{file.name}</p>
                      <p className="text-sm text-muted-foreground mt-0.5">
                        {(file.size / 1024 / 1024).toFixed(2)} MB
                      </p>
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={(e) => {
                      e.stopPropagation();
                      setFile(null);
                    }}
                    className="h-9 w-9 shrink-0 hover:bg-destructive/10 hover:text-destructive"
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              ) : (
                <div className="text-center">
                  <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-primary/10">
                    <Upload className="h-8 w-8 text-primary" />
                  </div>
                  <p className="mb-2 text-base font-medium">
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        fileInputRef.current?.click();
                      }}
                      className="text-primary hover:text-primary/80 hover:underline font-semibold"
                    >
                      点击上传文件
                    </button>
                    <span className="text-muted-foreground"> 或拖拽文件到此处</span>
                  </p>
                  <p className="text-xs text-muted-foreground">
                    支持图片 (JPG, PNG, GIF, WebP)、文档 (PDF, Word, TXT)
                  </p>
                  <p className="text-xs text-muted-foreground/70 mt-1">
                    文件大小限制：15MB
                  </p>
                  <p className="text-xs text-primary mt-2">
                    试用示例文件：
                    <button
                      type="button"
                      className="ml-1 hover:underline font-medium"
                      onClick={(e) => {
                        e.stopPropagation();
                        toast.info("示例文件功能开发中");
                      }}
                    >
                      WhatsApp-Delete.pdf
                    </button>
                    {" , "}
                    <button
                      type="button"
                      className="hover:underline font-medium"
                      onClick={(e) => {
                        e.stopPropagation();
                        toast.info("示例文件功能开发中");
                      }}
                    >
                      Zoom-Guide.pdf
                    </button>
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* API 接口文档 */}
          <div className="space-y-2">
            <Label className="text-sm font-medium">
              接口文档 <span className="text-muted-foreground font-normal">(可选)</span>
            </Label>
            <div className="rounded-lg border border-dashed border-muted-foreground/25 p-4">
              <input
                ref={apiDocInputRef}
                type="file"
                className="hidden"
                accept=".txt,.md,.docx"
                onChange={handleApiDocFileChange}
              />
              {apiDocFile ? (
                <div className="flex items-center justify-between gap-4">
                  <div className="flex min-w-0 flex-1 items-center gap-3">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-blue-50 text-blue-600">
                      <FileCode2 className="h-5 w-5" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{apiDocFile.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {(apiDocFile.size / 1024 / 1024).toFixed(2)} MB
                      </p>
                    </div>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() => {
                      setApiDocFile(null);
                      if (apiDocInputRef.current) {
                        apiDocInputRef.current.value = "";
                      }
                    }}
                    className="h-8 w-8 shrink-0 hover:bg-destructive/10 hover:text-destructive"
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              ) : (
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <p className="text-sm font-medium">补齐 API 内容并创建可执行闭环</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      支持 TXT、MD、DOCX 接口文档，解析后自动生成 API 用例和脚本
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => apiDocInputRef.current?.click()}
                    className="shrink-0"
                  >
                    <FileCode2 className="mr-2 h-4 w-4" />
                    上传接口文档
                  </Button>
                </div>
              )}

              {(apiDocFile || outputType === "test_case") && (
                <div className="mt-4 space-y-3">
                  <div className="flex items-start gap-2 rounded-md bg-muted/40 px-3 py-2">
                    <Checkbox
                      id="create-test-run"
                      checked={createTestRun}
                      onCheckedChange={(checked) => setCreateTestRun(checked === true)}
                      className="mt-0.5"
                    />
                    <div className="space-y-1">
                      <Label htmlFor="create-test-run" className="cursor-pointer text-sm font-medium">
                        生成后创建测试运行
                      </Label>
                      <p className="text-xs text-muted-foreground">
                        同步创建并启动最小闭环作业；Web 凭据仅从执行环境变量读取，不写入用例、脚本或报告
                      </p>
                    </div>
                  </div>

                  {apiDocFile && (
                    <div className="space-y-2 rounded-md border px-3 py-3">
                      <Label htmlFor="api-data-profile" className="text-sm font-medium">
                        API 数据配置
                      </Label>
                      <Select
                        value={apiDataProfile}
                        onValueChange={(value) => {
                          setApiDataProfile(value);
                          if (value !== "XYSJG_UAT_WRITE") {
                            setAllowMutatingApiTests(false);
                          }
                        }}
                      >
                        <SelectTrigger id="api-data-profile" className="h-9">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="DEFAULT">默认配置（不注入敏感数据）</SelectItem>
                          <SelectItem value="XYSJG_UAT_READONLY">小杨 UAT 只读</SelectItem>
                          <SelectItem value="XYSJG_UAT_WRITE">小杨 UAT 写入</SelectItem>
                        </SelectContent>
                      </Select>
                      <p className="text-xs text-muted-foreground">
                        仅保存配置名称；地址、授权头和测试数据由执行环境注入，不会写入用例、脚本或报告。
                      </p>

                      {apiDataProfile === "XYSJG_UAT_WRITE" && (
                        <div className="flex items-start gap-2 rounded-md bg-amber-50 px-3 py-2 text-amber-800">
                          <Checkbox
                            id="allow-mutating-api-tests"
                            checked={allowMutatingApiTests}
                            onCheckedChange={(checked) => setAllowMutatingApiTests(checked === true)}
                            className="mt-0.5"
                          />
                          <div className="space-y-1">
                            <Label htmlFor="allow-mutating-api-tests" className="cursor-pointer text-sm font-medium">
                              允许执行写接口
                            </Label>
                            <p className="text-xs">
                              充值、退款等接口默认跳过。仅在本次已获得业务确认且运行环境已配置写入专用凭据时启用。
                            </p>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* 知识空间选择 */}
          <div className="space-y-2">
            <Label htmlFor="knowledge-space" className="text-sm font-medium">知识库（可选）</Label>
            <Select
              value={selectedSpaceId}
              onValueChange={setSelectedSpaceId}
              disabled={spacesLoading}
            >
              <SelectTrigger id="knowledge-space" className="h-10">
                <SelectValue placeholder={spacesLoading ? "加载中..." : "选择知识空间（可选）"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">
                  <span className="text-muted-foreground">不使用知识库</span>
                </SelectItem>
                {spaces.map((space) => (
                  <SelectItem key={space.id} value={space.id}>
                    <div className="flex flex-col items-start">
                      <span className="font-medium">{space.name}</span>
                      <span className="text-xs text-muted-foreground">
                        {space.document_count} 个文档
                        {space.business_line ? ` · ${space.business_line}` : ""}
                      </span>
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {spaces.length === 0 && !spacesLoading && (
              <p className="text-xs text-muted-foreground">
                暂无知识空间，可先在「知识库」页面创建
              </p>
            )}
          </div>

          {/* JIRA 链接 */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-sm font-medium">
                添加 JIRA 需求 <span className="text-muted-foreground font-normal">(可选)</span>
              </Label>
            </div>
            <div className="space-y-2">
              {jiraLinks.map((link, index) => (
                <div key={index} className="flex items-center gap-2">
                  <input
                    type="text"
                    value={link}
                    onChange={(e) => {
                      const newLinks = [...jiraLinks];
                      newLinks[index] = e.target.value;
                      setJiraLinks(newLinks);
                    }}
                    placeholder="输入 JIRA 链接"
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={() => {
                      setJiraLinks(jiraLinks.filter((_, i) => i !== index));
                    }}
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setJiraLinks([...jiraLinks, ""])}
                className="w-full"
              >
                + 添加链接
              </Button>
            </div>
          </div>

          {/* 需求质量门控（阶段一）：状态 / 缺失项 / 歧义项 / 阻断原因 */}
          {file && (
            <div className="space-y-2">
              <Label className="text-sm font-medium">需求质量</Label>
              {qualityLoading && (
                <p className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  正在分析需求质量...
                </p>
              )}
              {qualityError && !qualityLoading && (
                <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  质量校验未完成：{qualityError}（生成时后端仍会执行硬门控）
                </p>
              )}
              {quality && !qualityLoading && (
                <RequirementQualityPanel data={quality} />
              )}
              {overrideApplied && (
                <div className="mt-2 rounded-md border border-green-200 bg-green-50 px-3 py-2 text-xs text-green-700">
                  ✅ 已人工放行（原因：{overrideReason}）
                </div>
              )}
              {!quality && !qualityLoading && !qualityError && (
                <p className="text-xs text-muted-foreground">
                  该文件为 PDF/图片等二进制文档，需求质量判定将在生成时由后端执行。
                </p>
              )}
            </div>
          )}

          {showOverrideSection && outputType === "test_case" && (
            <div className="space-y-2 rounded-md border border-amber-200 bg-amber-50 p-3">
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={showOverride} onChange={e => setShowOverride(e.target.checked)} /> 人工放行（需服务端凭证）</label>
              <p className="text-xs text-muted-foreground">
                {qualityBlocked
                  ? "仅用于本地验证或探索性测试；正式环境请补齐需求后重新生成"
                  : "PDF/图片等二进制文件由后端生成时判定质量；如被阻断可在此提前填写放行信息"}
              </p>
              {showOverride && <>
                <input type="password" value={overrideToken} onChange={e => setOverrideToken(e.target.value)} placeholder="放行凭证" className="w-full rounded border px-2 py-1 text-sm" />
                <Textarea value={overrideReason} onChange={e => setOverrideReason(e.target.value)} placeholder="填写人工放行原因（至少10个字符）" rows={2} />
                <p className={overrideReason.trim().length >= 10 ? "text-xs text-green-700" : "text-xs text-amber-700"}>
                  已输入 {overrideReason.trim().length}/10 个字符{overrideReason.trim().length < 10 ? "，还需补充原因" : "，可以提交"}
                </p>
              </>}
            </div>
          )}

          {/* 附加提示 */}
          <div className="space-y-2">
            <Label htmlFor="additional-prompt" className="text-sm font-medium">
              附加说明 <span className="text-muted-foreground font-normal">(可选)</span>
            </Label>
            <Textarea
              id="additional-prompt"
              value={additionalPrompt}
              onChange={(e) => setAdditionalPrompt(e.target.value)}
              placeholder="例如：重点关注边界条件测试、需要包含性能测试用例、关注安全性测试等..."
              rows={3}
              className="resize-none"
            />
            <p className="text-xs text-muted-foreground">
              提供额外的上下文信息，帮助 AI 更好地理解您的需求
            </p>
          </div>

          {/* 输出类型 */}
          <div className="space-y-2">
            <Label htmlFor="output-type" className="text-sm font-medium">
              输出类型 <span className="text-destructive">*</span>
            </Label>
            <Select
              value={outputType}
              onValueChange={(v) => setOutputType(v as "requirement_analysis" | "test_case")}
            >
              <SelectTrigger id="output-type" className="h-10">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="requirement_analysis" disabled={Boolean(apiDocFile)}>
                  <div className="flex flex-col items-start">
                    <span className="font-medium">需求分析报告（.md）</span>
                    <span className="text-xs text-muted-foreground">
                      解析文档并输出结构化 Markdown 报告，保存到工作目录
                    </span>
                  </div>
                </SelectItem>
                <SelectItem value="test_case">
                  <div className="flex flex-col items-start">
                    <span className="font-medium">测试用例</span>
                    <span className="text-xs text-muted-foreground">直接生成测试用例</span>
                  </div>
                </SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              {apiDocFile
                ? "已选择接口文档，本次将直接生成测试用例并创建 API 闭环资产"
                : "选择“需求分析报告”时仅生成 .md 报告，AI 会在保存后再询问是否继续生成测试用例"}
            </p>
          </div>

          {/* 测试模式 */}
          <div className="space-y-2">
            <Label htmlFor="test-mode" className="text-sm font-medium">
              测试模式 <span className="text-destructive">*</span>
            </Label>
            <Select
              value={testMode}
              onValueChange={(v) => setTestMode(v as "sit" | "uat" | "smoke" | "api")}
            >
              <SelectTrigger id="test-mode" className="h-10">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="sit">
                  <div className="flex flex-col items-start">
                    <span className="font-medium">SIT 全量功能测试</span>
                    <span className="text-xs text-muted-foreground">
                      系统内部集成测试，覆盖功能、边界、异常场景
                    </span>
                  </div>
                </SelectItem>
                <SelectItem value="uat">
                  <div className="flex flex-col items-start">
                    <span className="font-medium">UAT 业务场景验收</span>
                    <span className="text-xs text-muted-foreground">
                      用户验收测试，聚焦端到端业务流程验证
                    </span>
                  </div>
                </SelectItem>
                <SelectItem value="smoke">
                  <div className="flex flex-col items-start">
                    <span className="font-medium">冒烟测试</span>
                    <span className="text-xs text-muted-foreground">
                      核心功能快速验证，用例数 ≤ 25 条
                    </span>
                  </div>
                </SelectItem>
                <SelectItem value="api">
                  <div className="flex flex-col items-start">
                    <span className="font-medium">API 接口复合场景</span>
                    <span className="text-xs text-muted-foreground">
                      基于接口契约编排端到端 API 调用链
                    </span>
                  </div>
                </SelectItem>
              </SelectContent>
            </Select>
            <div className="rounded-lg bg-blue-50 px-3 py-2 text-sm text-blue-700">
              <p className="font-medium mb-1">💡 测试模式说明</p>
              <ul className="space-y-1 text-xs list-disc list-inside">
                <li><strong>SIT</strong>：按字段/边界/异常拆原子用例，执行完整六步测试分析法</li>
                <li><strong>UAT</strong>：按端到端业务流程拆场景，预期结果需业务方可直接验证</li>
                <li><strong>冒烟</strong>：仅覆盖核心 P0 功能正向路径和最高风险异常场景</li>
                <li><strong>API</strong>：提取接口契约，编排端到端调用链，聚焦契约验证</li>
              </ul>
            </div>
          </div>

          {/* 模板选择 */}
          <div className="space-y-2">
            <Label htmlFor="template" className="text-sm font-medium">测试用例模板</Label>
            <Select
              value={template}
              onValueChange={(v) => setTemplate(v as TestCaseTemplate)}
              disabled={outputType === "requirement_analysis"}
            >
              <SelectTrigger id="template" className="h-10">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="test_case">
                  <div className="flex flex-col items-start">
                    <span className="font-medium">测试步骤模板</span>
                    <span className="text-xs text-muted-foreground">传统的步骤-预期结果格式</span>
                  </div>
                </SelectItem>
                <SelectItem value="test_case_bdd">
                  <div className="flex flex-col items-start">
                    <span className="font-medium">BDD 模板</span>
                    <span className="text-xs text-muted-foreground">Given-When-Then 格式</span>
                  </div>
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <DialogFooter className="gap-2 flex-col sm:flex-row sm:items-center">
          {webLoopError && (
            <div
              className="mr-auto w-full rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 sm:max-w-[70%]"
              data-testid="web-loop-error"
              role="status"
            >
              <div className="font-medium">Web 冒烟自动化未创建</div>
              <div className="mt-1 break-words">{webLoopError}</div>
              <div className="mt-1 text-amber-700">
                用例已生成，但执行侧未建立；请按提示检查项目「路径配置」（入口 URL、登录 profile、导航路径）
                或子功能绑定后重试。
              </div>
            </div>
          )}
          {webLoopWarning && (
            <div
              className="mr-auto w-full rounded-md border border-amber-500 bg-amber-50 px-3 py-2 text-xs text-amber-900 sm:max-w-[70%]"
              data-testid="web-loop-receipt-warning"
              role="status"
            >
              <div className="font-medium">Web 冒烟编译回执（需关注）</div>
              <div className="mt-1 whitespace-pre-line break-words">{webLoopWarning}</div>
              <div className="mt-1 text-amber-700">
                资产状态仍会显示「已完成」：以上警告表示本次脚本可能不含可执行检查，请勿据此判定已覆盖。
              </div>
            </div>
          )}
          {outputType === "test_case" && qualityBlocked && !overrideReady && (
            <p className="mr-auto text-xs text-amber-800" data-testid="generate-blocked-hint">
              需求质量不满足测试设计条件，生成按钮已禁用（详见上方缺失项与阻断原因）。
            </p>
          )}
          {outputType === "test_case" && qualityBlocked && overrideReady && (
            <p className="mr-auto text-xs text-green-700" data-testid="generate-override-hint">
              已启用人工放行，将记录审计日志。
            </p>
          )}
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={generating}
            className="min-w-[100px]"
          >
            取消
          </Button>
          <Button
            onClick={handleGenerate}
            disabled={generateDisabled}
            title={
              outputType === "test_case" && qualityBlocked && !overrideReady
                ? "需求质量不满足测试设计条件，已禁用正式用例生成"
                : undefined
            }
            className="min-w-[140px]"
          >
            {generating ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                {apiDocFile ? "闭环创建中..." : "AI 分析生成中..."}
              </>
            ) : (
              <>
                {apiDocFile && outputType === "test_case" ? (
                  <PlayCircle className="mr-2 h-4 w-4" />
                ) : (
                  <Sparkles className="mr-2 h-4 w-4" />
                )}
                {apiDocFile && outputType === "test_case"
                  ? "生成并创建闭环"
                  : outputType === "requirement_analysis"
                  ? "生成需求分析报告"
                  : "生成测试用例"}
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
// NOTE  My80OmFIVnBZMlhscm9ua3VMazZSVnBXWmc9PTo2NWRlN2UwMw==
