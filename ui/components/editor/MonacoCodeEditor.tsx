// NOTE  MC80OmFIVnBZMlhscm9ua3VMazZVRzV1YXc9PTo1NWI2ZjAyNg==

/**
 * Monaco Code Editor 组件
 *
 * 使用 Monaco Editor (VS Code 的编辑器核心) 提供专业的代码编辑体验
 *
 * 加载方式：本地 AMD loader + public/monaco 静态资源。
 * 不使用 @monaco-editor/react 默认的 CDN(jsdelivr) 加载 —— 内网 / 离线环境下
 * CDN 不可达会导致 <script> 加载失败，编辑器永远停在 Loading...，
 * 测试用例 / 测试脚本的查看与编辑全部失效。
 */

"use client";
// eslint-disable  MS80OmFIVnBZMlhscm9ua3VMazZVRzV1YXc9PTo1NWI2ZjAyNg==

import React, { useEffect, useRef, useState } from "react";
import loader from "@monaco-editor/loader";

interface CodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  language: string;
  readOnly?: boolean;
  height?: string;
  minimap?: boolean;
  fontSize?: number;
  onSave?: () => void;
}
// FIXME  My80OmFIVnBZMlhscm9ua3VMazZVRzV1YXc9PTo1NWI2ZjAyNg==

// monaco 实例类型（从 loader 拿到，避免直接 import 整个 monaco-editor 走 SSR）
type Monaco = Awaited<ReturnType<typeof loader.init>>;

// 本地 monaco 静态资源根路径（public/monaco 由 Next 托管）
const MONACO_BASE_URL = "/monaco";

// 确保本地 AMD loader 只注入一次
let monacoLoaderPromise: Promise<Monaco> | null = null;

function loadMonaco(): Promise<Monaco> {
  if (!monacoLoaderPromise) {
    monacoLoaderPromise = new Promise<Monaco>((resolve, reject) => {
      // 配置 @monaco-editor/loader 使用本地 AMD loader.js，而不是 CDN
      loader.config({
        paths: {
          vs: MONACO_BASE_URL,
        },
      });

      // 在 worker 启动前设置 MonacoEnvironment，让 worker 也从本地加载。
      // monaco-editor 0.55 min 版把各语言 worker 打包为 assets/<label>.worker-<hash>.js，
      // 文件名哈希随本地拷贝固定，见 public/monaco/assets/。
      (window as any).MonacoEnvironment = {
        getWorkerUrl(_workerId: string, label: string): string {
          if (label === "json") {
            return `${MONACO_BASE_URL}/assets/json.worker-DKiEKt88.js`;
          }
          if (label === "typescript" || label === "javascript") {
            return `${MONACO_BASE_URL}/assets/ts.worker-CMbG-7ft.js`;
          }
          if (label === "html") {
            return `${MONACO_BASE_URL}/assets/html.worker-B51mlPHg.js`;
          }
          if (label === "css" || label === "scss" || label === "less") {
            return `${MONACO_BASE_URL}/assets/css.worker-HnVq6Ewq.js`;
          }
          return `${MONACO_BASE_URL}/assets/editor.worker-Be8ye1pW.js`;
        },
      };

      loader
        .init()
        .then((monaco) => resolve(monaco as unknown as Monaco))
        .catch((err) => {
          monacoLoaderPromise = null;
          reject(err);
        });
    });
  }
  return monacoLoaderPromise;
}

export function MonacoCodeEditor({
  value,
  onChange,
  language,
  readOnly = false,
  height = "500px",
  minimap = false,
  fontSize = 14,
  onSave,
}: CodeEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<any>(null);
  const [monaco, setMonaco] = useState<Monaco | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 仅在浏览器加载 monaco（SSR 阶段不执行，避免 window is not defined）
  useEffect(() => {
    let cancelled = false;
    loadMonaco()
      .then((m) => {
        if (!cancelled) {
          setMonaco(m);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          console.error("Monaco initialization: error:", err);
          setError(err?.message || String(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // monaco 加载完成后创建编辑器
  useEffect(() => {
    if (!monaco || !containerRef.current) {
      return;
    }

    const editor = monaco.editor.create(containerRef.current, {
      value,
      language,
      theme: "vs",
      readOnly,
      minimap: { enabled: minimap },
      fontSize,
      lineHeight: 20,
      fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
      fontLigatures: true,
      tabSize: 2,
      scrollBeyondLastLine: false,
      automaticLayout: true,
      wordWrap: "on",
      formatOnPaste: true,
      formatOnType: true,
    });

    editorRef.current = editor;

    // 内容变更回调
    const changeDisposable = editor.onDidChangeModelContent(() => {
      onChange(editor.getValue());
    });

    // 保存快捷键 (Ctrl+S / Cmd+S)
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
      if (onSave) {
        onSave();
      }
    });

    // 挂载后立即布局
    const layoutTimer = setTimeout(() => {
      editor.layout();
    }, 100);

    return () => {
      clearTimeout(layoutTimer);
      changeDisposable.dispose();
      editor.dispose();
      editorRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [monaco]);

  // 外部 value 变化时同步进编辑器（避免光标跳回开头）
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor || !monaco) {
      return;
    }
    const current = editor.getValue();
    if (current !== value) {
      editor.setValue(value);
    }
  }, [value, monaco]);

  // language 变化时切换语言
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor || !monaco) {
      return;
    }
    const model = editor.getModel();
    if (model) {
      monaco.editor.setModelLanguage(model, language);
    }
  }, [language, monaco]);

  if (error) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-red-200 bg-red-50 text-red-600"
        style={{ height, fontSize: 13 }}
      >
        编辑器加载失败：{error}
      </div>
    );
  }

  return (
    <div className="border-0 rounded-lg overflow-hidden shadow-sm h-full">
      {!monaco && (
        <div
          className="flex items-center justify-center text-gray-400"
          style={{ height, fontSize: 13 }}
        >
          Loading...
        </div>
      )}
      <div
        ref={containerRef}
        style={{ height, width: "100%", display: monaco ? "block" : "none" }}
      />
    </div>
  );
}
