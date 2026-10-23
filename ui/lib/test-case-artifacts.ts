// 用例成果物展示：把生成结果从「只有 toast + 列表行」升级为可读的成果物面板。
// 纯函数集中在此，便于脱离浏览器验证（前端无 jest/vitest，用 tsx 脚本自测）。

import type { TestCaseInfo } from "@/lib/api/types";

/** custom_fields 是自由 JSON；这里只声明会被展示的键，其余原样保留。 */
export interface AssertionView {
  kind: string;
  field: string;
  data: string;
  expect: string;
  rule: string;
  allowed?: string[];
}

export interface RequirementRuleView {
  field: string;
  type: string;
  required: boolean;
  condition: string;
  range_raw: string;
  evidence?: string;
  source?: {
    row?: number;
    line?: number;
    page?: number;
    table?: number;
    heading?: string;
    section?: string;
    heading_title?: string;
  };
}

export interface WebCoverageView {
  role?: string;
  rule_id?: string;
  bound_at?: string;
  web_test_id?: string;
  web_test_identifier?: string;
  compile_status?: string;
  assertion_count?: number;
  auto_executable?: boolean;
  sub_function_id?: string;
}

export interface CaseArtifactView {
  identifier: string;
  name: string;
  module: string;
  keyword: string;
  caseKind: string;
  sourceLocation: string;
  /** 生成期判定 auto/manual；**不等于**真实可跑（真实可跑看 webCoverage）。 */
  executableTrack: string;
  /** custom_fields.web_coverage 落地 → 已进入 Web 冒烟编译。 */
  compiled: boolean;
  assertionCount: number;
  assertions: AssertionView[];
  requirementRule: RequirementRuleView | null;
  steps: Array<{ step: string; result: string }>;
  webCoverage: WebCoverageView | null;
  /** 无法自动执行的根因（人工可读），为空表示无缺口。 */
  gapReason: string;
  /**
   * 缺口归因：unmapped（需求点没对上字段说明表）/ narrative（叙述导航类，抽不出断言）
   * / unbound（有断言但没绑定 Web 子功能）/ pending（auto 但资产没生成）/ ""（无缺口）。
   * 汇总层用它给出「各类原因各多少条」，避免一句笼统的"需人工执行"。
   */
  gapCode: GapCode;
}

export type GapCode = "unmapped" | "narrative" | "unbound" | "pending" | "api" | "";

const DASH = "-";

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asText(value: unknown, fallback = ""): string {
  if (value === null || value === undefined) return fallback;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return fallback;
  }
}

/**
 * 断言 data 可能是字符串/数字/数组（枚举 allowed 等），
 * 直接 String() 数组会变成 "a,b" 丢掉结构，这里保留 JSON 形态。
 */
function normalizeAssertion(raw: unknown): AssertionView | null {
  const o = asRecord(raw);
  const kind = asText(o.kind).trim();
  if (!kind) return null;
  const allowed = Array.isArray(o.allowed)
    ? o.allowed.map((v) => asText(v)).filter(Boolean)
    : undefined;
  return {
    kind,
    field: asText(o.field).trim(),
    // data 可能是数组/对象（如枚举 allowed），JSON 化保留结构，避免 "a,b" 歧义
    data: typeof o.data === "object" && o.data !== null ? asText(o.data) : asText(o.data),
    expect: asText(o.expect).trim(),
    rule: asText(o.rule),
    ...(allowed && allowed.length > 0 ? { allowed } : {}),
  };
}

function normalizeRule(cf: Record<string, unknown>): RequirementRuleView | null {
  const rule = asRecord(cf.requirement_rule);
  const field = asText(rule.field).trim();
  if (!field && !asText(rule.type) && !asText(rule.condition)) return null;
  const src = asRecord(rule.source);
  const hasSource = Object.keys(src).length > 0;
  return {
    field: field || DASH,
    type: asText(rule.type) || DASH,
    required: rule.required === true,
    condition: asText(rule.condition),
    range_raw: asText(rule.range_raw),
    evidence: asText(rule.evidence) || undefined,
    ...(hasSource
      ? {
          source: {
            row: typeof src.row === "number" ? src.row : undefined,
            line: typeof src.line === "number" ? src.line : undefined,
            page: typeof src.page === "number" ? src.page : undefined,
            table: typeof src.table === "number" ? src.table : undefined,
            heading: asText(src.heading) || undefined,
            section: asText(src.section) || undefined,
            heading_title: asText(src.heading_title) || undefined,
          },
        }
      : {}),
  };
}

