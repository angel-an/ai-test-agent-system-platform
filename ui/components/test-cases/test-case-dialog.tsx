// TODO  MC80OmFIVnBZMlhscm9ua3VMazZOamsyV0E9PTo2MWZhZDUxMg==

"use client";

import * as React from "react";
import { Plus, Trash2, GripVertical, Folder, Sparkles, Settings2, X, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { RichTextEditor } from "@/components/ui/rich-text-editor";
import { AttachmentUpload } from "./attachment-upload";
import { toast } from "sonner";
import { aiAssistField } from "@/lib/api/ai";
import type {
  TestCaseInfo,
  TestCaseCreate,
  TestStepCreate,
  Priority,
  TestCaseState,
  TestCaseType,
  TestCaseTemplate,
  AutomationStatus,
} from "@/lib/api/types";
// FIXME  MS80OmFIVnBZMlhscm9ua3VMazZOamsyV0E9PTo2MWZhZDUxMg==

interface TestCaseDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  testCase?: TestCaseInfo | null;
  onSubmit: (data: TestCaseCreate) => Promise<void>;
  submitting?: boolean;
  folderName?: string;
  projectId?: string;
}
// TODO  Mi80OmFIVnBZMlhscm9ua3VMazZOamsyV0E9PTo2MWZhZDUxMg==

const defaultFormData: TestCaseCreate = {
  name: "",
  description: "",
  preconditions: "",
  priority: "medium",
  status: "new",
  case_type: "functional",  // 默认改为功能测试，而不是 other
  template: "test_case",
  test_case_steps: [],
  tags: [],
};

