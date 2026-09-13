/**
 * 前端门控字段贯通校验（阶段一/二复核项 #5）
 *
 * 背景：TypeScript 类型检查通过只能证明类型正确，不能证明字段真的提交给了后端。
 * 本脚本通过 tsc 编译 lib/api/ai.ts 并校验 buildGenerateFromDocumentForm 的真实输出。
 *
 * 运行：
 *   cd ui
 *   node --experimental-strip-types ../scripts/verify-requirement-quality-form.ts
 * 或（Node < 22.6）：
 *   npx tsc lib/api/ai.ts --outDir .verify-out --module esnext --target es2020 \
 *     --moduleResolution bundler --skipLibCheck
 *   node ../scripts/verify-requirement-quality-form.mjs
 */

import assert from "node:assert/strict";

import { buildGenerateFromDocumentForm } from "../ui/lib/api/ai";

const file = { name: "req.md" } as unknown as File;

// 1. 最小调用：只传文件
const minimal = buildGenerateFromDocumentForm({ file });
assert.deepEqual(Object.keys(minimal), ["file"], "最小调用只应提交 file");

// 2. 完整调用：门控字段必须全部出现
const full = buildGenerateFromDocumentForm({
  file,
  folder_id: "FOLDER-1",
  additional_prompt: "重点关注边界",
  template: "test_case",
  knowledge_space_id: "KB-1",
  output_type: "test_case",
  requirement_id: "REQ-1",
  session_key: "req-abc123",
  analysis_payload: { status: "Complete" },
});

const expectedFields = [
  "file",
  "folder_id",
  "additional_prompt",
  "template",
  "knowledge_space_id",
  "output_type",
  "requirement_id",
  "session_key",
  "analysis_payload",
];

for (const field of expectedFields) {
  assert.ok(field in full, `缺少必填表单项: ${field}`);
}

assert.equal(full.output_type, "test_case");
assert.equal(full.requirement_id, "REQ-1");
assert.equal(full.session_key, "req-abc123");
assert.equal(
  full.analysis_payload,
  JSON.stringify({ status: "Complete" }),
  "analysis_payload 对象应被序列化为 JSON 字符串"
);

// 3. 字符串形式的 analysis_payload 原样提交
const asString = buildGenerateFromDocumentForm({
  file,
  analysis_payload: '{"status":"Complete"}',
});
assert.equal(asString.analysis_payload, '{"status":"Complete"}');

// 4. 需求分析报告模式
const analysisMode = buildGenerateFromDocumentForm({
  file,
  output_type: "requirement_analysis",
});
assert.equal(analysisMode.output_type, "requirement_analysis");

// 5. 空值不应产生空表单项
const blanks = buildGenerateFromDocumentForm({
  file,
  requirement_id: "",
  session_key: undefined,
  analysis_payload: null,
});
for (const field of ["requirement_id", "session_key", "analysis_payload"]) {
  assert.ok(!(field in blanks), `空值不应提交: ${field}`);
}

console.log("✓ 前端门控字段贯通校验通过（" + expectedFields.length + " 个字段）");