function normalizeWebCoverage(cf: Record<string, unknown>): WebCoverageView | null {
  const wc = asRecord(cf.web_coverage);
  if (Object.keys(wc).length === 0) return null;
  return {
    role: asText(wc.role) || undefined,
    rule_id: asText(wc.rule_id) || undefined,
    bound_at: asText(wc.bound_at) || undefined,
    web_test_id: asText(wc.web_test_id) || undefined,
    web_test_identifier: asText(wc.web_test_identifier) || undefined,
    compile_status: asText(wc.compile_status) || undefined,
    assertion_count:
      typeof wc.assertion_count === "number" ? wc.assertion_count : undefined,
    auto_executable:
      typeof wc.auto_executable === "boolean" ? wc.auto_executable : undefined,
    sub_function_id: asText(wc.sub_function_id) || undefined,
  };
}

/**
 * 把一条用例转成成果物视图。
 *
 * 关键口径（与后端一致，不要在这里"美化"）：
 * - `executable_track` 是**生成期**判定，`manual` 不代表永远不能跑；
 * - `web_coverage` 落地才是"已进入 Web 冒烟编译"，这是"能否自动执行"的实证；
 * - 两者不一致时必须把差异说出来，否则用户会以为 manual 用例是漏跑。
 */
export function toCaseArtifact(testCase: TestCaseInfo): CaseArtifactView {
  const cf = asRecord(testCase.custom_fields);
  const rawAssertions = Array.isArray(cf.assertions) ? cf.assertions : [];
  const assertions = rawAssertions
    .map(normalizeAssertion)
    .filter((a): a is AssertionView => a !== null);
  const webCoverage = normalizeWebCoverage(cf);
  const executableTrack = asText(cf.executable_track, "unknown");
  const compiled = Boolean(webCoverage?.web_test_id);
  const caseKind = testCase.case_kind || "sit";

  let gapReason = "";
  let gapCode: GapCode = "";
  if (!compiled) {
    const moduleSource = asText(cf.module_source);
    if (caseKind === "api") {
      // API 契约类用例走的是接口资产（Swagger/Postman），本来就不进 Web 冒烟编译。
      // 归到这里而不是 pending，否则会被读成"该编译却没编译"。
      gapCode = "api";
      gapReason = "接口契约类用例，走 API 资产而非 Web 冒烟，不参与表单编译";
    } else if (executableTrack === "manual") {
      if (moduleSource === "unmapped") {
        gapCode = "unmapped";
        gapReason = "需求点未关联到字段说明表，抽不出可判定断言 → 需人工执行";
      } else if (assertions.length === 0) {
        gapCode = "narrative";
        gapReason = "该需求点是叙述/导航类，未提取出断言 → 需人工执行";
      } else {
        gapCode = "unbound";
        gapReason = "有断言但未绑定 Web 子功能 → 尚未编译进冒烟";
      }
    } else if (executableTrack === "auto") {
      gapCode = "pending";
      gapReason = "标记为可自动执行，但尚未生成 Web 冒烟资产";
    } else {
      // 手工建的老用例没有 executable_track：不知道通道，不能猜成 auto。
      gapCode = "pending";
      gapReason = "未标注执行通道（非文档生成用例）→ 未进冒烟编译";
    }
  }

  return {
    identifier: testCase.identifier,
    name: testCase.name,
    module: testCase.module || DASH,
    keyword: testCase.keyword || "",
    caseKind,
    sourceLocation: testCase.source_location || "",
    executableTrack,
    compiled,
    assertionCount: assertions.length,
    assertions,
    requirementRule: normalizeRule(cf),
    steps: (testCase.test_case_steps || []).map((s) => ({
      step: asText(s.step),
      result: asText(s.result),
    })),
    webCoverage,
    gapReason,
    gapCode,
  };
}

export interface ArtifactSummary {
  total: number;
  byKeyword: Record<string, number>;
  byModule: Record<string, number>;
  /** 有断言（可在 UI 判定的）用例数 */
  withAssertions: number;
  /** 已进入 Web 冒烟编译的用例数（web_coverage 落地） */
  compiled: number;
  /** 自动执行通道：auto */
  autoTrack: number;
  /** 人工执行通道：manual */
  manualTrack: number;
  /** 断言总数（重复断言按条计） */
  assertionTotal: number;
  /** 断言 kind 分布，用于看覆盖的校验类型是否偏科 */
  assertionKinds: Record<string, number>;
  /** 未编译且有人工原因的用例数 */
  withGapReason: number;
  /** 缺口按原因分桶（unbound/narrative/unmapped/pending） */
  gapByReason: Record<string, number>;
  /** 未能解释的缺口：未编译但 gapReason 为空（应恒为 0，非 0 说明视图漏了维度） */
  unexplained: number;
}

