/**
 * 报告 API
 */

import { apiClient } from "./client";
import type { SuccessResponse } from "./types";

export interface PriorityDistributionItem {
  priority: string;
  count: number;
  percentage: number;
}

export interface TypeDistributionItem {
  type: string;
  count: number;
}

export interface RecentTestRunItem {
  id: string;
  identifier: string;
  name: string;
  passed: number;
  failed: number;
  skipped: number;
  blocked: number;
  total: number;
  pass_rate: number;
  run_state: string;
  created_at: string;
}

export interface ReportSummary {
  total_test_cases: number;
  total_test_runs: number;
  pass_rate: number;
  open_defects: number;
  avg_execution_time: string;
  priority_distribution: PriorityDistributionItem[];
  type_distribution: TypeDistributionItem[];
  recent_test_runs: RecentTestRunItem[];
}

/**
 * 获取报告统计摘要
 */
export async function getReportSummary(
  projectIdentifier: string,
  dateRange: string = "all"
): Promise<SuccessResponse<ReportSummary>> {
  return apiClient.get<SuccessResponse<ReportSummary>>(
    `/projects/${projectIdentifier}/test-runs/reports/summary`,
    { params: { date_range: dateRange } }
  );
}
