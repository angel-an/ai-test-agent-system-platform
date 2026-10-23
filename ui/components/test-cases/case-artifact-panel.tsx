"use client";

// 用例成果物面板：把生成结果从「toast + 列表行」升级为可核对的成果物视图。
// 设计口径（与后端一致，不在 UI 层美化）：
// - 有断言的才是「可判定」用例；没有断言的必须点名原因，不能显示成"已生成"就完事。
// - executable_track 是生成期判定，web_coverage 落地才是「已进冒烟编译」的实证。
// - 汇总层不给单一总数，给构成（正/反/边界、自动/人工、断言 kind 分布）。

import * as React from "react";
import {
  CircleCheckBig,
  TriangleAlert,
  ChevronDown,
  ChevronRight,
  Download,
  Copy,
  ArrowRight,
  ShieldCheck,
  FileText,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { TestCaseInfo } from "@/lib/api/types";
import {
  toCaseArtifact,
  summarizeArtifacts,
  explainCoverageGap,
  assertionKindLabel,
  EXECUTABLE_TRACK_LABELS,
  GAP_REASON_LABELS,
  type CaseArtifactView,
  type ArtifactSummary,
} from "@/lib/test-case-artifacts";

const KEYWORD_ORDER = ["正向", "反向", "边界"];

function StatCard({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  tone?: "default" | "good" | "warn";
}) {
  return (
    <div
      className={cn(
        "rounded-lg border px-3 py-2",
        tone === "good" && "border-green-200 bg-green-50",
        tone === "warn" && "border-amber-200 bg-amber-50"
      )}
    >
      <div className="text-xs text-muted-foreground">{label}</div>
      <div
        className={cn(
          "mt-0.5 text-lg font-semibold",
          tone === "good" && "text-green-700",
          tone === "warn" && "text-amber-700"
        )}
      >
        {value}
      </div>
      {hint && <div className="mt-0.5 text-[11px] text-muted-foreground">{hint}</div>}
    </div>
  );
}

/** 覆盖汇总条：数字 + 结论 + 断言 kind 分布。 */
export function ArtifactSummaryBar({
  summary,
  className,
}: {
  summary: ArtifactSummary;
  className?: string;
}) {
  const keywordParts = KEYWORD_ORDER.filter((k) => summary.byKeyword[k]).map(
    (k) => `${k} ${summary.byKeyword[k]}`
  );
  const otherKeywords = Object.keys(summary.byKeyword).filter(
    (k) => !KEYWORD_ORDER.includes(k)
  );
  for (const k of otherKeywords) keywordParts.push(`${k} ${summary.byKeyword[k]}`);
  const kindParts = Object.entries(summary.assertionKinds)
    .sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `${assertionKindLabel(k)} ${n}`);

  return (
    <div className={cn("space-y-3", className)}>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatCard label="生成用例" value={summary.total} hint={keywordParts.join(" / ")} />
        <StatCard
          label="带可判定断言"
          value={summary.withAssertions}
          hint={`断言共 ${summary.assertionTotal} 条`}
          tone={summary.withAssertions > 0 ? "good" : "warn"}
        />
        <StatCard
          label="已进冒烟编译"
          value={summary.compiled}
          hint={`自动通道 ${summary.autoTrack} / 人工通道 ${summary.manualTrack}`}
          tone={summary.compiled > 0 ? "good" : "default"}
        />
        <StatCard
          label="需人工执行"
          value={summary.total - summary.compiled}
          hint={
            summary.unexplained > 0
              ? `⚠ ${summary.unexplained} 条未说明原因`
              : "均已标注原因"
          }
          tone={summary.unexplained > 0 ? "warn" : "default"}
        />
      </div>
      {kindParts.length > 0 && (
        <p className="text-xs text-muted-foreground">
          断言类型分布：{kindParts.join(" · ")}
        </p>
      )}
      {Object.keys(summary.gapByReason).length > 0 && (
        <p className="text-xs text-muted-foreground">
          未自动执行的原因构成：
          {Object.keys(GAP_REASON_LABELS)
            .filter((k) => summary.gapByReason[k])
            .map((k) => `${GAP_REASON_LABELS[k]} ${summary.gapByReason[k]} 条`)
            .join(" · ")}
        </p>
      )}
      <p className="text-xs text-muted-foreground">{explainCoverageGap(summary)}</p>
    </div>
  );
}

