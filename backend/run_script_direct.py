#!/usr/bin/env python3
"""
直接调用 execute_web_script 工具执行测试脚本
"""
import asyncio
import sys
import os
import json

# 添加 backend 到路径
sys.path.insert(0, "d:/code/Pyproject/ai-test-agent-system-platform/backend")

os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@localhost:5432/test_management_db?sslmode=disable"
os.environ["MINIO_ENDPOINT"] = "localhost:9000"
os.environ["MINIO_ACCESS_KEY"] = "minioadmin"
os.environ["MINIO_SECRET_KEY"] = "minioadmin"
os.environ["MINIO_BUCKET_NAME"] = "test-management"
os.environ["MINIO_SECURE"] = "false"

async def main():
    from app.agents.tools.web.execution_tools import execute_web_script

    local_path = "d:/code/Pyproject/ai-test-agent-system-platform/backend/workspace/web_cli/tests/test-script.spec.ts"

    print(f"[执行器] 开始执行脚本: {local_path}")
    print(f"[执行器] 参数: project_identifier=PR-6, sub_function_id=d4d9f85a-a461-4390-864a-2eb4274cf2f2")

    result = await execute_web_script.ainvoke({
        "local_script_path": local_path,
        "framework": "auto",
        "reporter": "html",
        "project_identifier": "PR-6",
        "sub_function_id": "d4d9f85a-a461-4390-864a-2eb4274cf2f2",
        "workspace_mode": "web_cli",
    })

    print("\n" + "="*60)
    print("执行结果:")
    print("="*60)
    print(result)

    # 解析结果
    try:
        result_dict = json.loads(result)
        print("\n" + "="*60)
        print("解析结果:")
        print("="*60)
        print(f"成功: {result_dict.get('success')}")
        print(f"脚本路径: {result_dict.get('script_path')}")
        print(f"检测框架: {result_dict.get('detected_framework')}")
        if 'execution_result' in result_dict:
            er = result_dict['execution_result']
            print(f"返回码: {er.get('return_code')}")
            print(f"超时: {er.get('timed_out')}")
            print(f"耗时: {er.get('duration_seconds')} 秒")
            print(f"stdout 前500字符: {er.get('stdout', '')[:500]}")
            if er.get('stderr'):
                print(f"stderr 前500字符: {er.get('stderr', '')[:500]}")
        if 'report_attachment_ids' in result_dict:
            print(f"报告附件ID: {result_dict['report_attachment_ids']}")
        if 'error' in result_dict:
            print(f"错误: {result_dict['error']}")
    except Exception as e:
        print(f"解析结果失败: {e}")

if __name__ == "__main__":
    asyncio.run(main())
