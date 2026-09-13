/**
 * 统一导入的纯函数校验与分组逻辑（不依赖 React，便于离线测试）。
 *
 * 前端验收计数依赖这里：文件校验、冲突分组、导入可行性判定
 * 都必须可以在没有 DOM 的环境下被断言。
 */

import type {
  BundleConflict,
  BundleFiles,
  ConflictSeverity,
} from "./api-bundles";

/** 与后端 MAX_SOURCE_SIZE 保持一致（20MB） */
export const MAX_SOURCE_SIZE = 20 * 1024 * 1024;

/** 三个上传槽位定义（顺序即 UI 展示顺序） */
export const SOURCE_SLOTS = [
  {
    field: "excel_file",
    label: "Excel 接口契约",
    hint: "美心模板 .xlsx（1 导读 + N 接口）",
    accept: ".xlsx,.xls",
  },
  {
    field: "collection_file",
    label: "Postman Collection",
    hint: "collection.json（v2.1）",
    accept: ".json",
  },
  {
    field: "environment_file",
    label: "Postman Environment",
    hint: "environment.json（变量与密钥引用）",
    accept: ".json",
  },
] as const;

/** 冲突等级展示顺序：严重优先 */
export const SEVERITY_ORDER: ConflictSeverity[] = ["high", "medium", "low"];

export const SEVERITY_LABEL: Record<ConflictSeverity, string> = {
  high: "高（阻断导入）",
  medium: "中",
  low: "低",
};

export interface FileValidationResult {
  ok: boolean;
  /** 阻止提交的错误 */
  errors: string[];
  /** 不阻止提交、但需要提示的问题 */
  warnings: string[];
}

/**
 * 校验待上传的三文件。
 *
 * 规则：
 * - 至少一个文件（后端约束）；
 * - 单文件 ≤ 20MB（后端约束，前端提前拦截以免浪费上传）；
 * - 超出部分文件给出提示（联合导入的价值来自多来源匹配）。
 */
export function validateImportFiles(files: BundleFiles): FileValidationResult {
  const errors: string[] = [];
  const warnings: string[] = [];

  const chosen = SOURCE_SLOTS.filter((slot) => files[slot.field]);
  if (chosen.length === 0) {
    errors.push("至少需要选择一个来源文件");
  }

  for (const slot of SOURCE_SLOTS) {
    const file = files[slot.field];
    if (!file) continue;
    if (file.size === 0) {
      errors.push(`${slot.label} 文件为空`);
    }
    if (file.size > MAX_SOURCE_SIZE) {
      errors.push(
        `${slot.label} 超过 ${Math.floor(MAX_SOURCE_SIZE / 1024 / 1024)}MB 限制`
      );
    }
  }

  if (chosen.length === 1 && chosen[0].field === "environment_file") {
    warnings.push("只上传环境文件无法产生接口契约，建议同时上传 Excel 或 Collection");
  } else if (chosen.length < SOURCE_SLOTS.length) {
    warnings.push("未选择全部三个来源，匹配结果可能不完整");
  }

  return { ok: errors.length === 0, errors, warnings };
}

/** 按等级分组（high → medium → low），空组不返回 */
export function groupConflictsBySeverity(
  conflicts: BundleConflict[] | undefined | null
): Array<{ severity: ConflictSeverity; items: BundleConflict[] }> {
  const buckets = new Map<ConflictSeverity, BundleConflict[]>();
  for (const conflict of conflicts ?? []) {
    const list = buckets.get(conflict.severity) ?? [];
    list.push(conflict);
    buckets.set(conflict.severity, list);
  }
  return SEVERITY_ORDER.filter((severity) => buckets.has(severity)).map(
    (severity) => ({ severity, items: buckets.get(severity) as BundleConflict[] })
  );
}

/** 按 conflict_type 统计（用于分组头部摘要） */
export function countByConflictType(
  conflicts: BundleConflict[] | undefined | null
): Array<{ type: string; count: number }> {
  const counts = new Map<string, number>();
  for (const conflict of conflicts ?? []) {
    counts.set(conflict.conflict_type, (counts.get(conflict.conflict_type) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([type, count]) => ({ type, count }))
    .sort((a, b) => b.count - a.count || a.type.localeCompare(b.type));
}

/** 仍然阻断导入的冲突（high 且未确认/未豁免） */
export function openBlockingConflicts(
  conflicts: BundleConflict[] | undefined | null
): BundleConflict[] {
  return (conflicts ?? []).filter(
    (c) => c.severity === "high" && c.status !== "confirmed" && c.status !== "waived"
  );
}

/**
 * 判定是否可以执行导入。
 *
 * 注意：这只是 UI 侧预判。真正的门禁以服务端为准——confirm 仍可能在
 * 并发场景下返回 409。调用方必须处理该分支，不得仅依赖本函数。
 */
export function canConfirmImport(
  conflicts: BundleConflict[] | undefined | null
): { ok: boolean; reason: string | null } {
  const blocking = openBlockingConflicts(conflicts);
  if (blocking.length > 0) {
    return {
      ok: false,
      reason: `还有 ${blocking.length} 条高等级冲突未处理，服务端会拒绝导入`,
    };
  }
  return { ok: true, reason: null };
}

/** 变量只允许显示引用形式，任何看起来像明文的值都要被拒绝展示 */
export function isSafeVariableRef(valueRef: string | undefined | null): boolean {
  if (!valueRef) return false;
  return valueRef.startsWith("env:");
}

/** 从 confirm 结果生成结果汇总文案（供 UI 与验收脚本共用） */
export function buildImportSummary(data: {
  imported?: Array<unknown>;
  skipped?: Array<unknown>;
  failed?: Array<unknown>;
  folder_ids?: string[];
}): {
  importedCount: number;
  skippedCount: number;
  failedCount: number;
  folderCount: number;
  text: string;
} {
  const importedCount = data.imported?.length ?? 0;
  const skippedCount = data.skipped?.length ?? 0;
  const failedCount = data.failed?.length ?? 0;
  const folderCount = data.folder_ids?.length ?? 0;
  return {
    importedCount,
    skippedCount,
    failedCount,
    folderCount,
    text:
      `新建端点 ${importedCount} 个，复用 ${skippedCount} 个，` +
      `失败 ${failedCount} 个，文件夹 ${folderCount} 个`,
  };
}
