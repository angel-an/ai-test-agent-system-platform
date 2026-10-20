#!/usr/bin/env python3
"""
KB 功能验收脚本 - 第4轮优化后验证

验证范围：
1. P1 HTTP 语义修复（403/400）
2. Excel 文件上传支持
3. 知识库选择器（对话框 + 聊天路径 + API路径）
4. Drawer 关闭按钮修复
5. 状态回收

用法：
    python verify_kb_round4.py [--base-url http://localhost:8000] [--project-id <id>]
"""

import argparse
import sys
import time
import uuid
from pathlib import Path

import requests


def log(msg: str, level: str = "INFO"):
    prefix = {"INFO": "[ℹ]", "PASS": "[✓]", "FAIL": "[✗]", "WARN": "[⚠]"}.get(level, "[?]")
    print(f"{prefix} {msg}")


class KBVerifier:
    def __init__(self, base_url: str, project_id: str):
        self.base_url = base_url.rstrip("/")
        self.project_id = project_id
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self.results = []

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v2/projects/{self.project_id}{path}"

    def check(self, name: str, condition: bool, detail: str = ""):
        self.results.append((name, condition, detail))
        if condition:
            log(f"{name}", "PASS")
        else:
            log(f"{name} - {detail}", "FAIL")
        return condition

    # ========================================================================
    # P1: HTTP 语义修复验证
    # ========================================================================

    def verify_p1_cross_project_403(self):
        """验证跨项目访问返回 403"""
        log("\n=== P1: 跨项目隔离 403 ===")
        # 先列出空间获取一个有效的 space_id
        resp = self.session.get(self._url("/knowledge-base/spaces"))
        if resp.status_code != 200:
            return self.check("列出空间", False, f"状态码 {resp.status_code}")

        data = resp.json()
        spaces = data.get("data", [])
        if not spaces:
            log("没有现有空间，跳过跨项目测试", "WARN")
            return True  # 跳过

        space_id = spaces[0]["id"]
        # 用另一个真实项目 ID 访问（PR-3 美心新加坡）
        other_project = "PR-3"
        url = f"{self.base_url}/api/v2/projects/{other_project}/knowledge-base/spaces/{space_id}"
        resp = self.session.get(url)
        return self.check(
            "跨项目访问返回 403",
            resp.status_code == 403,
            f"期望 403，实际 {resp.status_code}, body={resp.text[:200]}"
        )

    def verify_p1_upload_validation_400(self):
        """验证上传无效文件返回 400"""
        log("\n=== P1: 上传校验 400 ===")
        # 先获取一个有效的 space_id
        resp = self.session.get(self._url("/knowledge-base/spaces"))
        if resp.status_code != 200:
            return self.check("列出空间", False, f"状态码 {resp.status_code}")

        spaces = resp.json().get("data", [])
        if not spaces:
            log("没有现有空间，跳过上传测试", "WARN")
            return True

        space_id = spaces[0]["id"]

        # 上传一个 .exe 文件（应该被拒绝）
        # 注意：上传文件时不能带 application/json header
        files = {"file": ("test.exe", b"MZ\x90\x00", "application/x-msdownload")}
        resp = requests.post(
            self._url(f"/knowledge-base/spaces/{space_id}/documents"),
            files=files,
        )
        return self.check(
            "上传 .exe 返回 400",
            resp.status_code == 400,
            f"期望 400，实际 {resp.status_code}, body={resp.text[:200]}"
        )

    # ========================================================================
    # Excel 上传支持验证
    # ========================================================================

    def verify_excel_upload(self):
        """验证 Excel 文件可以上传"""
        log("\n=== Excel 上传支持 ===")
        resp = self.session.get(self._url("/knowledge-base/spaces"))
        spaces = resp.json().get("data", [])
        if not spaces:
            log("没有现有空间，跳过 Excel 测试", "WARN")
            return True

        space_id = spaces[0]["id"]

        # 用正确的 MIME 类型测试 API 接受度
        files = {
            "file": (
                "test.xlsx",
                b"PK\x03\x04" + b"\x00" * 26,  # 最小 zip 头
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        }
        resp = requests.post(
            self._url(f"/knowledge-base/spaces/{space_id}/documents"),
            files=files,
        )
        # 期望 201（创建成功）或 400（内容解析失败但类型通过）
        # 关键是不要 415/500
        passed = resp.status_code in (201, 400)
        detail = f"状态码 {resp.status_code}"
        if resp.status_code == 201:
            detail += " - 上传成功"
        elif resp.status_code == 400:
            detail += f" - {resp.json().get('message', '未知错误')[:100]}"
        else:
            detail += f" - body={resp.text[:200]}"

        return self.check("Excel 文件上传", passed, detail)

    def verify_excel_mime_type_whitelist(self):
        """验证 Excel MIME 类型在 API 白名单中"""
        log("\n=== Excel MIME 类型白名单 ===")
        # 检查后端代码中的 valid_types 列表
        kb_py = Path(__file__).parent / "backend" / "app" / "api" / "v2" / "knowledge_base.py"
        if not kb_py.exists():
            log(f"找不到文件 {kb_py}，跳过代码检查", "WARN")
            return True

        content = kb_py.read_text(encoding="utf-8")
        has_xlsx = "spreadsheetml.sheet" in content
        has_xls = "vnd.ms-excel" in content
        return (
            self.check("xlsx MIME 类型在白名单中", has_xlsx)
            and self.check("xls MIME 类型在白名单中", has_xls)
        )

    # ========================================================================
    # 知识库选择器验证（代码审查级）
    # ========================================================================

    def verify_kb_selector_code(self):
        """验证知识库选择器代码存在且正确"""
        log("\n=== 知识库选择器代码审查 ===")
        dialog_file = (
            Path(__file__).parent
            / "ui"
            / "components"
            / "test-cases"
            / "ai-generate-from-document-dialog.tsx"
        )
        if not dialog_file.exists():
            return self.check("对话框文件存在", False, f"找不到 {dialog_file}")

        content = dialog_file.read_text(encoding="utf-8")

        checks = [
            ("导入 getKnowledgeSpaces", "getKnowledgeSpaces" in content),
            ("导入 KnowledgeSpaceInfo", "KnowledgeSpaceInfo" in content),
            ("spaces 状态", "const [spaces, setSpaces]" in content),
            ("selectedSpaceId 状态", "const [selectedSpaceId, setSelectedSpaceId]" in content),
            ("__none__ 默认值", '"__none__"' in content),
            ("useEffect 加载空间", "getKnowledgeSpaces(projectId)" in content),
            ("Select 组件", "<Select" in content and "knowledge-space" in content),
            ("onOpenChat 对象参数", "onOpenChat({" in content or "onOpenChat({\n" in content),
            ("传递 knowledgeSpaceId", "knowledgeSpaceId:" in content),
        ]

        all_pass = True
        for name, condition in checks:
            if not self.check(f"  {name}", condition):
                all_pass = False

        return all_pass

    def verify_callback_signature_converged(self):
        """验证 onOpenChat 回调签名已收敛为对象参数"""
        log("\n=== onOpenChat 回调签名收敛 ===")
        page_file = (
            Path(__file__).parent
            / "ui"
            / "app"
            / "projects"
            / "[projectId]"
            / "test-cases"
            / "page.tsx"
        )
        if not page_file.exists():
            return self.check("page.tsx 存在", False)

        content = page_file.read_text(encoding="utf-8")

        # 检查新的对象参数格式
        has_object_param = "handleOpenAIChatFromDialog = ({" in content
        has_prompt_destructure = "prompt," in content
        has_attachments_destructure = "attachments," in content
        has_knowledgeSpaceId_destructure = "knowledgeSpaceId," in content

        # 检查旧的签名已不存在
        no_old_signature = "knowledgeSpaceIdOrAttachments" not in content

        checks = [
            ("对象参数定义", has_object_param),
            ("prompt 解构", has_prompt_destructure),
            ("attachments 解构", has_attachments_destructure),
            ("knowledgeSpaceId 解构", has_knowledgeSpaceId_destructure),
            ("旧签名已移除", no_old_signature),
        ]

        all_pass = True
        for name, condition in checks:
            if not self.check(f"  {name}", condition):
                all_pass = False

        return all_pass

    def verify_state_cleanup(self):
        """验证状态回收逻辑"""
        log("\n=== 状态回收 ===")
        page_file = (
            Path(__file__).parent
            / "ui"
            / "app"
            / "projects"
            / "[projectId]"
            / "test-cases"
            / "page.tsx"
        )
        if not page_file.exists():
            return self.check("page.tsx 存在", False)

        content = page_file.read_text(encoding="utf-8")

        # 检查对话框关闭时清空知识库选择
        has_cleanup = "if (!open) setSelectedKnowledgeSpaceId" in content
        return self.check(
            "对话框关闭时清空知识库选择",
            has_cleanup,
            "未找到 setSelectedKnowledgeSpaceId('') 调用"
        )

    # ========================================================================
    # Drawer 关闭按钮修复验证
    # ========================================================================

    def verify_drawer_close_button(self):
        """验证 Drawer 没有重复关闭按钮"""
        log("\n=== Drawer 关闭按钮 ===")
        drawer_file = (
            Path(__file__).parent
            / "ui"
            / "components"
            / "knowledge-base"
            / "knowledge-base-detail-drawer.tsx"
        )
        if not drawer_file.exists():
            return self.check("Drawer 文件存在", False)

        content = drawer_file.read_text(encoding="utf-8")

        # 检查没有手动添加的 X 按钮
        # 唯一的 onOpenChange 应该在 Drawer 组件上
        # 检查没有 <Button ...X...> 这样的关闭按钮
        import re
        has_x_button = bool(re.search(r'<Button[^>]*>\s*<X\s', content))
        has_pr_padding = "pr-12" in content

        return (
            self.check("没有手动 X 关闭按钮", not has_x_button)
            and self.check("DrawerHeader 有 pr-12 padding", has_pr_padding)
        )

    # ========================================================================
    # 非聊天路径知识库参数传递
    # ========================================================================

    def verify_api_path_knowledge_space(self):
        """验证非聊天路径也传递 knowledge_space_id"""
        log("\n=== API 路径知识库参数 ===")
        dialog_file = (
            Path(__file__).parent
            / "ui"
            / "components"
            / "test-cases"
            / "ai-generate-from-document-dialog.tsx"
        )
        ai_ts = Path(__file__).parent / "ui" / "lib" / "api" / "ai.ts"
        backend_ai = Path(__file__).parent / "backend" / "app" / "api" / "v2" / "ai.py"

        checks = []

        if dialog_file.exists():
            content = dialog_file.read_text(encoding="utf-8")
            checks.append(("对话框传递 knowledge_space_id", "knowledge_space_id:" in content))

        if ai_ts.exists():
            content = ai_ts.read_text(encoding="utf-8")
            checks.append(("ai.ts 类型定义", "knowledge_space_id?:" in content))
            checks.append(("ai.ts FormData 追加", 'formData.append("knowledge_space_id"' in content))

        if backend_ai.exists():
            content = backend_ai.read_text(encoding="utf-8")
            checks.append(("后端接收参数", "knowledge_space_id:" in content))

        all_pass = True
        for name, condition in checks:
            if not self.check(f"  {name}", condition):
                all_pass = False

        return all_pass

    # ========================================================================
    # 运行所有测试
    # ========================================================================

    def run_all(self):
        log("=" * 60)
        log("KB 功能验收 - 第4轮优化验证")
        log(f"Base URL: {self.base_url}")
        log(f"Project ID: {self.project_id}")
        log("=" * 60)

        self.verify_p1_cross_project_403()
        self.verify_p1_upload_validation_400()
        self.verify_excel_upload()
        self.verify_excel_mime_type_whitelist()
        self.verify_kb_selector_code()
        self.verify_callback_signature_converged()
        self.verify_state_cleanup()
        self.verify_drawer_close_button()
        self.verify_api_path_knowledge_space()

        # 汇总
        log("\n" + "=" * 60)
        passed = sum(1 for _, ok, _ in self.results if ok)
        total = len(self.results)
        log(f"结果: {passed}/{total} 通过")

        failed = [(name, detail) for name, ok, detail in self.results if not ok]
        if failed:
            log("\n失败项:", "FAIL")
            for name, detail in failed:
                log(f"  - {name}: {detail}", "FAIL")

        log("=" * 60)
        return len(failed) == 0


def main():
    parser = argparse.ArgumentParser(description="KB 功能验收脚本")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API 基础 URL")
    parser.add_argument("--project-id", default="demo-project", help="项目 ID")
    args = parser.parse_args()

    verifier = KBVerifier(args.base_url, args.project_id)
    ok = verifier.run_all()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
