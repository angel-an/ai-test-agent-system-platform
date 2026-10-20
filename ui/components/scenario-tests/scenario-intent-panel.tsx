"use client";

/**
 * 场景意图输入面板（完整版）
 *
 * 自然语言描述完整业务流程，编译为多步骤场景配置
 */

import * as React from "react";
import { Sparkles, Eye, Save, Play, Loader2, CheckCircle, Workflow } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import { compileScenarioIntent, executeScenarioIntent } from "@/lib/api/scenarios";

interface ScenarioIntentPanelProps {
  projectId: string;
  scenarioId?: string | null;
  onIntentCompiled?: (intent: Record<string, unknown>) => void;
}

export function ScenarioIntentPanel({
  projectId,
  scenarioId,
  onIntentCompiled,
}: ScenarioIntentPanelProps) {
  const [nlInput, setNlInput] = React.useState("");
  const [compiling, setCompiling] = React.useState(false);
  const [executing, setExecuting] = React.useState(false);
  const [compiledIntent, setCompiledIntent] = React.useState<Record<string, unknown> | null>(null);
  const [executeResult, setExecuteResult] = React.useState<Record<string, unknown> | null>(null);

  const handleCompile = async () => {
    if (!nlInput.trim()) {
      toast.error("请输入场景描述");
      return;
    }

    if (!scenarioId) {
      toast.error("请先选择一个场景，或创建新场景");
      return;
    }

    setCompiling(true);
    setExecuteResult(null);
    try {
      const result = await compileScenarioIntent(
        scenarioId,
        nlInput.trim(),
      );

      if (result.success && result.intent) {
        setCompiledIntent(result.intent as Record<string, unknown>);
        onIntentCompiled?.(result.intent as Record<string, unknown>);
        toast.success("场景意图编译成功");
      } else {
        toast.error((result as any).errors?.[0] || "场景编译失败");
      }
    } catch (error) {
      console.error("编译失败:", error);
      toast.error("场景编译失败，请稍后重试");
    } finally {
      setCompiling(false);
    }
  };

  const handleExecute = async () => {
    if (!compiledIntent || !scenarioId) return;

    setExecuting(true);
    try {
      const result = await executeScenarioIntent(scenarioId, compiledIntent);
      setExecuteResult(result as unknown as Record<string, unknown>);
      toast.success(`场景执行${result.status === 'completed' ? '成功' : '完成'}`);
    } catch (error) {
      console.error("执行失败:", error);
      toast.error("场景执行失败");
    } finally {
      setExecuting(false);
    }
  };

  const steps = (compiledIntent?.steps as Array<Record<string, unknown>>) || [];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Workflow className="h-4 w-4 text-purple-500" />
            场景描述
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            value={nlInput}
            onChange={(e) => setNlInput(e.target.value)}
            placeholder="例如：用户登录后创建订单并支付，登录获取 token，创建订单获取 orderId..."
            rows={5}
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
              编译场景
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setNlInput("");
                setCompiledIntent(null);
                setExecuteResult(null);
              }}
            >
              清空
            </Button>
          </div>
        </CardContent>
      </Card>

      {compiledIntent && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Eye className="h-4 w-4 text-blue-500" />
              场景步骤预览
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {steps.length > 0 ? (
              <div className="space-y-2">
                {steps.map((step, index) => (
                  <div
                    key={index}
                    className="flex items-center gap-3 p-2 rounded-lg bg-muted/50"
                  >
                    <div className="w-6 h-6 rounded-full bg-primary/10 flex items-center justify-center text-xs font-semibold">
                      {index + 1}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">
                        {step.name as string}
                      </p>
                      <p className="text-xs text-muted-foreground truncate">
                        {step.description as string}
                      </p>
                    </div>
                    {(step.assertions as Array<unknown>)?.length > 0 && (
                      <Badge variant="outline" className="text-xs">
                        {(step.assertions as Array<unknown>).length} 断言
                      </Badge>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">暂无步骤，AI 将自动生成分步配置</p>
            )}

            <div className="flex gap-2">
              <Button size="sm" variant="outline" className="gap-2">
                <Save className="h-4 w-4" />
                保存场景
              </Button>
              {scenarioId && (
                <Button
                  size="sm"
                  onClick={handleExecute}
                  disabled={executing}
                  className="gap-2 bg-gradient-to-r from-green-500 to-emerald-500"
                >
                  {executing ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Play className="h-4 w-4" />
                  )}
                  执行场景
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {executeResult && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <CheckCircle className="h-4 w-4 text-green-500" />
              执行结果
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-sm space-y-1">
              <p>Run ID: {executeResult.run_id as string}</p>
              <p>状态: <Badge>{executeResult.status as string}</Badge></p>
              <p className="text-muted-foreground">{executeResult.message as string}</p>
            </div>
          </CardContent>
        </Card>
      )}

      <Card className="border-dashed">
        <CardHeader className="pb-2">
          <CardTitle className="text-xs font-medium text-muted-foreground">
            场景模板
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <TemplateButton
            label="下单流程"
            template="用户登录获取 token，查询商品列表，选择商品创建订单，使用优惠券后支付订单"
            onClick={setNlInput}
          />
          <TemplateButton
            label="活动发布"
            template="管理员登录，创建活动，配置活动规则，发布活动，用户端查看活动"
            onClick={setNlInput}
          />
          <TemplateButton
            label="贷款流程"
            template="用户注册，实名认证，绑定银行卡，申请贷款，审批通过后放款"
            onClick={setNlInput}
          />
        </CardContent>
      </Card>
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
