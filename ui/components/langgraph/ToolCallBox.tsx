// NOTE  MC80OmFIVnBZMlhscm9ua3VMazZZalUzVUE9PTo1ZjkxNmE1Mg==

"use client";

import React, { useState, useMemo, useCallback } from "react";
import {
  ChevronDown,
  ChevronUp,
  Terminal,
  AlertCircle,
  Loader2,
  CircleCheckBigIcon,
  StopCircle,
  Shield,
  Search,
  Bug,
  FileSearch,
  Globe,
  Lock,
  Unlock,
  Zap,
  Crosshair,
  Database,
  Server,
  Eye,
  Activity,
  FileDown,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ToolCall, ActionRequest, ReviewConfig } from "@/lib/langgraph/types";
import { cn } from "@/lib/utils";
import { LoadExternalComponent } from "@langchain/langgraph-sdk/react-ui";
import { ToolApprovalInterrupt } from "@/components/langgraph/ToolApprovalInterrupt";
// NOTE  MS80OmFIVnBZMlhscm9ua3VMazZZalUzVUE9PTo1ZjkxNmE1Mg==

interface ToolCallBoxProps {
  toolCall: ToolCall;
  uiComponent?: any;
  stream?: any;
  graphId?: string;
  actionRequest?: ActionRequest;
  reviewConfig?: ReviewConfig;
  onResume?: (value: any) => void;
  isLoading?: boolean;
}

// 工具名称到图标的映射
const TOOL_ICON_MAP: Record<string, React.ReactNode> = {
  // 信息收集
  recon_subdomains: <Globe size={14} />,
  recon_subdomain: <Globe size={14} />,
  recon_port_scan: <Server size={14} />,
  recon_port: <Server size={14} />,
  recon_directory_scan: <Search size={14} />,
  recon_dir_scan: <Search size={14} />,
  recon_fingerprint: <FingerprintIcon />,
  recon_full_scan: <Activity size={14} />,
  discover_apis_from_page: <Eye size={14} />,
  // 漏洞利用
  exploit_sqli: <Database size={14} />,
  exploit_xss: <Crosshair size={14} />,
  exploit_lfi: <FileSearch size={14} />,
  exploit_file_download: <FileDown size={14} />,
  exploit_full_scan: <Bug size={14} />,
  // Web 扫描
  scan_web_xss: <Crosshair size={14} />,
  scan_web_csrf: <Shield size={14} />,
  scan_web_clickjacking: <Eye size={14} />,
  scan_web_sensitive_info: <Lock size={14} />,
  // API 安全
  api_auth_bypass_test: <Unlock size={14} />,
  api_idor_test: <Eye size={14} />,
  api_input_validation_test: <Zap size={14} />,
  api_rate_limit_test: <Activity size={14} />,
  // 报告
  generate_pentest_report: <FileTextIcon />,
  generate_html_pentest_report: <FileTextIcon />,
  generate_executive_summary: <FileTextIcon />,
  // 存储
  storage_create_project: <Database size={14} />,
  storage_add_vulnerability: <Bug size={14} />,
  storage_save_scan_result: <Database size={14} />,
  // 管理
  mgmt_create_pentest: <Shield size={14} />,
  mgmt_save_pentest_report: <FileTextIcon />,
  mgmt_add_vulnerability: <Bug size={14} />,
};

function FingerprintIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 10a2 2 0 0 0-2 2c0 1.02-.1 2.51-.26 4"/><path d="M14 13.12c0 2.38 0 6.38-1 8.88"/><path d="M17.29 21.02c.12-.6.43-2.3.5-3.02"/><path d="M2 12a10 10 0 0 1 18-6"/><path d="M2 16h.01"/><path d="M21.8 16c.2-2 .131-5.354 0-6"/><path d="M5 19.5C5.5 18 6 15 6 12a6 6 0 0 1 .34-2"/><path d="M8.65 22c.21-.66.45-1.32.57-2"/><path d="M9 6.8a6 6 0 0 1 9 5.2v2"/>
    </svg>
  );
}

function FileTextIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>
    </svg>
  );
}

