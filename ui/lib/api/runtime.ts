/**
 * 统一运行时 API 客户端
 *
 * 提供产物发布、执行前预检、统一执行入口的 API 调用封装。
 */

const API_BASE = "/api/v2";

// ---------------------------------------------------------------------------
// 类型定义
// ---------------------------------------------------------------------------

export interface PublishRequest {
  run_id: string;
  artifact_id?: string;
  test_case_id?: string;
  scenario_id?: string;
  source_path: string;
  artifact_type?: string; // "web_script" | "api_script" | "scenario_manifest"
  expected_sha256?: string;
  artifact_version?: string;
  mode?: string; // "safe" | "live"
}

export interface PublishResponse {
  artifact_path: string;
  artifact_type: string;
  expected_sha256: string | null;
  actual_sha256: string;
  artifact_version: string;
  run_context: RunContextData;
  published_at: string;
  manifest_path: string;
  provenance_path: string;
}

export interface PreflightRequest {
  run_id: string;
  artifact_id?: string;
  test_case_id?: string;
  scenario_id?: string;
  artifact_version?: string;
  artifact_path?: string;
  track?: string; // "web" | "api" | "scenario"
  mode?: string;
}

export interface PreflightResponse {
  status: "passed" | "blocked";
  reason_code?: string;
  message?: string;
  run_id?: string;
  artifact_path?: string;
  expected_sha256?: string;
  actual_sha256?: string;
  checks?: Array<{
    check: string;
    passed: boolean;
    detail: string;
  }>;
}

export interface ExecuteRequest {
  run_id: string;
  artifact_id?: string;
  test_case_id?: string;
  scenario_id?: string;
  artifact_version?: string;
  track?: string;
  mode?: string;
  live?: boolean;
  base_url?: string;
}

export interface ExecuteResponse {
  status: string;
  run_id: string;
  project_id: string;
  track: string;
  mode: string;
  message: string;
  context: RunContextData;
}

export interface RuntimeStatusResponse {
  run_id: string;
  project_id: string;
  is_published: boolean;
  artifact: PublishResponse | null;
  directory_status: {
    run_dir_exists: boolean;
    run_dir_path: string;
    manifest_exists: boolean;
    provenance_exists: boolean;
  };
}

export interface RunContextData {
  project_id: string;
  run_id: string;
  mode: string;
  artifact_id?: string;
  test_case_id?: string;
  scenario_id?: string;
  artifact_version?: string;
  workspace_root?: string;
}

// ---------------------------------------------------------------------------
// API 函数
// ---------------------------------------------------------------------------

/**
 * 发布产物
 */
export async function publishArtifact(
  projectId: string,
  request: PublishRequest
): Promise<{ success: boolean; data: PublishResponse }> {
  const response = await fetch(
    `${API_BASE}/projects/${projectId}/runtime/publish`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }
  );

  const data = await response.json();
  if (!data.success) {
    throw new Error(data.data?.message || "发布产物失败");
  }
  return data;
}

/**
 * 执行前预检
 */
export async function preflightCheck(
  projectId: string,
  request: PreflightRequest
): Promise<{ success: boolean; data: PreflightResponse }> {
  const response = await fetch(
    `${API_BASE}/projects/${projectId}/runtime/preflight`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }
  );

  return await response.json();
}

/**
 * 统一执行入口
 */
export async function executeRuntime(
  projectId: string,
  request: ExecuteRequest
): Promise<{ success: boolean; data: ExecuteResponse }> {
  const response = await fetch(
    `${API_BASE}/projects/${projectId}/runtime/execute`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }
  );

  return await response.json();
}

/**
 * 获取运行状态
 */
export async function getRuntimeStatus(
  projectId: string,
  runId: string,
  artifactId?: string,
  testCaseId?: string,
  scenarioId?: string
): Promise<{ success: boolean; data: RuntimeStatusResponse }> {
  const params = new URLSearchParams();
  params.append("run_id", runId);
  if (artifactId) params.append("artifact_id", artifactId);
  if (testCaseId) params.append("test_case_id", testCaseId);
  if (scenarioId) params.append("scenario_id", scenarioId);

  const response = await fetch(
    `${API_BASE}/projects/${projectId}/runtime/status?${params.toString()}`
  );

  return await response.json();
}
