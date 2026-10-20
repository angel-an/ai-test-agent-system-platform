"use client";

/**
 * 统一导入（三文件联合导入）对话框。
 *
 * 流程：选择文件 → 解析 → 冲突确认/豁免 → 确认导入 → 结果汇总
 *
 * 关键约束：
 * - 门禁以服务端为准：即使 UI 预判可导入，confirm 仍可能返回 409/503，
 *   两者都必须被处理并在界面上给出可操作的提示；
 * - 变量只渲染 `env:NAME` 引用，不渲染任何明文；
 * - 导入成功后刷新，端点必须仍在（持久化验证在验收脚本中覆盖）。
 */

import * as React from "react";
import { toast } from "sonner";
import { AlertCircle, CheckCircle2, FileSpreadsheet, Loader2, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError } from "@/lib/api/client";
import {
  confirmBundle,
  confirmBundleConflicts,
  getBundle,
  getBundleAssets,
  getBundleVariables,
  parseBundle,
} from "@/lib/api/api-bundles";
import type {
  BundleAssetsData,
  BundleConflict,
  BundleFiles,
  BundleVariable,
  ConfirmBundleData,
  ConflictResolution,
  ParseBundleData,
} from "@/lib/api/api-bundles";
import {
  SOURCE_SLOTS,
  SEVERITY_LABEL,
  buildImportSummary,
  canConfirmImport,
  countByConflictType,
  groupConflictsBySeverity,
  isSafeVariableRef,
  validateImportFiles,
} from "@/lib/api/api-bundles.helpers";

type Step = "select" | "conflicts" | "done";

interface UnifiedImportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectIdentifier: string;
  onSuccess?: () => void;
}

