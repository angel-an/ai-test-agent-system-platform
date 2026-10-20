// eslint-disable  MC80OmFIVnBZMlhscm9ua3VMazZXSE5FVEE9PTpmMzgyZTk5OQ==

"use client";

import React, {
  useState,
  useRef,
  useCallback,
  useMemo,
  FormEvent,
  Fragment,
} from "react";
import { Button } from "@/components/ui/button";
import {
  Square,
  ArrowUp,
  CheckCircle,
  Clock,
  Circle,
  FileIcon,
  AlertCircle,
  WifiOff,
  SquarePen,
  RefreshCw,
} from "lucide-react";
import { ChatMessage } from "@/components/langgraph/ChatMessage";
import type {
  TodoItem,
  ToolCall,
  ActionRequest,
  ReviewConfig,
} from "@/lib/langgraph/types";
import { Assistant, Message } from "@langchain/langgraph-sdk";
import { getStreamErrorPresentation } from "@/hooks/useChat";
import { extractStringFromMessageContent } from "@/lib/langgraph/utils";
import { useChatContext } from "@/providers/ChatProvider";
import { cn } from "@/lib/utils";
import { useStickToBottom } from "use-stick-to-bottom";
import { FilesPopover } from "@/components/langgraph/TasksFilesSidebar";
// TODO  MS80OmFIVnBZMlhscm9ua3VMazZXSE5FVEE9PTpmMzgyZTk5OQ==

interface ChatInterfaceProps {
  assistant: Assistant | null;
  initialPrompt?: string;
  initialAttachments?: Array<{
    data: string;
    mimeType: string;
    metadata: { filename: string; size: number };
  }>;
  onReconnect?: () => void;
}

const getStatusIcon = (status: TodoItem["status"], className?: string) => {
  switch (status) {
    case "completed":
      return (
        <CheckCircle
          size={16}
          className={cn("text-success/80", className)}
        />
      );
    case "in_progress":
      return (
        <Clock
          size={16}
          className={cn("text-warning/80", className)}
        />
      );
    default:
      return (
        <Circle
          size={16}
          className={cn("text-tertiary/70", className)}
        />
      );
  }
};
// eslint-disable  Mi80OmFIVnBZMlhscm9ua3VMazZXSE5FVEE9PTpmMzgyZTk5OQ==

