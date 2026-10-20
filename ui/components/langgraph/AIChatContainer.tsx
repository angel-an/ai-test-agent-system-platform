// NOTE  MC80OmFIVnBZMlhscm9ua3VMazZZV0pYUVE9PTo3ODkxMjQ5Mw==

"use client";

import React, { useState, useCallback } from "react";
import { useQueryState } from "nuqs";
import { Button } from "@/components/ui/button";
import { Assistant } from "@langchain/langgraph-sdk";
import { MessagesSquare, SquarePen, X } from "lucide-react";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { ThreadList } from "./ThreadList";
import { ChatProvider } from "@/providers/ChatProvider";
import { ChatInterface } from "./ChatInterface";
// NOTE  MS80OmFIVnBZMlhscm9ua3VMazZZV0pYUVE9PTo3ODkxMjQ5Mw==

interface AIChatContainerProps {
  assistant: Assistant;
  initialPrompt?: string;
  initialAttachments?: Array<{
    data: string;
    mimeType: string;
    metadata: { filename: string; size: number };
  }>;
  onClose?: () => void;
  createNewThread?: boolean;
  onTestCaseCreated?: () => void; // 测试用例创建后的回调（已废弃）
  onTestCreated?: () => void; // 通用回调：测试创建后调用
  hideThreadControls?: boolean; // 隐藏对话列表和新建对话按钮
  externalThreadId?: string | null; // 外部传入的 thread ID（用于恢复历史对话）
  onThreadIdChange?: (threadId: string | null) => void; // 当对话线程变化时的回调
  onCreateNewThreadConsumed?: () => void; // 当 createNewThread 被消费后的回调
}

/**
 * 根据 assistant 信息获取对应的标题
 */
function getAssistantTitle(assistant: Assistant): string {
  // 优先使用 assistant.name
  if (assistant.name) {
    return assistant.name;
  }

  // 根据 assistant_id 映射
  const idMap: Record<string, string> = {
    testcase_generator_agent: "测试用例生成助手",
    api_agent: "API 测试助手",
    web_agent: "Web 测试助手",
    android_agent: "Android 测试助手",
    security_agent: "渗透测试助手",
  };

  if (assistant.assistant_id && idMap[assistant.assistant_id]) {
    return idMap[assistant.assistant_id];
  }

  // 根据 config 中的 template_type 映射
  const templateType =
    assistant.config?.configurable?.template_type as string | undefined;
  const typeMap: Record<string, string> = {
    test_case: "测试用例生成助手",
    api_test: "API 测试助手",
    scenario_test: "场景测试助手",
    web_test: "Web 测试助手",
    android_test: "Android 测试助手",
    pentest: "渗透测试助手",
  };

  if (templateType && typeMap[templateType]) {
    return typeMap[templateType];
  }

  return "AI 助手";
}
// FIXME  Mi80OmFIVnBZMlhscm9ua3VMazZZV0pYUVE9PTo3ODkxMjQ5Mw==

export function AIChatContainer({
  assistant,
  initialPrompt,
  initialAttachments,
  onClose,
  createNewThread = false,
  onTestCaseCreated,
  onTestCreated,
  hideThreadControls = false,
  externalThreadId,
  onThreadIdChange,
  onCreateNewThreadConsumed,
}: AIChatContainerProps) {
  const [threadId, setThreadId] = useQueryState("threadId");
  const [sidebar, setSidebar] = useQueryState("sidebar");
  const [mutateThreads, setMutateThreads] = useState<(() => void) | null>(null);
  const [interruptCount, setInterruptCount] = useState(0);
  // reconnectKey 用于强制 ChatProvider 重新挂载以触发 useStream 的 reconnectOnMount
  const [reconnectKey, setReconnectKey] = useState(0);

  // 合并回调函数
  const handleTestCreated = useCallback(() => {
    onTestCaseCreated?.();
    onTestCreated?.();
  }, [onTestCaseCreated, onTestCreated]);

  // 同步外部 threadId（仅在非 createNewThread 模式下）
  React.useEffect(() => {
    if (createNewThread) {
      // 强制创建新对话，忽略 externalThreadId
      // 只在 threadId 不为 null 时才重置，避免不必要的渲染
      if (threadId !== null) {
        setThreadId(null);
      }
      // 通知父组件已消费 createNewThread
      onCreateNewThreadConsumed?.();
    } else if (externalThreadId && threadId !== externalThreadId) {
      setThreadId(externalThreadId);
    }
  }, [externalThreadId, threadId, setThreadId, createNewThread, onCreateNewThreadConsumed]);

  // 监听 threadId 变化并通知父组件
  React.useEffect(() => {
    onThreadIdChange?.(threadId || null);
  }, [threadId, onThreadIdChange]);

  return (
    <div className="flex h-full flex-col">
      {/* 标题栏 - 嵌入模式时隐藏 */}
      {!hideThreadControls && (
      <header className="flex h-14 items-center justify-between border-b border-border px-4">
        <div className="flex items-center gap-4">
          <h1 className="text-base font-semibold">{getAssistantTitle(assistant)}</h1>
          {!sidebar && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSidebar("1")}
              className="rounded-md border border-border bg-card p-2 text-foreground hover:bg-accent"
            >
              <MessagesSquare className="mr-2 h-4 w-4" />
              对话列表
              {interruptCount > 0 && (
                <span className="ml-2 inline-flex min-h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] text-destructive-foreground">
                  {interruptCount}
                </span>
              )}
            </Button>
          )}
        </div>
        <div className="flex items-center gap-2">
          {/*<div className="text-xs text-muted-foreground">*/}
          {/*  <span className="font-medium">助手:</span> {assistant.assistant_id}*/}
          {/*</div>*/}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setThreadId(null)}
            disabled={!threadId}
            className="border-[#2F6868] bg-[#2F6868] text-white hover:bg-[#2F6868]/80"
          >
            <SquarePen className="mr-2 h-4 w-4" />
            新建对话
          </Button>
          {onClose && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onClose}
            >
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
      </header>
      )}

      {/* 主内容区 */}
      <div className="flex-1 overflow-hidden">
        <ResizablePanelGroup
          orientation="horizontal"
        >
          {sidebar && (
            <>
              <ResizablePanel
                defaultSize={30}
                minSize={30}
                className="relative"
              >
                <ThreadList
                  assistantId={assistant.assistant_id}
                  onThreadSelect={async (id) => {
                    await setThreadId(id);
                  }}
                  onMutateReady={(fn) => setMutateThreads(() => fn)}
                  onClose={() => setSidebar(null)}
                  onInterruptCountChange={setInterruptCount}
                />
              </ResizablePanel>
              <ResizableHandle withHandle className="w-1 bg-border hover:bg-primary/20" />
            </>
          )}

          <ResizablePanel
            defaultSize={70}
            className="relative flex flex-col"
          >
            <ChatProvider
              key={reconnectKey}
              activeAssistant={assistant}
              onHistoryRevalidate={() => mutateThreads?.()}
              onTestCaseCreated={handleTestCreated}
            >
              <ChatInterface
                assistant={assistant}
                initialPrompt={initialPrompt}
                initialAttachments={initialAttachments}
                onReconnect={() => setReconnectKey(prev => prev + 1)}
              />
            </ChatProvider>
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>
    </div>
  );
}

// eslint-disable  My80OmFIVnBZMlhscm9ua3VMazZZV0pYUVE9PTo3ODkxMjQ5Mw==
