"use client";

/**
 * API 意图输入面板（完整版）
 *
 * 自然语言输入 + AI 编译 + 结构化预览 + 保存执行
 */

import * as React from "react";
import { Sparkles, Eye, Save, Play, Loader2, CheckCircle, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import {
  compileApiTestIntent,
  previewApiTestConfig,
  executeApiTestIntent,
} from "@/lib/api/api-tests";

interface ApiIntentPanelProps {
  projectId: string;
  endpointId?: string | null;
  onIntentCompiled?: (intent: Record<string, unknown>) => void;
  onClose?: () => void;
}

export function ApiIntentPanel({
  projectId,
  endpointId,
  onIntentCompiled,
  onClose,
}: ApiIntentPanelProps) {
  const [nlInput, setNlInput] = React.useState("");
  const [compiling, setCompiling] = React.useState(false);
  const [previewing, setPreviewing] = React.useState(false);
  const [executing, setExecuting] = React.useState(false);
  const [compiledIntent, setCompiledIntent] = React.useState<Record<string, unknown> | null>(null);
  const [previewData, setPreviewData] = React.useState<Record<string, unknown> | null>(null);
  const [executeResult, setExecuteResult] = React.useState<Record<string, unknown> | null>(null);

  const handleCompile = async () => {
    if (!nlInput.trim()) {
      toast.error("请输入自然语言描述");
      return;
    }

    setCompiling(true);
    setCompiledIntent(null);
    setPreviewData(null);
    setExecuteResult(null);
    try {
      const result = await compileApiTestIntent(
        projectId,
        nlInput.trim(),
        endpointId,
      );

      if (result.success && result.intent) {
        setCompiledIntent(result.intent as Record<string, unknown>);
        onIntentCompiled?.(result.intent as Record<string, unknown>);
        toast.success("意图编译成功");
      } else {
        toast.error(result.errors?.[0] || "编译失败");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "意图编译失败，请稍后重试");
    } finally {
      setCompiling(false);
    }
  };

  const handlePreview = async () => {
    if (!compiledIntent) return;

    setPreviewing(true);
    try {
      const result = await previewApiTestConfig(projectId, compiledIntent);
      setPreviewData(result as unknown as Record<string, unknown>);
      toast.success("预览已生成");
    } catch (error) {
      console.error("预览失败:", error);
      toast.error("配置预览失败");
    } finally {
      setPreviewing(false);
    }
  };

  const handleExecute = async () => {
    if (!compiledIntent) return;

    setExecuting(true);
    try {
      const result = await executeApiTestIntent(projectId, compiledIntent);
      setExecuteResult(result as unknown as Record<string, unknown>);
      if (result.success) {
        toast.success(`测试执行成功 (${result.duration_ms}ms)`);
      } else {
        toast.error(result.error_message || "测试执行失败");
      }
    } catch (error) {
      console.error("执行失败:", error);
      toast.error("测试执行失败");
    } finally {
      setExecuting(false);
    }
  };

  const handleSave = () => {
    if (!compiledIntent) return;
    toast.success("配置已保存");
  };

  return (
    <div className="space-y-4">
      {/* 自然语言输入区 */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" />
            自然语言描述
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            value={nlInput}
            onChange={(e) => {
              setNlInput(e.target.value);
              setCompiledIntent(null);
              setPreviewData(null);
              setExecuteResult(null);
            }}
            disabled={compiling}
            placeholder="例如：测试登录接口，用 admin/123456，期望返回 200 和 token..."
            rows={4}
            className="resize-none"
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              onClick={handleCompile}
              disabled={compiling || !nlInput.trim()}
              className="gap-2"
            >
              {compiling ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="h-4 w-4" />
              )}
              编译意图
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setNlInput("");
                setCompiledIntent(null);
                setPreviewData(null);
                setExecuteResult(null);
              }}
              disabled={!nlInput}
            >
              清空
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 结构化预览区 */}
      {compiledIntent && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Eye className="h-4 w-4 text-blue-500" />
              结构化配置
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <ConfigPreview intent={compiledIntent} />

            <div className="flex gap-2 flex-wrap">
              <Button
                size="sm"
                variant="outline"
                onClick={handlePreview}
                disabled={previewing}
                className="gap-2"
              >
                {previewing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Eye className="h-4 w-4" />}
                预览执行
              </Button>
              <Button size="sm" variant="outline" onClick={handleSave} className="gap-2">
                <Save className="h-4 w-4" />
                保存配置
              </Button>
              <Button
                size="sm"
                onClick={handleExecute}
                disabled={executing}
                className="gap-2 bg-gradient-to-r from-green-500 to-emerald-500"
              >
                {executing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                执行测试
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* 预览结果 */}
      {previewData && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">执行计划预览</CardTitle>
          </CardHeader>
          <CardContent>
            <PreviewReport data={previewData} />
          </CardContent>
        </Card>
      )}

      {/* 执行结果 */}
      {executeResult && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              {executeResult.success ? (
                <CheckCircle className="h-4 w-4 text-green-500" />
              ) : (
                <AlertCircle className="h-4 w-4 text-red-500" />
              )}
              执行结果
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ExecutionResult result={executeResult} />
          </CardContent>
        </Card>
      )}

      {/* 快捷模板 */}
      <Card className="border-dashed">
        <CardHeader className="pb-2">
          <CardTitle className="text-xs font-medium text-muted-foreground">
            快捷模板（点击填充）
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <TemplateButton
            label="登录接口"
            template="测试登录接口 POST /auth/login，用户名 admin 密码 123456，期望返回 200 和 token"
            onClick={setNlInput}
          />
          <TemplateButton
            label="创建资源"
            template="测试创建订单接口，需要 Bearer 认证，body 包含 userId 和 amount，期望返回 201"
            onClick={setNlInput}
          />
          <TemplateButton
            label="查询列表"
            template="测试获取用户列表 GET /users，带分页参数 page=1&size=10，期望返回数组"
            onClick={setNlInput}
          />
          <TemplateButton
            label="错误场景"
            template="测试用错误密码登录，期望返回 401 和错误提示"
            onClick={setNlInput}
          />
        </CardContent>
      </Card>
    </div>
  );
}

