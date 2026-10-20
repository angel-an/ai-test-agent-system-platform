/**
 * iOS App 测试相关的 API 客户端函数
 */

import { apiClient } from "./client";

// ==================== 类型定义 ====================

export interface IOSApp {
  id: string;
  identifier: string;
  display_name: string;
  name: string;
  description: string | null;
  bundle_id: string;              // iOS: bundle_id 替代 app_package
  main_activity: string | null;
  version: string | null;
  device_udid: string | null;
  device_type: "physical" | "simulator" | null;  // iOS特有：设备类型
  wda_url: string | null;         // iOS特有：WebDriverAgent地址
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

export interface IOSSubFunction {
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

export interface CreateIOSAppRequest {
  display_name: string;
  name?: string;
  description?: string;
  bundle_id: string;              // iOS特有
  main_activity?: string;
  version?: string;
  device_udid?: string;
  device_type?: "physical" | "simulator";  // iOS特有
  wda_url?: string;               // iOS特有
  business_module?: string;
  navigation?: any;
  pages?: any;
  tags?: any;
  custom_config?: any;
  folder_id?: string | null;
  sort_order?: number;
}

export interface UpdateIOSAppRequest {
  display_name?: string;
  name?: string;
  description?: string;
  bundle_id?: string;
  main_activity?: string;
  version?: string;
  device_udid?: string;
  device_type?: "physical" | "simulator";
  wda_url?: string;
  business_module?: string;
  navigation?: any;
  pages?: any;
  tags?: any;
  custom_config?: any;
  folder_id?: string | null;
  sort_order?: number;
}

export interface CreateIOSSubFunctionRequest {
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

export interface UpdateIOSSubFunctionRequest {
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

export interface IOSAppListResponse {
  items: IOSApp[];
  total: number;
  offset: number;
  limit: number;
}

export interface IOSSubFunctionListResponse {
  items: IOSSubFunction[];
  total: number;
  offset: number;
  limit: number;
}

// ==================== API 函数 ====================

/**
 * 获取 iOS App 列表
 */
export async function listIOSApps(
  projectId: string,
  params?: {
    p?: number;
    page_size?: number;
    search?: string;
    folder_id?: string;
  }
): Promise<IOSAppListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.p) searchParams.set("p", String(params.p));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  if (params?.search) searchParams.set("search", params.search);
  if (params?.folder_id) searchParams.set("folder_id", params.folder_id);

  const query = searchParams.toString();
  const url = `/projects/${projectId}/ios-apps${query ? `?${query}` : ""}`;

  const response = await apiClient.get(url);
  return response as IOSAppListResponse;
}

/**
 * 获取 iOS App 详情
 */
export async function getIOSApp(
  projectId: string,
  appId: string
): Promise<IOSApp> {
  const response = await apiClient.get(`/projects/${projectId}/ios-apps/${appId}`);
  return response as IOSApp;
}

/**
 * 创建 iOS App
 */
export async function createIOSApp(
  projectId: string,
  data: CreateIOSAppRequest
): Promise<IOSApp> {
  const response = await apiClient.post(`/projects/${projectId}/ios-apps`, data);
  return response as IOSApp;
}

/**
 * 更新 iOS App
 */
export async function updateIOSApp(
  projectId: string,
  appId: string,
  data: UpdateIOSAppRequest
): Promise<IOSApp> {
  const response = await apiClient.put(`/projects/${projectId}/ios-apps/${appId}`, data);
  return response as IOSApp;
}

/**
 * 删除 iOS App
 */
export async function deleteIOSApp(
  projectId: string,
  appId: string
): Promise<void> {
  await apiClient.delete(`/projects/${projectId}/ios-apps/${appId}`);
}

/**
 * 获取 iOS 子功能列表
 */
export async function listIOSSubFunctions(
  projectId: string,
  params?: {
    function_id?: string;
    p?: number;
    page_size?: number;
    search?: string;
  }
): Promise<IOSSubFunctionListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.function_id) searchParams.set("function_id", params.function_id);
  if (params?.p) searchParams.set("p", String(params.p));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  if (params?.search) searchParams.set("search", params.search);

  const query = searchParams.toString();
  const url = `/projects/${projectId}/ios-apps/sub-functions${query ? `?${query}` : ""}`;

  const response = await apiClient.get(url);
  return response as IOSSubFunctionListResponse;
}

/**
 * 获取 iOS 子功能详情
 */
export async function getIOSSubFunction(
  projectId: string,
  subFunctionId: string
): Promise<IOSSubFunction> {
  const response = await apiClient.get(`/projects/${projectId}/ios-apps/sub-functions/${subFunctionId}`);
  return response as IOSSubFunction;
}

/**
 * 创建 iOS 子功能
 */
export async function createIOSSubFunction(
  projectId: string,
  data: CreateIOSSubFunctionRequest
): Promise<IOSSubFunction> {
  const response = await apiClient.post(`/projects/${projectId}/ios-apps/sub-functions`, data);
  return response as IOSSubFunction;
}

/**
 * 更新 iOS 子功能
 */
export async function updateIOSSubFunction(
  projectId: string,
  subFunctionId: string,
  data: UpdateIOSSubFunctionRequest
): Promise<IOSSubFunction> {
  const response = await apiClient.put(`/projects/${projectId}/ios-apps/sub-functions/${subFunctionId}`, data);
  return response as IOSSubFunction;
}

/**
 * 删除 iOS 子功能
 */
export async function deleteIOSSubFunction(
  projectId: string,
  subFunctionId: string
): Promise<void> {
  await apiClient.delete(`/projects/${projectId}/ios-apps/sub-functions/${subFunctionId}`);
}

/**
 * 获取文件夹下的 iOS Apps
 */
export async function getFolderIOSApps(
  projectId: string,
  folderId: string
): Promise<IOSAppListResponse> {
  const response = await apiClient.get(`/projects/${projectId}/folders/${folderId}/ios-apps`);
  return response as IOSAppListResponse;
}

/**
 * 获取文件夹下的 iOS 子功能
 */
export async function getFolderIOSSubFunctions(
  projectId: string,
  folderId: string
): Promise<IOSSubFunctionListResponse> {
  const response = await apiClient.get(`/projects/${projectId}/folders/${folderId}/ios-apps/sub-functions`);
  return response as IOSSubFunctionListResponse;
}
