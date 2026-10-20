"use client";

import * as React from "react";
import { toast } from "sonner";
import {
  Shield,
  Search,
  PenLine,
  GitMerge,
  AlertTriangle,
  Trash2,
  Play,
  Lock,
  Unlock,
  AlertCircle,
  CheckCircle2,
  XCircle,
  SkipForward,
  Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";
import {
  getAPILayerConfig,
  executeAPILayers,
  type APILayerConfig,
  type APILayerResult,
  type GateStatus,
} from "@/lib/api/api-tests";

const LAYER_ICONS: Record<string, React.ReactNode> = {
  p0_auth: <Shield className="h-4 w-4" />,
  p1_query: <Search className="h-4 w-4" />,
  p1_write: <PenLine className="h-4 w-4" />,
  p2_composite: <GitMerge className="h-4 w-4" />,
  p2_negative: <AlertTriangle className="h-4 w-4" />,
  p3_cleanup: <Trash2 className="h-4 w-4" />,
};

const LAYER_COLORS: Record<string, string> = {
  p0_auth: "bg-blue-500",
  p1_query: "bg-green-500",
  p1_write: "bg-yellow-500",
  p2_composite: "bg-purple-500",
  p2_negative: "bg-red-500",
  p3_cleanup: "bg-gray-500",
};

interface APILayerPanelProps {
  projectId: string;
}

export function APILayerPanel({ projectId }: APILayerPanelProps) {
  const [layers, setLayers] = React.useState<APILayerConfig[]>([]);
  const [gateStatus, setGateStatus] = React.useState<GateStatus | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [executing, setExecuting] = React.useState(false);
  const [results, setResults] = React.useState<APILayerResult[] | null>(null);
  const [selectedLayers, setSelectedLayers] = React.useState<Set<string>>(new Set());

  // 加载分层配置
  const loadConfig = React.useCallback(async () => {
    try {
      setLoading(true);
      const config = await getAPILayerConfig(projectId);
      setLayers(config.layers);
      setGateStatus(config.gate_status);
      // 默认选中已启用的层
      setSelectedLayers(new Set(config.layers.filter(l => l.enabled).map(l => l.layer)));
    } catch (error) {
      console.error("Failed to load API layer config:", error);
      toast.error("加载 API 分层配置失败");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  React.useEffect(() => {
    loadConfig();
  }, [loadConfig]);

  // 切换分层选择
  const toggleLayer = (layer: string) => {
    setSelectedLayers(prev => {
      const next = new Set(prev);
      if (next.has(layer)) {
        next.delete(layer);
      } else {
        next.add(layer);
      }
      return next;
    });
  };

  // 执行分层测试
  const handleExecute = async () => {
    if (selectedLayers.size === 0) {
      toast.error("请至少选择一个分层");
      return;
    }

    // 检查是否有风险层被选中
    const selectedRisky = layers.filter(
      l => selectedLayers.has(l.layer) && l.is_risky
    );
    if (selectedRisky.length > 0 && !gateStatus?.run_risky_negative) {
      toast.error(`风险层 (${selectedRisky.map(l => l.layer_name).join(", ")}) 需要开启 RUN_RISKY_NEG`);
      return;
    }

    try {
      setExecuting(true);
      setResults(null);
      const layerResults = await executeAPILayers(projectId, {
        layers: Array.from(selectedLayers),
      });
      setResults(layerResults);
      toast.success("API 分层测试执行完成");
    } catch (error: any) {
      console.error("Failed to execute API layers:", error);
      toast.error(error.message || "执行失败");
    } finally {
      setExecuting(false);
    }
  };

  // 全选/取消全选
  const toggleAll = () => {
    if (selectedLayers.size === layers.length) {
      setSelectedLayers(new Set());
    } else {
      setSelectedLayers(new Set(layers.map(l => l.layer)));
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="w-full min-w-0 space-y-4 break-words">
      {/* 安全模式提示 */}
      {gateStatus?.safe_mode && (
        <div className="rounded-lg border border-yellow-200 bg-yellow-50 dark:bg-yellow-950/20 p-3 flex items-start gap-2">
          <AlertCircle className="h-4 w-4 text-yellow-600 shrink-0 mt-0.5" />
          <div className="min-w-0 text-sm [overflow-wrap:anywhere]">
            <p className="font-medium text-yellow-800 dark:text-yellow-200">
              安全模式
            </p>
            <p className="text-yellow-700 dark:text-yellow-300/80">
              当前处于安全模式（LIVE_API 未开启）。需要 live 的分层（AUTH/QUERY/WRITE/COMPOSITE/NEGATIVE）将被跳过。只有 CLEANUP 层可直接执行。
            </p>
          </div>
        </div>
      )}

      {/* 门控状态 */}
      <div className="flex items-center gap-2 flex-wrap">
        <Badge variant={gateStatus?.live_api ? "default" : "secondary"} className="gap-1">
          {gateStatus?.live_api ? <Unlock className="h-3 w-3" /> : <Lock className="h-3 w-3" />}
          LIVE_API {gateStatus?.live_api ? "ON" : "OFF"}
        </Badge>
        <Badge variant={gateStatus?.run_risky_negative ? "destructive" : "secondary"} className="gap-1">
          <AlertTriangle className="h-3 w-3" />
          RISKY {gateStatus?.run_risky_negative ? "ON" : "OFF"}
        </Badge>
      </div>

      {/* 分层选择 */}
      <div className="rounded-lg border">
        <div className="flex items-center justify-between px-4 py-2 border-b bg-muted/30">
          <span className="text-sm font-medium">分层选择</span>
          <Button variant="ghost" size="sm" onClick={toggleAll}>
            {selectedLayers.size === layers.length ? "取消全选" : "全选"}
          </Button>
        </div>
        <div className="divide-y">
          {layers.map((layer) => (
            <div
              key={layer.layer}
              className={cn(
                "flex items-center gap-3 px-4 py-3 transition-colors",
                selectedLayers.has(layer.layer) && "bg-primary/5"
              )}
            >
              <Checkbox
                checked={selectedLayers.has(layer.layer)}
                onCheckedChange={() => toggleLayer(layer.layer)}
                disabled={executing}
              />
              <div className={cn("w-8 h-8 rounded-full flex items-center justify-center text-white", LAYER_COLORS[layer.layer])}>
                {LAYER_ICONS[layer.layer]}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-sm">{layer.layer_name}</span>
                  <code className="text-xs text-muted-foreground">{layer.layer}</code>
                </div>
                <div className="flex flex-wrap items-center gap-1 mt-0.5">
                  {layer.requires_live && (
                    <Badge variant="outline" className="text-xs h-5">
                      <Lock className="h-3 w-3 mr-1" />
                      需 live
                    </Badge>
                  )}
                  {layer.is_risky && (
                    <Badge variant="destructive" className="text-xs h-5">
                      <AlertTriangle className="h-3 w-3 mr-1" />
                      风险
                    </Badge>
                  )}
                  {!layer.enabled && (
                    <Badge variant="secondary" className="text-xs h-5">默认关闭</Badge>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 执行按钮 */}
      <Button
        onClick={handleExecute}
        disabled={executing || selectedLayers.size === 0}
        className="w-full gap-2"
      >
        {executing ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            执行中...
          </>
        ) : (
          <>
            <Play className="h-4 w-4" />
            执行选中的分层 ({selectedLayers.size})
          </>
        )}
      </Button>

      {/* 执行结果 */}
      {results && (
        <div className="rounded-lg border">
          <div className="px-4 py-2 border-b bg-muted/30">
            <span className="text-sm font-medium">执行结果</span>
          </div>
          <div className="divide-y">
            {results.map((result) => (
              <div key={result.layer} className="px-4 py-3 flex items-center gap-3">
                <div className={cn("w-8 h-8 rounded-full flex items-center justify-center text-white", LAYER_COLORS[result.layer])}>
                  {LAYER_ICONS[result.layer]}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-sm">{result.layer_name}</span>
                    <StatusBadge status={result.status} />
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    {result.test_count > 0 && (
                      <span>
                        {result.passed_count} 通过 / {result.failed_count} 失败 / {result.skipped_count} 跳过
                        {" "}·{" "}
                      </span>
                    )}
                    <span>{result.duration_ms}ms</span>
                    {result.error_message && (
                      <span className="text-red-500 ml-2">{result.error_message}</span>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  switch (status) {
    case "passed":
      return (
        <Badge variant="default" className="bg-green-500 text-white gap-1">
          <CheckCircle2 className="h-3 w-3" />
          通过
        </Badge>
      );
    case "failed":
      return (
        <Badge variant="destructive" className="gap-1">
          <XCircle className="h-3 w-3" />
          失败
        </Badge>
      );
    case "skipped":
      return (
        <Badge variant="secondary" className="gap-1">
          <SkipForward className="h-3 w-3" />
          跳过
        </Badge>
      );
    case "error":
      return (
        <Badge variant="destructive" className="gap-1">
          <AlertCircle className="h-3 w-3" />
          错误
        </Badge>
      );
    default:
      return <Badge variant="outline">{status}</Badge>;
  }
}