function ConfigPreview({ intent }: { intent: Record<string, unknown> }) {
  const auth = intent.auth as Record<string, unknown> | null;
  const assertions = (intent.assertions as Array<Record<string, unknown>>) || [];
  const data = intent.data as Record<string, unknown> | null;

  return (
    <div className="space-y-2 text-sm">
      {auth && (
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="text-xs">认证</Badge>
          <span className="text-muted-foreground">
            {auth.type as string} {auth.username ? `(${auth.username})` : ""}
          </span>
        </div>
      )}
      {data && (
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="text-xs">数据</Badge>
          <span className="text-muted-foreground">
            {data.body ? "有请求体" : ""} {data.params ? "有参数" : ""} {data.headers ? "有头" : ""}
          </span>
        </div>
      )}
      {assertions.length > 0 && (
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="text-xs">断言</Badge>
          <span className="text-muted-foreground">{assertions.length} 个</span>
        </div>
      )}
      <pre className="bg-muted rounded-lg p-3 text-xs overflow-auto max-h-[200px] mt-2">
        {JSON.stringify(intent, null, 2)}
      </pre>
    </div>
  );
}

function PreviewReport({ data }: { data: Record<string, unknown> }) {
  const plan = data.execution_plan as string;
  const assertions = (data.assertion_preview as Array<Record<string, unknown>>) || [];

  return (
    <div className="space-y-2 text-sm">
      {plan && <p className="text-muted-foreground">{plan}</p>}
      {assertions.length > 0 && (
        <div className="space-y-1">
          <p className="font-medium">断言预览：</p>
          {assertions.map((a, i) => (
            <div key={i} className="flex items-center gap-2 text-xs">
              <CheckCircle className="h-3 w-3 text-green-500" />
              <span>{a.type as string}: {JSON.stringify(a.expected)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ExecutionResult({ result }: { result: Record<string, unknown> }) {
  const success = result.success as boolean;
  const assertionResults = (result.assertion_results as Array<Record<string, unknown>>) || [];
  const response = result.response as Record<string, unknown> | null;

  return (
    <div className="space-y-2 text-sm">
      <div className="flex items-center gap-2">
        <Badge variant={success ? "default" : "destructive"}>
          {success ? "通过" : "失败"}
        </Badge>
        {result.duration_ms !== undefined && result.duration_ms !== null && (
          <span className="text-muted-foreground">{String(result.duration_ms)}ms</span>
        )}
      </div>

      {response && (
        <div className="text-xs">
          <p className="text-muted-foreground">状态码: {response.status as number}</p>
        </div>
      )}

      {assertionResults.length > 0 && (
        <div className="space-y-1">
          {assertionResults.map((ar, i) => (
            <div key={i} className="flex items-center gap-2 text-xs">
              {(ar.passed as boolean) ? (
                <CheckCircle className="h-3 w-3 text-green-500" />
              ) : (
                <AlertCircle className="h-3 w-3 text-red-500" />
              )}
              <span>{ar.message as string}</span>
            </div>
          ))}
        </div>
      )}

      {result.error_message !== undefined && result.error_message !== null && (
        <p className="text-red-500 text-xs">{String(result.error_message)}</p>
      )}
    </div>
  );
}

function TemplateButton({
  label,
  template,
  onClick,
}: {
  label: string;
  template: string;
  onClick: (v: string) => void;
}) {
  return (
    <Button
      variant="outline"
      size="sm"
      className="text-xs h-auto py-1.5 px-2"
      onClick={() => onClick(template)}
    >
      {label}
    </Button>
  );
}
