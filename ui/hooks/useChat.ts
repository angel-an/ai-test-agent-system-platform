// eslint-disable  MC80OmFIVnBZMlhscm9ua3VMazZNa1ZyTWc9PTpkNDhlNzM1Yw==

"use client";
// NOTE  MS80OmFIVnBZMlhscm9ua3VMazZNa1ZyTWc9PTpkNDhlNzM1Yw==

import React, { useCallback, useRef } from "react";
import { useStream } from "@langchain/langgraph-sdk/react";
import {
  type Message,
  type Assistant,
  type Checkpoint,
} from "@langchain/langgraph-sdk";
import { v4 as uuidv4 } from "uuid";
import type { UseStreamThread } from "@langchain/langgraph-sdk/react";
import type { TodoItem } from "@/lib/langgraph/types";
import { useClient } from "@/providers/ClientProvider";
import { useQueryState } from "nuqs";
import { toast } from "sonner";
// NOTE  Mi80OmFIVnBZMlhscm9ua3VMazZNa1ZyTWc9PTpkNDhlNzM1Yw==

export type StateType = {
  messages: Message[];
  todos: TodoItem[];
  files: Record<string, string>;
  email?: {
    id?: string;
    subject?: string;
    page_content?: string;
  };
  ui?: any;
  context?: {
    project_identifier?: string;
    folder_id?: string;
    template_type?: string;
  };
};

export type StreamErrorKind =
  | "normal_close"
  | "network_disconnected"
  | "llm_timeout"
  | "backend_timeout"
  | "context_limit"
  | "backend_run_error"
  | "unknown";

export type StreamErrorPresentation = {
  kind: StreamErrorKind;
  title: string;
  description: string;
  detail: string;
  canReconnect: boolean;
};

function getStreamErrorText(error: any): string {
  if (!error) return "";
  if (typeof error === "string") return error;
  return error?.message || String(error);
}

export function classifyStreamError(error: any): StreamErrorKind {
  const errorText = getStreamErrorText(error);
  const lowerText = `${error?.name || ""} ${errorText}`.toLowerCase();

  // 🔧 修复：将运行完成后的连接关闭归类为正常关闭
  // LangGraph in-memory runtime 在运行成功后可能会关闭 SSE 连接
  // 这种情况不应被视为错误
  if (
    lowerText.includes("aborted") ||
    lowerText.includes("cancel") ||
    lowerText.includes("aborterror") ||
    lowerText.includes("the user aborted a request")
  ) {
    return "normal_close";
  }

  // 🔧 修复：将 "closed" 相关的错误进一步细分
  // 如果错误信息只包含 "closed" 但没有其他错误描述，可能是正常关闭
  if (lowerText.includes("closed") && !lowerText.includes("error") && !lowerText.includes("fail")) {
    return "normal_close";
  }

  if (
    lowerText.includes("maximum context length") ||
    lowerText.includes("context length") ||
    lowerText.includes("1048565") ||
    (lowerText.includes("token") && lowerText.includes("reduce"))
  ) {
    return "context_limit";
  }

  if (
    lowerText.includes("stream_chunk_timeout") ||
    lowerText.includes("no streaming chunk received") ||
    lowerText.includes("apitimeouterror") ||
    lowerText.includes("request timed out")
  ) {
    return "llm_timeout";
  }

  if (
    lowerText.includes("background run timed out") ||
    lowerText.includes("run timed out")
  ) {
    return "backend_timeout";
  }

  if (
    error?.name === "ConnectionError" ||
    lowerText.includes("unable to connect to langgraph server") ||
    lowerText.includes("failed to fetch") ||
    lowerText.includes("networkerror") ||
    lowerText.includes("econnrefused") ||
    lowerText.includes("流连接已断开") ||
    lowerText.includes("stream disconnected") ||
    lowerText.includes("stream connection closed") ||
    lowerText.includes("connection closed")
  ) {
    return "network_disconnected";
  }

  if (errorText) {
    return "backend_run_error";
  }

  return "unknown";
}

