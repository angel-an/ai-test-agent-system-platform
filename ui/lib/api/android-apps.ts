/**
 * Android App 测试相关的 API 客户端函数
 */

import { apiClient } from "./client";

// ==================== 类型定义 ====================

export interface AndroidApp {
  id: string;
  identifier: string;
  display_name: string;
  name: string;
  description: string | null;
  app_package: string;
  main_activity: string | null;
  version: string | null;
  device_udid: string | null;
  business_module: string | null;
  navigation: any;
  pages: any;
  tags: any;
  custom_config: any;
  folder_id: string | null;
  total_sub_functions: number;
  total_test_cases: number;
  total_test_runs: number;
  last_run_status: string | null;
  sort_order: number;
  created_at: string;
  updated_at: string | null;
}

export interface AndroidSubFunction {
  id: string;
  identifier: string;
  function_id: string;
  display_name: string;
  name: string;
  description: string | null;
  test_type: string;
  target_pages: any;
  test_scenario: string | null;
  test_data: any;
  expected_results: any;
  priority: string;
  tags: any;
  custom_config: any;
  folder_id: string | null;
  total_test_cases: number;
  total_test_runs: number;
  last_run_status: string | null;
  sort_order: number;
  created_at: string;
  updated_at: string | null;
}

export interface CreateAndroidAppRequest {
  display_name: string;
  name?: string;
  description?: string;
  app_package: string;
  main_activity?: string;
  version?: string;
  device_udid?: string;
  business_module?: string;
  navigation?: any;
  pages?: any;
  tags?: any;
  custom_config?: any;
  folder_id?: string | null;
  sort_order?: number;
}

export interface UpdateAndroidAppRequest {
  display_name?: string;
  name?: string;
  description?: string;
  app_package?: string;
  main_activity?: string;
  version?: string;
  device_udid?: string;
  business_module?: string;
  navigation?: any;
  pages?: any;
  tags?: any;
  custom_config?: any;
  folder_id?: string | null;
  sort_order?: number;
}

export interface CreateAndroidSubFunctionRequest {
  function_id: string;
  display_name: string;
  name?: string;
  description?: string;
  test_type?: string;
  target_pages?: any;
  test_scenario?: string;
  test_data?: any;
  expected_results?: any;
  priority?: string;
  tags?: any;
  custom_config?: any;
  folder_id?: string | null;
  sort_order?: number;
}

export interface UpdateAndroidSubFunctionRequest {
  display_name?: string;
  name?: string;
  description?: string;
  test_type?: string;
  target_pages?: any;
  test_scenario?: string;
  test_data?: any;
  expected_results?: any;
  priority?: string;
  tags?: any;
  custom_config?: any;
  folder_id?: string | null;
  sort_order?: number;
}

export interface AndroidAppListResponse {
  items: AndroidApp[];
  total: number;
  offset: number;
  limit: number;
}

export interface AndroidSubFunctionListResponse {
  items: AndroidSubFunction[];
  total: number;
  offset: number;
  limit: number;
}

// ==================== API 函数 ====================

/**
 * 获取 Android App 列表
 */
export async function listAndroidApps(
  projectId: string,
  params?: {
    p?: number;
    page_size?: number;
    search?: string;
    folder_id?: string;
  }
): Promise<AndroidAppListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.p) searchParams.set("p", String(params.p));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  if (params?.search) searchParams.set("search", params.search);
  if (params?.folder_id) searchParams.set("folder_id", params.folder_id);

  const query = searchParams.toString();
  const url = `/projects/${projectId}/android-apps${query ? `?${query}` : ""}`;

  const response = await apiClient.get(url);
  return response as AndroidAppListResponse;
}

/**
 * 获取 Android App 详情
 */
export async function getAndroidApp(
  projectId: string,
  appId: string
): Promise<AndroidApp> {
  const response = await apiClient.get(`/projects/${projectId}/android-apps/${appId}`);
  return response as AndroidApp;
}

/**
 * 创建 Android App
 */
export async function createAndroidApp(
  projectId: string,
  data: CreateAndroidAppRequest
): Promise<AndroidApp> {
  const response = await apiClient.post(`/projects/${projectId}/android-apps`, data);
  return response as AndroidApp;
}

/**
 * 更新 Android App
 */
export async function updateAndroidApp(
  projectId: string,
  appId: string,
  data: UpdateAndroidAppRequest
): Promise<AndroidApp> {
  const response = await apiClient.put(`/projects/${projectId}/android-apps/${appId}`, data);
  return response as AndroidApp;
}

/**
 * 删除 Android App
 */
export async function deleteAndroidApp(
  projectId: string,
  appId: string
): Promise<void> {
  await apiClient.delete(`/projects/${projectId}/android-apps/${appId}`);
}

/**
 * 获取 Android 子功能列表
 */
export async function listAndroidSubFunctions(
  projectId: string,
  params?: {
    function_id?: string;
    p?: number;
    page_size?: number;
    search?: string;
  }
): Promise<AndroidSubFunctionListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.function_id) searchParams.set("function_id", params.function_id);
  if (params?.p) searchParams.set("p", String(params.p));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  if (params?.search) searchParams.set("search", params.search);

  const query = searchParams.toString();
  const url = `/projects/${projectId}/android-apps/sub-functions${query ? `?${query}` : ""}`;

  const response = await apiClient.get(url);
  return response as AndroidSubFunctionListResponse;
}

/**
 * 获取 Android 子功能详情
 */
export async function getAndroidSubFunction(
  projectId: string,
  subFunctionId: string
): Promise<AndroidSubFunction> {
  const response = await apiClient.get(`/projects/${projectId}/android-apps/sub-functions/${subFunctionId}`);
  return response as AndroidSubFunction;
}

/**
 * 创建 Android 子功能
 */
export async function createAndroidSubFunction(
  projectId: string,
  data: CreateAndroidSubFunctionRequest
): Promise<AndroidSubFunction> {
  const response = await apiClient.post(`/projects/${projectId}/android-apps/sub-functions`, data);
  return response as AndroidSubFunction;
}

/**
 * 更新 Android 子功能
 */
export async function updateAndroidSubFunction(
  projectId: string,
  subFunctionId: string,
  data: UpdateAndroidSubFunctionRequest
): Promise<AndroidSubFunction> {
  const response = await apiClient.put(`/projects/${projectId}/android-apps/sub-functions/${subFunctionId}`, data);
  return response as AndroidSubFunction;
}

/**
 * 删除 Android 子功能
 */
export async function deleteAndroidSubFunction(
  projectId: string,
  subFunctionId: string
): Promise<void> {
  await apiClient.delete(`/projects/${projectId}/android-apps/sub-functions/${subFunctionId}`);
}

/**
 * 获取文件夹下的 Android Apps
 */
export async function getFolderAndroidApps(
  projectId: string,
  folderId: string
): Promise<AndroidAppListResponse> {
  const response = await apiClient.get(`/projects/${projectId}/folders/${folderId}/android-apps`);
  return response as AndroidAppListResponse;
}

/**
 * 获取文件夹下的 Android 子功能
 */
export async function getFolderAndroidSubFunctions(
  projectId: string,
  folderId: string
): Promise<AndroidSubFunctionListResponse> {
  const response = await apiClient.get(`/projects/${projectId}/folders/${folderId}/android-apps/sub-functions`);
  return response as AndroidSubFunctionListResponse;
}
