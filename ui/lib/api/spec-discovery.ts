/**
 * Spec 自动发现 API 客户端
 */

import { apiClient } from "./client";

// ==================== 类型定义 ====================

export interface SpecCandidate {
  url: string;
  valid: boolean;
  endpoint_count: number;
  error?: string;
}

export interface SpecDiscoveryResult {
  source_type: string;
  ui_url?: string;
  spec_url: string | null;
  format: string | null;
  version: string | null;
  endpoint_count: number;
  requires_auth: boolean;
  candidates: SpecCandidate[];
  message?: string;
}

export interface SpecDiscoveryRequest {
  url: string;
  headers?: Record<string, string>;
}

// ==================== API 函数 ====================

/**
 * 自动发现 OpenAPI/Swagger Spec 地址
 *
 * 支持从 Swagger UI / Knife4j 页面地址自动发现背后的 OpenAPI JSON/YAML 文档地址
 */
export async function discoverSpecAPI(
  request: SpecDiscoveryRequest
): Promise<SpecDiscoveryResult> {
  const response = await apiClient.post<{ success: boolean; data: SpecDiscoveryResult }>(
    "/spec-discovery/discover",
    request
  );
  return response.data;
}

/**
 * 验证单个 Spec URL
 */
export async function validateSpecAPI(
  url: string,
  headers?: Record<string, string>
): Promise<{
  valid: boolean;
  url: string;
  format: string | null;
  version: string | null;
  endpoint_count: number;
  error?: string;
}> {
  const response = await apiClient.post<{ success: boolean; data: any }>(
    "/spec-discovery/validate",
    { url, headers: headers || {} }
  );
  return response.data;
}
