import { apiClient } from "./client";
import type {
  TestCaseInfo,
  TestCaseCreate,
  TestCaseUpdate,
  PaginationInfo,
  Priority,
  TestCaseState,
  RequirementReport,
} from "./types";

interface TestCaseResponse {
  success: boolean;
  test_case: TestCaseInfo;
}
// NOTE  MC80OmFIVnBZMlhscm9ua3VMazZNME0yYWc9PTo5MWNmODE2MA==

export interface ImportTestCasesResult {
  created_count: number;
  error_count: number;
  cases?: Array<{ identifier: string; name: string }>;
  errors?: Array<{ row: number; name: string; error: string }>;
}

/** 从 xlsx 导入外部（人工编写）测试用例到指定文件夹。
 *  后端自动识别表头（繁/简），步骤切分入 test_steps；返回逐行结果。 */
export async function importTestCasesExcel(
  projectId: string,
  folderId: string,
  file: File
): Promise<ImportTestCasesResult> {
  const formData = new FormData();
  formData.append("file", file);
  const resp = await apiClient.form<{ success: boolean; data: ImportTestCasesResult }>(
    `/projects/${projectId}/folders/${folderId}/test-cases/import-excel`,
    formData
  );
  return resp.data;
}

interface TestCaseListResponse {
  success: boolean;
  data?: TestCaseInfo[];
  test_cases?: TestCaseInfo[];
  info?: PaginationInfo;
  pagination?: PaginationInfo;
}

interface TestCaseDeleteResponse {
  success: boolean;
  message: string;
}

export interface TestCaseQueryParams {
  p?: number;
  page_size?: number;
  folder_id?: string;
  search?: string;
  priority?: Priority | string;
  status?: TestCaseState | string;
  case_kind?: string;
  owner?: string;
  tags?: string;
  minify?: boolean;
}

// 获取项目下的测试用例列表
export function getTestCases(
  projectId: string,
  params?: TestCaseQueryParams
) {
  return apiClient.get<TestCaseListResponse>(
    `/projects/${projectId}/test-cases`,
    { params: params as Record<string, string | number | boolean | undefined> }
  );
}
// NOTE  MS80OmFIVnBZMlhscm9ua3VMazZNME0yYWc9PTo5MWNmODE2MA==

// 获取文件夹下的测试用例
export function getFolderTestCases(
  projectId: string,
  folderId: string,
  params?: TestCaseQueryParams
) {
  return apiClient.get<TestCaseListResponse>(
    `/projects/${projectId}/folders/${folderId}/test-cases`,
    { params: params as Record<string, string | number | boolean | undefined> }
  );
}

// 获取测试用例详情
export function getTestCase(projectId: string, testCaseId: string) {
  return apiClient.get<TestCaseResponse>(
    `/projects/${projectId}/test-cases/${testCaseId}`
  );
}

// 创建测试用例
export function createTestCase(
  projectId: string,
  folderId: string | null,
  data: TestCaseCreate
) {
  const url = folderId
    ? `/projects/${projectId}/folders/${folderId}/test-cases`
    : `/projects/${projectId}/test-cases`;
  return apiClient.post<TestCaseResponse>(url, data);
}

// 更新测试用例
export function updateTestCase(
  projectId: string,
  testCaseId: string,
  data: TestCaseUpdate
) {
  return apiClient.patch<TestCaseResponse>(
    `/projects/${projectId}/test-cases/${testCaseId}`,
    data
  );
}
// TODO  Mi80OmFIVnBZMlhscm9ua3VMazZNME0yYWc9PTo5MWNmODE2MA==

// 删除测试用例
export function deleteTestCase(projectId: string, testCaseId: string) {
  return apiClient.delete<TestCaseDeleteResponse>(
    `/projects/${projectId}/test-cases/${testCaseId}`
  );
}

// 移动测试用例到其他文件夹
export function moveTestCase(
  projectId: string,
  testCaseId: string,
  folderId: string | null
) {
  return apiClient.patch<TestCaseResponse>(
    `/projects/${projectId}/test-cases/${testCaseId}/move`,
    { folder_id: folderId }
  );
}

interface BulkOperationResponse {
  success: boolean;
  message: string;
  affected_count: number;
}

// 批量删除测试用例
export function bulkDeleteTestCases(
  projectId: string,
  testCaseIds: string[]
) {
  return apiClient.delete<BulkOperationResponse>(
    `/projects/${projectId}/test-cases`,
    {
      data: {
        test_case_ids: testCaseIds,
      },
    }
  );
}
// TODO  My80OmFIVnBZMlhscm9ua3VMazZNME0yYWc9PTo5MWNmODE2MA==

// 批量更新测试用例
export function bulkUpdateTestCases(
  projectId: string,
  testCaseIds: string[],
  updateData: Partial<TestCaseUpdate>
) {
  return apiClient.post(`/projects/${projectId}/test-cases/bulk/update`, {
    test_case_ids: testCaseIds,
    update_data: updateData,
  });
}

// 导出测试用例为 Excel
export function exportTestCases(
  projectId: string,
  params: {
    folder_id?: string;
    test_case_ids?: string;
    export_type?: "test_cases" | "req_analysis";
    requirement_name?: string;
  }
) {
  const searchParams = new URLSearchParams();
  if (params.folder_id) searchParams.set("folder_id", params.folder_id);
  if (params.test_case_ids) searchParams.set("test_case_ids", params.test_case_ids);
  if (params.export_type) searchParams.set("export_type", params.export_type);
  if (params.requirement_name) searchParams.set("requirement_name", params.requirement_name);

  const query = searchParams.toString();
  const url = `/projects/${projectId}/test-cases/export-excel${query ? `?${query}` : ""}`;

  return apiClient.download(url);
}

// 获取需求分析报告列表
export function getRequirementReports(projectId: string) {
  return apiClient.get<{ success: boolean; data: RequirementReport[]; message: string }>(
    `/projects/${projectId}/requirement-reports`
  );
}

// 导出需求分析报告
export function exportRequirementReport(projectId: string, filename: string) {
  const url = `/projects/${projectId}/requirement-reports/${encodeURIComponent(filename)}/export`;
  return apiClient.download(url);
}

