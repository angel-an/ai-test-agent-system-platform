// NOTE  MC80OmFIVnBZMlhscm9ua3VMazZUbU5VWVE9PToyYWMzODM5Ng==

"use client";
// eslint-disable-next-line MS80OmFIVnBZMlhscm9ua3VMazZUbU5VWVE9PToyYWMzODM5Ng==

import * as React from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Loader2, FileSpreadsheet, FileText, Download, CheckSquare, Square } from "lucide-react";
import { toast } from "sonner";
import type { TestCaseInfo, RequirementReport } from "@/lib/api/types";

interface ExportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  testCases: TestCaseInfo[];
  selectedIds: Set<string>;
  onExportTestCases: (ids: string[], requirementName?: string) => Promise<void>;
  onExportReqReport: (filename: string) => Promise<void>;
  onLoadReports: () => Promise<RequirementReport[]>;
  exporting: boolean;
}

export function ExportDialog({
  open,
  onOpenChange,
  projectId,
  testCases,
  selectedIds,
  onExportTestCases,
  onExportReqReport,
  onLoadReports,
  exporting,
}: ExportDialogProps) {
  const [activeTab, setActiveTab] = React.useState<"test_cases" | "reports">("test_cases");
  const [localSelectedIds, setLocalSelectedIds] = React.useState<Set<string>>(new Set());
  const [reports, setReports] = React.useState<RequirementReport[]>([]);
  const [selectedReport, setSelectedReport] = React.useState<string | null>(null);
  const [loadingReports, setLoadingReports] = React.useState(false);
  const [requirementName, setRequirementName] = React.useState("");

  // 当对话框打开时，初始化选中状态
  React.useEffect(() => {
    if (open) {
      setLocalSelectedIds(new Set(selectedIds));
      setSelectedReport(null);
      setActiveTab("test_cases");
      setRequirementName(""); // 重置需求名称
      // 加载需求报告列表
      loadReports();
    }
  }, [open, selectedIds]);

  const loadReports = async () => {
    try {
      setLoadingReports(true);
      const data = await onLoadReports();
      setReports(data);
    } catch (error) {
      console.error("Failed to load reports:", error);
      toast.error("加载需求分析报告失败");
    } finally {
      setLoadingReports(false);
    }
  };

  // 全选/取消全选测试用例
  const handleSelectAllTestCases = (checked: boolean) => {
    if (checked) {
      setLocalSelectedIds(new Set(testCases.map((tc) => tc.id)));
    } else {
      setLocalSelectedIds(new Set());
    }
  };

  // 单选测试用例
  const handleSelectTestCase = (id: string, checked: boolean) => {
    const newIds = new Set(localSelectedIds);
    if (checked) {
      newIds.add(id);
    } else {
      newIds.delete(id);
    }
    setLocalSelectedIds(newIds);
  };

  // 选择需求报告
  const handleSelectReport = (filename: string) => {
    setSelectedReport(filename === selectedReport ? null : filename);
  };

  // 导出测试用例
  const handleExportTestCases = async () => {
    if (localSelectedIds.size === 0) {
      toast.error("请至少选择一个测试用例");
      return;
    }
    const ids = Array.from(localSelectedIds);
    await onExportTestCases(ids, requirementName.trim() || undefined);
    onOpenChange(false);
  };

  // 导出需求报告
  const handleExportReport = async () => {
    if (!selectedReport) {
      toast.error("请选择一个需求分析报告");
      return;
    }
    await onExportReqReport(selectedReport);
    onOpenChange(false);
  };

  const isAllTestCasesSelected =
    testCases.length > 0 && localSelectedIds.size === testCases.length;
  const isPartialTestCasesSelected =
    localSelectedIds.size > 0 && localSelectedIds.size < testCases.length;

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const formatDate = (isoString: string) => {
    try {
      return new Date(isoString).toLocaleString("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return isoString;
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Download className="h-5 w-5" />
            导出
          </DialogTitle>
          <DialogDescription>
            选择要导出的测试用例或需求分析报告
          </DialogDescription>
        </DialogHeader>

        <Tabs
          value={activeTab}
          onValueChange={(v) => setActiveTab(v as "test_cases" | "reports")}
          className="flex-1 flex flex-col min-h-0"
        >
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="test_cases" className="flex items-center gap-2">
              <FileSpreadsheet className="h-4 w-4" />
              测试用例 (Excel)
              {localSelectedIds.size > 0 && (
                <Badge variant="secondary" className="ml-1 h-5 px-1.5 text-xs">
                  {localSelectedIds.size}
                </Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="reports" className="flex items-center gap-2">
              <FileText className="h-4 w-4" />
              需求分析报告 (MD)
              {selectedReport && (
                <Badge variant="secondary" className="ml-1 h-5 px-1.5 text-xs">
                  1
                </Badge>
              )}
            </TabsTrigger>
          </TabsList>

          {/* 测试用例列表 */}
          <TabsContent value="test_cases" className="flex-1 flex flex-col min-h-0 mt-4">
            <div className="flex items-center gap-2 mb-2 px-1">
              <Button
                variant="ghost"
                size="sm"
                className="h-8 gap-1 text-xs"
                onClick={() => handleSelectAllTestCases(!isAllTestCasesSelected)}
              >
                {isAllTestCasesSelected ? (
                  <CheckSquare className="h-4 w-4" />
                ) : (
                  <Square className="h-4 w-4" />
                )}
                {isAllTestCasesSelected ? "取消全选" : "全选"}
              </Button>
              <span className="text-xs text-muted-foreground">
                已选择 {localSelectedIds.size} / {testCases.length} 个用例
              </span>
            </div>

            <ScrollArea className="flex-1 border rounded-md">
              <div className="p-2">
                {testCases.length === 0 ? (
                  <div className="text-center py-8 text-muted-foreground text-sm">
                    当前没有测试用例可导出
                  </div>
                ) : (
                  <div className="space-y-1">
                    {testCases.map((tc) => (
                      <div
                        key={tc.id}
                        className="flex items-start gap-3 p-2 rounded-md hover:bg-muted/50 cursor-pointer transition-colors"
                        onClick={() => handleSelectTestCase(tc.id, !localSelectedIds.has(tc.id))}
                      >
                        <Checkbox
                          checked={localSelectedIds.has(tc.id)}
                          onCheckedChange={(checked) =>
                            handleSelectTestCase(tc.id, checked as boolean)
                          }
                          onClick={(e) => e.stopPropagation()}
                          className="mt-0.5"
                        />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-xs text-muted-foreground font-mono shrink-0">
                              {tc.identifier}
                            </span>
                            <span className="text-sm font-medium truncate">
                              {tc.name}
                            </span>
                          </div>
                          <div className="flex items-center gap-2 mt-0.5">
                            <span className="text-xs text-muted-foreground">
                              {tc.module || "-"}
                            </span>
                            <span className="text-xs text-muted-foreground">·</span>
                            <Badge
                              variant="outline"
                              className="text-[10px] h-4 px-1"
                            >
                              {tc.priority}
                            </Badge>
                            <Badge
                              variant="outline"
                              className="text-[10px] h-4 px-1"
                            >
                              {tc.status}
                            </Badge>
                            {tc.case_kind && (
                              <Badge
                                variant="outline"
                                className="text-[10px] h-4 px-1"
                              >
                                {tc.case_kind?.toUpperCase() || "SIT"}
                              </Badge>
                            )}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </ScrollArea>

            <DialogFooter className="mt-4">
              <div className="flex items-center gap-3 w-full">
                <div className="flex-1">
                  <Label className="text-xs text-muted-foreground mb-1 block">需求名称（用于文件名）</Label>
                  <Input
                    placeholder="如：开屏广告功能测试"
                    value={requirementName}
                    onChange={(e) => setRequirementName(e.target.value)}
                    className="h-9"
                  />
                </div>
                <Button variant="outline" onClick={() => onOpenChange(false)} className="mt-5">
                  取消
                </Button>
                <Button
                  onClick={handleExportTestCases}
                  disabled={exporting || localSelectedIds.size === 0}
                  className="gap-2 mt-5"
                >
                  {exporting ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <FileSpreadsheet className="h-4 w-4" />
                  )}
                  导出 Excel ({localSelectedIds.size} 个)
                </Button>
              </div>
            </DialogFooter>
          </TabsContent>

          {/* 需求分析报告列表 */}
          <TabsContent value="reports" className="flex-1 flex flex-col min-h-0 mt-4">
            {loadingReports ? (
              <div className="flex-1 flex items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : reports.length === 0 ? (
              <div className="flex-1 flex flex-col items-center justify-center gap-3 text-muted-foreground">
                <FileText className="h-10 w-10 opacity-30" />
                <p className="text-sm">暂无需求分析报告</p>
                <p className="text-xs">
                  使用 AI 助手生成需求分析报告后将显示在这里
                </p>
              </div>
            ) : (
              <>
                <ScrollArea className="flex-1 border rounded-md">
                  <div className="p-2 space-y-1">
                    {reports.map((report) => (
                      <div
                        key={report.id}
                        className={`flex items-start gap-3 p-3 rounded-md cursor-pointer transition-colors ${
                          selectedReport === report.filename
                            ? "bg-primary/10 border border-primary/30"
                            : "hover:bg-muted/50 border border-transparent"
                        }`}
                        onClick={() => handleSelectReport(report.filename)}
                      >
                        <div className="mt-0.5">
                          {selectedReport === report.filename ? (
                            <CheckSquare className="h-4 w-4 text-primary" />
                          ) : (
                            <Square className="h-4 w-4 text-muted-foreground" />
                          )}
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium truncate">
                            {report.title}
                          </p>
                          <div className="flex items-center gap-2 mt-1">
                            <span className="text-xs text-muted-foreground">
                              {formatFileSize(report.size)}
                            </span>
                            <span className="text-xs text-muted-foreground">·</span>
                            <span className="text-xs text-muted-foreground">
                              {formatDate(report.created_at)}
                            </span>
                          </div>
                          <p className="text-xs text-muted-foreground mt-0.5 font-mono">
                            {report.filename}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>
                </ScrollArea>

                <DialogFooter className="mt-4">
                  <Button variant="outline" onClick={() => onOpenChange(false)}>
                    取消
                  </Button>
                  <Button
                    onClick={handleExportReport}
                    disabled={exporting || !selectedReport}
                    className="gap-2"
                  >
                    {exporting ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <FileText className="h-4 w-4" />
                    )}
                    导出 Markdown
                  </Button>
                </DialogFooter>
              </>
            )}
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
// NOTE  My80OmFIVnBZMlhscm9ua3VMazZUbU5VWVE9PToyYWMzODM5Ng==