export function getStreamErrorPresentation(error: any): StreamErrorPresentation {
  const kind = classifyStreamError(error);
  const detail = getStreamErrorText(error);

  switch (kind) {
    case "network_disconnected":
      return {
        kind,
        title: "连接已断开",
        description: "任务可能仍在后台运行",
        detail,
        canReconnect: true,
      };
    case "llm_timeout":
      return {
        kind,
        title: "模型响应超时",
        description: "模型服务响应过慢，本次任务可能已失败，请稍后重试或简化输入。",
        detail,
        canReconnect: false,
      };
    case "backend_timeout":
      return {
        kind,
        title: "任务执行超时",
        description: "后台任务执行时间过长，本次任务已超时。",
        detail,
        canReconnect: false,
      };
    case "context_limit":
      return {
        kind,
        title: "上下文过长",
        description: "当前对话历史已超出模型可处理范围。",
        detail,
        canReconnect: false,
      };
    case "backend_run_error":
      return {
        kind,
        title: "任务执行失败",
        description: "后端执行过程中发生错误，请查看日志后重试。",
        detail,
        canReconnect: false,
      };
    case "normal_close":
      return {
        kind,
        title: "连接已关闭",
        description: "本次流已正常结束。",
        detail,
        canReconnect: false,
      };
    default:
      return {
        kind,
        title: "连接异常",
        description: "请稍后重试。",
        detail,
        canReconnect: false,
      };
  }
}

