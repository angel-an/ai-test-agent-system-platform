/**
 * 需求质量面板（阶段一 + 阶段二）
 *
 * 展示：
 * - 需求质量状态（Complete / Incomplete / Blocked）
 * - 缺失项与歧义项
 * - 阻断原因
 * - 功能点与用例关联（覆盖率）
 * - 生成状态（按钮禁用依据）
 *
 * 说明：该组件只读取门控结果，不改变任何生成链路；生成按钮的禁用由调用方根据
 * `data.prd_quality.can_generate_testcases` 决定。
 */

"use client";

import * as React from "react";
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  CircleHelp,
  Info,
  Layers,
  Tag,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type {
  CoverageReport,
  PRDQualityStatus,
  RequirementAnalysisData,
  RequirementGap,
} from "@/lib/api/requirement-quality";

export interface RequirementQualityPanelProps {
  /** 门控结果 */
  data: RequirementAnalysisData | null;
  /** 覆盖率报告（可选，功能点与用例关联展示） */
  coverage?: CoverageReport | null;
  /** 默认展开缺失项明细 */
  defaultOpen?: boolean;
  className?: string;
}

const STATUS_STYLES: Record<
  PRDQualityStatus,
  { badge: string; icon: React.ElementType; hint: string }
> = {
  Complete: {
    badge: "bg-green-50 text-green-700 border-green-200",
    icon: CheckCircle2,
    hint: "满足测试设计条件，可以生成测试用例",
  },
  Incomplete: {
    badge: "bg-amber-50 text-amber-800 border-amber-200",
    icon: AlertTriangle,
    hint: "存在缺失但仍可理解，已暂停正式用例生成",
  },
  Blocked: {
    badge: "bg-red-50 text-red-700 border-red-200",
    icon: Ban,
    hint: "目标或流程无法确认，已阻断用例生成",
  },
};

