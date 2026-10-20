/**
 * 定时运行 API 客户端
 */
import { apiClient } from "./client";

export interface ScheduledRun {
  id: string;
  identifier: string;
  name: string;
  description?: string;
  api_endpoint_ids: string[];
  execution_config?: Record<string, unknown>;
  cron_expression: string;
  timezone: string;
  is_active: boolean;
  last_executed_at?: string;
  created_at: string;
  updated_at?: string;
}

export interface ScheduledRunExecution {
  id: string;
  identifier: string;
  scheduled_run_id: string;
  status: string;
  api_test_run_ids: string[];
  total_tests: number;
  passed_tests: number;
  failed_tests: number;
  error_message?: string;
  report_path?: string;
  created_at: string;
}

interface ListResponse<T> {
  data: { items: T[]; total: number; page: number; page_size: number };
}

export async function listScheduledRuns(projectId: string, params?: { p?: number; page_size?: number }) {
  return apiClient.get<ListResponse<ScheduledRun>>(
    `/projects/${projectId}/scheduled-runs`,
    { params: params as Record<string, string | number | boolean | undefined> }
  );
}

export async function createScheduledRun(projectId: string, body: {
  name: string;
  cron_expression: string;
  api_endpoint_ids: string[];
  description?: string;
  execution_config?: Record<string, unknown>;
  timezone?: string;
}) {
  return apiClient.post<{ data: ScheduledRun }>(
    `/projects/${projectId}/scheduled-runs`,
    body
  );
}

export async function updateScheduledRun(projectId: string, runId: string, body: Partial<{
  name: string;
  description: string;
  cron_expression: string;
  api_endpoint_ids: string[];
  execution_config: Record<string, unknown>;
  timezone: string;
  is_active: boolean;
}>) {
  return apiClient.patch<{ data: ScheduledRun }>(
    `/projects/${projectId}/scheduled-runs/${runId}`,
    body
  );
}

export async function deleteScheduledRun(projectId: string, runId: string) {
  return apiClient.delete(`/projects/${projectId}/scheduled-runs/${runId}`);
}

export async function triggerScheduledRun(projectId: string, runId: string) {
  return apiClient.post<{ data: { run_id: string; status: string } }>(
    `/projects/${projectId}/scheduled-runs/${runId}/trigger`
  );
}

export async function listExecutions(projectId: string, runId: string, params?: { p?: number; page_size?: number }) {
  return apiClient.get<ListResponse<ScheduledRunExecution>>(
    `/projects/${projectId}/scheduled-runs/${runId}/executions`,
    { params: params as Record<string, string | number | boolean | undefined> }
  );
}
