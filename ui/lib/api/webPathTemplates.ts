import { apiClient } from "./client";
import type { SuccessResponse } from "./types";

export interface WebPathTemplate {
  id: string;
  project_id: string;
  name: string;
  side?: string | null;
  module?: string | null;
  business_type?: string | null;
  action?: string | null;
  base_url?: string | null;
  login_profile?: string | null;
  match_keywords: string[];
  navigation_path: string[];
  description?: string | null;
  status: "active" | "inactive" | string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface WebPathTemplatePayload {
  name: string;
  side?: string | null;
  module?: string | null;
  business_type?: string | null;
  action?: string | null;
  base_url?: string | null;
  login_profile?: string | null;
  match_keywords: string[];
  navigation_path: string[];
  description?: string | null;
  status: string;
}

export interface WebPathTemplateMatchRequest {
  text: string;
  side?: string | null;
  module?: string | null;
  business_type?: string | null;
  action?: string | null;
}

export interface WebPathTemplateMatchResult {
  matched: boolean;
  template: WebPathTemplate | null;
  score: number;
}

interface WebPathTemplateListData {
  items: WebPathTemplate[];
  total: number;
}

export function getWebPathTemplates(projectId: string, status?: string) {
  return apiClient.get<SuccessResponse<WebPathTemplateListData>>(
    `/projects/${projectId}/web-path-templates`,
    { params: { status } }
  );
}

export function createWebPathTemplate(
  projectId: string,
  data: WebPathTemplatePayload
) {
  return apiClient.post<SuccessResponse<WebPathTemplate>>(
    `/projects/${projectId}/web-path-templates`,
    data
  );
}

export function updateWebPathTemplate(
  projectId: string,
  templateId: string,
  data: WebPathTemplatePayload
) {
  return apiClient.put<SuccessResponse<WebPathTemplate>>(
    `/projects/${projectId}/web-path-templates/${templateId}`,
    data
  );
}

export function matchWebPathTemplate(
  projectId: string,
  data: WebPathTemplateMatchRequest
) {
  return apiClient.post<SuccessResponse<WebPathTemplateMatchResult>>(
    `/projects/${projectId}/web-path-templates/match`,
    data
  );
}