export function UnifiedImportDialog({
  open,
  onOpenChange,
  projectIdentifier,
  onSuccess,
}: UnifiedImportDialogProps) {
  const [step, setStep] = React.useState<Step>("select");
  const [files, setFiles] = React.useState<BundleFiles>({});
  const [bundleName, setBundleName] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [parsed, setParsed] = React.useState<ParseBundleData | null>(null);
  const [conflicts, setConflicts] = React.useState<BundleConflict[]>([]);
  const [resolutions, setResolutions] = React.useState<Record<string, ConflictResolution>>({});
  const [imported, setImported] = React.useState<ConfirmBundleData | null>(null);
  const [assets, setAssets] = React.useState<BundleAssetsData | null>(null);
  const [variables, setVariables] = React.useState<BundleVariable[]>([]);
  const [serverError, setServerError] = React.useState<string | null>(null);

  const validation = React.useMemo(() => validateImportFiles(files), [files]);
  const groups = React.useMemo(() => groupConflictsBySeverity(conflicts), [conflicts]);
  const gate = React.useMemo(() => canConfirmImport(conflicts), [conflicts]);
  const openHighCount = React.useMemo(
    () => conflicts.filter((c) => c.severity === "high" && c.status === "open").length,
    [conflicts]
  );
  const pendingResolutionCount = Object.keys(resolutions).length;
  const summary = React.useMemo(
    () => (imported ? buildImportSummary(imported) : null),
    [imported]
  );

  const resetAll = React.useCallback(() => {
    setStep("select");
    setFiles({});
    setBundleName("");
    setParsed(null);
    setConflicts([]);
    setResolutions({});
    setImported(null);
    setAssets(null);
    setVariables([]);
    setServerError(null);
    setBusy(false);
  }, []);

  React.useEffect(() => {
    if (!open) {
      resetAll();
    }
  }, [open, resetAll]);

  /** 从 ApiError 中取出结构化 detail（client 的 message 只取第一层，会丢结构） */
  const extractDetail = (error: unknown): Record<string, unknown> | null => {
    if (error instanceof ApiError && error.data && typeof error.data === "object") {
      const data = error.data as Record<string, unknown>;
      const detail = data.detail;
      if (detail && typeof detail === "object") {
        return detail as Record<string, unknown>;
      }
      return data;
    }
    return null;
  };

  const refreshBundleState = React.useCallback(
    async (bundleId: string) => {
      const [detail, assetsResp, variablesResp] = await Promise.all([
        getBundle(projectIdentifier, bundleId),
        getBundleAssets(projectIdentifier, bundleId),
        getBundleVariables(projectIdentifier, bundleId),
      ]);
      setConflicts(detail.data.conflicts ?? []);
      setAssets(assetsResp.data);
      setVariables(variablesResp.data ?? []);
    },
    [projectIdentifier]
  );

  const handleParse = async () => {
    if (!validation.ok) {
      toast.error(validation.errors[0]);
      return;
    }
    setBusy(true);
    setServerError(null);
    try {
      const effectiveName =
        bundleName.trim() ||
        `联合导入-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "")}`;
      const resp = await parseBundle(projectIdentifier, {
        ...files,
        bundle_name: effectiveName,
      });
      const data = resp.data;
      setParsed(data);
      setConflicts(data.conflicts ?? []);
      setBundleName(effectiveName);
      const highCount = (data.conflicts ?? []).filter((c) => c.severity === "high").length;
      toast.success(
        `解析完成：契约 ${data.contracts?.length ?? 0} 个，冲突 ${data.conflicts?.length ?? 0} 个` +
          (highCount > 0 ? `（其中高等级 ${highCount} 个需处理）` : "")
      );
      await refreshBundleState(data.bundle_id);
      setStep("conflicts");
    } catch (error) {
      const detail = extractDetail(error);
      const message =
        (detail?.detail as string) ||
        (error instanceof Error ? error.message : "解析失败");
      setServerError(String(message));
      toast.error(String(message));
    } finally {
      setBusy(false);
    }
  };

  const handleResolve = async (conflictId: string, resolution: ConflictResolution) => {
    setResolutions((prev) => ({ ...prev, [conflictId]: resolution }));
  };

  const handleSubmitResolutions = async () => {
    if (!parsed) return;
    const payload = Object.entries(resolutions).map(([conflict_id, resolution]) => ({
      conflict_id,
      resolution,
    }));
    if (payload.length === 0) {
      toast.error("请先对冲突做出处理");
      return;
    }
    setBusy(true);
    setServerError(null);
    try {
      await confirmBundleConflicts(projectIdentifier, parsed.bundle_id, payload);
      await refreshBundleState(parsed.bundle_id);
      setResolutions({});
      toast.success(`已处理 ${payload.length} 条冲突`);
    } catch (error) {
      const detail = extractDetail(error);
      const message =
        (detail?.detail as string) ||
        (error instanceof Error ? error.message : "冲突处理失败");
      setServerError(String(message));
      toast.error(String(message));
    } finally {
      setBusy(false);
    }
  };

  const handleConfirm = async () => {
    if (!parsed) return;
    setBusy(true);
    setServerError(null);
    try {
      const resp = await confirmBundle(projectIdentifier, parsed.bundle_id);
      setImported(resp.data);
      setStep("done");
      toast.success(buildImportSummary(resp.data).text);
      await refreshBundleState(parsed.bundle_id);
      onSuccess?.();
    } catch (error) {
      const detail = extractDetail(error);
      if (error instanceof ApiError && error.status === 409 && detail) {
        // 服务端门禁：列出仍未确认的高等级冲突
        const unconfirmed = (detail.unconfirmed_conflicts as Array<Record<string, unknown>>) ?? [];
        setServerError(
          `服务端拒绝了导入：还有 ${detail.unconfirmed_count ?? unconfirmed.length} 条高等级冲突未处理`
        );
        const refreshed = await getBundle(projectIdentifier, parsed.bundle_id).catch(() => null);
        if (refreshed) setConflicts(refreshed.data.conflicts ?? []);
        toast.error("存在未处理的高等级冲突，服务端已拒绝导入");
      } else if (error instanceof ApiError && error.status === 503 && detail) {
        const missing = (detail.missing as string[]) ?? [];
        setServerError(
          `数据库结构未就绪：${missing.join("; ") || String(detail.message ?? "")}` +
            `（请执行 ${detail.remedy ?? "alembic upgrade head"}）`
        );
        toast.error("数据库结构未就绪，需要先执行迁移");
      } else {
        const message =
          (detail?.detail as string) ||
          (error instanceof Error ? error.message : "导入失败");
        setServerError(String(message));
        toast.error(String(message));
      }
    } finally {
      setBusy(false);
    }
  };

  const onPickFile = (field: keyof BundleFiles, file: File | null) => {
    setFiles((prev) => ({ ...prev, [field]: file }));
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>三文件联合导入</DialogTitle>
          <DialogDescription>
            上传 Excel 契约、Postman Collection 与 Environment，平台会跨来源匹配并检测冲突，
            确认后创建接口端点。变量只保存 <code className="text-xs">env:NAME</code> 引用，不落明文密钥。
          </DialogDescription>
        </DialogHeader>

        {/* 步骤指示 */}
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <StepBadge active={step === "select"} done={step !== "select"} label="1 选择文件" />
          <span>→</span>
          <StepBadge active={step === "conflicts"} done={step === "done"} label="2 冲突确认" />
          <span>→</span>
          <StepBadge active={step === "done"} done={false} label="3 导入结果" />
        </div>

        <Separator />

        {serverError && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
            <span className="break-all">{serverError}</span>
          </div>
        )}

        {step === "select" && (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="bundle-name">资产包名称（可留空自动生成）</Label>
              <Input
                id="bundle-name"
                value={bundleName}
                onChange={(e) => setBundleName(e.target.value)}
                placeholder="例如：美心 POS 联合导入 20260914"
              />
            </div>

            {SOURCE_SLOTS.map((slot) => (
              <div key={slot.field} className="rounded-lg border border-dashed p-3">
                <Label htmlFor={`file-${slot.field}`} className="flex items-center gap-2 text-sm">
                  <FileSpreadsheet className="h-4 w-4" />
                  {slot.label}
                </Label>
                <p className="mt-1 text-xs text-muted-foreground">{slot.hint}</p>
                <Input
                  id={`file-${slot.field}`}
                  type="file"
                  accept={slot.accept}
                  className="mt-2"
                  data-testid={`file-input-${slot.field}`}
                  onChange={(e) => onPickFile(slot.field, e.target.files?.[0] ?? null)}
                />
                {files[slot.field] && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    已选择：{files[slot.field]?.name}（
                    {Math.ceil((files[slot.field]?.size ?? 0) / 1024)} KB）
                  </p>
                )}
              </div>
            ))}

            {validation.errors.length > 0 && (
              <ul className="space-y-1 text-sm text-destructive">
                {validation.errors.map((msg) => (
                  <li key={msg}>• {msg}</li>
                ))}
              </ul>
            )}
            {validation.warnings.length > 0 && (
              <ul className="space-y-1 text-xs text-muted-foreground">
                {validation.warnings.map((msg) => (
                  <li key={msg}>• {msg}</li>
                ))}
              </ul>
            )}

            {parsed && (
              <ParseStatsPanel data={parsed} />
            )}
          </div>
        )}

        {step === "conflicts" && parsed && (
          <div className="space-y-4">
            <ParseStatsPanel data={parsed} compact />

            <div>
              <div className="flex items-center justify-between">
                <h4 className="text-sm font-medium">
                  冲突 {conflicts.length} 条
                  {openHighCount > 0 && (
                    <span className="ml-2 text-destructive">
                      （{openHighCount} 条高等级未处理，会阻断导入）
                    </span>
                  )}
                </h4>
                {gate.ok ? (
                  <Badge variant="secondary" className="gap-1">
                    <CheckCircle2 className="h-3 w-3" /> 门禁已放行
                  </Badge>
                ) : (
                  <Badge variant="destructive" className="gap-1">
                    <AlertCircle className="h-3 w-3" /> 待处理
                  </Badge>
                )}
              </div>

              {groups.length === 0 && (
                <p className="mt-2 text-sm text-muted-foreground">没有检测到冲突，可直接导入。</p>
              )}

              <ScrollArea className="mt-2 max-h-72 rounded-md border">
                <div className="space-y-3 p-3">
                  {groups.map((group) => (
                    <div key={group.severity} data-testid={`conflict-group-${group.severity}`}>
                      <div className="flex items-center gap-2">
                        <Badge
                          variant={group.severity === "high" ? "destructive" : "secondary"}
                        >
                          {SEVERITY_LABEL[group.severity]}
                        </Badge>
                        <span className="text-xs text-muted-foreground">
                          {group.items.length} 条 ·{" "}
                          {countByConflictType(group.items)
                            .map((c) => `${c.type}×${c.count}`)
                            .join("，")}
                        </span>
                      </div>
                      <ul className="mt-2 space-y-1">
                        {group.items.map((conflict) => (
                          <li
                            key={conflict.id}
                            className="flex items-start justify-between gap-3 rounded border p-2 text-xs"
                            data-testid={`conflict-item-${conflict.id}`}
                            data-status={resolutions[conflict.id] ?? conflict.status}
                          >
                            <div className="min-w-0">
                              <div className="break-words">{conflict.message}</div>
                              <div className="mt-0.5 text-muted-foreground">
                                {conflict.conflict_type}
                                {conflict.status !== "open" && ` · 已${conflict.status === "confirmed" ? "确认" : "豁免"}`}
                              </div>
                            </div>
                            <div className="flex shrink-0 gap-1">
                              <Button
                                size="sm"
                                variant={
                                  resolutions[conflict.id] === "accept" ||
                                  conflict.status === "confirmed"
                                    ? "default"
                                    : "outline"
                                }
                                disabled={busy || conflict.status !== "open"}
                                onClick={() => handleResolve(conflict.id, "accept")}
                              >
                                接受
                              </Button>
                              <Button
                                size="sm"
                                variant={
                                  resolutions[conflict.id] === "waive" ||
                                  conflict.status === "waived"
                                    ? "default"
                                    : "outline"
                                }
                                disabled={busy || conflict.status !== "open"}
                                onClick={() => handleResolve(conflict.id, "waive")}
                              >
                                豁免
                              </Button>
                            </div>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              </ScrollArea>
            </div>

            {variables.length > 0 && (
              <div>
                <h4 className="text-sm font-medium">变量（仅引用）</h4>
                <div className="mt-1 flex flex-wrap gap-1">
                  {variables.map((v) => (
                    <Badge
                      key={v.id}
                      variant="outline"
                      className={`font-mono text-xs ${
                        v.source_type === "postman_collection"
                          ? "border-blue-300 bg-blue-50 text-blue-700"
                          : v.source_type === "manual"
                            ? "border-purple-300 bg-purple-50 text-purple-700"
                            : ""
                      }`}
                    >
                      {v.name}: {isSafeVariableRef(v.value_ref) ? v.value_ref : "（非引用，已隐藏）"}
                      {v.is_secret ? " 🔒" : ""}
                      {v.source_type === "postman_collection" && (
                        <span className="ml-1 text-[10px] opacity-60">Collection</span>
                      )}
                      {v.source_type === "manual" && (
                        <span className="ml-1 text-[10px] opacity-60">手动</span>
                      )}
                    </Badge>
                  ))}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  蓝色 = Collection 自动识别 · 紫色 = 手动添加 · 默认 = Environment 导入
                </p>
              </div>
            )}
          </div>
        )}

        {step === "done" && imported && summary && (
          <div className="space-y-4">
            <div className="flex items-center gap-2 text-sm font-medium text-green-600">
              <CheckCircle2 className="h-4 w-4" /> 导入完成
            </div>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <Stat label="新建端点" value={summary.importedCount} />
              <Stat label="复用（幂等命中）" value={summary.skippedCount} />
              <Stat label="失败" value={summary.failedCount} />
              <Stat label="文件夹" value={summary.folderCount} />
            </div>
            {assets && (
              <div className="rounded-md border p-3 text-xs">
                <div className="font-medium">落库资产</div>
                <div className="mt-1 text-muted-foreground">
                  契约 {assets.contracts} · 已回填端点 {assets.contracts_with_endpoint} · 请求模板{" "}
                  {assets.request_templates} · 断言 {assets.assertions} · 依赖 {assets.dependencies}
                  {assets.assertions_by_source &&
                    Object.keys(assets.assertions_by_source).length > 0 &&
                    `（断言来源：${Object.entries(assets.assertions_by_source)
                      .map(([k, v]) => `${k}×${v}`)
                      .join("，")}）`}
                </div>
              </div>
            )}
            {imported.imported.length > 0 && (
              <ScrollArea className="max-h-48 rounded-md border">
                <ul className="space-y-1 p-3 text-xs">
                  {imported.imported.map((item) => (
                    <li key={item.endpoint_id} className="font-mono">
                      {item.method} {item.path}
                    </li>
                  ))}
                </ul>
              </ScrollArea>
            )}
          </div>
        )}

        <DialogFooter>
          {step === "select" && (
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
                取消
              </Button>
              <Button onClick={handleParse} disabled={busy || !validation.ok} data-testid="parse-button">
                {busy ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" /> 解析中…
                  </>
                ) : (
                  <>
                    <Upload className="mr-2 h-4 w-4" /> 开始解析
                  </>
                )}
              </Button>
            </>
          )}

          {step === "conflicts" && (
            <>
              <Button
                variant="outline"
                onClick={() => {
                  setStep("select");
                  setParsed(null);
                  setConflicts([]);
                  setResolutions({});
                }}
                disabled={busy}
              >
                返回修改文件
              </Button>
              <Button
                variant="secondary"
                onClick={handleSubmitResolutions}
                disabled={busy || pendingResolutionCount === 0}
                data-testid="resolve-button"
              >
                {busy ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : null}
                提交处理（{pendingResolutionCount}）
              </Button>
              <Button
                onClick={handleConfirm}
                disabled={busy || !gate.ok}
                title={gate.reason ?? undefined}
                data-testid="confirm-button"
              >
                {busy ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" /> 导入中…
                  </>
                ) : (
                  "确认导入"
                )}
              </Button>
            </>
          )}

          {step === "done" && (
            <>
              <Button
                variant="outline"
                onClick={() => {
                  resetAll();
                }}
              >
                再导入一个
              </Button>
              <Button onClick={() => onOpenChange(false)}>完成</Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function StepBadge({ active, done, label }: { active: boolean; done: boolean; label: string }) {
  return (
    <span
      className={
        active
          ? "rounded bg-primary px-2 py-0.5 text-primary-foreground"
          : done
            ? "rounded bg-muted px-2 py-0.5"
            : "rounded px-2 py-0.5"
      }
    >
      {label}
    </span>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold">{value}</div>
    </div>
  );
}

function ParseStatsPanel({ data, compact }: { data: ParseBundleData; compact?: boolean }) {
  const stats = data.stats ?? {};
  return (
    <div className="rounded-md bg-muted p-3 text-xs">
      <div className="font-medium">解析结果</div>
      <div className="mt-1 text-muted-foreground">
        来源 {data.sources?.length ?? 0} · 契约 {data.contracts?.length ?? 0} · 冲突{" "}
        {data.conflicts?.length ?? 0}
        {typeof stats.excel_interfaces === "number" && ` · Excel 接口 ${stats.excel_interfaces}`}
        {typeof stats.collection_requests === "number" &&
          ` · Collection 请求 ${stats.collection_requests}`}
        {typeof stats.environment_variables === "number" &&
          ` · 环境变量 ${stats.environment_variables}`}
        {typeof stats.skipped_no_path === "number" &&
          stats.skipped_no_path > 0 &&
          ` · 空路径跳过 ${stats.skipped_no_path}`}
      </div>
      {!compact && (data.warnings?.length ?? 0) > 0 && (
        <ul className="mt-1 text-muted-foreground">
          {data.warnings.map((w) => (
            <li key={w}>• {w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
