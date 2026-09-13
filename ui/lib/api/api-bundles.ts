/**
 * 统一导入（unified import / parse-bundle + confirm）API
 *
 * 对应后端：backend/app/api/v2/api_bundles.py
 * 前缀：/api/v2/projects/{project_identifier}/api-bundles
 *
 * 设计约束：
 * - 变量只以 `env:NAME` 引用出现，**前端不得渲染明文密钥**；
 * - confirm 的 409/503 携带结构化 detail，调用方需从 `ApiError.data` 读取
 *   （client 的 message 提取只取 detail 的第一层字符串，会丢掉结构）。
 */

import { apiClient } from "./client";
import type { SuccessResponse } from "./types";

/** 来源类型 */
export type BundleSourceType =
  | "excel_mx"
  | "postman_collection"
  | "postman_environment";

/** 冲突等级（high 需要在导入前确认或豁免） */
export type ConflictSeverity = "high" | "medium" | "low";

/** 冲突处理状态 */
export type ConflictStatus = "open" | "confirmed" | "waived";

/** 冲突处理动作 */
export type ConflictResolution = "accept" | "waive";

/** bundle 状态 */
export type BundleStatus = "parsed" | "imported" | "failed";

/** 解析来源 */
export interface ParsedSource {
  source_type: BundleSourceType;
  file_name: string;
  file_hash?: string;
}

/** 解析统计 */
export interface ParseStats {
  excel_interfaces?: number;
  collection_requests?: number;
  environment_variables?: number;
  contracts?: number;
  conflicts?: number;
  skipped_no_path?: number;
  errors?: string[];
  [key: string]: unknown;
}

/** 冲突条目 */
export interface BundleConflict {
  id: string;
  contract_id: string | null;
  conflict_type: string;
  severity: ConflictSeverity;
  status: ConflictStatus;
  message: string;
  details?: Record<string, unknown> | null;
}

/** 统一契约条目 */
export interface BundleContract {
  id: string;
  endpoint_key: string | null;
  method: string;
  path: string;
  normalized_path: string;
  name: string | null;
  status: string;
  source_types: string[];
  imported_endpoint_id: string | null;
  pending_confirmations: unknown[];
}

/** parse-bundle 响应数据 */
export interface ParseBundleData {
  bundle_id: string;
  status: BundleStatus;
  stats: ParseStats;
  sources: ParsedSource[];
  contracts: BundleContract[];
  conflicts: BundleConflict[];
  warnings: string[];
  errors: string[];
}

/** bundle 详情响应数据 */
export interface BundleDetailData {
  bundle: {
    id: string;
    name: string;
    system_type: string | null;
    status: BundleStatus;
    current_version: number | null;
    parse_result: Record<string, unknown> | null;
  };
  contracts: BundleContract[];
  conflicts: BundleConflict[];
}

/** 资产统计响应数据 */
export interface BundleAssetsData {
  bundle_id: string;
  status: BundleStatus;
  contracts: number;
  contracts_with_endpoint: number;
  request_templates: number;
  assertions: number;
  dependencies: number;
  assertions_by_source?: Record<string, number>;
}

/** 变量条目（仅引用，无明文） */
export interface BundleVariable {
  id: string;
  name: string;
  value_ref: string;
  is_secret: boolean;
  environment: string | null;
  source_type: string | null;
  enabled: boolean;
}

/** confirm 结果数据 */
export interface ConfirmBundleData {
  bundle_id: string;
  status: BundleStatus;
  imported: Array<{
    contract_id: string;
    endpoint_id: string;
    method: string;
    path: string;
  }>;
  skipped: Array<{ contract_id: string; endpoint_id: string; reason: string }>;
  failed: unknown[];
  folder_ids: string[];
  imported_count: number;
  skipped_count: number;
  schema_check?: string;
}

