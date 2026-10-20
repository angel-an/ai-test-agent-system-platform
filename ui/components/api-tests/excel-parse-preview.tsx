// Excel API 文档解析预览与确认导入（美心模板）
// 流程：上传 .xlsx → 展示接口列表 → 查看单接口详情（含来源/待确认项）→ 编辑 → 确认导入

"use client";

import * as React from "react";
import { useState } from "react";
import {
  Upload,
  Loader2,
  CheckCircle2,
  AlertCircle,
  ChevronRight,
  ChevronLeft,
  FileSpreadsheet,
  ShieldAlert,
  Pencil,
  Save,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
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
import {
  parseExcelApiDoc,
  getExcelInterfaceDetail,
  confirmExcelInterface,
  type ExcelParseTaskSummary,
  type ExcelInterfaceDetail,
  type UnifiedInterface,
} from "@/lib/api/api-endpoints";

interface ExcelParsePreviewProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectIdentifier: string;
  parentFolderId?: string | null;
  onSuccess?: () => void;
}

type Step = "upload" | "parsing" | "list" | "detail" | "importing" | "done";

export function ExcelParsePreview({
  open,
  onOpenChange,
  projectIdentifier,
  parentFolderId,
  onSuccess,
}: ExcelParsePreviewProps) {
  const [step, setStep] = useState<Step>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [task, setTask] = useState<ExcelParseTaskSummary | null>(null);
  const [detail, setDetail] = useState<ExcelInterfaceDetail | null>(null);
  const [edited, setEdited] = useState<UnifiedInterface | null>(null);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [imported, setImported] = useState<{ endpoint_id: string; display_name: string } | null>(null);

  const reset = () => {
    setStep("upload");
    setFile(null);
    setTask(null);
    setDetail(null);
    setEdited(null);
    setSelectedIndex(null);
    setImported(null);
  };

  const handleOpenChange = (o: boolean) => {
    if (!o) reset();
    onOpenChange(o);
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".xlsx")) {
      toast.error("仅支持 .xlsx 文件");
      return;
    }
    if (f.size > 15 * 1024 * 1024) {
      toast.error("文件大小不能超过 15MB");
      return;
    }
    setFile(f);
  };

  const handleParse = async () => {
    if (!file) return;
    setStep("parsing");
    try {
      const fd = new FormData();
      fd.append("file", file);
      if (parentFolderId) fd.append("parent_folder_id", parentFolderId);
      const result = await parseExcelApiDoc(projectIdentifier, fd);
      setTask(result);
      if (result.status === "completed") {
        setStep("list");
        toast.success(`识别出 ${result.interface_count} 个接口`);
      } else {
        toast.error(`解析失败: ${result.error || "未知错误"}`);
        setStep("upload");
      }
    } catch (err: any) {
      toast.error(err.message || "解析失败");
      setStep("upload");
    }
  };

  const handleViewDetail = async (index: number) => {
    if (!task) return;
    setSelectedIndex(index);
    try {
      const d = await getExcelInterfaceDetail(projectIdentifier, task.task_id, index);
      setDetail(d);
      setEdited(JSON.parse(JSON.stringify(d.interface))); // 深拷贝供编辑
      setStep("detail");
    } catch (err: any) {
      toast.error(err.message || "加载详情失败");
    }
  };

  const handleImport = async () => {
    if (!task || selectedIndex === null || !edited) return;
    setStep("importing");
    try {
      const result = await confirmExcelInterface(projectIdentifier, task.task_id, selectedIndex, {
        edited_spec: edited,
        parent_folder_id: parentFolderId ?? null,
      });
      setImported(result);
      setStep("done");
      onSuccess?.();
      toast.success(`已导入 ${result.display_name}`);
    } catch (err: any) {
      toast.error(err.message || "导入失败");
      setStep("detail");
    }
  };

  const updateField = (section: "request" | "response", idx: number, patch: Partial<{ name: string; required: boolean; example: string | null }>) => {
    if (!edited) return;
    const fields = [...edited[section].fields];
    fields[idx] = { ...fields[idx], ...patch };
    setEdited({ ...edited, [section]: { ...edited[section], fields } });
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-xl">
            <FileSpreadsheet className="h-5 w-5 text-primary" />
            {step === "upload" && "导入 Excel API 文档"}
            {step === "parsing" && "正在解析..."}
            {step === "list" && "选择接口"}
            {step === "detail" && "接口详情与确认"}
            {step === "importing" && "正在导入..."}
            {step === "done" && "导入成功"}
          </DialogTitle>
          <DialogDescription className="text-base">
            {step === "upload" && "上传美心 Excel 模板（.xlsx），自动识别接口、入参/出参与签名规则"}
            {step === "list" && `共识别 ${task?.interface_count ?? 0} 个接口，点击查看详情并选择导入`}
            {step === "detail" && "核对解析结果（含来源与待确认项），可编辑后导入"}
          </DialogDescription>
        </DialogHeader>

        {/* 上传 */}
        {step === "upload" && (
          <div className="space-y-4 py-4">
            <div className="border-2 border-dashed rounded-lg p-10 text-center hover:border-primary transition-colors">
              <input type="file" id="excelFile" accept=".xlsx" className="hidden" onChange={handleFileSelect} />
              <label htmlFor="excelFile" className="cursor-pointer">
                <div className="flex flex-col items-center gap-3">
                  {file ? (
                    <>
                      <FileSpreadsheet className="h-12 w-12 text-primary" />
                      <p className="font-medium">{file.name}</p>
                      <p className="text-xs text-muted-foreground">{(file.size / 1024).toFixed(1)} KB</p>
                    </>
                  ) : (
                    <>
                      <Upload className="h-12 w-12 text-muted-foreground" />
                      <p className="font-medium">点击选择 .xlsx 文件</p>
                      <p className="text-xs text-muted-foreground">支持美心 CRM API Spec 模板</p>
                    </>
                  )}
                </div>
              </label>
            </div>
          </div>
        )}

        {/* 解析中 */}
        {step === "parsing" && (
          <div className="flex flex-col items-center py-16 gap-4">
            <Loader2 className="h-14 w-14 animate-spin text-primary" />
            <p className="text-muted-foreground">正在读取 Sheet、合并单元格与公共规则...</p>
          </div>
        )}

        {/* 接口列表 */}
        {step === "list" && task && (
          <div className="py-2 max-h-[55vh] overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-muted">
                <tr>
                  <th className="text-left p-2">接口</th>
                  <th className="text-left p-2">方法</th>
                  <th className="text-left p-2">路径</th>
                  <th className="text-left p-2">待确认</th>
                  <th className="p-2"></th>
                </tr>
              </thead>
              <tbody>
                {task.interfaces.map((it) => (
                  <tr key={it.index} className="border-t hover:bg-muted/40">
                    <td className="p-2 font-medium">{it.name}</td>
                    <td className="p-2"><Badge variant="secondary">{it.http_method}</Badge></td>
                    <td className="p-2 text-muted-foreground text-xs max-w-[220px] truncate">{it.path}</td>
                    <td className="p-2">
                      {it.pending_count > 0 ? (
                        <Badge variant="outline" className="text-amber-600 border-amber-300">
                          <ShieldAlert className="h-3 w-3 mr-1" />{it.pending_count}
                        </Badge>
                      ) : (
                        <CheckCircle2 className="h-4 w-4 text-green-600" />
                      )}
                    </td>
                    <td className="p-2">
                      <Button size="sm" variant="outline" onClick={() => handleViewDetail(it.index)}>
                        详情 <ChevronRight className="h-3 w-3 ml-1" />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* 接口详情 */}
        {step === "detail" && edited && (
          <div className="space-y-4 py-2 max-h-[60vh] overflow-y-auto">
            <div className="flex items-center justify-between">
              <div>
                <p className="font-semibold">{edited.name}</p>
                <p className="text-xs text-muted-foreground">
                  来源 Sheet: {edited.source?.sheet} · 行 {edited.source?.rows?.join(", ")}
                </p>
              </div>
              <Badge variant="secondary">{edited.http_method} {edited.path}</Badge>
            </div>

            {/* 待确认项 */}
            {edited.pending_confirmations.length > 0 && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 space-y-1">
                <p className="text-sm font-medium text-amber-800 flex items-center gap-1">
                  <ShieldAlert className="h-4 w-4" /> 待确认项（{edited.pending_confirmations.length}）
                </p>
                {edited.pending_confirmations.map((p, i) => (
                  <p key={i} className="text-xs text-amber-700">· {p.message}</p>
                ))}
              </div>
            )}

            {/* 鉴权 */}
            {edited.auth && (
              <div className="rounded-lg border p-3">
                <p className="text-sm font-medium mb-1">鉴权</p>
                <p className="text-xs text-muted-foreground">{edited.auth.sign_formula}</p>
                <p className="text-xs text-muted-foreground">字段: {(edited.auth.sign_fields || []).join(" + ")}</p>
              </div>
            )}

            {/* 入参 */}
            <div>
              <p className="text-sm font-medium mb-2 flex items-center gap-1"><Pencil className="h-3 w-3" /> 入参（可编辑）</p>
              <table className="w-full text-sm">
                <thead className="bg-muted">
                  <tr>
                    <th className="text-left p-1.5">字段</th>
                    <th className="text-left p-1.5">必填</th>
                    <th className="text-left p-1.5">类型</th>
                    <th className="text-left p-1.5">示例</th>
                  </tr>
                </thead>
                <tbody>
                  {edited.request.fields.map((f, i) => (
                    <tr key={i} className="border-t">
                      <td className="p-1">
                        <Input value={f.name} className="h-7 text-xs" onChange={(e) => updateField("request", i, { name: e.target.value })} />
                      </td>
                      <td className="p-1 text-center">
                        <input type="checkbox" checked={f.required} onChange={(e) => updateField("request", i, { required: e.target.checked })} />
                      </td>
                      <td className="p-1 text-xs text-muted-foreground">{f.type}{f.length ? `(${f.length})` : ""}</td>
                      <td className="p-1">
                        <Input value={f.example || ""} className="h-7 text-xs" onChange={(e) => updateField("request", i, { example: e.target.value })} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* 出参 */}
            <div>
              <p className="text-sm font-medium mb-2">出参</p>
              <div className="rounded-lg border p-2 space-y-1">
                {edited.response.fields.map((f, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <Badge variant="outline" className="text-[10px]">{f.type}</Badge>
                    <span className="font-medium">{f.name}</span>
                    {f.example && <span className="text-muted-foreground">例: {f.example}</span>}
                    {f.diff && <span className="text-amber-600">差异: {f.diff.slice(0, 60)}</span>}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* 导入成功 */}
        {step === "done" && imported && (
          <div className="flex flex-col items-center py-10 gap-3">
            <CheckCircle2 className="h-14 w-14 text-green-600" />
            <p className="font-semibold">{imported.display_name}</p>
            <p className="text-sm text-muted-foreground">已创建 APIEndpoint，可进入端点执行契约测试</p>
          </div>
        )}

        <DialogFooter className="gap-2">
          {step === "upload" && (
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button>
              <Button onClick={handleParse} disabled={!file}>
                <FileSpreadsheet className="mr-2 h-4 w-4" /> 解析
              </Button>
            </>
          )}
          {step === "list" && (
            <Button variant="outline" onClick={() => setStep("upload")}>
              <ChevronLeft className="mr-2 h-4 w-4" /> 重新上传
            </Button>
          )}
          {step === "detail" && (
            <>
              <Button variant="outline" onClick={() => setStep("list")}>
                <ChevronLeft className="mr-2 h-4 w-4" /> 返回列表
              </Button>
              <Button onClick={handleImport}>
                <Save className="mr-2 h-4 w-4" /> 确认导入
              </Button>
            </>
          )}
          {step === "done" && (
            <Button onClick={() => onOpenChange(false)}>
              <CheckCircle2 className="mr-2 h-4 w-4" /> 完成
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
