/**
 * 需求质量门控 API（阶段一 + 阶段二）
 *
 * 对应后端：app/api/v2/requirement_quality.py
 * 响应契约：{ prd_quality, analysis_report, testcase_generation }
 */

import { apiClient } from "./client";
import type { SuccessResponse } from "./types";

/** 需求质量状态（V1.2） */
export type PRDQualityStatus = "Complete" | "Incomplete" | "Blocked";

/** 测试用例生成状态 */
export type TestCaseGenerationStatus =
  | "not_started"
  | "blocked"
  | "generated"
  | "rejected";

/** 需求质量摘要 */
export interface PRDQualitySummary {
  status: PRDQualityStatus;
  status_label: string;
  can_generate_testcases: boolean;
  missing_count: number;
  ambiguous_count: number;
  blocked_count: number;
}

/** 缺口/风险项 */
export interface RequirementGap {
  item: string;
  kind:
    | "required_missing"
    | "ambiguous"
    | "conditional_missing"
    | "flagged";
  detail: string;
  blocking?: boolean;
  evidence?: string;
}

/** 需求分析报告 */
export interface RequirementAnalysisReport {
  status: PRDQualityStatus;
  status_label: string;
  can_generate_testcases: boolean;
  blocked_reasons: string[];
  missing_items: RequirementGap[];
  ambiguous_items: RequirementGap[];
  flagged_items: RequirementGap[];
  affected_features: string[];
  parse_error?: string | null;
}

/** 生成状态 */
export interface TestCaseGenerationInfo {
  status: TestCaseGenerationStatus;
  reasons: string[];
  created_count: number;
}

/** 需求分析接口响应数据 */
export interface RequirementAnalysisData {
  prd_quality: PRDQualitySummary;
  analysis_report: RequirementAnalysisReport;
  testcase_generation: TestCaseGenerationInfo;
  analysis_report_markdown?: string;
}

/** 功能点（阶段二） */
export interface FeaturePointInfo {
  feature_id: string;
  feature_name: string;
  business_goal?: string;
  roles_permissions?: string[];
  preconditions?: string[];
  operation_flow?: string[];
  input_rules?: string[];
  business_rules?: string[];
  normal_results?: string[];
  exception_cases?: string[];
  acceptance_criteria?: string[];
  priority: string;
  requirement_id?: string;
  source_location?: string;
}

/** 功能点覆盖情况 */
export interface FeatureCoverageInfo {
  feature_id: string;
  feature_name: string;
  priority: string;
  covered: boolean;
  case_count: number;
  case_ids: string[];
  test_point_ids: string[];
  acceptance_total: number;
  acceptance_covered: number;
  acceptance_rate: number;
  uncovered_criteria: Array<{ acceptance_id: string; text: string }>;
}

/** 覆盖报告 */
export interface CoverageReport {
  requirement_id: string;
  feature_count: number;
  covered_feature_count: number;
  feature_coverage_rate: number;
  acceptance_coverage_rate: number;
  total_cases: number;
  traced_case_count: number;
  untraced_info: {
    legacy_case_count: number;
    legacy_case_ids: string[];
    untraced_case_count: number;
    untraced_case_ids: string[];
    orphan_case_ids: string[];
  };
  features: FeatureCoverageInfo[];
  uncovered_features: FeatureCoverageInfo[];
  uncovered_acceptance_criteria: Array<{
    acceptance_id: string;
    feature_id: string;
    text: string;
  }>;
  gaps: string[];
}

/** 用例追踪问题 */
export interface TraceabilityProblem {
  case_id: string;
  title: string;
  problems: string[];
}

/** 契约校验结果 */
export interface ContractValidationInfo {
  passed: boolean;
  contract_problems: Array<{
    index: number;
    case_id: string;
    problems: string[];
  }>;
  traceability_problems: TraceabilityProblem[];
}

