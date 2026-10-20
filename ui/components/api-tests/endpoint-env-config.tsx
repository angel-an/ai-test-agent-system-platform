// 契约端点的环境配置与执行对话框
// 配置 base_url / 鉴权引用 / 测试数据 → 生成并执行 → 展示状态（含 blocked/脱敏）

"use client";

import * as React from "react";
import { useState, useRef, useEffect } from "react";
import {
  Loader2,
  Play,
  ShieldAlert,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Settings2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import { generateAndRunEndpoint } from "@/lib/api/api-endpoints";

interface EndpointEnvConfigProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectIdentifier: string;
  endpointId: string;
  endpointName: string;
  onExecuted?: (runId: string, status: string) => void;
}

export function EndpointEnvConfig({
  open,
  onOpenChange,
  projectIdentifier,
  endpointId,
  endpointName,
  onExecuted,
}: EndpointEnvConfigProps) {
  const [baseUrl, setBaseUrl] = useState("");
  const [appidEnv, setAppidEnv] = useState("MX_APPID");
  const [secretEnv, setSecretEnv] = useState("MX_APPSECRET");
  const [appidValue, setAppidValue] = useState("");
  const [secretValue, setSecretValue] = useState("");
  const [existingMobile, setExistingMobile] = useState("");
  const [notExistingMobile, setNotExistingMobile] = useState("");
  // 自定义环境变量（键值对列表），执行时注入 process.env
  const [customEnvVars, setCustomEnvVars] = useState<Array<{ key: string; value: string; isSecret: boolean }>>([]);
  const [running, setRunning] = useState(false);
  const [runElapsed, setRunElapsed] = useState(0); // 已等待秒数，用于友好提示
  const [result, setResult] = useState<{ runId: string; status: string; message?: string } | null>(null);

  // 挂载保护：请求期间若组件被重新挂载/卸载（如父组件重渲染导致条件渲染变化），
  // 异步 finally 里 setRunning(false) 不会执行，UI 会无限转圈。
  // 用 ref 跟踪挂载状态与"是否已有结果"，确保即使重挂也能复位 running。
  const mountedRef = useRef(true);
  const runDoneRef = useRef(false);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // 同步执行接口整体较慢（生成脚本 + 真实执行），前端在通用 30s 超时之上
  // 再给一个更长的兜底：超过仍无响应则提示用户去运行记录查看，避免无限转圈。
  const RUN_STALL_THRESHOLD_S = 90;

  const reset = () => {
    setResult(null);
    setRunning(false);
    setRunElapsed(0);
    setCustomEnvVars([]);
    runDoneRef.current = false;
  };
  const handleOpenChange = (o: boolean) => {
    if (!o) reset();
    onOpenChange(o);
  };

  // 执行前缺失项检查（前端预检，后端仍会再校验）
  const missing = (): string[] => {
    const m: string[] = [];
    if (!baseUrl.trim()) m.push("base_url（环境地址）");
    return m;
  };

  // 鉴权提示（非阻断）：声明了 appid/secret 变量名但未填值时，执行将回退到
  // 后端运行环境的同名环境变量。若后端也未设置，脚本会因缺 APPID/APPSECRET
  // 把所有用例 test.skip（全 skipped，看似执行实则没跑）。这里只提示不拦截。
  // 注意：后端该变量是否已配置，前端无法确认，最终以执行结果为准。
  const authHints = (): string[] => {
    const hints: string[] = [];
    if (appidEnv.trim() && !appidValue.trim()) {
      hints.push(`appid 将用后端环境变量 ${appidEnv.trim()}（本机未填值，后端是否已配置无法确认，以执行结果为准）`);
    }
    if (secretEnv.trim() && !secretValue.trim()) {
      hints.push(`appsercret 将用后端环境变量 ${secretEnv.trim()}（本机未填值，后端是否已配置无法确认，以执行结果为准）`);
    }
    return hints;
  };

  const handleRun = async () => {
    const miss = missing();
    if (miss.length > 0) {
      toast.error(`缺配置: ${miss.join("、")}`);
      return;
    }
    // 鉴权提示改为常驻 UI（见下方 hint 区块），不再用一闪而过的 toast，
    // 避免"提示消失但请求仍在跑"造成的误解。
    setRunning(true);
    setResult(null);
    setRunElapsed(0);
    // 等待期间每秒累计已等待时长，用于在 UI 上给出"还要多久/是否异常"的提示
    const timer = setInterval(() => setRunElapsed((s) => s + 1), 1000);
    try {
      const envConfig: Record<string, any> = {
        requires_env: true,
        base_url: baseUrl.trim(),
      };
      // 鉴权环境变量名（用于脚本中 process.env.XXX 引用）
      if (appidEnv.trim()) {
        envConfig.appid_env = appidEnv.trim();
        envConfig.appid_ref = `env:${appidEnv.trim()}`;
      }
      if (secretEnv.trim()) {
        envConfig.secret_env = secretEnv.trim();
        envConfig.secret_ref = `env:${secretEnv.trim()}`;
      }
      // 鉴权实际值（用于后端执行时注入环境变量）。
      // 注意：这里读的是当前 React state，若弹窗被重新挂载/重置，值会丢——
      // 故在控制台打印 env_config 便于排查"输入了值但请求体为空"的情况。
      const appidVal = appidValue.trim();
      const secretVal = secretValue.trim();
      if (appidVal) envConfig.appid = appidVal;
      if (secretVal) envConfig.appsecret = secretVal;
      if (existingMobile.trim()) envConfig.existing_mobile = existingMobile.trim();
      if (notExistingMobile.trim()) envConfig.not_existing_mobile = notExistingMobile.trim();
      // 自定义环境变量：打包为 env 对象，执行时注入子进程环境
      const customEnv: Record<string, string> = {};
      for (const item of customEnvVars) {
        const k = item.key.trim();
        const v = item.value.trim();
        if (k && v) {
          customEnv[k] = v;
        }
      }
      if (Object.keys(customEnv).length > 0) {
        envConfig.env = customEnv;
      }
      console.log("[EndpointEnvConfig] 提交 env_config 键:", Object.keys(envConfig),
        "| appid 是否含值:", !!envConfig.appid, "| appsecret 是否含值:", !!envConfig.appsecret,
        "| 自定义变量数:", Object.keys(customEnv).length);

      const r = await generateAndRunEndpoint(projectIdentifier, endpointId, envConfig);
      runDoneRef.current = true;
      setResult({ runId: r.run_id, status: r.status, message: r.message });
      onExecuted?.(r.run_id, r.status);
      if (r.status === "blocked") {
        toast.warning("缺环境配置，已阻塞（未执行）");
      } else {
        toast.success("已提交执行");
      }
    } catch (err: any) {
      // 超时/网络错误/后端异常都在这里捕获，确保 running 一定复位，避免无限转圈
      console.error("[EndpointEnvConfig] 执行失败:", err);
      toast.error(err.message || "执行失败，请稍后重试");
    } finally {
      clearInterval(timer);
      // 仅当组件仍挂载时才更新 state；若已卸载（重挂），由新实例的 reset 负责复位
      if (mountedRef.current) {
        setRunning(false);
      }
    }
  };

  const miss = missing();

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Settings2 className="h-5 w-5 text-primary" />
            环境与执行配置
          </DialogTitle>
          <DialogDescription>
            {endpointName} · 配置环境地址与鉴权/数据引用（密钥不入库，执行时从环境变量解析）
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-1.5">
            <Label className="text-sm">环境地址 base_url *</Label>
            <Input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://mx-uat.example.com"
            />
            {!baseUrl.trim() && (
              <p className="text-xs text-amber-600 flex items-center gap-1">
                <ShieldAlert className="h-3 w-3" /> 缺失：补齐后方可执行，否则阻塞
              </p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-sm">appid 环境变量名</Label>
              <Input value={appidEnv} onChange={(e) => setAppidEnv(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label className="text-sm">appsercret 环境变量名</Label>
              <Input value={secretEnv} onChange={(e) => setSecretEnv(e.target.value)} />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-sm">appid 值（选填）</Label>
              <Input
                type="password"
                autoComplete="off"
                value={appidValue}
                onChange={(e) => setAppidValue(e.target.value)}
                placeholder="留空则用后端环境变量"
              />
              {appidValue.trim() ? (
                <p className="text-xs text-green-600">已填入 {appidValue.length} 个字符（将注入环境变量）</p>
              ) : appidEnv.trim() ? (
                <p className="text-xs text-muted-foreground flex items-center gap-1">
                  留空将用后端环境变量 {appidEnv.trim()}
                </p>
              ) : null}
            </div>
            <div className="space-y-1.5">
              <Label className="text-sm">appsercret 值（选填）</Label>
              <Input
                type="password"
                autoComplete="off"
                value={secretValue}
                onChange={(e) => setSecretValue(e.target.value)}
                placeholder="留空则用后端环境变量"
              />
              {secretValue.trim() ? (
                <p className="text-xs text-green-600">已填入 {secretValue.length} 个字符（将注入环境变量）</p>
              ) : secretEnv.trim() ? (
                <p className="text-xs text-muted-foreground flex items-center gap-1">
                  留空将用后端环境变量 {secretEnv.trim()}
                </p>
              ) : null}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-sm">已存在手机号（选填）</Label>
              <Input value={existingMobile} onChange={(e) => setExistingMobile(e.target.value)} placeholder="查重-存在分支" />
            </div>
            <div className="space-y-1.5">
              <Label className="text-sm">不存在手机号（选填）</Label>
              <Input value={notExistingMobile} onChange={(e) => setNotExistingMobile(e.target.value)} placeholder="查重-不存在分支" />
            </div>
          </div>

          {/* 自定义环境变量：键值对列表，执行时注入 process.env */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-sm">自定义环境变量（选填）</Label>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setCustomEnvVars((prev) => [...prev, { key: "", value: "", isSecret: false }])}
              >
                + 添加
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              执行时注入为 process.env.KEY，供脚本引用。例如：url → https://console.uat.eatizen.com
            </p>
            {customEnvVars.length > 0 && (
              <div className="space-y-2">
                {customEnvVars.map((item, idx) => (
                  <div key={idx} className="flex items-center gap-2">
                    <Input
                      className="flex-1"
                      placeholder="变量名"
                      value={item.key}
                      onChange={(e) =>
                        setCustomEnvVars((prev) =>
                          prev.map((it, i) => (i === idx ? { ...it, key: e.target.value } : it))
                        )
                      }
                    />
                    <Input
                      className="flex-1"
                      type={item.isSecret ? "password" : "text"}
                      placeholder="值"
                      value={item.value}
                      onChange={(e) =>
                        setCustomEnvVars((prev) =>
                          prev.map((it, i) => (i === idx ? { ...it, value: e.target.value } : it))
                        )
                      }
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() =>
                        setCustomEnvVars((prev) =>
                          prev.map((it, i) => (i === idx ? { ...it, isSecret: !it.isSecret } : it))
                        )
                      }
                      title={item.isSecret ? "切换为明文" : "切换为密文"}
                    >
                      {item.isSecret ? "🔒" : "👁"}
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => setCustomEnvVars((prev) => prev.filter((_, i) => i !== idx))}
                    >
                      ✕
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 常驻鉴权提示：声明了变量名但未填值时，执行将回退到后端环境变量。
              常驻显示（非 toast），避免"提示一闪而过、请求仍在跑"的误解。 */}
          {authHints().length > 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50/70 p-3 space-y-1">
              <div className="flex items-center gap-2 text-sm font-medium text-amber-800">
                <AlertCircle className="h-4 w-4" />
                鉴权值留空，将使用后端环境变量
              </div>
              {authHints().map((h, i) => (
                <p key={i} className="text-xs text-amber-700 leading-relaxed">· {h}</p>
              ))}
              <p className="text-xs text-amber-600">
                若后端未配置该变量，用例将全部跳过（skipped）。后端是否已配置无法由前端确认，以执行结果为准。
              </p>
            </div>
          )}

          {result && (
            <div className={`rounded-lg border p-3 flex items-center gap-2 text-sm ${
              result.status === "blocked" ? "border-amber-300 bg-amber-50 text-amber-800"
              : result.status === "running" ? "border-blue-300 bg-blue-50 text-blue-800"
              : "border-green-300 bg-green-50 text-green-800"
            }`}>
              {result.status === "blocked" ? <ShieldAlert className="h-4 w-4" />
                : result.status === "running" ? <Loader2 className="h-4 w-4 animate-spin" />
                : <CheckCircle2 className="h-4 w-4" />}
              <div>
                <Badge variant="outline" className="mr-2">{result.status}</Badge>
                run: {result.runId.slice(0, 8)}...
                {result.message && <p className="text-xs mt-1">{result.message}</p>}
              </div>
            </div>
          )}

          {/* 执行中的友好提示：同步接口较慢，告知用户正在做什么、大概多久、卡久了怎么办 */}
          {running && (
            <div className="rounded-lg border border-blue-200 bg-blue-50/60 p-3 space-y-1.5">
              <div className="flex items-center gap-2 text-sm font-medium text-blue-800">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在生成脚本并执行，请稍候…
              </div>
              <p className="text-xs text-blue-700 leading-relaxed">
                这个过程包含「生成脚本 → 真实调用目标环境执行」，通常需要
                <span className="font-medium"> 1~3 分钟</span>。
                目标环境响应慢时可能更久，请耐心等待，<span className="font-medium">请勿重复点击</span>。
              </p>
              {runElapsed > 0 && (
                <p className="text-xs text-blue-600">已等待 {runElapsed} 秒</p>
              )}
              {runElapsed >= RUN_STALL_THRESHOLD_S && (
                <p className="text-xs text-amber-700 flex items-start gap-1">
                  <AlertCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                  已等待较久。后端执行有 5 分钟超时兜底，若一直无结果可关闭本窗口，
                  稍后在「测试运行 / 测试报告」中查看该 run 的实际结果。
                </p>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>关闭</Button>
          <Button
            onClick={handleRun}
            disabled={running || miss.length > 0}
          >
            {running ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}
            {miss.length > 0 ? `缺 ${miss.length} 项配置` : "生成并执行"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
