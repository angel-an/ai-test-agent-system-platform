import { apiClient } from "./client";
import type {
  KnowledgeSpaceInfo,
  KnowledgeDocumentInfo,
  KnowledgeSpaceCreate,
  KnowledgeRetrievalResponse,
  PaginatedResponse,
  SuccessResponse,
  MessageResponse,
} from "./types";

// 获取项目下的知识空间列表
export function getKnowledgeSpaces(
  projectId: string,
  params?: { p?: number; page_size?: number; search?: string; business_line?: string }
) {
  return apiClient.get<PaginatedResponse<KnowledgeSpaceInfo>>(
    `/projects/${projectId}/knowledge-base/spaces`,
    { params }
  );
}

// 创建知识空间
export function createKnowledgeSpace(
  projectId: string,
  data: KnowledgeSpaceCreate
) {
  return apiClient.post<SuccessResponse<KnowledgeSpaceInfo>>(
    `/projects/${projectId}/knowledge-base/spaces`,
    data
  );
}

// 更新知识空间
export function updateKnowledgeSpace(
  projectId: string,
  spaceId: string,
  data: Partial<KnowledgeSpaceCreate>
) {
  return apiClient.put<SuccessResponse<KnowledgeSpaceInfo>>(
    `/projects/${projectId}/knowledge-base/spaces/${spaceId}`,
    data
  );
}

// 删除知识空间
export function deleteKnowledgeSpace(projectId: string, spaceId: string) {
  return apiClient.delete<MessageResponse>(
    `/projects/${projectId}/knowledge-base/spaces/${spaceId}`
  );
}

// 上传文档到知识空间
export function uploadDocument(
  projectId: string,
  spaceId: string,
  file: File,
  title?: string
) {
  const formData = new FormData();
  formData.append("file", file);
  if (title) {
    formData.append("title", title);
  }
  return apiClient.form<SuccessResponse<{ document_id: string; file_name: string; file_size: number; status: string; message: string }>>(
    `/projects/${projectId}/knowledge-base/spaces/${spaceId}/documents`,
    formData
  );
}

// 获取空间下的文档列表
export function getDocuments(
  projectId: string,
  spaceId: string,
  params?: { p?: number; page_size?: number }
) {
  return apiClient.get<PaginatedResponse<KnowledgeDocumentInfo>>(
    `/projects/${projectId}/knowledge-base/spaces/${spaceId}/documents`,
    { params }
  );
}

// 删除文档
export function deleteDocument(
  projectId: string,
  spaceId: string,
  docId: string
) {
  return apiClient.delete<MessageResponse>(
    `/projects/${projectId}/knowledge-base/spaces/${spaceId}/documents/${docId}`
  );
}

// 重建索引
export function rebuildIndex(projectId: string, spaceId: string) {
  return apiClient.post<SuccessResponse<{ total: number; success: number; failed: number }>>(
    `/projects/${projectId}/knowledge-base/spaces/${spaceId}/rebuild`
  );
}

// 执行知识检索
export function retrieveKnowledge(
  projectId: string,
  query: string,
  spaceId?: string,
  topK: number = 5
) {
  return apiClient.post<KnowledgeRetrievalResponse>(
    `/projects/${projectId}/knowledge-base/retrieve`,
    {
      query,
      space_id: spaceId,
      top_k: topK,
    }
  );
}
