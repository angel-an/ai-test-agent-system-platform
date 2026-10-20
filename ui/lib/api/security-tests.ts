/**
 * 安全测试（渗透测试）相关的 API 客户端函数
 */

import { apiClient } from "./client";

// ==================== 类型定义 ====================

export interface SecurityTest {
  id: string;
  identifier: string;
  name: string;
  target: string;
  description?: string;
  status: string;
  scan_config?: Record<string, unknown>;
  total_vulnerabilities: number;
  critical_count: number;
  high_count: number;
  medium_count: number;
  low_count: number;
  info_count: number;
  risk_score?: number;
  thread_id?: string;
  folder_id?: string;
  created_at: string;
  updated_at?: string;
}

export interface SecurityVulnerability {
  id: string;
  vuln_id: string;
  title: string;
  severity: string;
  vuln_type?: string;
  affected_url?: string;
  parameter?: string;
  description?: string;
  reproduction?: string;
  evidence?: string;
  remediation?: string;
  cvss_score?: number;
  status: string;
  created_at: string;
}

export interface SecurityReport {
  id: string;
  name: string;
  report_type: string;
  format: string;
  content?: string;
  file_path?: string;
  risk_score?: number;
  summary?: Record<string, unknown>;
  created_at: string;
}

export interface CreateSecurityTestRequest {
  name: string;
  target: string;
  description?: string;
  scan_config?: Record<string, unknown>;
  folder_id?: string;
}

export interface AddVulnerabilityRequest {
  vuln_id: string;
  title: string;
  severity: string;
  vuln_type?: string;
  affected_url?: string;
  parameter?: string;
  description?: string;
  reproduction?: string;
  evidence?: string;
  remediation?: string;
  cvss_score?: number;
}

export interface SaveReportRequest {
  name: string;
  report_type?: string;
  format?: string;
  content: string;
  file_path?: string;
  risk_score?: number;
  summary?: string;
}

// ==================== API 函数 ====================

export async function listSecurityTests(
  projectIdentifier: string,
  params?: {
    page?: number;
    page_size?: number;
    search?: string;
    status?: string;
  }
): Promise<{ items: SecurityTest[]; total: number; page: number; page_size: number; total_pages: number }> {
  const response = await apiClient.get<{ data: { items: SecurityTest[]; total: number; page: number; page_size: number; total_pages: number } }>(
    `/projects/${projectIdentifier}/security-tests`,
    { params }
  );
  return response.data;
}

export async function getSecurityTest(
  projectIdentifier: string,
  securityTestId: string
): Promise<SecurityTest> {
  const response = await apiClient.get<{ data: SecurityTest }>(
    `/projects/${projectIdentifier}/security-tests/${securityTestId}`
  );
  return response.data;
}

export async function createSecurityTest(
  projectIdentifier: string,
  data: CreateSecurityTestRequest
): Promise<SecurityTest> {
  const formData = new FormData();
  formData.append("name", data.name);
  formData.append("target", data.target);
  if (data.description) formData.append("description", data.description);
  if (data.scan_config) formData.append("scan_config", JSON.stringify(data.scan_config));
  if (data.folder_id) formData.append("folder_id", data.folder_id);

  const response = await fetch(`/api/v2/projects/${projectIdentifier}/security-tests`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to create security test");
  }

  const result = await response.json();
  return result.data;
}

export async function updateSecurityTestStatus(
  projectIdentifier: string,
  securityTestId: string,
  status: string
): Promise<SecurityTest> {
  const response = await apiClient.patch<{ data: SecurityTest }>(
    `/projects/${projectIdentifier}/security-tests/${securityTestId}/status`,
    { status }
  );
  return response.data;
}

export async function updateSecurityTestThreadId(
  projectIdentifier: string,
  securityTestId: string,
  threadId: string
): Promise<SecurityTest> {
  const response = await apiClient.patch<{ data: SecurityTest }>(
    `/projects/${projectIdentifier}/security-tests/${securityTestId}/thread-id`,
    { thread_id: threadId }
  );
  return response.data;
}