/** 汇总：口径与后端 COVERAGE 对账一致——**总数不给结论，构成才给结论**。 */
export function summarizeArtifacts(cases: TestCaseInfo[]): ArtifactSummary {
  const views = cases.map(toCaseArtifact);
  const byKeyword: Record<string, number> = {};
  const byModule: Record<string, number> = {};
  const assertionKinds: Record<string, number> = {};
  const gapByReason: Record<string, number> = {};
  let withAssertions = 0;
  let compiled = 0;
  let autoTrack = 0;
  let manualTrack = 0;
  let assertionTotal = 0;
  let withGapReason = 0;
  let unexplained = 0;

  for (const v of views) {
    const kw = v.keyword || "未标注";
    byKeyword[kw] = (byKeyword[kw] || 0) + 1;
    byModule[v.module] = (byModule[v.module] || 0) + 1;
    if (v.assertionCount > 0) withAssertions += 1;
    assertionTotal += v.assertionCount;
    for (const a of v.assertions) {
      assertionKinds[a.kind] = (assertionKinds[a.kind] || 0) + 1;
    }
    if (v.compiled) compiled += 1;
    if (v.executableTrack === "auto") autoTrack += 1;
    else manualTrack += 1;
    if (!v.compiled) {
      if (v.gapReason) withGapReason += 1;
      else unexplained += 1;
      if (v.gapCode) gapByReason[v.gapCode] = (gapByReason[v.gapCode] || 0) + 1;
    }
  }

  return {
    total: views.length,
    byKeyword,
    byModule,
    withAssertions,
    compiled,
    autoTrack,
    manualTrack,
    assertionTotal,
    assertionKinds,
    withGapReason,
    gapByReason,
    unexplained,
  };
}

/** 缺口原因 → 人话（顺序即展示顺序：可修的排前面）。 */
export const GAP_REASON_LABELS: Record<string, string> = {
  unbound: "有断言但未绑定 Web 子功能",
  narrative: "叙述/导航类，抽不出断言",
  unmapped: "需求点没对上字段说明表",
  pending: "标记可自动执行但资产未生成",
  api: "接口契约类，走 API 资产不走 Web 冒烟",
};

/**
 * 「N 条只跑了 M 条」类质疑的正面回答：说明差异来自通道构成，不是漏跑。
 * 数字全部来自实际数据，不做假设。
 */
export function explainCoverageGap(summary: ArtifactSummary): string {
  const { total, autoTrack, manualTrack, withAssertions, compiled, gapByReason } = summary;
  if (total === 0) return "本批次无用例";
  const parts: string[] = [`共 ${total} 条：可自动执行通道 ${autoTrack} 条、人工通道 ${manualTrack} 条`];
  parts.push(`其中 ${withAssertions} 条带可判定断言，${compiled} 条已进入 Web 冒烟编译`);
  const notCompiled = total - compiled;
  if (notCompiled > 0) {
    const detail = Object.keys(GAP_REASON_LABELS)
      .filter((k) => gapByReason[k])
      .map((k) => `${GAP_REASON_LABELS[k]} ${gapByReason[k]} 条`)
      .join("、");
    parts.push(
      detail
        ? `未编译的 ${notCompiled} 条不是漏跑，原因构成：${detail}`
        : `未编译的 ${notCompiled} 条不是漏跑：人工通道用例需要人工执行，生成期已按需求点标注原因`
    );
  }
  return parts.join("；");
}

export const ASSERTION_KIND_LABELS: Record<string, string> = {
  required: "必填",
  enum: "枚举取值",
  boundary: "边界值",
  range: "取值范围",
  precision: "精度",
  max_length: "最大长度",
  max_count: "最大数量",
  file_format: "文件格式",
  file_size: "文件大小",
  default: "默认值",
  visibility: "显示/隐藏",
  condition: "条件联动",
};

export function assertionKindLabel(kind: string): string {
  return ASSERTION_KIND_LABELS[kind] || kind;
}

export const EXECUTABLE_TRACK_LABELS: Record<string, string> = {
  auto: "自动执行",
  manual: "人工执行",
  unknown: "未判定",
};
