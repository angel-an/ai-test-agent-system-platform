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

/** 同步预览/导入共用的行载荷（与后端 ChecklistRowIn 对齐） */
export interface ChecklistImportItem {
  row_id: string;
  row?: number | null;
  title: string;
  main_module: string;
  sub_module: string;
  feature: string;
  expected: string;
  priority: string;
  probe_kind: string;
  data_mode: string;
  content_md5: string;
  extra?: Record<string, unknown> | null;
}

export interface SyncDiffDetail {
  row_id: string;
  action: "created" | "updated";
  title: string;
  main_module: string;
  /** updated 行的字段级 before/after */
  changes: Record<string, [string, string]>;
}

export interface SyncPreviewData {
  counts: { created: number; updated: number; skipped: number };
  total: number;
  dup_row_ids: number;
  details: SyncDiffDetail[];
  items: ChecklistImportItem[];
}

/** 第一步：上传腾讯文档导出的表格文件，预览 diff（不落库） */
export function previewChecklistSync(projectId: string, file: File) {
  const formData = new FormData();
  formData.append("file", file);
  return apiClient.form<SuccessResponse<SyncPreviewData>>(
    `/projects/${projectId}/checklist/sync-preview`,
    formData
  );
}

/** 第二步：确认导入（幂等 upsert）。dryRun=true 时等同再算一遍 diff。 */
export function importChecklist(projectId: string, items: ChecklistImportItem[], dryRun = false) {
  return apiClient.post<SuccessResponse<SyncPreviewData>>(
    `/projects/${projectId}/checklist/import`,
    { rows: items, dry_run: dryRun }
  );
}
