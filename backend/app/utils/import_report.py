"""
导入已有 Markdown 测试报告到系统

将 backend/workspace/webwright/test_report_run_004.md 转换为 HTML 可视化报告，
并保存到 MinIO 和数据库中，让前端可以展示。

使用方法:
    cd backend
    python -m app.utils.import_report
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
backend_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(backend_dir))

from app.utils.markdown_report_converter import MarkdownReportConverter, import_markdown_report


# 子功能 ID 映射（从 Markdown 报告中提取）
# 这些 ID 来自报告中的 "测试产物关联" 部分
SUB_FUNCTION_IDS = {
    "会员管理": "9fa66b24-2b67-4c16-add0-d5a121a964e5",
    "标签管理": "7bd772ca-d99a-4ad9-8aa8-3a216216a79d",
    "人群包管理": "a57d3913-c28b-4d89-81fe-e3fbc984d0a3",
    "自定义标签": "3090c1cf-79e7-4174-bff0-a6d55302bf35",
}

# Markdown 报告路径
REPORT_PATH = backend_dir / "workspace" / "webwright" / "test_report_run_004.md"

# 项目标识符
PROJECT_IDENTIFIER = "PR-2"


async def main():
    """主函数"""
    print("=" * 60)
    print("[导入] Markdown 测试报告到系统")
    print("=" * 60)

    # 检查文件是否存在
    if not REPORT_PATH.exists():
        print(f"[错误] 报告文件不存在: {REPORT_PATH}")
        return

    print(f"[报告] 文件: {REPORT_PATH}")
    print(f"[项目] {PROJECT_IDENTIFIER}")
    print(f"[子功能] 映射:")
    for name, sf_id in SUB_FUNCTION_IDS.items():
        print(f"   - {name}: {sf_id}")
    print()

    # 导入报告
    results = await import_markdown_report(
        md_file_path=str(REPORT_PATH),
        project_identifier=PROJECT_IDENTIFIER,
        sub_function_ids=SUB_FUNCTION_IDS
    )

    print()
    print("=" * 60)
    print("[结果] 导入结果")
    print("=" * 60)

    success_count = 0
    error_count = 0

    for result in results:
        if "error" in result and not result.get("success"):
            print(f"[错误] {result['error']}")
            if "module_name" in result:
                print(f"   模块: {result['module_name']}")
            error_count += 1
        elif result.get("success"):
            print(f"[成功] 导入报告")
            print(f"   模块: {result['sub_function_name']}")
            print(f"   附件 ID: {result['attachment_id']}")
            print(f"   文件名: {result['file_name']}")
            print(f"   查看 URL: /api/v2/attachments/{result['attachment_id']}/report-html")
            success_count += 1
        print()

    print("=" * 60)
    print(f"[统计] 总计: {success_count} 成功, {error_count} 失败")
    print("=" * 60)

    if success_count > 0:
        print()
        print("[提示]")
        print("   1. 刷新前端页面，在 Web 测试页面中查看成果物")
        print("   2. 点击 '测试报告' 类型的 '查看' 按钮即可查看可视化报告")
        print("   3. 报告包含 Chart.js 图表：饼图、柱状图等")


if __name__ == "__main__":
    asyncio.run(main())