export const ChatInterface = React.memo<ChatInterfaceProps>(({ assistant, initialPrompt, initialAttachments, onReconnect }) => {
  const [metaOpen, setMetaOpen] = useState<"tasks" | "files" | null>(null);
  const tasksContainerRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const [input, setInput] = useState("");
  const { scrollRef, contentRef } = useStickToBottom();
  const initialPromptSentRef = useRef(false);
  const sendMessageRef = useRef<((content: string, attachments?: Array<{
    data: string;
    mimeType: string;
    metadata: { filename: string; size: number };
  }>) => void) | null>(null);
  const isMountedRef = useRef(true);

  const {
    stream,
    messages,
    todos,
    files,
    ui,
    setFiles,
    isLoading,
    isThreadLoading,
    interrupt,
    sendMessage,
    stopStream,
    resumeInterrupt,
    contextLimitExceeded,
    resetContextLimit,
  } = useChatContext();

  // 保持 sendMessage 的最新引用
  React.useEffect(() => {
    sendMessageRef.current = sendMessage;
  }, [sendMessage]);

  // 组件挂载/卸载标记
  React.useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // 页面可见性变化处理：从其他应用切换回来时，如果流处于错误状态则尝试恢复
  React.useEffect(() => {
    const handleVisibility = () => {
      if (!document.hidden && isMountedRef.current) {
        // 页面重新可见时，检查是否有流错误
        // useStream 已配置 reconnectOnMount，这里只处理额外的恢复逻辑
        const streamError = (stream as any)?.error;
        if (streamError) {
          console.log('[ChatInterface] Page visible, stream had error:', streamError);
          // 不主动干预重连，让 useStream 的 reconnectOnMount 处理
          // 但记录日志以便调试
        }
        // 如果线程正在加载但长时间卡住，可以考虑刷新
        if (isThreadLoading && messages.length === 0) {
          console.log('[ChatInterface] Page visible, thread loading with no messages');
        }
      }
    };
    document.addEventListener('visibilitychange', handleVisibility);
    return () => document.removeEventListener('visibilitychange', handleVisibility);
  }, [stream, isThreadLoading, messages.length]);

  // 当 initialPrompt 改变时重置标记
  React.useEffect(() => {
    initialPromptSentRef.current = false;
  }, [initialPrompt]);

  // 自动发送初始提示词（含附件）
  React.useEffect(() => {
    if (
      initialPrompt &&
      !isLoading &&
      !isThreadLoading &&
      assistant &&
      messages.length === 0 &&
      !initialPromptSentRef.current
    ) {
      // 标记为已发送，防止重复
      initialPromptSentRef.current = true;

      console.log('[ChatInterface] Scheduling initial prompt send:', initialPrompt);

      // 使用 setTimeout 确保组件完全初始化
      const timer = setTimeout(() => {
        // 检查组件是否仍然挂载
        if (isMountedRef.current && sendMessageRef.current) {
          console.log('[ChatInterface] Sending initial prompt with attachments:', initialPrompt);
          // 如果有附件，通过 sendMessage 的第二个参数传递
          if (initialAttachments && initialAttachments.length > 0) {
            sendMessageRef.current(initialPrompt, initialAttachments);
          } else {
            sendMessageRef.current(initialPrompt);
          }
        } else {
          console.log('[ChatInterface] Component unmounted, skipping initial prompt');
        }
      }, 100);

      return () => {
        console.log('[ChatInterface] Cleaning up initial prompt timer');
        clearTimeout(timer);
      };
    }
  }, [initialPrompt, initialAttachments, isLoading, isThreadLoading, assistant, messages.length]);
  // 注意：不依赖 sendMessage，而是使用 sendMessageRef.current

  // 处理消息：提取 tool calls，计算 showAvatar，全量重建保证正确性
  const processedMessages = useMemo(() => {
    const messageMap = new Map<
      string,
      { message: Message; toolCalls: ToolCall[] }
    >();

    for (let i = 0; i < messages.length; i++) {
      const message = messages[i];
      // 稳定的 key：优先使用 id，否则用索引（在同一轮渲染中稳定）
      const key = message.id || `__idx__${i}`;

      if (message.type === "ai") {
        const toolCallsInMessage: Array<{
          id?: string;
          function?: { name?: string; arguments?: unknown };
          name?: string;
          type?: string;
          args?: unknown;
          input?: unknown;
        }> = [];
        if (
          message.additional_kwargs?.tool_calls &&
          Array.isArray(message.additional_kwargs.tool_calls)
        ) {
          toolCallsInMessage.push(...message.additional_kwargs.tool_calls);
        } else if (message.tool_calls && Array.isArray(message.tool_calls)) {
          toolCallsInMessage.push(
            ...message.tool_calls.filter(
              (toolCall: { name?: string }) => toolCall.name !== ""
            )
          );
        } else if (Array.isArray(message.content)) {
          const toolUseBlocks = message.content.filter(
            (block: { type?: string }) => block.type === "tool_use"
          );
          toolCallsInMessage.push(...toolUseBlocks);
        }
        const toolCallsWithStatus = toolCallsInMessage.map(
          (toolCall: {
            id?: string;
            function?: { name?: string; arguments?: unknown };
            name?: string;
            type?: string;
            args?: unknown;
            input?: unknown;
          }) => {
            const name =
              toolCall.function?.name ||
              toolCall.name ||
              toolCall.type ||
              "unknown";
            const args =
              toolCall.function?.arguments ||
              toolCall.args ||
              toolCall.input ||
              {};
            return {
              id: toolCall.id || `tool-${Math.random()}`,
              name,
              args,
              status: interrupt ? "interrupted" : ("pending" as const),
            } as ToolCall;
          }
        );
        messageMap.set(key, {
          message,
          toolCalls: toolCallsWithStatus,
        });
      } else if (message.type === "tool") {
        const toolCallId = message.tool_call_id;
        if (!toolCallId) {
          continue;
        }
        for (const [, data] of Array.from(messageMap.entries())) {
          const toolCallIndex = data.toolCalls.findIndex(
            (tc: ToolCall) => tc.id === toolCallId
          );
          if (toolCallIndex === -1) {
            continue;
          }
          data.toolCalls[toolCallIndex] = {
            ...data.toolCalls[toolCallIndex],
            status: "completed" as const,
            result: extractStringFromMessageContent(message),
          };
          break;
        }
      } else if (message.type === "human") {
        messageMap.set(key, {
          message,
          toolCalls: [],
        });
      }
    }

    const processedArray = Array.from(messageMap.entries());
    return processedArray.map(([key, data], index) => {
      const prevMessage = index > 0 ? processedArray[index - 1][1].message : null;
      return {
        key,
        ...data,
        showAvatar: data.message.type !== prevMessage?.type,
      };
    });
  }, [messages, interrupt]);

  const groupedTodos = {
    in_progress: todos.filter((t) => t.status === "in_progress"),
    pending: todos.filter((t) => t.status === "pending"),
    completed: todos.filter((t) => t.status === "completed"),
  };

  const hasTasks = todos.length > 0;
  const hasFiles = Object.keys(files).length > 0;

  // Parse out any action requests or review configs from the interrupt
  const actionRequestsMap: Map<string, ActionRequest> | null = useMemo(() => {
    const actionRequests =
      interrupt?.value && (interrupt.value as any)["action_requests"];
    if (!actionRequests) return new Map<string, ActionRequest>();
    return new Map(actionRequests.map((ar: ActionRequest) => [ar.name, ar]));
  }, [interrupt]);

  const reviewConfigsMap: Map<string, ReviewConfig> | null = useMemo(() => {
    const reviewConfigs =
      interrupt?.value && (interrupt.value as any)["review_configs"];
    if (!reviewConfigs) return new Map<string, ReviewConfig>();
    return new Map(
      reviewConfigs.map((rc: ReviewConfig) => [rc.actionName, rc])
    );
  }, [interrupt]);

  // Agent 处于中断等待状态（没有具体的 action requests 需要审批）
  const isInterruptPaused =
    interrupt !== undefined &&
    actionRequestsMap !== null &&
    actionRequestsMap.size === 0;

  // 检测流连接错误状态
  const [streamErrorDismissed, setStreamErrorDismissed] = useState(false);
  const isStreamError = (stream as any)?.error != null && !streamErrorDismissed;
  const streamErrorPresentation = isStreamError
    ? getStreamErrorPresentation((stream as any)?.error)
    : null;
  const isActuallyRunning = isLoading && !isStreamError;

  // 当流恢复正常时重置 dismissed 状态
  React.useEffect(() => {
    if (!(stream as any)?.error) {
      setStreamErrorDismissed(false);
    }
  }, [(stream as any)?.error]);

  const submitDisabled = (!isInterruptPaused && isActuallyRunning) || !assistant;

  const handleSubmit = useCallback(
    (e?: FormEvent) => {
      if (e) {
        e.preventDefault();
      }
      const messageText = input.trim();
      if (!messageText || submitDisabled) return;

      // 🔧 修复：在中断暂停状态下，使用 resumeInterrupt 而不是 sendMessage
      // 这样可以让 Agent 从 interrupt 状态正确恢复
      if (isInterruptPaused) {
        console.log('[ChatInterface] Resuming from interrupt with message:', messageText);
        resumeInterrupt(messageText);
      } else {
        sendMessage(messageText);
      }
      setInput("");
    },
    [input, sendMessage, setInput, submitDisabled, isInterruptPaused, resumeInterrupt]
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (submitDisabled) return;
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit, submitDisabled]
  );

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div
        className="flex-1 overflow-y-auto overflow-x-hidden overscroll-contain"
        ref={scrollRef}
      >
        <div
          className="mx-auto w-full max-w-[1024px] px-6 pb-6 pt-4"
          ref={contentRef}
        >
          {isThreadLoading ? (
            <div className="flex items-center justify-center p-8">
              <p className="text-muted-foreground">加载中...</p>
            </div>
          ) : (
            <>
              {processedMessages.map((data, index) => {
                const messageUi = ui?.filter(
                  (u: any) => u.metadata?.message_id === data.message.id
                );
                const isLastMessage = index === processedMessages.length - 1;
                return (
                  <ChatMessage
                    key={data.key}
                    message={data.message}
                    toolCalls={data.toolCalls}
                    isLoading={isLoading}
                    actionRequestsMap={
                      isLastMessage ? actionRequestsMap : undefined
                    }
                    reviewConfigsMap={
                      isLastMessage ? reviewConfigsMap : undefined
                    }
                    ui={messageUi}
                    stream={stream}
                    onResumeInterrupt={resumeInterrupt}
                    graphId={assistant?.graph_id}
                  />
                );
              })}
            </>
          )}
        </div>
      </div>

      <div className="flex-shrink-0 bg-background">
        <div
          className={cn(
            "mx-4 mb-6 flex flex-shrink-0 flex-col overflow-hidden rounded-xl border border-border bg-background",
            "mx-auto w-[calc(100%-32px)] max-w-[1024px] transition-colors duration-200 ease-in-out"
          )}
        >
          {(hasTasks || hasFiles) && (
            <div className="flex max-h-72 flex-col overflow-y-auto border-b border-border bg-muted empty:hidden">
              {!metaOpen && (
                <>
                  {(() => {
                    const activeTask = todos.find(
                      (t) => t.status === "in_progress"
                    );

                    const totalTasks = todos.length;
                    const remainingTasks =
                      totalTasks - groupedTodos.pending.length;
                    const isCompleted = totalTasks === remainingTasks;

                    const tasksTrigger = (() => {
                      if (!hasTasks) return null;
                      return (
                        <button
                          type="button"
                          onClick={() =>
                            setMetaOpen((prev) =>
                              prev === "tasks" ? null : "tasks"
                            )
                          }
                          className="grid w-full cursor-pointer grid-cols-[auto_auto_1fr] items-center gap-3 px-[18px] py-3 text-left"
                          aria-expanded={metaOpen === "tasks"}
                        >
                          {(() => {
                            if (isCompleted) {
                              return [
                                <CheckCircle
                                  key="icon"
                                  size={16}
                                  className="text-success/80"
                                />,
                                <span
                                  key="label"
                                  className="ml-[1px] min-w-0 truncate text-sm"
                                >
                                  所有任务已完成
                                </span>,
                              ];
                            }

                            if (activeTask != null) {
                              return [
                                <div key="icon">
                                  {getStatusIcon(activeTask.status)}
                                </div>,
                                <span
                                  key="label"
                                  className="ml-[1px] min-w-0 truncate text-sm"
                                >
                                  任务{" "}
                                  {totalTasks - groupedTodos.pending.length} / {" "}
                                  {totalTasks}
                                </span>,
                                <span
                                  key="content"
                                  className="min-w-0 gap-2 truncate text-sm text-muted-foreground"
                                >
                                  {activeTask.content}
                                </span>,
                              ];
                            }

                            return [
                              <Circle
                                key="icon"
                                size={16}
                                className="text-tertiary/70"
                              />,
                              <span
                                key="label"
                                className="ml-[1px] min-w-0 truncate text-sm"
                              >
                                任务 {totalTasks - groupedTodos.pending.length}{" "}
                                / {totalTasks}
                              </span>,
                            ];
                          })()}
                        </button>
                      );
                    })();

                    const filesTrigger = (() => {
                      if (!hasFiles) return null;
                      return (
                        <button
                          type="button"
                          onClick={() =>
                            setMetaOpen((prev) =>
                              prev === "files" ? null : "files"
                            )
                          }
                          className="flex flex-shrink-0 cursor-pointer items-center gap-2 px-[18px] py-3 text-left text-sm"
                          aria-expanded={metaOpen === "files"}
                        >
                          <FileIcon size={16} />
                          文件 (状态)
                          <span className="h-4 min-w-4 rounded-full bg-[#2F6868] px-0.5 text-center text-[10px] leading-[16px] text-white">
                            {Object.keys(files).length}
                          </span>
                        </button>
                      );
                    })();

                    return (
                      <div className="grid grid-cols-[1fr_auto_auto] items-center">
                        {tasksTrigger}
                        {filesTrigger}
                      </div>
                    );
                  })()}
                </>
              )}

              {metaOpen && (
                <>
                  <div className="sticky top-0 flex items-stretch bg-muted text-sm">
                    {hasTasks && (
                      <button
                        type="button"
                        className="py-3 pr-4 first:pl-[18px] aria-expanded:font-semibold"
                        onClick={() =>
                          setMetaOpen((prev) =>
                            prev === "tasks" ? null : "tasks"
                          )
                        }
                        aria-expanded={metaOpen === "tasks"}
                      >
                        任务
                      </button>
                    )}
                    {hasFiles && (
                      <button
                        type="button"
                        className="inline-flex items-center gap-2 py-3 pr-4 first:pl-[18px] aria-expanded:font-semibold"
                        onClick={() =>
                          setMetaOpen((prev) =>
                            prev === "files" ? null : "files"
                          )
                        }
                        aria-expanded={metaOpen === "files"}
                      >
                        文件 (状态)
                        <span className="h-4 min-w-4 rounded-full bg-[#2F6868] px-0.5 text-center text-[10px] leading-[16px] text-white">
                          {Object.keys(files).length}
                        </span>
                      </button>
                    )}
                    <button
                      aria-label="Close"
                      className="flex-1"
                      onClick={() => setMetaOpen(null)}
                    />
                  </div>
                  <div
                    ref={tasksContainerRef}
                    className="px-[18px]"
                  >
                    {metaOpen === "tasks" &&
                      Object.entries(groupedTodos)
                        .filter(([_, todos]) => todos.length > 0)
                        .map(([status, todos]) => (
                          <div
                            key={status}
                            className="mb-4"
                          >
                            <h3 className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-tertiary">
                              {
                                {
                                  pending: "待处理",
                                  in_progress: "进行中",
                                  completed: "已完成",
                                }[status]
                              }
                            </h3>
                            <div className="grid grid-cols-[auto_1fr] gap-3 rounded-sm p-1 pl-0 text-sm">
                              {todos.map((todo, index) => (
                                <Fragment key={`${status}_${todo.id}_${index}`}>
                                  {getStatusIcon(todo.status, "mt-0.5")}
                                  <span className="break-words text-inherit">
                                    {todo.content}
                                  </span>
                                </Fragment>
                              ))}
                            </div>
                          </div>
                        ))}

                    {metaOpen === "files" && (
                      <div className="mb-6">
                        <FilesPopover
                          files={files}
                          setFiles={setFiles}
                          editDisabled={
                            isActuallyRunning === true || interrupt !== undefined
                          }
                        />
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          )}
          <form
            onSubmit={handleSubmit}
            className="flex flex-col"
          >
            {/* 流连接错误提示 - 增强版：添加重连按钮 */}
            {isStreamError && (
              <div className="mx-[18px] mt-3 flex flex-col gap-2 rounded-md border border-red-300 bg-red-50 px-3 py-3 text-sm text-red-800">
                <div className="flex items-center gap-2">
                  <WifiOff size={16} className="shrink-0" />
                  <span className="font-medium">{streamErrorPresentation?.title}</span>
                  <span className="text-red-600">{streamErrorPresentation?.description}</span>
                </div>
                <div className="flex items-center gap-2 ml-6">
                  <span className="text-xs text-red-600">
                    {streamErrorPresentation?.canReconnect
                      ? "任务可能仍在后台运行"
                      : "请查看错误详情后重试"}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    type="button"
                    onClick={() => {
                      setStreamErrorDismissed(true);
                      if (streamErrorPresentation?.canReconnect) {
                        onReconnect?.();
                      }
                    }}
                    className="h-7 text-xs border-red-300 text-red-700 hover:bg-red-100"
                  >
                    <RefreshCw className="mr-1 h-3 w-3" />
                    {streamErrorPresentation?.canReconnect ? "重新连接" : "忽略"}
                  </Button>
                </div>
              </div>
            )}
            {/* Agent 中断等待提示 - 增强版 */}
            {isInterruptPaused && (
              <div className="mx-[18px] mt-3 flex flex-col gap-2 rounded-md border-2 border-amber-400 bg-amber-100 px-4 py-3 text-sm text-amber-900">
                <div className="flex items-center gap-2">
                  <Clock size={18} className="shrink-0 animate-spin" style={{ animationDuration: '2s' }} />
                  <span className="font-semibold">⏸️ Agent 已暂停，等待继续执行</span>
                </div>
                <div className="ml-7 space-y-1">
                  <p className="text-xs text-amber-800">
                    Agent 执行到需要人工确认的步骤。请在下方输入消息继续，例如：
                  </p>
                  <div className="flex flex-wrap gap-2 mt-1">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setInput("继续执行");
                        handleSubmit();
                      }}
                      className="h-7 text-xs border-amber-500 text-amber-800 hover:bg-amber-200"
                    >
                      <CheckCircle className="mr-1 h-3 w-3" />
                      继续执行
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setInput("请继续");
                        handleSubmit();
                      }}
                      className="h-7 text-xs border-amber-500 text-amber-800 hover:bg-amber-200"
                    >
                      请继续
                    </Button>
                  </div>
                </div>
              </div>
            )}
            {/* 运行中但无中断的提示 */}
            {isActuallyRunning && !isInterruptPaused && (
              <div className="mx-[18px] mt-3 flex items-center gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-800">
                <AlertCircle size={16} className="shrink-0 animate-pulse" />
                <span>Agent 正在运行中，请稍候...</span>
              </div>
            )}
            {/* 上下文长度超限提示 */}
            {contextLimitExceeded && (
              <div className="mx-[18px] mt-3 flex flex-col gap-2 rounded-md border border-orange-300 bg-orange-50 px-3 py-3 text-sm text-orange-800">
                <div className="flex items-center gap-2">
                  <AlertCircle size={16} className="shrink-0 text-orange-600" />
                  <span className="font-semibold">对话上下文过长，无法继续处理</span>
                </div>
                <p className="text-xs text-orange-700 ml-6">
                  当前对话历史已超出模型最大上下文限制（约 100 万 tokens）。请新建对话以继续工作，之前的测试成果物已保存在系统中。
                </p>
                <div className="flex gap-2 ml-6 mt-1">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      resetContextLimit();
                      // 触发新建对话：通过刷新页面或调用外部回调
                      window.location.reload();
                    }}
                    className="border-orange-400 text-orange-700 hover:bg-orange-100"
                  >
                    <SquarePen className="mr-1 h-3 w-3" />
                    新建对话
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={resetContextLimit}
                    className="text-orange-600 hover:text-orange-800"
                  >
                    忽略
                  </Button>
                </div>
              </div>
            )}
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={isActuallyRunning && !isInterruptPaused ? "运行中..." : "输入您的消息..."}
              className="font-inherit field-sizing-content flex-1 resize-none border-0 bg-transparent px-[18px] pb-[13px] pt-[14px] text-sm leading-7 text-primary outline-none placeholder:text-tertiary"
              rows={1}
            />
            <div className="flex justify-between gap-2 p-3">
              <div className="flex justify-end gap-2">
                <Button
                  type={isActuallyRunning && !isInterruptPaused ? "button" : "submit"}
                  variant={isActuallyRunning && !isInterruptPaused ? "destructive" : "default"}
                  onClick={isActuallyRunning && !isInterruptPaused ? stopStream : handleSubmit}
                  disabled={!isActuallyRunning && (submitDisabled || !input.trim())}
                >
                  {isActuallyRunning && !isInterruptPaused ? (
                    <>
                      <Square size={14} />
                      <span>停止</span>
                    </>
                  ) : (
                    <>
                      <ArrowUp size={18} />
                      <span>发送</span>
                    </>
                  )}
                </Button>
              </div>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
});

ChatInterface.displayName = "ChatInterface";
// FIXME  My80OmFIVnBZMlhscm9ua3VMazZXSE5FVEE9PTpmMzgyZTk5OQ==