/** 未确认高等级冲突（confirm 被 409 拒绝时返回） */
export interface UnconfirmedConflictPayload {
  bundle_id: string;
  unconfirmed_count: number;
  unconfirmed_conflicts: Array<{
    id: string;
    conflict_type: string;
    severity: ConflictSeverity;
    message: string;
  }>;
}

/** 结构未就绪（confirm 被 503 拒绝时返回） */
export interface SchemaNotReadyPayload {
  error: "schema_not_ready";
  message: string;
  missing: string[];
  remedy: string;
}

/** bundle 列表条目 */
export interface BundleListItem {
  id: string;
  name: string;
  system_type: string | null;
  status: BundleStatus;
  parse_result: Record<string, unknown> | null;
  created_at: string;
}

/** 上传文件集合（键与后端 Form 字段名一致） */
export interface BundleFiles {
  excel_file?: File | null;
  collection_file?: File | null;
  environment_file?: File | null;
}

export interface ParseBundleRequest extends BundleFiles {
  bundle_name: string;
  system_type?: string;
}

const base = (projectIdentifier: string) =>
  `/projects/${projectIdentifier}/api-bundles`;

/** 解析三文件并落库（至少一个文件；不创建 APIEndpoint） */
export function parseBundle(
  projectIdentifier: string,
  request: ParseBundleRequest
) {
  const fd = new FormData();
  fd.append("bundle_name", request.bundle_name);
  if (request.system_type) {
    fd.append("system_type", request.system_type);
  }
  const files: Array<[keyof BundleFiles, File | null | undefined]> = [
    ["excel_file", request.excel_file],
    ["collection_file", request.collection_file],
    ["environment_file", request.environment_file],
  ];
  for (const [field, file] of files) {
    // 单一赋值：同名 Form 字段重复 append 会让后端拿到数组，FastAPI 解析失败
    if (file) {
      fd.append(field, file);
    }
  }
  return apiClient.form<SuccessResponse<ParseBundleData>>(
    `${base(projectIdentifier)}/parse-bundle`,
    fd
  );
}

/** 列出项目下的资产包 */
export function listBundles(projectIdentifier: string) {
  return apiClient.get<SuccessResponse<BundleListItem[]>>(
    base(projectIdentifier)
  );
}

/** 查询 bundle 详情（契约 + 冲突，可用于刷新后校验持久化） */
export function getBundle(projectIdentifier: string, bundleId: string) {
  return apiClient.get<SuccessResponse<BundleDetailData>>(
    `${base(projectIdentifier)}/${bundleId}`
  );
}

/** 查询资产统计 */
export function getBundleAssets(projectIdentifier: string, bundleId: string) {
  return apiClient.get<SuccessResponse<BundleAssetsData>>(
    `${base(projectIdentifier)}/${bundleId}/assets`
  );
}

/** 查询变量（仅 env: 引用） */
export function getBundleVariables(
  projectIdentifier: string,
  bundleId: string
) {
  return apiClient.get<SuccessResponse<BundleVariable[]>>(
    `${base(projectIdentifier)}/${bundleId}/variables`
  );
}

/** 确认 / 豁免冲突（解除导入门禁） */
export function confirmBundleConflicts(
  projectIdentifier: string,
  bundleId: string,
  resolutions: Array<{
    conflict_id: string;
    resolution: ConflictResolution;
    note?: string;
  }>
) {
  return apiClient.post<SuccessResponse<BundleConflict[]>>(
    `${base(projectIdentifier)}/${bundleId}/conflicts/confirm`,
    { resolutions }
  );
}

/** 确认导入（单事务真实创建端点；幂等键 = 项目 + method + normalized_path） */
export function confirmBundle(
  projectIdentifier: string,
  bundleId: string,
  options: {
    contract_ids?: string[];
    parent_folder_id?: string;
    folder_name?: string;
  } = {}
) {
  return apiClient.post<SuccessResponse<ConfirmBundleData>>(
    `${base(projectIdentifier)}/${bundleId}/confirm`,
    options
  );
}