// 获取工具图标
function getToolIcon(name: string, status: ToolCall["status"]): React.ReactNode {
  // 如果工具有特定图标，使用特定图标
  if (TOOL_ICON_MAP[name]) {
    return TOOL_ICON_MAP[name];
  }
  // 根据工具名称前缀匹配
  if (name.startsWith("recon_")) return <Search size={14} />;
  if (name.startsWith("exploit_")) return <Bug size={14} />;
  if (name.startsWith("scan_web_")) return <Shield size={14} />;
  if (name.startsWith("scan_")) return <Search size={14} />;
  if (name.startsWith("api_")) return <Zap size={14} />;
  if (name.startsWith("generate_")) return <FileTextIcon />;
  if (name.startsWith("storage_")) return <Database size={14} />;
  if (name.startsWith("mgmt_")) return <Shield size={14} />;
  // 默认
  return <Terminal size={14} />;
}

// 获取工具显示名称
function getToolDisplayName(name: string): string {
  const nameMap: Record<string, string> = {
    recon_subdomains: "子域名枚举",
    recon_port_scan: "端口扫描",
    recon_directory_scan: "目录扫描",
    recon_fingerprint: "指纹识别",
    recon_full_scan: "综合信息收集",
    discover_apis_from_page: "页面接口发现",
    exploit_sqli: "SQL 注入检测",
    exploit_xss: "XSS 检测",
    exploit_lfi: "LFI 检测",
    exploit_file_download: "文件下载检测",
    exploit_full_scan: "综合漏洞扫描",
    scan_web_xss: "Web XSS 扫描",
    scan_web_csrf: "CSRF 检测",
    scan_web_clickjacking: "点击劫持检测",
    scan_web_sensitive_info: "敏感信息泄露检测",
    api_auth_bypass_test: "认证绕过测试",
    api_idor_test: "越权访问测试",
    api_input_validation_test: "输入验证测试",
    api_rate_limit_test: "速率限制测试",
    generate_pentest_report: "生成渗透测试报告",
    generate_html_pentest_report: "生成 HTML 报告",
    generate_executive_summary: "生成执行摘要",
    storage_create_project: "创建项目",
    storage_add_vulnerability: "添加漏洞",
    storage_save_scan_result: "保存扫描结果",
    storage_list_vulnerabilities: "查询漏洞列表",
    storage_get_statistics: "获取统计信息",
    mgmt_create_pentest: "创建渗透测试任务",
    mgmt_save_pentest_report: "保存报告",
    mgmt_add_vulnerability: "添加漏洞",
    mgmt_list_pentests: "查询任务列表",
    mgmt_list_reports: "查询报告列表",
    mgmt_list_vulnerabilities: "查询漏洞列表",
    mgmt_update_pentest_status: "更新任务状态",
  };
  return nameMap[name] || name;
}

// 尝试解析和美化 JSON 结果
function formatToolResult(result: string): { type: "json" | "text"; content: string; data?: any } {
  if (!result) return { type: "text", content: "" };

  // 尝试解析 JSON
  try {
    const parsed = JSON.parse(result);
    return { type: "json", content: JSON.stringify(parsed, null, 2), data: parsed };
  } catch {
    // 不是 JSON，返回原文
    return { type: "text", content: result };
  }
}

// 渲染结构化的 JSON 结果（针对渗透测试工具）
function StructuredResultView({ data, toolName }: { data: any; toolName: string }) {
  if (!data || typeof data !== "object") return null;

  // 处理漏洞扫描结果
  if (toolName.startsWith("exploit_") || toolName.startsWith("scan_") || toolName.startsWith("api_")) {
    return <VulnerabilityResultView data={data} />;
  }

  // 处理信息收集结果
  if (toolName.startsWith("recon_")) {
    return <ReconResultView data={data} />;
  }

  // 处理报告生成结果
  if (toolName.startsWith("generate_")) {
    return <ReportResultView data={data} />;
  }

  return null;
}

