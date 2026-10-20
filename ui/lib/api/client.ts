const API_BASE_URL = "/api/v2";
const REQUEST_TIMEOUT_MS = 30000; // 30 秒超时
// TODO  MC80OmFIVnBZMlhscm9ua3VMazZTbXBuV0E9PTpiN2NjZGQ1MQ==

interface RequestOptions extends RequestInit {
  params?: Record<string, string | number | boolean | undefined>;
  /** 自定义超时（毫秒），覆盖默认 30 秒。用于已知耗时的长操作（如真实执行）。 */
  timeoutMs?: number;
}
// TODO  MS80OmFIVnBZMlhscm9ua3VMazZTbXBuV0E9PTpiN2NjZGQ1MQ==

function getAuthHeaders(): Record<string, string> {
  try {
    // 优先读取 AuthProvider 使用的 auth_token
    const token = localStorage.getItem("auth_token");
    if (token) {
      return { Authorization: `Bearer ${token}` };
    }

    // 回退到 platform-auth-session（兼容旧版或其他登录方式）
    const session = localStorage.getItem("platform-auth-session");
    if (session) {
      const parsed = JSON.parse(session);
      if (parsed?.access_token) {
        return { Authorization: `Bearer ${parsed.access_token}` };
      }
    }
  } catch {
    // ignore
  }
  return {};
}

class ApiError extends Error {
  status: number;
  data?: unknown;

  constructor(message: string, status: number, data?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

/**
 * 带超时的 fetch
 */
async function fetchWithTimeout(
  url: string,
  options: RequestInit,
  timeoutMs: number
): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
    });
    return response;
  } catch (error: any) {
    if (error.name === 'AbortError') {
      throw new ApiError(`请求超时（${timeoutMs / 1000}秒），请检查：\n1. 后端服务是否正常运行\n2. 网络连接是否稳定\n3. 可尝试刷新页面后重试`, 0);
    }
    throw new ApiError(`网络错误: ${error.message || '无法连接到服务器'}，请检查网络连接或稍后重试`, 0);
  } finally {
    clearTimeout(timeoutId);
  }
}

async function handleResponse<T>(response: Response): Promise<T> {
  const contentType = response.headers.get("content-type");
  const isJson = contentType?.includes("application/json");

  // Handle 204 No Content responses (empty body)
  if (response.status === 204) {
    if (!response.ok) {
      throw new ApiError(`HTTP error! status: ${response.status}`, response.status);
    }
    return undefined as T;
  }

  const data = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const message =
      (isJson && typeof data === 'object' && ((data as any)?.message || (data as any)?.detail)) || `HTTP error! status: ${response.status}`;
    throw new ApiError(message, response.status, data);
  }

  return data as T;
}
// TODO  Mi80OmFIVnBZMlhscm9ua3VMazZTbXBuV0E9PTpiN2NjZGQ1MQ==

function buildUrl(
  path: string,
  params?: Record<string, string | number | boolean | undefined>
): string {
  const url = new URL(`${API_BASE_URL}${path}`, window.location.origin);

  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        url.searchParams.append(key, String(value));
      }
    });
  }

  return url.toString();
}

function mergeHeaders(options?: RequestOptions): HeadersInit {
  return {
    ...getAuthHeaders(),
    ...options?.headers,
  };
}

export const apiClient = {
  async get<T>(path: string, options?: RequestOptions): Promise<T> {
    const url = buildUrl(path, options?.params);
    const response = await fetchWithTimeout(url, {
      ...options,
      method: "GET",
      headers: {
        "Content-Type": "application/json",
        ...mergeHeaders(options),
      },
    }, REQUEST_TIMEOUT_MS);
    return handleResponse<T>(response);
  },

  async post<T>(
    path: string,
    body?: unknown,
    options?: RequestOptions
  ): Promise<T> {
    const url = buildUrl(path, options?.params);
    const response = await fetchWithTimeout(url, {
      ...options,
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...mergeHeaders(options),
      },
      body: body ? JSON.stringify(body) : undefined,
    }, options?.timeoutMs ?? REQUEST_TIMEOUT_MS);
    return handleResponse<T>(response);
  },

  async form<T>(
    path: string,
    formData: FormData,
    options?: RequestOptions
  ): Promise<T> {
    const url = buildUrl(path, options?.params);
    const response = await fetchWithTimeout(url, {
      ...options,
      method: options?.method || "POST",
      headers: {
        ...mergeHeaders(options),
      },
      body: formData,
    }, REQUEST_TIMEOUT_MS * 2);
    return handleResponse<T>(response);
  },

  async put<T>(
    path: string,
    body?: unknown,
    options?: RequestOptions
  ): Promise<T> {
    const url = buildUrl(path, options?.params);
    const response = await fetchWithTimeout(url, {
      ...options,
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        ...mergeHeaders(options),
      },
      body: body ? JSON.stringify(body) : undefined,
    }, REQUEST_TIMEOUT_MS);
    return handleResponse<T>(response);
  },

  async patch<T>(
    path: string,
    body?: unknown,
    options?: RequestOptions
  ): Promise<T> {
    const url = buildUrl(path, options?.params);
    const response = await fetchWithTimeout(url, {
      ...options,
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        ...mergeHeaders(options),
      },
      body: body ? JSON.stringify(body) : undefined,
    }, REQUEST_TIMEOUT_MS);
    return handleResponse<T>(response);
  },

  async delete<T>(
    path: string,
    options?: RequestOptions & { data?: unknown }
  ): Promise<T> {
    const url = buildUrl(path, options?.params);
    const { data, ...restOptions } = options || {};
    const response = await fetchWithTimeout(url, {
      ...restOptions,
      method: "DELETE",
      headers: {
        "Content-Type": "application/json",
        ...mergeHeaders(restOptions),
      },
      body: data ? JSON.stringify(data) : undefined,
    }, REQUEST_TIMEOUT_MS);
    return handleResponse<T>(response);
  },

  /**
   * 下载文件（返回 Blob）
   */
  async download(
    path: string,
    options?: RequestOptions
  ): Promise<{ blob: Blob; filename: string }> {
    const url = buildUrl(path, options?.params);
    const response = await fetchWithTimeout(url, {
      ...options,
      method: "GET",
      headers: {
        ...mergeHeaders(options),
      },
    }, REQUEST_TIMEOUT_MS * 2); // 下载超时更长

    if (!response.ok) {
      throw new ApiError(`下载失败: HTTP ${response.status}`, response.status);
    }

    // 从 Content-Disposition 头提取文件名
    // 支持两种格式：filename="name" 和 filename*=UTF-8''name
    const contentDisposition = response.headers.get("content-disposition");
    let filename = "download";
    if (contentDisposition) {
      // 优先匹配 filename*=UTF-8''encoded_name
      const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;\n]*)/i);
      if (utf8Match) {
        try {
          filename = decodeURIComponent(utf8Match[1]);
        } catch {
          filename = utf8Match[1];
        }
      } else {
        // 回退到 filename="name" 或 filename=name
        const match = contentDisposition.match(/filename[^;*=\n]*=((['"]).*?\2|[^;\n]*)/);
        if (match) {
          filename = match[1].replace(/['"]/g, "");
        }
      }
    }

    const blob = await response.blob();
    return { blob, filename };
  },
};

export { ApiError };
// NOTE  My80OmFIVnBZMlhscm9ua3VMazZTbXBuV0E9PTpiN2NjZGQ1MQ==
