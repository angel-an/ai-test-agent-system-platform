"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { CheckCircle2, Info, Loader2, Plus, RefreshCw, Route, Save } from "lucide-react";
import { MainLayout } from "@/components/layout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  createWebPathTemplate,
  getWebPathTemplates,
  matchWebPathTemplate,
  updateWebPathTemplate,
  type WebPathTemplate,
  type WebPathTemplatePayload,
} from "@/lib/api/webPathTemplates";

const DEFAULT_FORM: WebPathTemplatePayload = {
  name: "",
  side: "B端",
  module: "",
  business_type: "",
  action: "新建",
  base_url: "https://example.invalid/console/login",
  login_profile: "WEB_TEST_DEFAULT",
  match_keywords: [],
  navigation_path: [
    "登录企业控制台",
    "进入门户中心",
    "点击目标业务卡片",
    "点击目标菜单",
    "进入目标功能管理",
    "点击创建",
    "选择目标活动类型",
  ],
  description: "脱敏路径模板，不包含真实环境地址、账号或密码",
  status: "active",
};

function splitLines(value: string) {
  return value
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function toTextarea(value: string[]) {
  return value.join("\n");
}

export default function WebPathTemplatesPage() {
  const params = useParams();
  const projectId = params.projectId as string;
  const [templates, setTemplates] = React.useState<WebPathTemplate[]>([]);
  const [selected, setSelected] = React.useState<WebPathTemplate | null>(null);
  const [form, setForm] = React.useState<WebPathTemplatePayload>(DEFAULT_FORM);
  const [keywordsText, setKeywordsText] = React.useState("");
  const [pathText, setPathText] = React.useState(toTextarea(DEFAULT_FORM.navigation_path));
  const [matchText, setMatchText] = React.useState("储值免单活动 新建 营销活动管理 B端");
  const [matchResult, setMatchResult] = React.useState<string>("");
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [matching, setMatching] = React.useState(false);

  const loadTemplates = React.useCallback(async () => {
    try {
      setLoading(true);
      const response = await getWebPathTemplates(projectId);
      if (response.success) {
        setTemplates(response.data.items);
      }
    } catch (error) {
      console.error("Failed to load web path templates:", error);
      toast.error("加载路径配置失败");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  React.useEffect(() => {
    if (projectId) {
      loadTemplates();
    }
  }, [projectId, loadTemplates]);

  const selectTemplate = (template: WebPathTemplate) => {
    const nextForm: WebPathTemplatePayload = {
      name: template.name,
      side: template.side || "",
      module: template.module || "",
      business_type: template.business_type || "",
      action: template.action || "",
      base_url: template.base_url || "",
      login_profile: template.login_profile || "WEB_TEST_DEFAULT",
      match_keywords: template.match_keywords || [],
      navigation_path: template.navigation_path || [],
      description: template.description || "",
      status: template.status || "active",
    };
    setSelected(template);
    setForm(nextForm);
    setKeywordsText(toTextarea(nextForm.match_keywords));
    setPathText(toTextarea(nextForm.navigation_path));
    setMatchResult("");
  };

  const createNew = () => {
    setSelected(null);
    setForm(DEFAULT_FORM);
    setKeywordsText("");
    setPathText(toTextarea(DEFAULT_FORM.navigation_path));
    setMatchResult("");
  };

  const buildPayload = (): WebPathTemplatePayload => ({
    ...form,
    match_keywords: splitLines(keywordsText),
    navigation_path: splitLines(pathText),
    base_url: form.base_url || "https://example.invalid/console/login",
    login_profile: form.login_profile || "WEB_TEST_DEFAULT",
  });

  const saveTemplate = async () => {
    const payload = buildPayload();
    if (!payload.name.trim()) {
      toast.error("请输入模板名称");
      return;
    }
    if (payload.navigation_path.length === 0) {
      toast.error("请至少配置一个 Web 验证路径步骤");
      return;
    }

    try {
      setSaving(true);
      const response = selected
        ? await updateWebPathTemplate(projectId, selected.id, payload)
        : await createWebPathTemplate(projectId, payload);
      if (response.success) {
        toast.success(selected ? "路径配置已更新" : "路径配置已创建");
        setSelected(response.data);
        await loadTemplates();
      }
    } catch (error) {
      console.error("Failed to save web path template:", error);
      toast.error("保存路径配置失败");
    } finally {
      setSaving(false);
    }
  };

  const testMatch = async () => {
    try {
      setMatching(true);
      const response = await matchWebPathTemplate(projectId, {
        text: matchText,
        side: form.side,
        module: form.module,
        business_type: form.business_type,
        action: form.action,
      });
      if (response.success && response.data.matched && response.data.template) {
        setMatchResult(`命中：${response.data.template.name}，得分 ${response.data.score}`);
      } else {
        setMatchResult("未命中路径模板");
      }
    } catch (error) {
      console.error("Failed to match web path template:", error);
      toast.error("匹配验证失败");
    } finally {
      setMatching(false);
    }
  };

  return (
    <MainLayout title="项目路径配置">
      <div className="grid h-[calc(100vh-8rem)] grid-cols-[320px_1fr] gap-4">
        <Card className="overflow-hidden">
          <CardHeader className="flex-row items-center justify-between space-y-0 border-b p-4">
            <CardTitle className="text-base">路径模板</CardTitle>
            <Button size="sm" variant="outline" onClick={createNew}>
              <Plus className="mr-2 h-4 w-4" />
              新建
            </Button>
          </CardHeader>
          <CardContent className="space-y-2 p-3">
            <Button variant="ghost" className="w-full justify-start" onClick={loadTemplates}>
              {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
              刷新列表
            </Button>
            {templates.map((template) => (
              <button
                key={template.id}
                type="button"
                onClick={() => selectTemplate(template)}
                className="w-full rounded-md border p-3 text-left text-sm hover:bg-accent"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{template.name}</span>
                  <Badge variant={template.status === "active" ? "default" : "outline"}>
                    {template.status === "active" ? "启用" : "停用"}
                  </Badge>
                </div>
                <div className="mt-1 truncate text-xs text-muted-foreground">
                  {[template.side, template.module, template.business_type, template.action].filter(Boolean).join(" / ")}
                </div>
              </button>
            ))}
            {!loading && templates.length === 0 && (
              <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
                暂无路径配置
              </div>
            )}
          </CardContent>
        </Card>

        <div className="space-y-4 overflow-auto">
          <Card>
            <CardHeader className="border-b p-4">
              <CardTitle className="flex items-center text-base">
                <Info className="mr-2 h-4 w-4" />
                使用说明
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 p-4 text-sm text-muted-foreground">
              <p>
                路径配置用于补充需求文档中未明确的 Web 入口和菜单路径。生成或刷新 Web 用例时，系统会按端类型、模块、业务类型、动作和关键词匹配启用模板。
              </p>
              <p>
                命中后会把验证路径写入用例，并用于 Web 最小闭环脚本。建议填写模板名称、端类型、模块、业务类型、动作、匹配关键词和路径步骤；入口 URL 和登录配置请使用脱敏占位或环境变量，不保存真实账号密码。
              </p>
              <p>
                保存前可在下方“匹配验证”中输入需求标题或用例描述，确认是否能命中当前项目路径模板。
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="border-b p-4">
              <CardTitle className="flex items-center text-base">
                <Route className="mr-2 h-4 w-4" />
                {selected ? "编辑路径配置" : "新建路径配置"}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 p-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>模板名称</Label>
                  <Input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
                </div>
                <div className="space-y-2">
                  <Label>状态</Label>
                  <Select value={form.status} onValueChange={(value) => setForm({ ...form, status: value })}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="active">启用</SelectItem>
                      <SelectItem value="inactive">停用</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>端类型</Label>
                  <Input value={form.side || ""} onChange={(event) => setForm({ ...form, side: event.target.value })} />
                </div>
                <div className="space-y-2">
                  <Label>模块</Label>
                  <Input value={form.module || ""} onChange={(event) => setForm({ ...form, module: event.target.value })} />
                </div>
                <div className="space-y-2">
                  <Label>业务类型</Label>
                  <Input value={form.business_type || ""} onChange={(event) => setForm({ ...form, business_type: event.target.value })} />
                </div>
                <div className="space-y-2">
                  <Label>动作</Label>
                  <Input value={form.action || ""} onChange={(event) => setForm({ ...form, action: event.target.value })} />
                </div>
                <div className="space-y-2">
                  <Label>入口 URL</Label>
                  <Input value={form.base_url || ""} onChange={(event) => setForm({ ...form, base_url: event.target.value })} />
                </div>
                <div className="space-y-2">
                  <Label>登录配置引用</Label>
                  <Input value={form.login_profile || ""} onChange={(event) => setForm({ ...form, login_profile: event.target.value })} />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>匹配关键词</Label>
                  <Textarea rows={7} value={keywordsText} onChange={(event) => setKeywordsText(event.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>Web 验证路径</Label>
                  <Textarea rows={7} value={pathText} onChange={(event) => setPathText(event.target.value)} />
                </div>
              </div>

              <div className="space-y-2">
                <Label>说明</Label>
                <Textarea rows={3} value={form.description || ""} onChange={(event) => setForm({ ...form, description: event.target.value })} />
              </div>

              <div className="flex items-center justify-end gap-2">
                <Button variant="outline" onClick={testMatch} disabled={matching}>
                  {matching ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}
                  验证匹配
                </Button>
                <Button onClick={saveTemplate} disabled={saving}>
                  {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                  保存
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="border-b p-4">
              <CardTitle className="text-base">匹配验证</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 p-4">
              <Textarea rows={3} value={matchText} onChange={(event) => setMatchText(event.target.value)} />
              {matchResult && (
                <div className="rounded-md border bg-muted px-3 py-2 text-sm">{matchResult}</div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </MainLayout>
  );
}
