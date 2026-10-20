"use client";

import * as React from "react";
import {
  getTestRun,
  getScriptJobs,
  subscribeToTestRunEvents,
  type TestRunInfo,
  type TestRunScriptJobInfo,
} from "@/lib/api";

interface UseTestRunRealtimeOptions {
  projectId: string;
  runId: string;
  enabled?: boolean;
  pollInterval?: number;
}

interface UseTestRunRealtimeReturn {
  testRun: TestRunInfo | null;
  scriptJobs: TestRunScriptJobInfo[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useTestRunRealtime({
  projectId,
  runId,
  enabled = true,
  pollInterval = 3000,
}: UseTestRunRealtimeOptions): UseTestRunRealtimeReturn {
  const [testRun, setTestRun] = React.useState<TestRunInfo | null>(null);
  const [scriptJobs, setScriptJobs] = React.useState<TestRunScriptJobInfo[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  // ref 用于 SSE handler 中读取最新状态，避免闭包问题
  const testRunRef = React.useRef<TestRunInfo | null>(null);
  React.useEffect(() => {
    testRunRef.current = testRun;
  }, [testRun]);

  const loadDetail = React.useCallback(async () => {
    if (!projectId || !runId) return;
    try {
      const response = await getTestRun(projectId, runId);
      setTestRun(response.data);
      setError(null);
    } catch (err: any) {
      setError(err?.message || "加载测试运行详情失败");
    }
  }, [projectId, runId]);

  const loadScriptJobs = React.useCallback(async () => {
    if (!projectId || !runId) return;
    try {
      const response = await getScriptJobs(projectId, runId, { page: 1, page_size: 100 });
      setScriptJobs(response.data.items);
    } catch {
      setScriptJobs([]);
    }
  }, [projectId, runId]);

  const refresh = React.useCallback(async () => {
    setLoading(true);
    await Promise.all([loadDetail(), loadScriptJobs()]);
    setLoading(false);
  }, [loadDetail, loadScriptJobs]);

  // 初始加载
  React.useEffect(() => {
    if (!enabled || !projectId || !runId) return;
    refresh();
  }, [enabled, projectId, runId, refresh]);

  // SSE + 轮询实时更新
  React.useEffect(() => {
    if (!enabled || !projectId || !runId || !testRun) return;
    if (testRun.run_state !== "in_progress") return;

    let sseBroken = false;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let reconnectAttempts = 0;
    const maxReconnectAttempts = 5;

    const startPolling = () => {
      if (pollTimer) return;
      pollTimer = setInterval(() => {
        loadDetail();
        loadScriptJobs();
      }, pollInterval);
    };

    const stopPolling = () => {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const connectSSE = () => {
      const es = subscribeToTestRunEvents(
        projectId,
        runId,
        (data) => {
          if (typeof data !== "object" || data === null) return;
          const payload = data as Record<string, unknown>;

          // SSE 推送了错误事件 → 关闭 SSE，启动轮询
          if (payload.event === "error") {
            sseBroken = true;
            es.close();
            startPolling();
            return;
          }

          // 1. 实时更新脚本作业列表
          if (payload.jobs && Array.isArray(payload.jobs)) {
            setScriptJobs(payload.jobs as TestRunScriptJobInfo[]);
          }

          // 2. 实时更新 overall_progress 和 run_state
          if (payload.overall_progress || payload.run_state) {
            setTestRun((prev) => {
              if (!prev) return prev;
              return {
                ...prev,
                run_state: (payload.run_state as TestRunInfo["run_state"]) || prev.run_state,
                overall_progress: (payload.overall_progress as TestRunInfo["overall_progress"]) || prev.overall_progress,
              };
            });
          }

          // 3. run_state 发生实际变化时，重新加载完整详情
          if (payload.run_state && payload.run_state !== testRunRef.current?.run_state) {
            loadDetail();
            loadScriptJobs();
          }

          // 4. 执行完成后关闭 SSE 并刷新详情
          if (payload.event === "completed") {
            loadDetail();
            loadScriptJobs();
            es.close();
            stopPolling();
          }
        },
        () => {
          // onError: SSE 连接断开（网络错误等）→ 尝试重连或启动轮询
          if (!sseBroken) {
            sseBroken = true;
            es.close();
            if (reconnectAttempts < maxReconnectAttempts) {
              reconnectAttempts++;
              setTimeout(connectSSE, Math.min(1000 * Math.pow(2, reconnectAttempts), 30000));
            } else {
              startPolling();
            }
          }
        }
      );
    };

    connectSSE();

    return () => {
      stopPolling();
    };
  }, [projectId, runId, testRun?.run_state, enabled, pollInterval, loadDetail, loadScriptJobs]);

  return { testRun, scriptJobs, loading, error, refresh };
}