export async function deleteSecurityTest(
  projectIdentifier: string,
  securityTestId: string
): Promise<void> {
  await apiClient.delete(
    `/projects/${projectIdentifier}/security-tests/${securityTestId}`
  );
}

// ==================== 漏洞管理 ====================

export async function listVulnerabilities(
  projectIdentifier: string,
  securityTestId: string,
  params?: {
    page?: number;
    page_size?: number;
    severity?: string;
    status?: string;
  }
): Promise<{ items: SecurityVulnerability[]; total: number; page: number; page_size: number; total_pages: number }> {
  const response = await apiClient.get<{ data: { items: SecurityVulnerability[]; total: number; page: number; page_size: number; total_pages: number } }>(
    `/projects/${projectIdentifier}/security-tests/${securityTestId}/vulnerabilities`,
    { params }
  );
  return response.data;
}

export async function addVulnerability(
  projectIdentifier: string,
  securityTestId: string,
  data: AddVulnerabilityRequest
): Promise<SecurityVulnerability> {
  const formData = new FormData();
  formData.append("vuln_id", data.vuln_id);
  formData.append("title", data.title);
  formData.append("severity", data.severity);
  if (data.vuln_type) formData.append("vuln_type", data.vuln_type);
  if (data.affected_url) formData.append("affected_url", data.affected_url);
  if (data.parameter) formData.append("parameter", data.parameter);
  if (data.description) formData.append("description", data.description);
  if (data.reproduction) formData.append("reproduction", data.reproduction);
  if (data.evidence) formData.append("evidence", data.evidence);
  if (data.remediation) formData.append("remediation", data.remediation);
  if (data.cvss_score !== undefined) formData.append("cvss_score", String(data.cvss_score));

  const response = await fetch(`/api/v2/projects/${projectIdentifier}/security-tests/${securityTestId}/vulnerabilities`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to add vulnerability");
  }

  const result = await response.json();
  return result.data;
}

// ==================== 报告管理 ====================

export async function listReports(
  projectIdentifier: string,
  securityTestId: string,
  params?: {
    page?: number;
    page_size?: number;
  }
): Promise<{ items: SecurityReport[]; total: number; page: number; page_size: number; total_pages: number }> {
  const response = await apiClient.get<{ data: { items: SecurityReport[]; total: number; page: number; page_size: number; total_pages: number } }>(
    `/projects/${projectIdentifier}/security-tests/${securityTestId}/reports`,
    { params }
  );
  return response.data;
}

export async function saveReport(
  projectIdentifier: string,
  securityTestId: string,
  data: SaveReportRequest
): Promise<SecurityReport> {
  const formData = new FormData();
  formData.append("name", data.name);
  formData.append("content", data.content);
  if (data.report_type) formData.append("report_type", data.report_type);
  if (data.format) formData.append("format", data.format);
  if (data.file_path) formData.append("file_path", data.file_path);
  if (data.risk_score !== undefined) formData.append("risk_score", String(data.risk_score));
  if (data.summary) formData.append("summary", data.summary);

  const response = await fetch(`/api/v2/projects/${projectIdentifier}/security-tests/${securityTestId}/reports`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to save report");
  }

  const result = await response.json();
  return result.data;
}

export interface DownloadReportResponse {
  download_url: string;
  expires_in: number;
  expires_at: string;
}

export async function updateSecurityTest(
  projectIdentifier: string,
  securityTestId: string,
  data: {
    name?: string;
    target?: string;
    description?: string;
  }
): Promise<SecurityTest> {
  const response = await apiClient.patch<{ data: SecurityTest }>(
    `/projects/${projectIdentifier}/security-tests/${securityTestId}`,
    data
  );
  return response.data;
}

export async function downloadReport(
  projectIdentifier: string,
  reportId: string,
): Promise<DownloadReportResponse> {
  const response = await fetch(`/api/v2/projects/${projectIdentifier}/security-tests/reports/${reportId}/download`);

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to get download link");
  }

  return await response.json();
}
