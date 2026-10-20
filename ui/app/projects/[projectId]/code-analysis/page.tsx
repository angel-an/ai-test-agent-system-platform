"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import {
  Code2, GitBranch, Network, Shield, TestTube, Globe, FileCode,
  Workflow, ExternalLink, RefreshCw, Settings, CheckCircle2,
  BookOpen, Search, ChevronDown, AlertCircle, Zap, ArrowRight,
} from "lucide-react";
import { MainLayout } from "@/components/layout";
import { useLanguage } from "@/providers/LanguageProvider";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const GITNEXUS_URL = "http://localhost:5173";
type TabType = "knowledge-graph" | "test-entry" | "repo-config";

const mockRepos = [
  { id: "1", name: "ai-test-platform", language: "TypeScript", branch: "main" },
  { id: "2", name: "backend-service", language: "Python", branch: "develop" },
  { id: "3", name: "mobile-app", language: "Java", branch: "main" },
];

const testEntryCards = [
  {
    id: "api-test", icon: Code2, title: "API 测试",
    description: "结合 OpenAPI 与代码调用链生成/执行接口测试",
    accent: "from-blue-500 to-blue-600", iconBg: "bg-blue-500/10", iconColor: "text-blue-600",
  },
  {
    id: "ui-web-test", icon: Globe, title: "UI / Web 测试",
    description: "基于页面结构与代码路径生成 Playwright 脚本",
    accent: "from-violet-500 to-purple-600", iconBg: "bg-violet-500/10", iconColor: "text-violet-600",
  },
  {
    id: "scenario-test", icon: Workflow, title: "场景 / 端到端测试",
    description: "多接口业务流编排与回归",
    accent: "from-orange-500 to-amber-600", iconBg: "bg-orange-500/10", iconColor: "text-orange-600",
  },
  {
    id: "bdd-test", icon: BookOpen, title: "手工 / BDD 用例",
    description: "从需求与代码上下文生成测试用例",
    accent: "from-emerald-500 to-green-600", iconBg: "bg-emerald-500/10", iconColor: "text-emerald-600",
  },
  {
    id: "unit-test", icon: TestTube, title: "单元测试",
    description: "基于符号与依赖图定位待测模块并生成单测骨架",
    accent: "from-cyan-500 to-teal-600", iconBg: "bg-cyan-500/10", iconColor: "text-cyan-600",
  },
  {
    id: "security-test", icon: Shield, title: "安全测试",
    description: "结合代码图谱进行入口、鉴权与敏感数据流分析",
    accent: "from-rose-500 to-red-600", iconBg: "bg-rose-500/10", iconColor: "text-rose-600",
  },
  {
    id: "static-analysis", icon: Search, title: "静态代码分析",
    description: "知识图谱 + 规则检索，辅助发现坏味道与风险点",
    accent: "from-amber-500 to-yellow-600", iconBg: "bg-amber-500/10", iconColor: "text-amber-600",
  },
];