function AssertionTable({ view }: { view: CaseArtifactView }) {
  if (view.assertionCount === 0) return null;
  return (
    <div className="overflow-x-auto rounded border">
      <table className="w-full text-xs">
        <thead className="bg-muted/50 text-muted-foreground">
          <tr>
            <th className="px-2 py-1.5 text-left font-medium">类型</th>
            <th className="px-2 py-1.5 text-left font-medium">字段</th>
            <th className="px-2 py-1.5 text-left font-medium">输入值</th>
            <th className="px-2 py-1.5 text-left font-medium">预期</th>
          </tr>
        </thead>
        <tbody>
          {view.assertions.map((a, i) => (
            <tr key={i} className="border-t align-top">
              <td className="px-2 py-1.5">
                <Badge variant="outline" className="text-[11px]">
                  {assertionKindLabel(a.kind)}
                </Badge>
              </td>
              <td className="px-2 py-1.5 font-medium">{a.field || "-"}</td>
              <td className="px-2 py-1.5 text-muted-foreground">
                {a.data === "" ? <span className="italic">（空）</span> : a.data}
                {a.allowed && a.allowed.length > 0 && (
                  <div className="mt-0.5 text-[11px] text-muted-foreground/70">
                    合法：{a.allowed.join("、")}
                  </div>
                )}
              </td>
              <td className="px-2 py-1.5">{a.expect || "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CaseRow({ view }: { view: CaseArtifactView }) {
  const [open, setOpen] = React.useState(false);
  return (
    <div className="rounded-lg border">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-start gap-2 p-3 text-left hover:bg-muted/40"
      >
        {open ? (
          <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-muted-foreground">{view.identifier}</span>
            <span className="text-sm font-medium">{view.name}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            {view.keyword && (
              <Badge variant="secondary" className="text-[11px]">
                {view.keyword}
              </Badge>
            )}
            <Badge
              variant="outline"
              className={cn(
                "text-[11px]",
                view.executableTrack === "auto"
                  ? "border-green-300 text-green-700"
                  : "border-amber-300 text-amber-700"
              )}
            >
              {EXECUTABLE_TRACK_LABELS[view.executableTrack] || view.executableTrack}
            </Badge>
            {view.compiled ? (
              <Badge variant="success" className="text-[11px]">
                已编译{view.webCoverage?.web_test_identifier
                  ? ` · ${view.webCoverage.web_test_identifier}`
                  : ""}
              </Badge>
            ) : (
              <span className="text-[11px] text-muted-foreground">未进冒烟</span>
            )}
            <span className="text-[11px] text-muted-foreground">
              断言 {view.assertionCount} 条
            </span>
            <span className="text-[11px] text-muted-foreground">· {view.module}</span>
          </div>
          {!view.compiled && view.gapReason && (
            <p className="mt-1 text-[11px] text-amber-700">⚠ {view.gapReason}</p>
          )}
        </div>
      </button>

      {open && (
        <div className="space-y-3 border-t bg-muted/20 p-3">
          {view.sourceLocation && (
            <p className="text-xs text-muted-foreground">
              需求来源：{view.sourceLocation}
            </p>
          )}

          {view.requirementRule && (
            <div className="rounded border bg-background p-2">
              <div className="mb-1 text-xs font-medium">字段规则（编译依据）</div>
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs sm:grid-cols-3">
                <div>
                  <dt className="text-muted-foreground">字段</dt>
                  <dd>{view.requirementRule.field}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">类型</dt>
                  <dd>{view.requirementRule.type}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">必填</dt>
                  <dd>{view.requirementRule.required ? "是" : "否"}</dd>
                </div>
                {view.requirementRule.range_raw && (
                  <div className="col-span-2">
                    <dt className="text-muted-foreground">取值约束</dt>
                    <dd>{view.requirementRule.range_raw}</dd>
                  </div>
                )}
                {view.requirementRule.condition && (
                  <div className="col-span-2">
                    <dt className="text-muted-foreground">显示条件</dt>
                    <dd>{view.requirementRule.condition}</dd>
                  </div>
                )}
              </dl>
              {view.requirementRule.evidence && (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  原文：{view.requirementRule.evidence}
                </p>
              )}
            </div>
          )}

          {view.assertionCount > 0 ? (
            <AssertionTable view={view} />
          ) : (
            <p className="rounded border border-amber-200 bg-amber-50 px-2 py-1.5 text-xs text-amber-800">
              无可判定断言 —— {view.gapReason || "该用例只能人工执行"}
            </p>
          )}

          {view.steps.length > 0 && (
            <div className="rounded border bg-background">
              <div className="border-b px-2 py-1.5 text-xs font-medium">测试步骤</div>
              <ol className="divide-y">
                {view.steps.map((s, i) => (
                  <li key={i} className="grid gap-1 px-2 py-1.5 text-xs sm:grid-cols-2">
                    <div>
                      <span className="mr-1 text-muted-foreground">{i + 1}.</span>
                      {s.step || "-"}
                    </div>
                    <div className="text-muted-foreground">
                      <span className="mr-1">预期：</span>
                      {s.result || "-"}
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function buildMarkdown(cases: TestCaseInfo[]): string {
  const views = cases.map(toCaseArtifact);
  const summary = summarizeArtifacts(cases);
  const lines: string[] = [];
  lines.push(`# 测试用例成果物（${summary.total} 条）`);
  lines.push("");
  lines.push(
    `- 构成：` +
      Object.entries(summary.byKeyword)
        .map(([k, v]) => `${k} ${v}`)
        .join(" / ")
  );
  lines.push(
    `- 带可判定断言：${summary.withAssertions} 条（断言共 ${summary.assertionTotal} 条）`
  );
  lines.push(
    `- 已进冒烟编译：${summary.compiled} 条；自动通道 ${summary.autoTrack} / 人工通道 ${summary.manualTrack}`
  );
  lines.push("");
  for (const v of views) {
    lines.push(`## ${v.identifier} ${v.name}`);
    lines.push(`- 模块：${v.module}${v.keyword ? ` · ${v.keyword}` : ""}`);
    if (v.sourceLocation) lines.push(`- 需求来源：${v.sourceLocation}`);
    lines.push(
      `- 执行通道：${EXECUTABLE_TRACK_LABELS[v.executableTrack] || v.executableTrack}${
        v.compiled ? `（已编译 ${v.webCoverage?.web_test_identifier || ""}）` : "（未进冒烟）"
      }`
    );
    if (v.requirementRule) {
      const r = v.requirementRule;
      lines.push(
        `- 字段规则：${r.field} · ${r.type} · ${r.required ? "必填" : "非必填"}${
          r.range_raw ? ` · ${r.range_raw}` : ""
        }`
      );
    }
    if (v.assertionCount > 0) {
      lines.push("- 断言：");
      for (const a of v.assertions) {
        lines.push(
          `  - [${assertionKindLabel(a.kind)}] ${a.field} 输入「${a.data || "空"}」→ ${a.expect}`
        );
      }
    } else {
      lines.push(`- 断言：无（${v.gapReason || "需人工执行"}）`);
    }
    if (v.steps.length > 0) {
      lines.push("- 步骤：");
      v.steps.forEach((s, i) => {
        lines.push(`  ${i + 1}. ${s.step} → 预期：${s.result}`);
      });
    }
    lines.push("");
  }
  return lines.join("\n");
}

export interface CaseArtifactPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  testCases: TestCaseInfo[];
  fileName?: string | null;
  /** 生成完成时间（展示用），缺省不显示 */
  generatedAt?: string | null;
  /** 编译回执摘要文本（来自 Web 冒烟编译），用于说明"脚本在但覆盖多少" */
  compileReceiptText?: string | null;
  /** Web 冒烟未创建的原因 */
  webLoopError?: string | null;
  /** 模糊词告警（rev183）：文档里「适当/尽量/多种」这类无阈值写法 —— 不自动替换，只点名 */
  vagueWordWarning?: string | null;
  /** 模糊词命中明细（词 / 原句 / 为什么判不了 / 改写建议） */
  vagueWordHits?: Array<{
    word: string;
    point: string;
    clause: string;
    reason: string;
    suggestion: string;
  }> | null;
  /** 跳到列表页继续处理（关闭后触发） */
  onGoToList?: () => void;
}

/**
 * 「生成完成」弹窗：默认就在生成成功时弹出，用户第一眼看到的就是用例成果物本身，
 * 而不是先看到列表再逐条点开。列表页另有入口以同一面板查看存量用例。
 */
export function CaseArtifactPanel({
  open,
  onOpenChange,
  testCases,
  fileName,
  generatedAt,
  compileReceiptText,
  webLoopError,
  vagueWordWarning,
  vagueWordHits,
  onGoToList,
}: CaseArtifactPanelProps) {
  const summary = React.useMemo(() => summarizeArtifacts(testCases), [testCases]);
  const [keywordFilter, setKeywordFilter] = React.useState<string>("all");
  const [onlyTodo, setOnlyTodo] = React.useState(false);
  const [search, setSearch] = React.useState("");

  const views = React.useMemo(() => testCases.map(toCaseArtifact), [testCases]);
  const filtered = views.filter((v) => {
    if (keywordFilter !== "all" && (v.keyword || "未标注") !== keywordFilter) return false;
    if (onlyTodo && v.compiled) return false;
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      if (
        !v.name.toLowerCase().includes(q) &&
        !v.identifier.toLowerCase().includes(q) &&
        !v.module.toLowerCase().includes(q)
      ) {
        return false;
      }
    }
    return true;
  });

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(buildMarkdown(testCases));
      toast.success("已复制 Markdown 成果物");
    } catch {
      toast.error("复制失败，请改用下载");
    }
  };

  const handleDownload = () => {
    const blob = new Blob([buildMarkdown(testCases)], {
      type: "text/markdown;charset=utf-8",
    });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `测试用例成果物-${new Date().toISOString().slice(0, 10)}.md`;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
  };

  const keywordOptions = ["all", ...KEYWORD_ORDER];
  for (const k of Object.keys(summary.byKeyword)) {
    if (!keywordOptions.includes(k)) keywordOptions.push(k);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="flex h-[88vh] max-w-6xl flex-col gap-0 p-0"
      >
        <DialogHeader className="border-b px-6 py-4">
          <DialogTitle className="flex items-center gap-2 text-lg">
            <FileText className="h-5 w-5 text-primary" />
            测试用例成果物
            <span className="text-sm font-normal text-muted-foreground">
              {summary.total} 条
            </span>
          </DialogTitle>
          <DialogDescription className="text-xs">
            {fileName ? `需求文档：${fileName}` : "本批次生成结果"}
            {generatedAt ? ` · 生成于 ${generatedAt}` : ""}
          </DialogDescription>
        </DialogHeader>

        <div className="border-b bg-muted/30 px-6 py-3">
          <ArtifactSummaryBar summary={summary} />
          {webLoopError && (
            <div className="mt-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              <div className="font-medium">Web 冒烟自动化未创建</div>
              <div className="mt-0.5 break-words">{webLoopError}</div>
              <div className="mt-0.5 text-amber-700">
                用例已生成，但执行侧未建立；请检查项目「路径配置」或子功能绑定后重试。
              </div>
            </div>
          )}
          {compileReceiptText && (
            <div className="mt-2 rounded-md border border-amber-500 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              <div className="font-medium">Web 冒烟编译回执（需关注）</div>
              <div className="mt-0.5 whitespace-pre-line break-words">
                {compileReceiptText}
              </div>
              <div className="mt-0.5 text-amber-700">
                资产状态仍显示「已完成」不代表已覆盖，请对照上方「已进冒烟编译」条数。
              </div>
            </div>
          )}
          {vagueWordWarning && (
            <div
              className="mt-2 rounded-md border border-sky-400 bg-sky-50 px-3 py-2 text-xs text-sky-900"
              data-testid="vague-word-warning"
            >
              <div className="font-medium">
                需求文档有 {vagueWordHits?.length ?? 0} 处模糊表述（未自动改写）
              </div>
              <div className="mt-0.5 text-sky-800">
                这些写法没有可判定的阈值/闭集，对应条目只能人工执行。平台不会替你猜数值——
                凭空补一个阈值会让用例跑绿也只验证了自编的数。
              </div>
              {vagueWordHits && vagueWordHits.length > 0 && (
                <ul className="mt-1.5 space-y-1">
                  {vagueWordHits.slice(0, 6).map((h, i) => (
                    <li key={i} className="border-l-2 border-sky-300 pl-2">
                      <span className="font-medium">「{h.word}」</span>
                      <span className="text-sky-700"> — {h.reason}</span>
                      <div className="text-sky-600">
                        原句：{(h.clause || h.point).slice(0, 60)}
                      </div>
                      <div className="text-sky-600">改法：{h.suggestion}</div>
                    </li>
                  ))}
                  {vagueWordHits.length > 6 && (
                    <li className="text-sky-600">…另有 {vagueWordHits.length - 6} 处</li>
                  )}
                </ul>
              )}
            </div>
          )}
        </div>

        {/* 过滤条 */}
        <div className="flex flex-wrap items-center gap-2 border-b px-6 py-2">
          <div className="flex flex-wrap gap-1">
            {keywordOptions.map((k) => (
              <Button
                key={k}
                variant={keywordFilter === k ? "default" : "outline"}
                size="sm"
                className="h-7 text-xs"
                onClick={() => setKeywordFilter(k)}
              >
                {k === "all" ? "全部" : k}
                {k !== "all" && summary.byKeyword[k] ? ` ${summary.byKeyword[k]}` : ""}
              </Button>
            ))}
          </div>
          <Button
            variant={onlyTodo ? "default" : "outline"}
            size="sm"
            className="h-7 text-xs"
            onClick={() => setOnlyTodo(!onlyTodo)}
          >
            仅看需人工执行（{summary.total - summary.compiled}）
          </Button>
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索标题 / 编号 / 模块"
            className="ml-auto h-7 w-56 text-xs"
          />
        </div>

        <ScrollArea className="flex-1">
          <div className="space-y-2 p-4">
            {filtered.length === 0 && (
              <p className="py-10 text-center text-sm text-muted-foreground">
                没有符合条件的用例
              </p>
            )}
            {filtered.map((v) => (
              <CaseRow key={v.identifier} view={v} />
            ))}
          </div>
        </ScrollArea>

        <DialogFooter className="border-t px-6 py-3 sm:justify-between">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {summary.withAssertions === 0 ? (
              <span className="flex items-center gap-1 text-amber-700">
                <TriangleAlert className="h-3.5 w-3.5" />
                本批次没有可判定断言，全部需人工执行
              </span>
            ) : (
              <span className="flex items-center gap-1 text-green-700">
                <ShieldCheck className="h-3.5 w-3.5" />
                {summary.withAssertions} 条带可判定断言，{summary.compiled} 条已进冒烟
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={handleCopy}>
              <Copy className="mr-1 h-3.5 w-3.5" />
              复制
            </Button>
            <Button variant="ghost" size="sm" onClick={handleDownload}>
              <Download className="mr-1 h-3.5 w-3.5" />
              下载 .md
            </Button>
            <Button
              variant="outline"
              onClick={() => {
                onOpenChange(false);
                onGoToList?.();
              }}
            >
              去用例列表
              <ArrowRight className="ml-1 h-3.5 w-3.5" />
            </Button>
            <Button onClick={() => onOpenChange(false)}>
              <CircleCheckBig className="mr-1 h-4 w-4" />
              完成
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
