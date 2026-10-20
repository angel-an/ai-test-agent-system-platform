"""
MinIO 数据迁移到本地备份脚本

在重启服务前，将已生成的 Web 测试成果物从 MinIO 下载并备份到本地文件系统
"""

import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

# 加载 .env 文件
from dotenv import load_dotenv
env_path = Path(__file__).parent / "app" / ".env"
load_dotenv(env_path)

from app.config.database import async_session_factory
from app.config.minio_client import MinIOClient
from app.models.attachment import Attachment
from sqlalchemy import select


def get_backup_root() -> Path:
    """获取本地备份根目录"""
    cli_root = Path(os.environ.get('WEB_CLI_WORKSPACE_ROOT', r'D:\code\Pyproject\ai-test-agent-system-platform\backend\workspace\web_cli'))
    if cli_root.exists():
        return cli_root / "artifacts_backup"
    mcp_root = Path(os.environ.get('WEB_MCP_WORKSPACE_ROOT', r'D:\code\Pyproject\ai-test-agent-system-platform\backend\workspace\web_mcp'))
    return mcp_root / "artifacts_backup"


async def migrate_attachments():
    """迁移所有 Web 测试附件到本地"""
    backup_root = get_backup_root()
    print(f"备份根目录: {backup_root}")

    async with async_session_factory() as session:
        # 查询所有 Web 测试相关的附件
        result = await session.execute(
            select(Attachment).where(
                Attachment.object_name.like('web-tests/%')
            )
        )
        attachments = result.scalars().all()

        print(f"找到 {len(attachments)} 个 Web 测试附件")

        success_count = 0
        failed_count = 0
        skipped_count = 0

        for attachment in attachments:
            object_name = attachment.object_name
            backup_path = backup_root / object_name

            # 检查本地是否已存在
            if backup_path.exists():
                print(f"  [SKIP] 已存在: {object_name}")
                skipped_count += 1
                continue

            try:
                # 从 MinIO 下载
                content_bytes = MinIOClient.download_file(object_name)

                # 确保目录存在
                backup_path.parent.mkdir(parents=True, exist_ok=True)

                # 写入本地文件
                with open(backup_path, 'wb') as f:
                    f.write(content_bytes)

                print(f"  [OK] {attachment.entity_type.value}: {object_name} ({len(content_bytes)} bytes)")
                success_count += 1

            except Exception as e:
                print(f"  [FAIL] {object_name}: {e}")
                failed_count += 1

        print(f"\n迁移完成:")
        print(f"  成功: {success_count}")
        print(f"  跳过(已存在): {skipped_count}")
        print(f"  失败: {failed_count}")
        print(f"\n备份位置: {backup_root}")


if __name__ == "__main__":
    print("=" * 60)
    print("MinIO 数据迁移到本地备份")
    print("=" * 60)
    print(f"开始时间: {datetime.now().isoformat()}")
    print()

    asyncio.run(migrate_attachments())

    print()
    print(f"结束时间: {datetime.now().isoformat()}")
    print("=" * 60)