export function useChat({
  activeAssistant,
  onHistoryRevalidate,
  thread,
  onTestCaseCreated,
}: {
  activeAssistant: Assistant | null;
  onHistoryRevalidate?: () => void;
  thread?: UseStreamThread<StateType>;
  onTestCaseCreated?: () => void;
}) {
  const [threadId, setThreadId] = useQueryState("threadId");
  const [assistantId, setAssistantId] = useQueryState("assistantId");
  const client = useClient();

  // 错误去重：记录上次错误信息和时间，避免重复 toast
  const lastErrorRef = useRef("");
  const lastErrorTimeRef = useRef(0);
  // 上下文长度超限标记
  const [contextLimitExceeded, setContextLimitExceeded] = React.useState(false);
  // 重连重试计数
  const retryCountRef = useRef(0);
  const reconnectTimerRef = useRef<NodeJS.Timeout | null>(null);

  // 同步 assistantId 到 URL（仅在需要时写入，避免不必要的 URL 变动）
  React.useEffect(() => {
    if (activeAssistant?.assistant_id && !assistantId) {
      // 只在 URL 中没有 assistantId 时才写入，避免覆盖外部传入的值
      setAssistantId(activeAssistant.assistant_id);
    }
  }, [activeAssistant?.assistant_id, assistantId, setAssistantId]);

  // 处理流完成事件
  const handleFinish = useCallback(() => {
    // 连接成功时重置重试计数
    retryCountRef.current = 0;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    onHistoryRevalidate?.();
    // 检测是否创建了测试用例（通过检查最后的消息中是否包含工具调用）
    onTestCaseCreated?.();
  }, [onHistoryRevalidate, onTestCaseCreated]);

  // 处理流错误：带去重过滤的用户提示
  const handleError = useCallback((error: any) => {
    console.error("[useChat] Stream error:", error);

    const errorMsg = error?.message || String(error);
    const now = Date.now();
    const errorKind = classifyStreamError(error);

    if (errorKind === "normal_close") {
      onHistoryRevalidate?.();
      onTestCaseCreated?.();
      return;
    }

    if (errorKind === "context_limit") {
      console.warn("[useChat] Context length exceeded:", errorMsg);
      setContextLimitExceeded(true);
      onHistoryRevalidate?.();
      return;
    }

    // 🔧 修复：处理 backend_run_error 类型的错误
    // 如果错误信息非常简单（只有 "closed" 或空），且消息列表中有工具调用结果
    // 则可能是运行完成后的正常连接关闭，不应显示错误
    if (errorKind === "backend_run_error" || errorKind === "unknown") {
      // 检查是否是运行完成后的连接关闭
      const isSimpleClose =
        !errorMsg ||
        errorMsg === "closed" ||
        errorMsg === "Closed" ||
        errorMsg.toLowerCase() === "error" ||
        (errorMsg.length < 50 && !errorMsg.includes("fail") && !errorMsg.includes("error"));

      // 如果错误信息表明是连接关闭相关，视为正常关闭
      if (isSimpleClose) {
        console.log("[useChat] Treating simple connection close as normal close, errorMsg:", errorMsg);
        onHistoryRevalidate?.();
        onTestCaseCreated?.();
        return;
      }

      const presentation = getStreamErrorPresentation(error);
      if (errorMsg === lastErrorRef.current && now - lastErrorTimeRef.current < 5000) {
        onHistoryRevalidate?.();
        return;
      }
      lastErrorRef.current = errorMsg;
      lastErrorTimeRef.current = now;
      toast.error(presentation.title, {
        description: presentation.description,
        duration: 8000,
      });
      onHistoryRevalidate?.();
      return;
    }

    // 过滤正常的连接关闭/取消（非真正错误）
    const isNormalClose =
      errorMsg.includes("aborted") ||
      errorMsg.includes("cancel") ||
      errorMsg.includes("AbortError") ||
      errorMsg.includes("closed") ||
      errorMsg.includes("The user aborted a request");

    if (isNormalClose) {
      onHistoryRevalidate?.();
      onTestCaseCreated?.(); // 流正常关闭时也需要触发测试创建回调（如工具执行完成后）
      return;
    }

    // 过滤连接错误：后端重启或网络瞬断时，自动提示用户刷新
    const isConnectionError =
      error?.name === "ConnectionError" ||
      errorMsg.includes("Unable to connect to LangGraph server") ||
      errorMsg.includes("Failed to fetch") ||
      errorMsg.includes("NetworkError") ||
      errorMsg.includes("ECONNREFUSED") ||
      errorMsg.includes("流连接已断开") ||
      errorMsg.includes("stream") ||
      errorMsg.includes("disconnect");

    if (isConnectionError) {
      console.warn("[useChat] Connection error (backend may be restarting):", errorMsg);
      retryCountRef.current++;
      const delay = Math.min(3000 * Math.pow(2, retryCountRef.current - 1), 30000);
      // 只在非首次加载时显示 toast，避免页面刷新后出现残留提示
      if (retryCountRef.current <= 2) {
        toast.info(`连接已断开，${delay / 1000}秒后尝试自动重连...`, {
          description: "任务可能仍在后台运行，点击重新连接按钮可立即恢复",
          duration: 5000,
          id: "stream-reconnect-toast", // 固定 ID 防止重复
        });
      }
      // 指数退避重连，不强制刷新页面
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
      reconnectTimerRef.current = setTimeout(() => {
        // useStream 的 reconnectOnMount 会自动处理重连
        // 只需要保持 retryCount 用于下次计算退避时间
        console.log("[useChat] Attempting reconnect after backoff...");
      }, delay);

      // 🔧 修复：连接错误发生后，主动获取线程最新状态
      // Redis/SSE 连接断开时，后端运行可能仍在继续或已完成
      // 通过获取线程状态来恢复最新的消息和结果
      if (threadId && client) {
        console.log("[useChat] Fetching thread state after connection error, threadId:", threadId);
        client.threads.getState(threadId)
          .then((state: any) => {
            console.log("[useChat] Thread state recovered:", state);
            // 触发历史记录刷新，让 UI 显示最新状态
            onHistoryRevalidate?.();
            // 如果有新的消息产生，也触发测试创建检查
            if (state?.values?.messages?.length > 0) {
              onTestCaseCreated?.();
            }
          })
          .catch((err: any) => {
            console.warn("[useChat] Failed to fetch thread state after error:", err);
          });
      }

      onHistoryRevalidate?.();
      return;
    }

    // 检测上下文长度超限错误（模型最大上下文长度限制）
    const isContextLimitError =
      errorMsg.includes("maximum context length") ||
      errorMsg.includes("context length") ||
      errorMsg.includes("1048565") ||
      errorMsg.includes("token") && errorMsg.includes("reduce");

    if (isContextLimitError) {
      console.warn("[useChat] Context length exceeded:", errorMsg);
      setContextLimitExceeded(true);
      // 不显示通用错误 toast，由 UI 组件显示专门的上下文超限提示
      onHistoryRevalidate?.();
      return;
    }

    // 5 秒内相同错误不重复提示
    if (errorMsg === lastErrorRef.current && now - lastErrorTimeRef.current < 5000) {
      onHistoryRevalidate?.();
      return;
    }

    lastErrorRef.current = errorMsg;
    lastErrorTimeRef.current = now;

    // 显示用户可见的错误提示
    toast.error("连接中断: " + errorMsg, {
      description: "请尝试刷新页面或重新发送消息",
      duration: 8000,
    });

    onHistoryRevalidate?.();
  }, [onHistoryRevalidate]);

  // 重置上下文超限状态
  const resetContextLimit = useCallback(() => {
    setContextLimitExceeded(false);
  }, []);

  const stream = useStream<StateType>({
    assistantId: activeAssistant?.assistant_id || "",
    client: client ?? undefined,
    // 禁用 reconnectOnMount，避免组件挂载时自动重连导致请求冲突
    reconnectOnMount: false,
    threadId: threadId ?? null,
    onThreadId: setThreadId,
    fetchStateHistory: true,
    // Revalidate thread list when stream finishes, errors, or creates new thread
    onFinish: handleFinish,
    onError: handleError,
    onCreated: onHistoryRevalidate,
    // 添加流式事件回调，用于调试连接状态
    onUpdateEvent: (event) => {
      console.log("[useChat] Update event:", event);
    },
    ...(thread ? { thread } : {}),
  });

  // 心跳保活：每25秒获取线程状态，防止SSE连接被空闲切断
  // 优化：只在SSE空闲超过60秒时才发送心跳，减少无效请求
  const lastEventTimeRef = useRef(Date.now());

  React.useEffect(() => {
    if (!stream.isLoading || !threadId || !client) return;

    // 监听流事件更新最后活动时间
    const updateLastEvent = () => {
      lastEventTimeRef.current = Date.now();
    };
    // 订阅 stream 变化作为事件活动的信号
    updateLastEvent();

    const heartbeat = setInterval(() => {
      const idleTime = Date.now() - lastEventTimeRef.current;
      // 只有空闲超过60秒才发心跳，避免活跃期间频繁请求
      if (idleTime > 60000) {
        client.threads.getState(threadId).catch(() => {
          // 静默处理，不干扰正常流程
        });
      }
    }, 25000);

    return () => clearInterval(heartbeat);
  }, [stream.isLoading, threadId, client]);

  const sendMessage = useCallback(
    (content: string, attachments?: Array<{
      data: string;  // base64 encoded file data
      mimeType: string;
      metadata: { filename: string; [key: string]: any };
    }>) => {
      // 检测是否处于中断状态，如果是则提示使用 resumeInterrupt
      if (stream.interrupt) {
        console.warn(
          "[useChat] Agent is in interrupt state. Use resumeInterrupt() instead of sendMessage() to continue."
        );
      }

      const newMessage: Message = {
        id: uuidv4(),
        type: "human",
        content,
        ...(attachments && attachments.length > 0
          ? { additional_kwargs: { attachments } }
          : {}),
      };

      // 从 assistant config 中提取 context 信息
      const context = activeAssistant?.config?.configurable || {};

      // 构建 context 对象
      const agentContext: Record<string, any> = {
        project_identifier: context.project_identifier || "",
        folder_id: context.folder_id || "",
        template_type: context.template_type || "test_case",
        knowledge_space_id: context.knowledge_space_id || "",
      };

      stream.submit(
        {
          messages: [newMessage],
          // 将 context 信息传递给后端智能体
          context: agentContext
        },
        {
          optimisticValues: (prev) => ({
            messages: [...(prev.messages ?? []), newMessage],
          }),
          config: { ...(activeAssistant?.config ?? {}), recursion_limit: 1000 },
          // FIX-2026-09-20: SSE 长连接可能被网络层（跨网段路由器/防火墙的空闲连接超时）
          // 在静默期提前掐断。onDisconnect 默认 "cancel" 会让后端在客户端断开时立即取消 Agent，
          // 导致执行中的任务被误中断。改为 "continue" 让后端跑完，前端靠 handleError→getState 恢复结果。
          onDisconnect: "continue",
        }
      );
      // Update thread list immediately when sending a message
      onHistoryRevalidate?.();
    },
    [stream, activeAssistant?.config, onHistoryRevalidate]
  );

  const runSingleStep = useCallback(
    (
      messages: Message[],
      checkpoint?: Checkpoint,
      isRerunningSubagent?: boolean,
      optimisticMessages?: Message[]
    ) => {
      if (checkpoint) {
        stream.submit(undefined, {
          ...(optimisticMessages
            ? { optimisticValues: { messages: optimisticMessages } }
            : {}),
          config: activeAssistant?.config,
          checkpoint: checkpoint,
          onDisconnect: "continue",
          ...(isRerunningSubagent
            ? { interruptAfter: ["tools"] }
            : { interruptBefore: ["tools"] }),
        });
      } else {
        stream.submit(
          { messages },
          { config: activeAssistant?.config, interruptBefore: ["tools"], onDisconnect: "continue" }
        );
      }
    },
    [stream, activeAssistant?.config]
  );

  const setFiles = useCallback(
    async (files: Record<string, string>) => {
      if (!threadId) return;
      // TODO: missing a way how to revalidate the internal state
      // I think we do want to have the ability to externally manage the state
      await client.threads.updateState(threadId, { values: { files } });
    },
    [client, threadId]
  );

  const continueStream = useCallback(
    (hasTaskToolCall?: boolean) => {
      stream.submit(undefined, {
        config: {
          ...(activeAssistant?.config || {}),
          recursion_limit: 1000,
        },
        onDisconnect: "continue",
        ...(hasTaskToolCall
          ? { interruptAfter: ["tools"] }
          : { interruptBefore: ["tools"] }),
      });
      // Update thread list when continuing stream
      onHistoryRevalidate?.();
    },
    [stream, activeAssistant?.config, onHistoryRevalidate]
  );

  const markCurrentThreadAsResolved = useCallback(() => {
    stream.submit(null, { command: { goto: "__end__", update: null } });
    // Update thread list when marking thread as resolved
    onHistoryRevalidate?.();
  }, [stream, onHistoryRevalidate]);

  const resumeInterrupt = useCallback(
    (value: any) => {
      // 恢复中断后可能继续执行长任务（如 execute_api_script 跑 playwright），
      // 同样加 onDisconnect: "continue"，避免恢复执行后又被网络空闲超时误取消
      stream.submit(null, { command: { resume: value }, onDisconnect: "continue" });
      // Update thread list when resuming from interrupt
      onHistoryRevalidate?.();
    },
    [stream, onHistoryRevalidate]
  );

  const stopStream = useCallback(() => {
    stream.stop();
  }, [stream]);

  // 优先使用 stream.values.messages（状态中的消息），如果为空则回退到 stream.messages
  // 这是为了修复 reconnectOnMount 时 stream.messages 为空的问题
  const messages = stream.values.messages?.length > 0
    ? stream.values.messages
    : (stream.messages || []);

  return {
    stream,
    todos: stream.values?.todos ?? [],
    files: stream.values?.files ?? {},
    email: stream.values?.email,
    ui: stream.values?.ui,
    setFiles,
    messages,
    isLoading: stream.isLoading,
    isThreadLoading: stream.isThreadLoading,
    interrupt: stream.interrupt,
    getMessagesMetadata: stream.getMessagesMetadata,
    contextLimitExceeded,
    resetContextLimit,
    sendMessage,
    runSingleStep,
    continueStream,
    stopStream,
    markCurrentThreadAsResolved,
    resumeInterrupt,
  };
}
// NOTE  My80OmFIVnBZMlhscm9ua3VMazZNa1ZyTWc9PTpkNDhlNzM1Yw==