export default function CodeAnalysisPage() {
  const params = useParams();
  const projectId = params.projectId as string;
  const { t } = useLanguage();

  const [activeTab, setActiveTab] = React.useState<TabType>("test-entry");
  const [selectedRepo, setSelectedRepo] = React.useState(mockRepos[0]);
  const [gitNexusStatus, setGitNexusStatus] = React.useState<"connected" | "disconnected" | "checking">("checking");
  const [refreshing, setRefreshing] = React.useState(false);

  const checkConnection = React.useCallback(async () => {
    setGitNexusStatus("checking");
    setRefreshing(true);
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 3000);
      await fetch(GITNEXUS_URL, { mode: "no-cors", signal: controller.signal });
      clearTimeout(timeoutId);
      setGitNexusStatus("connected");
    } catch {
      setGitNexusStatus("disconnected");
    } finally {
      setRefreshing(false);
    }
  }, []);

  React.useEffect(() => { checkConnection(); }, [checkConnection]);

  const tabs = [
    { id: "knowledge-graph" as TabType, label: "代码图谱", icon: Network },
    { id: "test-entry" as TabType, label: "测试入口", icon: Zap },
    { id: "repo-config" as TabType, label: "仓库配置", icon: Settings },
  ];

  const handleEnterCard = (cardId: string) => {
    if (gitNexusStatus !== "connected") return;
    window.open(`${GITNEXUS_URL}/#/${cardId}?repo=${selectedRepo.name}`, "_blank");
  };

  return (
    <MainLayout title={t("codeAnalysis.title")}>
      <div className="flex flex-col h-full overflow-auto bg-background">
        {/* 页面头部 */}
        <div className="px-6 pt-6 pb-2">
          <div className="flex items-start justify-between">
            <div>
              <h1 className="text-xl font-bold tracking-tight">{t("codeAnalysis.title")}</h1>
              <p className="text-sm text-muted-foreground mt-1">
                基于 GitNexus 知识图谱，驱动 API、UI、单元、安全与静态分析类测试
              </p>
            </div>
            <div className="flex items-center gap-2">
              <div className={cn(
                "flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium border",
                gitNexusStatus === "connected"
                  ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                  : gitNexusStatus === "checking"
                  ? "bg-yellow-50 text-yellow-700 border-yellow-200"
                  : "bg-red-50 text-red-700 border-red-200"
              )}>
                <span className={cn(
                  "w-1.5 h-1.5 rounded-full",
                  gitNexusStatus === "connected" ? "bg-emerald-500 animate-pulse" :
                  gitNexusStatus === "checking" ? "bg-yellow-500 animate-pulse" : "bg-red-500"
                )} />
                {gitNexusStatus === "connected" ? "GitNexus 已连接" :
                 gitNexusStatus === "checking" ? "检测中..." : "未连接"}
              </div>
              {gitNexusStatus === "connected" && (
                <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium bg-blue-50 text-blue-700 border border-blue-200">
                  <CheckCircle2 className="w-3 h-3" />
                  已索引
                </div>
              )}
              <Button variant="ghost" size="icon" className="h-8 w-8" onClick={checkConnection} disabled={refreshing}>
                <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
              </Button>
            </div>
          </div>
        </div>

        {/* Tab 导航 */}
        <div className="px-6 mt-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1 p-1 rounded-lg bg-muted/50">
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={cn(
                    "flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md transition-all",
                    activeTab === tab.id
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  <tab.icon className="h-3.5 w-3.5" />
                  {tab.label}
                </button>
              ))}
            </div>

            {/* 仓库切换 */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs">
                  <GitBranch className="h-3.5 w-3.5" />
                  <span className="font-medium">{selectedRepo.name}</span>
                  <span className="text-muted-foreground">/ {selectedRepo.branch}</span>
                  <ChevronDown className="h-3 w-3 opacity-50" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-60">
                {mockRepos.map((repo) => (
                  <DropdownMenuItem
                    key={repo.id}
                    onClick={() => setSelectedRepo(repo)}
                    className={cn("flex items-center justify-between py-2", selectedRepo.id === repo.id && "bg-accent")}
                  >
                    <div className="flex items-center gap-2">
                      <GitBranch className="h-3.5 w-3.5 text-muted-foreground" />
                      <div>
                        <p className="text-sm font-medium">{repo.name}</p>
                        <p className="text-[11px] text-muted-foreground">{repo.branch}</p>
                      </div>
                    </div>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted font-mono">{repo.language}</span>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        {/* Tab 内容 */}
        <div className="flex-1 px-6 py-5">
          {/* 测试入口 */}
          {activeTab === "test-entry" && (
            <div className="space-y-4">
              {gitNexusStatus === "disconnected" && (
                <div className="flex items-center gap-3 px-4 py-3 rounded-lg border border-amber-200 bg-amber-50">
                  <AlertCircle className="h-4 w-4 text-amber-600 shrink-0" />
                  <p className="text-sm text-amber-700">
                    GitNexus 未连接，请确认服务已启动（{GITNEXUS_URL}）后点击刷新
                  </p>
                </div>
              )}
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {testEntryCards.map((card) => (
                  <div
                    key={card.id}
                    className={cn(
                      "group relative rounded-xl border bg-card overflow-hidden transition-all duration-200",
                      gitNexusStatus === "connected"
                        ? "hover:shadow-lg hover:-translate-y-0.5 cursor-pointer"
                        : "opacity-60 cursor-not-allowed"
                    )}
                    onClick={() => handleEnterCard(card.id)}
                  >
                    <div className={cn("h-1 w-full bg-gradient-to-r", card.accent)} />
                    <div className="p-5">
                      <div className="flex items-center gap-3 mb-3">
                        <div className={cn("p-2 rounded-lg", card.iconBg)}>
                          <card.icon className={cn("h-5 w-5", card.iconColor)} />
                        </div>
                        <h3 className="font-semibold text-sm">{card.title}</h3>
                      </div>
                      <p className="text-xs text-muted-foreground leading-relaxed mb-4">{card.description}</p>
                      <div className="flex items-center justify-between">
                        <span className={cn(
                          "inline-flex items-center gap-1 text-xs font-medium",
                          gitNexusStatus === "connected" ? "text-primary" : "text-muted-foreground"
                        )}>
                          进入
                          <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
                        </span>
                        <ExternalLink className="h-3.5 w-3.5 text-muted-foreground/30 group-hover:text-muted-foreground/60 transition-colors" />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 代码图谱 */}
          {activeTab === "knowledge-graph" && (
            <div className="h-full min-h-[500px]">
              {gitNexusStatus === "connected" ? (
                <div className="h-full rounded-xl border overflow-hidden bg-card">
                  <div className="flex items-center justify-between px-4 py-2.5 border-b bg-muted/30">
                    <div className="flex items-center gap-2">
                      <Network className="h-4 w-4 text-primary" />
                      <span className="text-sm font-medium">代码知识图谱</span>
                      <span className="text-[11px] text-muted-foreground">— {selectedRepo.name}</span>
                    </div>
                    <Button
                      variant="ghost" size="sm" className="h-7 text-xs gap-1"
                      onClick={() => window.open(GITNEXUS_URL, "_blank")}
                    >
                      新窗口打开
                      <ExternalLink className="h-3 w-3" />
                    </Button>
                  </div>
                  <iframe
                    src={GITNEXUS_URL}
                    className="w-full h-[calc(100%-41px)] border-0"
                    title="GitNexus Code Graph"
                    sandbox="allow-same-origin allow-scripts allow-popups allow-forms"
                  />
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full min-h-[400px] rounded-xl border bg-muted/10">
                  <div className="p-4 rounded-full bg-muted/50 mb-4">
                    <Network className="h-10 w-10 text-muted-foreground/50" />
                  </div>
                  <h3 className="text-base font-medium mb-1.5">代码知识图谱</h3>
                  <p className="text-sm text-muted-foreground mb-5 text-center max-w-sm">
                    可视化展示代码结构、依赖关系与调用链路。需要先连接 GitNexus 服务。
                  </p>
                  <Button onClick={checkConnection} variant="outline" className="gap-2">
                    <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />
                    重新检测连接
                  </Button>
                </div>
              )}
            </div>
          )}

          {/* 仓库配置 */}
          {activeTab === "repo-config" && (
            <div className="max-w-2xl space-y-4">
              <div className="rounded-xl border bg-card overflow-hidden">
                <div className="px-5 py-3 border-b bg-muted/20">
                  <h3 className="text-sm font-semibold flex items-center gap-2">
                    <Settings className="h-4 w-4 text-muted-foreground" />
                    连接配置
                  </h3>
                </div>
                <div className="p-5">
                  <div className="grid grid-cols-2 gap-x-8 gap-y-4 text-sm">
                    <div>
                      <p className="text-xs text-muted-foreground mb-1">仓库名称</p>
                      <p className="font-medium">{selectedRepo.name}</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground mb-1">默认分支</p>
                      <p className="font-medium font-mono text-xs">{selectedRepo.branch}</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground mb-1">主要语言</p>
                      <p className="font-medium">{selectedRepo.language}</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground mb-1">GitNexus 地址</p>
                      <a href={GITNEXUS_URL} target="_blank" rel="noopener noreferrer"
                        className="font-medium text-primary hover:underline text-xs font-mono">
                        {GITNEXUS_URL}
                      </a>
                    </div>
                  </div>
                </div>
              </div>

              <div className="rounded-xl border bg-card overflow-hidden">
                <div className="px-5 py-3 border-b bg-muted/20">
                  <h3 className="text-sm font-semibold flex items-center gap-2">
                    <FileCode className="h-4 w-4 text-muted-foreground" />
                    索引状态
                  </h3>
                </div>
                <div className="p-5">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div className={cn(
                        "w-2.5 h-2.5 rounded-full",
                        gitNexusStatus === "connected" ? "bg-emerald-500" : "bg-muted-foreground/30"
                      )} />
                      <span className="text-sm">
                        {gitNexusStatus === "connected" ? "代码已索引，知识图谱就绪" : "等待连接 GitNexus..."}
                      </span>
                    </div>
                    <Button variant="outline" size="sm" className="h-7 text-xs gap-1.5" disabled={gitNexusStatus !== "connected"}>
                      <RefreshCw className="h-3 w-3" />
                      重新索引
                    </Button>
                  </div>
                </div>
              </div>

              <div className="rounded-xl border bg-card overflow-hidden">
                <div className="px-5 py-3 border-b bg-muted/20">
                  <h3 className="text-sm font-semibold flex items-center gap-2">
                    <Network className="h-4 w-4 text-muted-foreground" />
                    GitNexus 管理
                  </h3>
                </div>
                <div className="p-5">
                  <p className="text-xs text-muted-foreground mb-3">
                    前往 GitNexus 管理面板进行高级配置、查看分析报告与代码图谱
                  </p>
                  <Button variant="outline" size="sm" className="h-8 text-xs gap-1.5"
                    onClick={() => window.open(GITNEXUS_URL, "_blank")}
                    disabled={gitNexusStatus !== "connected"}>
                    打开 GitNexus
                    <ExternalLink className="h-3 w-3" />
                  </Button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </MainLayout>
  );
}
