"use client";

import { createContext, useContext, useMemo, ReactNode } from "react";
import { Client } from "@langchain/langgraph-sdk";

interface ClientContextValue {
  client: Client;
}
// NOTE  MC8yOmFIVnBZMlhscm9ua3VMazZSbnBuY2c9PTo1M2RkOGEwYw==

const ClientContext = createContext<ClientContextValue | null>(null);

interface ClientProviderProps {
  children: ReactNode;
  deploymentUrl: string;
  apiKey: string;
}
// FIXME  MS8yOmFIVnBZMlhscm9ua3VMazZSbnBuY2c9PTo1M2RkOGEwYw==

export function ClientProvider({
  children,
  deploymentUrl,
  apiKey,
}: ClientProviderProps) {
  const client = useMemo(() => {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (apiKey) {
      headers["X-Api-Key"] = apiKey;
    }

    // 🔧 修复：将相对路径转换为绝对 URL
    // LangGraph SDK 的 Client 类使用 new URL() 构造请求 URL，
    // 需要绝对 URL 才能正常工作
    let apiUrl = deploymentUrl;
    if (apiUrl.startsWith("/")) {
      // 相对路径，转换为当前页面的绝对 URL
      const baseUrl = typeof window !== "undefined"
        ? `${window.location.protocol}//${window.location.host}`
        : "http://localhost:3000";
      apiUrl = `${baseUrl}${apiUrl}`;
    }

    return new Client({
      apiUrl: apiUrl,
      // 禁用 SDK 级别的 fetch 超时，让流式连接保持活跃
      // SSE 连接由浏览器和服务器维持，不需要客户端超时
      // @ts-ignore - SDK 支持 null 但类型定义未更新
      timeoutMs: null,
      defaultHeaders: headers,
    });
  }, [deploymentUrl, apiKey]);

  const value = useMemo(() => ({ client }), [client]);

  return (
    <ClientContext.Provider value={value}>{children}</ClientContext.Provider>
  );
}

export function useClient(): Client {
  const context = useContext(ClientContext);

  if (!context) {
    throw new Error("useClient must be used within a ClientProvider");
  }
  return context.client;
}
