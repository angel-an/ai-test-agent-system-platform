import { apiClient } from "./client";
import type { SuccessResponse } from "./types";

export interface ChecklistRow {
  row_id: string;
  source_row?: number | null;
  title: string;
  main_module: string;
  sub_module: string;
  feature: string;
  expected: string;
  priority: string;
  probe_kind: string;
  data_mode: string;
  content_md5: string;
  latest_status?: "pass" | "fail" | "unverified" | null;
  latest_error?: string | null;
  latest_evidence?: string | null;
  latest_consistency?: string | null;
  latest_run_at?: string | null;
}

export interface ChecklistRunSummary {
  run_id: string;
  run_verdict: "pass" | "fail" | "unverified" | "empty" | string;
  profile_name: string;
  summary?: { pass?: number; fail?: number; unverified?: number } | null;
  skipped_flow?: number;
  report_url?: string | null;
  created_at?: string | null;
}

export function getChecklistRows(
  projectId: string,
  filters?: { main_module?: string; priority?: string; probe_kind?: string }
) {
  return apiClient.get<SuccessResponse<ChecklistRow[]>>(
    `/projects/${projectId}/checklist/rows`,
    { params: filters }
  );
}

export function getChecklistRuns(projectId: string, limit = 20) {
  return apiClient.get<SuccessResponse<ChecklistRunSummary[]>>(
    `/projects/${projectId}/checklist/runs`,
    { params: { limit } }
  );
}

export function getChecklistRun(projectId: string, runId: string) {
  return apiClient.get<SuccessResponse<{
    run_id: string;
    run_verdict: string;
    profile_name: string;
    summary?: Record<string, number> | null;
    skipped_flow?: number;
    report_url?: string | null;
    results: Array<{
      row_id: string;
      status: string;
      error?: string | null;
      evidence?: string | null;
      consistency?: string | null;
      feature?: string | null;
      main_module?: string | null;
    }>;
  }>>(`/projects/${projectId}/checklist/runs/${runId}`);
}