// 漏洞扫描结果视图
function VulnerabilityResultView({ data }: { data: any }) {
  const findings = data.findings || data.vulnerabilities || data.results;
  const hasFindings = findings && Array.isArray(findings) && findings.length > 0;

  return (
    <div className="space-y-3">
      {/* 状态/摘要信息 */}
      {data.success !== undefined && (
        <div className={cn(
          "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
          data.success
            ? "bg-green-50 text-green-700 border border-green-200"
            : "bg-red-50 text-red-700 border border-red-200"
        )}>
          {data.success ? <CircleCheckBigIcon size={12} /> : <AlertCircle size={12} />}
          {data.success ? "扫描完成" : "扫描失败"}
        </div>
      )}
      {data.message && (
        <p className="text-sm text-muted-foreground">{data.message}</p>
      )}
      {data.summary && (
        <div className="rounded-md bg-muted/40 p-3 text-xs">
          <pre className="whitespace-pre-wrap break-all font-mono leading-5">
            {typeof data.summary === "string" ? data.summary : JSON.stringify(data.summary, null, 2)}
          </pre>
        </div>
      )}

      {/* 漏洞列表 */}
      {hasFindings && (
        <div className="space-y-2">
          <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            发现 {findings.length} 个漏洞
          </h5>
          {findings.map((finding: any, index: number) => (
            <VulnerabilityCard key={index} finding={finding} index={index} />
          ))}
        </div>
      )}

      {/* 原始输出 */}
      {data.output && (
        <div className="rounded-md bg-muted/30 p-3">
          <h5 className="text-xs font-semibold text-muted-foreground mb-2">原始输出</h5>
          <pre className="whitespace-pre-wrap break-all font-mono text-xs leading-5 text-foreground">
            {typeof data.output === "string" ? data.output : JSON.stringify(data.output, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

// 单个漏洞卡片
function VulnerabilityCard({ finding, index }: { finding: any; index: number }) {
  const severity = finding.severity || finding.level || "Info";
  const severityColors: Record<string, string> = {
    Critical: "bg-red-50 border-red-200 text-red-800",
    High: "bg-orange-50 border-orange-200 text-orange-800",
    Medium: "bg-yellow-50 border-yellow-200 text-yellow-800",
    Low: "bg-blue-50 border-blue-200 text-blue-800",
    Info: "bg-gray-50 border-gray-200 text-gray-700",
  };
  const severityColor = severityColors[severity] || severityColors.Info;

  return (
    <div className={cn("rounded-md border p-3", severityColor)}>
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs font-mono font-bold">
          {finding.id || finding.vuln_id || `VL-${String(index + 1).padStart(3, "0")}`}
        </span>
        <span className="text-xs font-medium">{finding.title || finding.name || "未命名漏洞"}</span>
        <span className={cn(
          "ml-auto rounded-full px-2 py-0.5 text-[10px] font-bold uppercase",
          severity === "Critical" && "bg-red-500 text-white",
          severity === "High" && "bg-orange-500 text-white",
          severity === "Medium" && "bg-yellow-500 text-white",
          severity === "Low" && "bg-blue-500 text-white",
          severity === "Info" && "bg-gray-400 text-white",
        )}>
          {severity}
        </span>
      </div>
      {finding.type && (
        <p className="text-xs mb-1"><strong>类型:</strong> {finding.type}</p>
      )}
      {finding.url && (
        <p className="text-xs mb-1"><strong>URL:</strong> <code className="font-mono">{finding.url}</code></p>
      )}
      {finding.parameter && (
        <p className="text-xs mb-1"><strong>参数:</strong> <code className="font-mono">{finding.parameter}</code></p>
      )}
      {finding.description && (
        <p className="text-xs mt-2 leading-relaxed">{finding.description}</p>
      )}
    </div>
  );
}

// 信息收集结果视图
function ReconResultView({ data }: { data: any }) {
  return (
    <div className="space-y-3">
      {data.success !== undefined && (
        <div className={cn(
          "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
          data.success
            ? "bg-green-50 text-green-700 border border-green-200"
            : "bg-red-50 text-red-700 border border-red-200"
        )}>
          {data.success ? <CircleCheckBigIcon size={12} /> : <AlertCircle size={12} />}
          {data.success ? "扫描完成" : "扫描失败"}
        </div>
      )}
      {data.message && (
        <p className="text-sm text-muted-foreground">{data.message}</p>
      )}
      {/* 子域名 */}
      {data.subdomains && Array.isArray(data.subdomains) && data.subdomains.length > 0 && (
        <div>
          <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">
            子域名 ({data.subdomains.length})
          </h5>
          <div className="flex flex-wrap gap-1">
            {data.subdomains.map((s: string, i: number) => (
              <code key={i} className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono">{s}</code>
            ))}
          </div>
        </div>
      )}
      {/* 端口 */}
      {data.ports && Array.isArray(data.ports) && data.ports.length > 0 && (
        <div>
          <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">
            开放端口 ({data.ports.length})
          </h5>
          <div className="grid grid-cols-3 gap-1">
            {data.ports.map((p: any, i: number) => (
              <div key={i} className="text-xs bg-muted px-2 py-1 rounded font-mono">
                {typeof p === "object" ? `${p.port}/${p.protocol || "tcp"}` : p}
              </div>
            ))}
          </div>
        </div>
      )}
      {/* 目录 */}
      {data.directories && Array.isArray(data.directories) && data.directories.length > 0 && (
        <div>
          <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">
            发现目录 ({data.directories.length})
          </h5>
          <div className="space-y-0.5 max-h-32 overflow-y-auto">
            {data.directories.map((d: any, i: number) => (
              <div key={i} className="text-xs font-mono">
                {typeof d === "object" ? d.path || d.url : d}
              </div>
            ))}
          </div>
        </div>
      )}
      {/* 指纹 */}
      {data.technologies && Array.isArray(data.technologies) && data.technologies.length > 0 && (
        <div>
          <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">
            技术栈
          </h5>
          <div className="flex flex-wrap gap-1">
            {data.technologies.map((t: string, i: number) => (
              <span key={i} className="text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full">{t}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// 报告生成结果视图
function ReportResultView({ data }: { data: any }) {
  return (
    <div className="space-y-3">
      {data.success !== undefined && (
        <div className={cn(
          "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
          data.success
            ? "bg-green-50 text-green-700 border border-green-200"
            : "bg-red-50 text-red-700 border border-red-200"
        )}>
          {data.success ? <CircleCheckBigIcon size={12} /> : <AlertCircle size={12} />}
          {data.success ? "报告生成成功" : "报告生成失败"}
        </div>
      )}
      {data.project && (
        <p className="text-sm"><strong>项目:</strong> {data.project}</p>
      )}
      {data.target && (
        <p className="text-sm"><strong>目标:</strong> {data.target}</p>
      )}
      {data.total_vulnerabilities !== undefined && (
        <p className="text-sm"><strong>漏洞总数:</strong> {data.total_vulnerabilities}</p>
      )}
      {data.risk_score !== undefined && (
        <p className="text-sm"><strong>风险评分:</strong> {data.risk_score}/10</p>
      )}
      {data.severity_distribution && (
        <div className="text-xs">
          <strong>风险分布:</strong>
          <div className="mt-1 flex gap-2">
            {Object.entries(data.severity_distribution).map(([level, count]) => (
              <span key={level} className={cn(
                "px-1.5 py-0.5 rounded text-[10px] font-medium",
                level === "Critical" && "bg-red-100 text-red-700",
                level === "High" && "bg-orange-100 text-orange-700",
                level === "Medium" && "bg-yellow-100 text-yellow-700",
                level === "Low" && "bg-blue-100 text-blue-700",
                level === "Info" && "bg-gray-100 text-gray-600",
              )}>
                {level}: {count as number}
              </span>
            ))}
          </div>
        </div>
      )}
      {data.object_name && (
        <p className="text-xs text-muted-foreground font-mono">文件: {data.object_name}</p>
      )}
    </div>
  );
}

export const ToolCallBox = React.memo<ToolCallBoxProps>(
  ({
    toolCall,
    uiComponent,
    stream,
    graphId,
    actionRequest,
    reviewConfig,
    onResume,
    isLoading,
  }) => {
    const [isExpanded, setIsExpanded] = useState(
      () => !!uiComponent || !!actionRequest
    );
    const [expandedArgs, setExpandedArgs] = useState<Record<string, boolean>>(
      {}
    );
    const [showRawResult, setShowRawResult] = useState(false);

    const { name, args, result, status } = useMemo(() => {
      return {
        name: toolCall.name || "Unknown Tool",
        args: toolCall.args || {},
        result: toolCall.result,
        status: toolCall.status || "completed",
      };
    }, [toolCall]);

    const displayName = useMemo(() => getToolDisplayName(name), [name]);

    const statusIcon = useMemo(() => {
      switch (status) {
        case "completed":
          return <CircleCheckBigIcon size={14} />;
        case "error":
          return (
            <AlertCircle
              size={14}
              className="text-destructive"
            />
          );
        case "pending":
          return (
            <Loader2
              size={14}
              className="animate-spin"
            />
          );
        case "interrupted":
          return (
            <StopCircle
              size={14}
              className="text-orange-500"
            />
          );
        default:
          return getToolIcon(name, status);
      }
    }, [status, name]);

    const formattedResult = useMemo(() => {
      if (!result) return null;
      return formatToolResult(result);
    }, [result]);

    const toggleExpanded = useCallback(() => {
      setIsExpanded((prev) => !prev);
    }, []);

    const toggleArgExpanded = useCallback((argKey: string) => {
      setExpandedArgs((prev) => ({
        ...prev,
        [argKey]: !prev[argKey],
      }));
    }, []);

    const hasContent = result || Object.keys(args).length > 0;

    return (
      <div
        className={cn(
          "w-full overflow-hidden rounded-lg border-none shadow-none outline-none transition-colors duration-200 hover:bg-accent",
          isExpanded && hasContent && "bg-accent"
        )}
      >
        <Button
          variant="ghost"
          size="sm"
          onClick={toggleExpanded}
          className={cn(
            "flex w-full items-center justify-between gap-2 border-none px-2 py-2 text-left shadow-none outline-none focus-visible:ring-0 focus-visible:ring-offset-0 disabled:cursor-default"
          )}
          disabled={!hasContent}
        >
          <div className="flex w-full items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              {statusIcon}
              <span className="text-[15px] font-medium tracking-[-0.6px] text-foreground">
                {displayName}
              </span>
              <span className="text-xs text-muted-foreground font-mono">
                {name !== displayName && name}
              </span>
            </div>
            {hasContent &&
              (isExpanded ? (
                <ChevronUp
                  size={14}
                  className="shrink-0 text-muted-foreground"
                />
              ) : (
                <ChevronDown
                  size={14}
                  className="shrink-0 text-muted-foreground"
                />
              ))}
          </div>
        </Button>

        {isExpanded && hasContent && (
          <div className="px-4 pb-4">
            {uiComponent && stream && graphId ? (
              <div className="mt-4">
                <LoadExternalComponent
                  key={uiComponent.id}
                  stream={stream}
                  message={uiComponent}
                  namespace={graphId}
                  meta={{ status, args, result: result ?? "暂无结果" }}
                />
              </div>
            ) : actionRequest && onResume ? (
              // Show tool approval UI when there's an action request but no GenUI
              <div className="mt-4">
                <ToolApprovalInterrupt
                  actionRequest={actionRequest}
                  reviewConfig={reviewConfig}
                  onResume={onResume}
                  isLoading={isLoading}
                />
              </div>
            ) : (
              <>
                {Object.keys(args).length > 0 && (
                  <div className="mt-4">
                    <h4 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      参数
                    </h4>
                    <div className="space-y-2">
                      {Object.entries(args).map(([key, value]) => (
                        <div
                          key={key}
                          className="rounded-sm border border-border"
                        >
                          <button
                            onClick={() => toggleArgExpanded(key)}
                            className="flex w-full items-center justify-between bg-muted/30 p-2 text-left text-xs font-medium transition-colors hover:bg-muted/50"
                          >
                            <span className="font-mono">{key}</span>
                            {expandedArgs[key] ? (
                              <ChevronUp
                                size={12}
                                className="text-muted-foreground"
                              />
                            ) : (
                              <ChevronDown
                                size={12}
                                className="text-muted-foreground"
                              />
                            )}
                          </button>
                          {expandedArgs[key] && (
                            <div className="border-t border-border bg-muted/20 p-2">
                              <pre className="m-0 overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs leading-6 text-foreground">
                                {typeof value === "string"
                                  ? value
                                  : JSON.stringify(value, null, 2)}
                              </pre>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {result && (
                  <div className="mt-4">
                    <div className="flex items-center justify-between mb-1">
                      <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        结果
                      </h4>
                      {formattedResult?.type === "json" && (
                        <button
                          onClick={() => setShowRawResult((prev) => !prev)}
                          className="text-[10px] text-muted-foreground hover:text-foreground transition-colors"
                        >
                          {showRawResult ? "查看结构化" : "查看原始 JSON"}
                        </button>
                      )}
                    </div>

                    {/* 结构化展示 */}
                    {formattedResult?.type === "json" && !showRawResult && formattedResult.data ? (
                      <div className="rounded-sm border border-border bg-muted/20 p-3">
                        <StructuredResultView data={formattedResult.data} toolName={name} />
                      </div>
                    ) : (
                      <pre className="m-0 overflow-x-auto whitespace-pre-wrap break-all rounded-sm border border-border bg-muted/40 p-2 font-mono text-xs leading-7 text-foreground">
                        {formattedResult?.content || result}
                      </pre>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>
    );
  }
);

ToolCallBox.displayName = "ToolCallBox";