/** 需求链路摘要 */
export interface RequirementLinkInfo {
  requirement_id: string;
  status: PRDQualityStatus;
  can_generate_testcases: boolean;
  feature_count: number;
  test_point_count: number;
  case_count: number;
  affected_features: string[];
  features: Array<{
    feature_id: string;
    feature_name: string;
    priority: string;
    case_count: number;
    case_ids: string[];
  }>;
}

/** 生成接口响应数据 */
export interface RequirementGenerateData extends RequirementAnalysisData {
  requirements: RequirementLinkInfo;
  feature_points: FeaturePointInfo[];
  cases: Array<Record<string, unknown>>;
  coverage?: CoverageReport;
  contract_validation: ContractValidationInfo;
  stage?: string;
}

/** 覆盖报告接口响应数据 */
export interface RequirementCoverageData {
  coverage: CoverageReport;
  cases_by_feature?: Array<Record<string, unknown>>;
  compatibility: {
    total: number;
    legacy_case_ids: string[];
    traced_case_ids: string[];
    untraced_case_ids: string[];
    traceability_problems: TraceabilityProblem[];
    gaps: string[];
  };
}

export interface AnalyzeRequirementRequest {
  requirement_text: string;
  requirement_id?: string;
  source?: string;
  analysis_payload?: unknown;
  session_key?: string;
}

export interface GenerateFromRequirementRequest extends AnalyzeRequirementRequest {
  test_cases?: Array<Record<string, unknown>>;
}

export interface CoverageReportRequest {
  requirement_text?: string;
  requirement_id?: string;
  feature_points?: FeaturePointInfo[];
  test_cases?: Array<Record<string, unknown>>;
  results_by_case?: Record<string, unknown>;
  feature_id?: string;
}

/** 需求分析 → 质量判定（Complete / Incomplete / Blocked） */
export function analyzeRequirement(
  projectId: string,
  data: AnalyzeRequirementRequest
) {
  return apiClient.post<SuccessResponse<RequirementAnalysisData>>(
    `/projects/${projectId}/requirements/analyze`,
    data
  );
}

/** 带硬门控的测试用例生成入口 */
export function generateTestcasesWithGate(
  projectId: string,
  data: GenerateFromRequirementRequest
) {
  return apiClient.post<SuccessResponse<RequirementGenerateData>>(
    `/projects/${projectId}/requirements/generate-testcases`,
    data
  );
}

/** 覆盖率与缺口报告 */
export function getRequirementCoverage(
  projectId: string,
  data: CoverageReportRequest
) {
  return apiClient.post<SuccessResponse<RequirementCoverageData>>(
    `/projects/${projectId}/requirements/coverage`,
    data
  );
}

/** 查询某功能点关联的全部测试用例 */
export function getCasesByFeature(
  projectId: string,
  featureId: string,
  data: CoverageReportRequest
) {
  return getRequirementCoverage(projectId, { ...data, feature_id: featureId });
}

/** 查询生成会话的门控状态 */
export function getRequirementGateStatus(
  projectId: string,
  sessionKey?: string
) {
  return apiClient.get<
    SuccessResponse<{
      exists: boolean;
      session_key?: string;
      status: TestCaseGenerationStatus;
      reasons: string[];
    }>
  >(`/projects/${projectId}/requirements/gate`, {
    params: sessionKey ? { session_key: sessionKey } : undefined,
  });
}

/** 历史数据兼容转换（旧格式 → 平台格式） */
export function convertLegacyCases(
  projectId: string,
  data: {
    test_cases: Array<Record<string, unknown>>;
    feature_id?: string;
    requirement_id?: string;
  }
) {
  return apiClient.post<
    SuccessResponse<{
      converted_cases: Array<Record<string, unknown>>;
      classification: {
        legacy: string[];
        traced: string[];
        untraced: string[];
      };
    }>
  >(`/projects/${projectId}/requirements/compatibility`, data);
}
