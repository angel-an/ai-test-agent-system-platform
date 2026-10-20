#!/usr/bin/env python3
"""
验证 MinIOClient 本地回退机制
"""
import sys
import os

sys.path.insert(0, "d:/code/Pyproject/ai-test-agent-system-platform/backend")

os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@localhost:5432/test_management_db?sslmode=disable"
os.environ["MINIO_ENDPOINT"] = "localhost:9000"
os.environ["MINIO_ACCESS_KEY"] = "minioadmin"
os.environ["MINIO_SECRET_KEY"] = "minioadmin"
os.environ["MINIO_BUCKET_NAME"] = "test-management"
os.environ["MINIO_SECURE"] = "false"

async def main():
    from app.config.minio_client import MinIOClient

    object_name = "web-tests/PR-6/sub-functions/d4d9f85a-a461-4390-864a-2eb4274cf2f2/test-report-html-20260714_124444.html"

    print(f"[测试] 尝试下载: {object_name}")
    try:
        data = MinIOClient.download_file(object_name)
        print(f"[测试] 成功！读取到 {len(data)} bytes")
        print(f"[测试] 内容前100字符: {data[:100]}")
    except Exception as e:
        print(f"[测试] 失败: {e}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