export function TestCaseDialog({
  open,
  onOpenChange,
  testCase,
  onSubmit,
  submitting,
  folderName,
  projectId,
}: TestCaseDialogProps) {
  const [formData, setFormData] = React.useState<TestCaseCreate>(defaultFormData);
  const [steps, setSteps] = React.useState<TestStepCreate[]>([{ step: "", result: "" }]);
  const [template, setTemplate] = React.useState<TestCaseTemplate>("test_case");
  const [createAnother, setCreateAnother] = React.useState(false);
  const [tagInput, setTagInput] = React.useState("");
  const [automationStatus, setAutomationStatus] = React.useState<AutomationStatus>("not_automated");
  const [owner, setOwner] = React.useState("Myself");
  const [aiAssisting, setAiAssisting] = React.useState<string | null>(null);
  const [attachmentFiles, setAttachmentFiles] = React.useState<File[]>([]);
  // 从 ai-test-agent-system 迁移的字段
  const [module, setModule] = React.useState("");
  const [keyword, setKeyword] = React.useState("");
  const [tpCode, setTpCode] = React.useState("");
  const [riskLevel, setRiskLevel] = React.useState("");
  const [caseKind, setCaseKind] = React.useState("sit");

  // rev66：规则与断言（编译用，补齐后重编译即可转自动执行）
  const [ruleField, setRuleField] = React.useState("");
  const [ruleType, setRuleType] = React.useState("");
  const [ruleRequired, setRuleRequired] = React.useState(false);
  const [ruleRange, setRuleRange] = React.useState("");
  const [ruleCondition, setRuleCondition] = React.useState("");
  const [assertions, setAssertions] = React.useState<
    Array<{ kind: string; field: string; data: string; expect: string; rule: string }>
  >([]);

  // 初始化表单数据
  React.useEffect(() => {
    if (testCase) {
      setFormData({
        name: testCase.name,
        description: testCase.description || "",
        preconditions: testCase.preconditions || "",
        priority: testCase.priority,
        status: testCase.status,
        case_type: testCase.case_type,
        template: testCase.template,
        tags: testCase.tags || [],
        feature: testCase.feature || "",
        scenario: testCase.scenario || "",
        background: testCase.background || "",
      });
      setSteps(
        testCase.test_case_steps?.map((s) => ({
          step: s.step,
          result: s.result || "",
        })) || [{ step: "", result: "" }]
      );
      setTemplate(testCase.template);
      // 从 ai-test-agent-system 迁移的字段
      setModule(testCase.module || "");
      setKeyword(testCase.keyword || "");
      setTpCode(testCase.tp_code || "");
      setRiskLevel(testCase.risk_level || "");
      setCaseKind(testCase.case_kind || "sit");
      // 回填规则与断言（custom_fields 为整字段替换，提交时全量带回）
      const cf = (testCase.custom_fields || {}) as Record<string, unknown>;
      const rule = cf.requirement_rule as
        | { field?: string; type?: string; required?: boolean; condition?: string; range_raw?: string }
        | undefined;
      setRuleField(rule?.field || "");
      setRuleType(rule?.type || "");
      setRuleRequired(!!rule?.required);
      setRuleCondition(rule?.condition || "");
      setRuleRange(rule?.range_raw || "");
      const rawAssertions = Array.isArray(cf.assertions) ? cf.assertions : [];
      setAssertions(
        rawAssertions.map((a) => {
          const o = (a || {}) as Record<string, unknown>;
          return {
            kind: String(o.kind ?? "boundary"),
            field: String(o.field ?? ""),
            data:
              typeof o.data === "object" && o.data !== null
                ? JSON.stringify(o.data)
                : String(o.data ?? ""),
            expect: String(o.expect ?? ""),
            rule: String(o.rule ?? ""),
          };
        })
      );
    } else {
      setFormData(defaultFormData);
      setSteps([{ step: "", result: "" }]);
      setTemplate("test_case");
      // 重置迁移字段
      setModule("");
      setKeyword("");
      setTpCode("");
      setRiskLevel("");
      setCaseKind("sit");
      setRuleField("");
      setRuleType("");
      setRuleRequired(false);
      setRuleRange("");
      setRuleCondition("");
      setAssertions([]);
    }
  }, [testCase, open]);

  // 添加步骤
  const addStep = () => {
    setSteps([...steps, { step: "", result: "" }]);
  };

  // 删除步骤
  const removeStep = (index: number) => {
    if (steps.length > 1) {
      setSteps(steps.filter((_, i) => i !== index));
    }
  };

  // 更新步骤
  const updateStep = (
    index: number,
    field: keyof TestStepCreate,
    value: string
  ) => {
    const newSteps = [...steps];
    newSteps[index] = { ...newSteps[index], [field]: value };
    setSteps(newSteps);
  };

  // 添加标签
  const addTag = (tag: string) => {
    const trimmed = tag.trim();
    if (trimmed && !formData.tags?.includes(trimmed)) {
      setFormData({ ...formData, tags: [...(formData.tags || []), trimmed] });
    }
    setTagInput("");
  };

  // 删除标签
  const removeTag = (tag: string) => {
    setFormData({ ...formData, tags: formData.tags?.filter((t) => t !== tag) });
  };

  // AI辅助填充字段
  const handleAIAssist = async (field: "description" | "preconditions" | "steps") => {
    if (!projectId) {
      toast.error("项目ID未提供");
      return;
    }

    if (!formData.name.trim()) {
      toast.error("请先输入测试用例标题");
      return;
    }

    try {
      setAiAssisting(field);
      const response = await aiAssistField(projectId, {
        field,
        context: {
          title: formData.name,
          description: formData.description,
          preconditions: formData.preconditions,
          existing_steps: steps,
        },
      });

      if (response.success) {
        if (field === "description") {
          setFormData({ ...formData, description: response.content as string });
          toast.success("AI 已生成描述内容");
        } else if (field === "preconditions") {
          setFormData({ ...formData, preconditions: response.content as string });
          toast.success("AI 已生成前置条件");
        } else if (field === "steps") {
          const generatedSteps = response.content as Array<{ step: string; result: string }>;
          setSteps(generatedSteps.length > 0 ? generatedSteps : [{ step: "", result: "" }]);
          toast.success("AI 已生成测试步骤");
        }
      } else {
        toast.error(response.message || "AI 辅助失败");
      }
    } catch (error) {
      console.error("AI assist failed:", error);
      toast.error("AI 辅助失败，请稍后重试");
    } finally {
      setAiAssisting(null);
    }
  };

  // 提交表单
  const handleSubmit = async () => {
    // 验证必填字段
    if (!formData.name.trim()) {
      toast.error("请输入测试用例标题");
      return;
    }

    // 验证关键词（必填）
    if (!keyword) {
      toast.error("请选择关键词");
      return;
    }

    // 验证测试步骤（至少填写一个步骤）
    if (template === "test_case") {
      const validSteps = steps.filter(s => s.step.trim());
      if (validSteps.length === 0) {
        toast.error("请至少填写一个测试步骤");
        return;
      }
      // 验证每个步骤是否有预期结果
      const stepsWithoutResult = validSteps.filter(s => !s.result?.trim());
      if (stepsWithoutResult.length > 0) {
        toast.error("请为所有测试步骤填写预期结果");
        return;
      }
    }

    // rev66：合并规则与断言（custom_fields 为整字段替换，必须带全量旧值）
    const nextCf: Record<string, unknown> = { ...(testCase?.custom_fields || {}) };
    if (ruleField.trim() || ruleRange.trim() || ruleCondition.trim()) {
      nextCf.requirement_rule = {
        ...((nextCf.requirement_rule as Record<string, unknown> | undefined) || {}),
        field: ruleField.trim(),
        type: ruleType.trim(),
        required: ruleRequired,
        condition: ruleCondition.trim(),
        range_raw: ruleRange.trim(),
        source: { from: "manual_supplement", via: "case_edit_dialog" },
      };
    }
    const validAssertions = assertions.filter(
      (a) => a.field.trim() || a.data.trim() || a.expect.trim()
    );
    if (validAssertions.length > 0) {
      nextCf.assertions = validAssertions.map((a) => ({
        kind: a.kind,
        field: a.field.trim(),
        data: a.data.trim(),
        expect: a.expect.trim(),
        rule: a.rule.trim(),
      }));
    }

    const data: TestCaseCreate = {
      ...formData,
      template,
      owner: owner === "Myself" ? undefined : owner,
      automation_status: automationStatus,
      test_case_steps: template === "test_case" ? steps.filter(s => s.step.trim()) : undefined,
      // 从 ai-test-agent-system 迁移的字段
      module: module || undefined,
      keyword: keyword || undefined,
      tp_code: tpCode || undefined,
      risk_level: riskLevel || undefined,
      case_kind: caseKind || undefined,
      ...(Object.keys(nextCf).length > 0 ? { custom_fields: nextCf } : {}),
    };
    await onSubmit(data);
    if (createAnother && !testCase) {
      setFormData(defaultFormData);
      setSteps([{ step: "", result: "" }]);
    }
  };

  const isEdit = !!testCase;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false} className="max-w-5xl max-h-[90vh] p-0 gap-0">
        {/* 头部 */}
        <div className="flex items-center justify-between border-b px-6 py-4">
          <h2 className="text-lg font-semibold">
            {isEdit ? "编辑测试用例" : "创建测试用例"}
          </h2>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => onOpenChange(false)}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* 主体内容 */}
        <div className="flex flex-1 overflow-hidden">
          {/* 左侧表单区域 */}
          <div className="flex-1 overflow-hidden">
            <ScrollArea className="h-[calc(90vh-140px)]">
              <div className="space-y-6 p-6">
                {/* 标题和模板 */}
                <div className="flex gap-4">
                  <div className="flex-1 space-y-2">
                    <Label htmlFor="name">
                      标题 <span className="text-destructive">*</span>
                    </Label>
                    <Input
                      id="name"
                      value={formData.name}
                      onChange={(e) =>
                        setFormData({ ...formData, name: e.target.value })
                      }
                      placeholder="输入测试用例名称"
                    />
                  </div>
                  <div className="w-48 space-y-2">
                    <Label>模板</Label>
                    <Select
                      value={template}
                      onValueChange={(v) => {
                        setTemplate(v as TestCaseTemplate);
                        setFormData({ ...formData, template: v as TestCaseTemplate });
                      }}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="test_case">测试步骤</SelectItem>
                        <SelectItem value="test_case_bdd">BDD 测试用例</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                {/* 所在目录 */}
                {folderName && (
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Folder className="h-4 w-4" />
                    <span>{folderName}</span>
                  </div>
                )}

                {/* 描述 */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Label htmlFor="description">描述</Label>
                    {projectId && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-7 text-xs gap-1 text-primary hover:text-primary hover:bg-primary/10"
                        onClick={() => handleAIAssist("description")}
                        disabled={aiAssisting === "description" || !formData.name.trim()}
                      >
                        {aiAssisting === "description" ? (
                          <>
                            <Loader2 className="h-3 w-3 animate-spin" />
                            <span>AI 生成中...</span>
                          </>
                        ) : (
                          <>
                            <Sparkles className="h-3 w-3" />
                            <span>AI 辅助</span>
                          </>
                        )}
                      </Button>
                    )}
                  </div>
                  <RichTextEditor
                    id="description"
                    value={formData.description || ""}
                    onChange={(value) =>
                      setFormData({ ...formData, description: value })
                    }
                    placeholder="简要描述测试内容"
                    rows={3}
                  />
                </div>

                {/* 前置条件 */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Label htmlFor="preconditions">前置条件</Label>
                    {projectId && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-7 text-xs gap-1 text-primary hover:text-primary hover:bg-primary/10"
                        onClick={() => handleAIAssist("preconditions")}
                        disabled={aiAssisting === "preconditions" || !formData.name.trim()}
                      >
                        {aiAssisting === "preconditions" ? (
                          <>
                            <Loader2 className="h-3 w-3 animate-spin" />
                            <span>AI 生成中...</span>
                          </>
                        ) : (
                          <>
                            <Sparkles className="h-3 w-3" />
                            <span>AI 辅助</span>
                          </>
                        )}
                      </Button>
                    )}
                  </div>
                  <RichTextEditor
                    id="preconditions"
                    value={formData.preconditions || ""}
                    onChange={(value) =>
                      setFormData({ ...formData, preconditions: value })
                    }
                    placeholder="定义测试的前置条件"
                    rows={2}
                  />
                </div>

                {/* 测试步骤和预期结果 - 只在标准模板下显示 */}
                {template === "test_case" && (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <Label>
                        测试步骤和预期结果 <span className="text-destructive">*</span>
                      </Label>
                      {projectId && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-7 text-xs gap-1 text-primary hover:text-primary hover:bg-primary/10"
                          onClick={() => handleAIAssist("steps")}
                          disabled={aiAssisting === "steps" || !formData.name.trim()}
                        >
                          {aiAssisting === "steps" ? (
                            <>
                              <Loader2 className="h-3 w-3 animate-spin" />
                              <span>AI 生成中...</span>
                            </>
                          ) : (
                            <>
                              <Sparkles className="h-3 w-3" />
                              <span>AI 生成步骤</span>
                            </>
                          )}
                        </Button>
                      )}
                    </div>

                    {steps.map((step, index) => (
                      <div key={index} className="flex items-start gap-2">
                        <div className="flex items-center gap-2 pt-8">
                          <GripVertical className="h-4 w-4 cursor-grab text-muted-foreground" />
                          <span className="w-4 text-center text-sm text-muted-foreground">
                            {index + 1}
                          </span>
                        </div>
                        <div className="grid flex-1 grid-cols-2 gap-2">
                          <div className="space-y-1">
                            {index === 0 && (
                              <Label className="text-xs text-muted-foreground">步骤</Label>
                            )}
                            <RichTextEditor
                              value={step.step}
                              onChange={(value) => updateStep(index, "step", value)}
                              placeholder="步骤详情"
                              rows={2}
                            />
                          </div>
                          <div className="space-y-1">
                            {index === 0 && (
                              <Label className="text-xs text-muted-foreground">预期结果</Label>
                            )}
                            <RichTextEditor
                              value={step.result || ""}
                              onChange={(value) => updateStep(index, "result", value)}
                              placeholder="预期结果"
                              rows={2}
                            />
                          </div>
                        </div>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="mt-8 text-muted-foreground hover:text-destructive"
                          onClick={() => removeStep(index)}
                          disabled={steps.length <= 1}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    ))}

                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={addStep}
                      className="text-primary"
                    >
                      <Plus className="mr-1 h-4 w-4" />
                      添加步骤
                    </Button>
                  </div>
                )}

                {/* BDD 模板 */}
                {template === "test_case_bdd" && (
                  <div className="space-y-4">
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <Label htmlFor="background">背景</Label>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-6 text-xs text-muted-foreground hover:text-primary"
                          onClick={() => {
                            const template = "Given [前置条件]\nAnd [其他前置条件]";
                            setFormData({
                              ...formData,
                              background: formData.background
                                ? formData.background + "\n" + template
                                : template
                            });
                          }}
                        >
                          + 创建
                        </Button>
                      </div>
                      <RichTextEditor
                        id="background"
                        value={formData.background || ""}
                        onChange={(value) =>
                          setFormData({ ...formData, background: value })
                        }
                        placeholder="Given [前置条件]"
                        rows={3}
                      />
                    </div>
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <Label htmlFor="feature">功能</Label>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-6 text-xs text-muted-foreground hover:text-primary"
                          onClick={() => {
                            const template = "Feature: [功能名称]\n  作为 [角色]\n  我想要 [功能]\n  以便 [价值]";
                            setFormData({
                              ...formData,
                              feature: formData.feature
                                ? formData.feature + "\n\n" + template
                                : template
                            });
                          }}
                        >
                          + 创建
                        </Button>
                      </div>
                      <RichTextEditor
                        id="feature"
                        value={formData.feature || ""}
                        onChange={(value) =>
                          setFormData({ ...formData, feature: value })
                        }
                        placeholder="Feature: [功能名称]"
                        rows={3}
                      />
                    </div>
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <Label htmlFor="scenario">场景</Label>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-6 text-xs text-muted-foreground hover:text-primary"
                          onClick={() => {
                            const template = "Scenario: [场景名称]\n  Given [前置条件]\n  When [操作]\n  Then [预期结果]";
                            setFormData({
                              ...formData,
                              scenario: formData.scenario
                                ? formData.scenario + "\n\n" + template
                                : template
                            });
                          }}
                        >
                          + 创建
                        </Button>
                      </div>
                      <RichTextEditor
                        id="scenario"
                        value={formData.scenario || ""}
                        onChange={(value) =>
                          setFormData({ ...formData, scenario: value })
                        }
                        placeholder="Scenario: [场景名称]"
                        rows={6}
                      />
                    </div>
                  </div>
                )}

                {/* 规则与断言（编辑模式；补齐后重编译可转自动执行） */}
                {testCase && template === "test_case" && (
                  <div className="rounded-lg border p-4 space-y-3">
                    <div>
                      <Label className="text-sm font-medium">规则与断言（自动编译用）</Label>
                      <p className="text-xs text-muted-foreground mt-1">
                        补齐字段规则与可判定断言后，重新生成 Web 冒烟即可把该用例从「需人工执行」转为自动执行。断言值须来自需求文档真实约束。
                      </p>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div className="space-y-1">
                        <Label className="text-xs">规则字段名</Label>
                        <Input value={ruleField} onChange={(e) => setRuleField(e.target.value)}
                          placeholder="如：企业账户充值金额" className="h-8 text-sm" />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">字段类型</Label>
                        <Input value={ruleType} onChange={(e) => setRuleType(e.target.value)}
                          placeholder="如：数字 / 单选组" className="h-8 text-sm" />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">取值范围 / 约束原文</Label>
                        <Input value={ruleRange} onChange={(e) => setRuleRange(e.target.value)}
                          placeholder="如：(0,99999] / 限制500個字" className="h-8 text-sm" />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">显示条件</Label>
                        <Input value={ruleCondition} onChange={(e) => setRuleCondition(e.target.value)}
                          placeholder="可选，如：变更方式=批量导入" className="h-8 text-sm" />
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <Checkbox id="rule-required" checked={ruleRequired}
                        onCheckedChange={(v) => setRuleRequired(v === true)} />
                      <Label htmlFor="rule-required" className="text-xs font-normal cursor-pointer">
                        是否必填（勾选后编译器会追加留空拒绝断言）
                      </Label>
                    </div>

                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <Label className="text-xs font-medium">断言列表（定位值 / 预期判定）</Label>
                        <Button type="button" variant="outline" size="sm" className="h-7 text-xs"
                          onClick={() => setAssertions([...assertions, { kind: "boundary", field: "", data: "", expect: "", rule: "" }])}>
                          <Plus className="h-3 w-3 mr-1" />加断言
                        </Button>
                      </div>
                      {assertions.length === 0 && (
                        <p className="text-xs text-muted-foreground">暂无断言——没有断言的用例会被标记为「需人工执行」。</p>
                      )}
                      {assertions.map((a, i) => (
                        <div key={i} className="rounded border p-2 space-y-2 bg-muted/20">
                          <div className="flex items-center gap-2">
                            <Select value={a.kind}
                              onValueChange={(v) => setAssertions(assertions.map((x, j) => j === i ? { ...x, kind: v } : x))}>
                              <SelectTrigger className="h-7 w-32 text-xs"><SelectValue /></SelectTrigger>
                              <SelectContent>
                                {["required", "enum", "boundary", "precision", "max_length", "max_count", "file_format", "file_size"].map((k) => (
                                  <SelectItem key={k} value={k} className="text-xs">{k}</SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                            <Input value={a.field} className="h-7 flex-1 text-xs"
                              placeholder="字段名"
                              onChange={(e) => setAssertions(assertions.map((x, j) => j === i ? { ...x, field: e.target.value } : x))} />
                            <Button type="button" variant="ghost" size="sm" className="h-7 w-7 p-0 text-destructive"
                              onClick={() => setAssertions(assertions.filter((_, j) => j !== i))}>
                              <Trash2 className="h-3 w-3" />
                            </Button>
                          </div>
                          <div className="grid grid-cols-3 gap-2">
                            <Input value={a.data} className="h-7 text-xs" placeholder="输入值（data）"
                              onChange={(e) => setAssertions(assertions.map((x, j) => j === i ? { ...x, data: e.target.value } : x))} />
                            <Input value={a.expect} className="h-7 text-xs" placeholder="预期（accept/reject…）"
                              onChange={(e) => setAssertions(assertions.map((x, j) => j === i ? { ...x, expect: e.target.value } : x))} />
                            <Input value={a.rule} className="h-7 text-xs" placeholder="规则原文（rule）"
                              onChange={(e) => setAssertions(assertions.map((x, j) => j === i ? { ...x, rule: e.target.value } : x))} />
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* 附件上传 - 只在标准模板下显示 */}
                {template === "test_case" && (
                  <AttachmentUpload
                    testCaseId={testCase?.id}
                    testCaseIdentifier={testCase?.identifier}
                    projectIdentifier={projectId}
                    onFilesChange={setAttachmentFiles}
                    autoUpload={!!testCase} // 编辑模式自动上传
                    maxFiles={10}
                    maxSize={50}
                  />
                )}
              </div>
            </ScrollArea>
          </div>

          {/* 右侧字段面板 */}
          <div className="w-64 shrink-0 border-l bg-muted/30">
            <ScrollArea className="h-[calc(90vh-140px)]">
              <div className="p-4">
                <div className="mb-4 flex items-center justify-between">
                  <span className="text-sm font-medium">测试用例字段</span>
                  <Button variant="link" size="sm" className="h-auto p-0 text-primary">
                    <Settings2 className="mr-1 h-3 w-3" />
                    配置
                  </Button>
                </div>

                <div className="space-y-4">
                  {/* Owner */}
                  <div className="space-y-1.5">
                    <Label className="text-sm">
                      负责人 <span className="text-destructive">*</span>
                    </Label>
                    <Select value={owner} onValueChange={setOwner}>
                      <SelectTrigger className="h-9">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="Myself">我自己</SelectItem>
                        <SelectItem value="Unassigned">未分配</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  {/* State */}
                  <div className="space-y-1.5">
                    <Label className="text-sm">
                      状态 <span className="text-destructive">*</span>
                    </Label>
                    <Select
                      value={formData.status}
                      onValueChange={(value: TestCaseState) =>
                        setFormData({ ...formData, status: value })
                      }
                    >
                      <SelectTrigger className="h-9">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {/* 设计阶段 */}
                        <SelectItem value="new">🆕 新建</SelectItem>
                        <SelectItem value="review_pending">⏳ 待评审</SelectItem>
                        <SelectItem value="reviewed">✅ 已评审</SelectItem>
                        {/* 执行阶段 */}
                        <SelectItem value="not_run">⚪ 未执行</SelectItem>
                        <SelectItem value="passed">✅ 通过</SelectItem>
                        <SelectItem value="failed">❌ 失败</SelectItem>
                        <SelectItem value="blocked">🚫 阻塞</SelectItem>
                        <SelectItem value="skipped">⏭️ 跳过</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Priority */}
                  <div className="space-y-1.5">
                    <Label className="text-sm">
                      优先级 <span className="text-destructive">*</span>
                    </Label>
                    <Select
                      value={formData.priority}
                      onValueChange={(value: Priority) =>
                        setFormData({ ...formData, priority: value })
                      }
                    >
                      <SelectTrigger className="h-9">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="critical">🔴 紧急</SelectItem>
                        <SelectItem value="high">🟠 高</SelectItem>
                        <SelectItem value="medium">🟡 中</SelectItem>
                        <SelectItem value="low">🟢 低</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Type of Test Case */}
                  <div className="space-y-1.5">
                    <Label className="text-sm">
                      用例类型 <span className="text-destructive">*</span>
                    </Label>
                    <Select
                      value={formData.case_type}
                      onValueChange={(value: TestCaseType) =>
                        setFormData({ ...formData, case_type: value })
                      }
                    >
                      <SelectTrigger className="h-9">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="functional">功能测试</SelectItem>
                        <SelectItem value="smoke_sanity">冒烟测试</SelectItem>
                        <SelectItem value="regression">回归测试</SelectItem>
                        <SelectItem value="security">安全测试</SelectItem>
                        <SelectItem value="performance">性能测试</SelectItem>
                        <SelectItem value="usability">可用性测试</SelectItem>
                        <SelectItem value="acceptance">验收测试</SelectItem>
                        <SelectItem value="integration">集成测试</SelectItem>
                        <SelectItem value="exploratory">探索性测试</SelectItem>
                        <SelectItem value="other">其他</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Automation Status */}
                  <div className="space-y-1.5">
                    <Label className="text-sm">
                      自动化状态 <span className="text-destructive">*</span>
                    </Label>
                    <Select
                      value={automationStatus}
                      onValueChange={(value) => setAutomationStatus(value as AutomationStatus)}
                    >
                      <SelectTrigger className="h-9">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="not_automated">未自动化</SelectItem>
                        <SelectItem value="automated">已自动化</SelectItem>
                        <SelectItem value="in_progress">自动化进行中</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Tags */}
                  <div className="space-y-1.5">
                    <Label className="text-sm">标签</Label>
                    <Input
                      placeholder="添加标签后回车"
                      value={tagInput}
                      onChange={(e) => setTagInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          addTag(tagInput);
                        }
                      }}
                      className="h-9"
                    />
                    {formData.tags && formData.tags.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {formData.tags.map((tag) => (
                          <Badge
                            key={tag}
                            variant="secondary"
                            className="gap-1 pr-1"
                          >
                            {tag}
                            <button
                              onClick={() => removeTag(tag)}
                              className="ml-1 rounded-full hover:bg-muted"
                            >
                              <X className="h-3 w-3" />
                            </button>
                          </Badge>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* 分隔线 */}
                  <div className="border-t pt-4">
                    <span className="text-xs font-medium text-muted-foreground">扩展字段</span>
                  </div>

                  {/* 所属模块 */}
                  <div className="space-y-1.5">
                    <Label className="text-sm">所属模块</Label>
                    <Input
                      placeholder="输入模块名称"
                      value={module}
                      onChange={(e) => setModule(e.target.value)}
                      className="h-9"
                    />
                  </div>

                  {/* 关键词 */}
                  <div className="space-y-1.5">
                    <Label className="text-sm">
                      关键词 <span className="text-destructive">*</span>
                    </Label>
                    <Select value={keyword || "none"} onValueChange={(v) => setKeyword(v === "none" ? "" : v)}>
                      <SelectTrigger className="h-9">
                        <SelectValue placeholder="选择关键词" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">无</SelectItem>
                        <SelectItem value="正向">正向</SelectItem>
                        <SelectItem value="反向">反向</SelectItem>
                        <SelectItem value="边界">边界</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  {/* 风险等级 */}
                  <div className="space-y-1.5">
                    <Label className="text-sm">风险等级</Label>
                    <Select value={riskLevel || "none"} onValueChange={(v) => setRiskLevel(v === "none" ? "" : v)}>
                      <SelectTrigger className="h-9">
                        <SelectValue placeholder="选择风险等级" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">无</SelectItem>
                        <SelectItem value="high">高</SelectItem>
                        <SelectItem value="medium">中</SelectItem>
                        <SelectItem value="low">低</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  {/* 测试点编号 */}
                  <div className="space-y-1.5">
                    <Label className="text-sm">测试点编号</Label>
                    <Input
                      placeholder="如 TP-001"
                      value={tpCode}
                      onChange={(e) => setTpCode(e.target.value)}
                      className="h-9"
                    />
                  </div>

                  {/* 用例种类 */}
                  <div className="space-y-1.5">
                    <Label className="text-sm">用例种类</Label>
                    <Select value={caseKind} onValueChange={setCaseKind}>
                      <SelectTrigger className="h-9">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="sit">SIT</SelectItem>
                        <SelectItem value="uat">UAT</SelectItem>
                        <SelectItem value="smoke">冒烟</SelectItem>
                        <SelectItem value="api">API</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </div>
            </ScrollArea>
          </div>
        </div>

        {/* 底部操作栏 */}
        <div className="flex items-center justify-between border-t px-6 py-4">
          <div className="flex items-center gap-2">
            <Checkbox
              id="createAnother"
              checked={createAnother}
              onCheckedChange={(checked) => setCreateAnother(checked as boolean)}
            />
            <Label htmlFor="createAnother" className="text-sm font-normal">
              继续创建下一个
            </Label>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button onClick={handleSubmit} disabled={submitting || !formData.name.trim()}>
              {submitting ? "保存中..." : isEdit ? "保存" : "创建"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
// NOTE  My80OmFIVnBZMlhscm9ua3VMazZOamsyV0E9PTo2MWZhZDUxMg==