function GapList({
  title,
  gaps,
  icon: Icon,
  tone,
}: {
  title: string;
  gaps: RequirementGap[];
  icon: React.ElementType;
  tone: string;
}) {
  if (!gaps.length) return null;
  return (
    <div className="space-y-1.5">
      <p className={cn("flex items-center gap-1.5 text-xs font-medium", tone)}>
        <Icon className="h-3.5 w-3.5" />
        {title}（{gaps.length}）
      </p>
      <ul className="space-y-1 pl-5">
        {gaps.map((gap, index) => (
          <li key={`${gap.item}-${index}`} className="text-xs leading-relaxed">
            <span className="font-medium">{gap.item}</span>
            <span className="text-muted-foreground">：{gap.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function RequirementQualityPanel({
  data,
  coverage,
  defaultOpen = true,
  className,
}: RequirementQualityPanelProps) {
  const [open, setOpen] = React.useState(defaultOpen);

  if (!data) return null;

  const { prd_quality: quality, analysis_report: report } = data;
  const style = STATUS_STYLES[quality.status] ?? STATUS_STYLES.Incomplete;
  const StatusIcon = style.icon;

  const blocked = !quality.can_generate_testcases;

  return (
    <div
      className={cn(
        "rounded-lg border p-3 text-sm",
        blocked ? "border-amber-200 bg-amber-50/40" : "border-green-200 bg-green-50/40",
        className
      )}
      data-testid="requirement-quality-panel"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-semibold",
              style.badge
            )}
            data-testid="requirement-quality-status"
          >
            <StatusIcon className="h-3.5 w-3.5" />
            需求质量：{quality.status_label}（{quality.status}）
          </span>
          <span className="text-xs text-muted-foreground">
            缺失 {quality.missing_count} · 歧义 {quality.ambiguous_count} · 阻断{" "}
            {quality.blocked_count}
          </span>
        </div>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="shrink-0 text-xs text-muted-foreground hover:text-foreground"
        >
          {open ? "收起" : "展开"}
        </button>
      </div>

      <p className="mt-2 text-xs text-muted-foreground">{style.hint}</p>

      {blocked && (
        <p
          className="mt-2 rounded-md bg-background/70 px-2 py-1.5 text-xs font-medium text-destructive"
          data-testid="requirement-quality-blocked-hint"
        >
          生成按钮已禁用：不满足测试设计条件时不生成正式测试用例。
        </p>
      )}

      {open && (
        <div className="mt-3 space-y-3">
          {report.parse_error && (
            <p className="flex items-start gap-1.5 rounded-md bg-background/70 px-2 py-1.5 text-xs text-amber-800">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              结构化分析结果解析失败，已降级为 Incomplete：{report.parse_error}
            </p>
          )}

          {report.blocked_reasons.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-medium text-destructive">
                阻断原因（{report.blocked_reasons.length}）
              </p>
              <ul
                className="list-disc space-y-1 pl-5 text-xs text-destructive"
                data-testid="requirement-quality-blocked-reasons"
              >
                {report.blocked_reasons.map((reason, index) => (
                  <li key={index}>{reason}</li>
                ))}
              </ul>
            </div>
          )}

          {data.testcase_generation.reasons.length > 0 &&
            report.blocked_reasons.length === 0 && (
              <div className="space-y-1">
                <p className="text-xs font-medium text-amber-800">生成阻断原因</p>
                <ul className="list-disc space-y-1 pl-5 text-xs text-amber-800">
                  {data.testcase_generation.reasons.map((reason, index) => (
                    <li key={index}>{reason}</li>
                  ))}
                </ul>
              </div>
            )}

          <GapList
            title="缺失项"
            gaps={report.missing_items}
            icon={AlertTriangle}
            tone="text-amber-800"
          />
          <GapList
            title="歧义项 / 待确认"
            gaps={report.ambiguous_items}
            icon={CircleHelp}
            tone="text-blue-700"
          />
          <GapList
            title="标记项（不阻断）"
            gaps={report.flagged_items}
            icon={Tag}
            tone="text-muted-foreground"
          />

          {report.affected_features.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground">受影响功能点</p>
              <div className="flex flex-wrap gap-1.5">
                {report.affected_features.map((featureId) => (
                  <span
                    key={featureId}
                    className="rounded border bg-background px-1.5 py-0.5 font-mono text-xs"
                  >
                    {featureId}
                  </span>
                ))}
              </div>
            </div>
          )}

          {coverage && coverage.feature_count > 0 && (
            <div className="space-y-2" data-testid="requirement-quality-coverage">
              <p className="flex items-center gap-1.5 text-xs font-medium">
                <Layers className="h-3.5 w-3.5" />
                功能点与用例关联：功能点覆盖 {coverage.covered_feature_count}/
                {coverage.feature_count}（
                {(coverage.feature_coverage_rate * 100).toFixed(1)}%）· 验收标准覆盖{" "}
                {(coverage.acceptance_coverage_rate * 100).toFixed(1)}% · 可追踪用例{" "}
                {coverage.traced_case_count}/{coverage.total_cases}
              </p>
              <ul className="space-y-1">
                {coverage.features.map((feature) => (
                  <li
                    key={feature.feature_id}
                    className="flex items-start gap-2 rounded-md bg-background/70 px-2 py-1 text-xs"
                  >
                    <span
                      className={cn(
                        "mt-0.5 shrink-0 font-mono",
                        feature.covered ? "text-green-700" : "text-destructive"
                      )}
                    >
                      {feature.feature_id}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="font-medium">{feature.feature_name}</span>
                      <span className="ml-1 text-muted-foreground">
                        [{feature.priority}] {feature.covered ? `关联 ${feature.case_count} 条用例` : "未覆盖"}
                      </span>
                      {feature.case_ids.length > 0 && (
                        <span className="mt-0.5 block truncate font-mono text-muted-foreground">
                          {feature.case_ids.join("、")}
                        </span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>

              {coverage.untraced_info.untraced_case_count > 0 && (
                <p className="text-xs text-amber-800">
                  有 {coverage.untraced_info.untraced_case_count} 条用例缺少功能点追踪（需补齐
                  feature_id）：{coverage.untraced_info.untraced_case_ids.slice(0, 6).join("、")}
                </p>
              )}
              {coverage.untraced_info.legacy_case_count > 0 && (
                <p className="text-xs text-muted-foreground">
                  历史用例 {coverage.untraced_info.legacy_case_count} 条（无追踪字段，允许读取与执行，不强制迁移）
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default RequirementQualityPanel;
