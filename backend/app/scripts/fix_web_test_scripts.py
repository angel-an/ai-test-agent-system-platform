#!/usr/bin/env python3
"""
修复天味项目 Web 测试脚本不展示问题

问题：测试脚本文件存在于文件系统备份目录中，但数据库 attachments 表中缺少对应记录
修复：扫描备份目录中的测试脚本文件，为缺少数据库记录的文件创建 Attachment 记录

用法:
    cd backend
    python -m app.scripts.fix_web_test_scripts

或者:
    python backend/app/scripts/fix_web_test_scripts.py
"""

import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID
from datetime import datetime, timezone

# 添加项目根目录到路径
backend_dir = Path(__file__).resolve().parent.parent.parent
project_root = backend_dir.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(backend_dir))  # 添加backend目录以便导入app模块

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import async_session_factory
from app.models.attachment import Attachment, AttachmentEntityType
from app.models.web_function import WebSubFunction, WebFunction
from app.models.project import Project
from app.config.minio_client import MinIOClient


async def fix_web_test_scripts():
    """
    扫描备份目录中的测试脚本，为缺少数据库记录的文件创建 Attachment 记录
    """
    print("=" * 60)
    print("开始修复 Web 测试脚本不展示问题")
    print("=" * 60)

    # 备份目录
    backup_root = Path(project_root) / "backend" / "workspace" / "web_cli" / "artifacts_backup" / "web-tests"

    if not backup_root.exists():
        print(f"❌ 备份目录不存在: {backup_root}")
        return

    print(f"📁 备份目录: {backup_root}")
    print()

    async with async_session_factory() as session:
        # 统计
        total_found = 0
        total_fixed = 0
        total_already_exists = 0
        total_errors = 0

        # 遍历所有项目目录 (PR-1, PR-2, PR-6, 等)
        for project_dir in sorted(backup_root.iterdir()):
            if not project_dir.is_dir():
                continue

            project_identifier = project_dir.name
            print(f"\n📂 项目: {project_identifier}")

            # 查找子功能目录
            sub_functions_dir = project_dir / "sub-functions"
            if not sub_functions_dir.exists():
                print(f"   ⏭️  跳过: 没有 sub-functions 目录")
                continue

            # 获取项目ID
            project_result = await session.execute(
                select(Project).where(Project.identifier == project_identifier)
            )
            project = project_result.scalar_one_or_none()

            if not project:
                print(f"   ⚠️  警告: 数据库中找不到项目 {project_identifier}")
                continue

            print(f"   🆔 项目ID: {project.id}")

            # 遍历子功能目录
            for sub_func_dir in sorted(sub_functions_dir.iterdir()):
                if not sub_func_dir.is_dir():
                    continue

                sub_function_id = sub_func_dir.name

                try:
                    sub_function_uuid = UUID(sub_function_id)
                except ValueError:
                    print(f"   ⚠️  跳过: 无效的子功能ID: {sub_function_id}")
                    continue

                # 验证子功能是否存在
                sub_function_result = await session.execute(
                    select(WebSubFunction).where(WebSubFunction.id == sub_function_uuid)
                )
                sub_function = sub_function_result.scalar_one_or_none()

                if not sub_function:
                    print(f"   ⚠️  跳过: 数据库中找不到子功能 {sub_function_id}")
                    continue

                # 查找测试脚本文件
                script_files = list(sub_func_dir.glob("test-script*"))
                if not script_files:
                    continue

                for script_file in script_files:
                    total_found += 1
                    print(f"\n   📄 脚本文件: {script_file.name}")
                    print(f"      子功能: {sub_function.display_name} ({sub_function_id})")

                    # 构建 object_name (与 save_web_test_script 一致)
                    extension = script_file.suffix.lstrip('.') or "ts"
                    object_name = f"web-tests/{project_identifier}/sub-functions/{sub_function_id}/test-script.{extension}"

                    # 检查数据库中是否已存在
                    existing_result = await session.execute(
                        select(Attachment).where(
                            and_(
                                Attachment.object_name == object_name,
                                Attachment.entity_id == sub_function_uuid
                            )
                        )
                    )
                    existing = existing_result.scalar_one_or_none()

                    if existing:
                        print(f"      ✅ 数据库中已存在，跳过")
                        total_already_exists += 1
                        continue

                    # 读取文件内容
                    try:
                        content = script_file.read_text(encoding='utf-8')
                        content_bytes = content.encode('utf-8')
                    except Exception as e:
                        print(f"      ❌ 读取文件失败: {e}")
                        total_errors += 1
                        continue

                    # 上传到 MinIO
                    try:
                        MinIOClient.upload_bytes(
                            object_name=object_name,
                            data=content_bytes,
                            content_type="text/plain"
                        )
                        print(f"      ✅ 已上传到 MinIO: {object_name}")
                    except Exception as e:
                        print(f"      ⚠️  MinIO 上传失败 (可能已存在): {e}")

                    # 创建 Attachment 记录
                    try:
                        attachment = Attachment(
                            entity_type=AttachmentEntityType.WEB_TEST_SCRIPT,
                            entity_id=sub_function_uuid,
                            project_id=project.id,
                            file_name=f"test-script.{extension}",
                            file_size=len(content_bytes),
                            content_type="text/plain",
                            object_name=object_name,
                            description=f"Web 子功能 {sub_function.display_name} 的测试脚本 (playwright - typescript)",
                            created_by="fix-script"
                        )
                        session.add(attachment)
                        await session.commit()
                        await session.refresh(attachment)

                        print(f"      ✅ 已创建数据库记录: {attachment.id}")
                        total_fixed += 1

                    except Exception as e:
                        await session.rollback()
                        print(f"      ❌ 创建数据库记录失败: {e}")
                        total_errors += 1

        print("\n" + "=" * 60)
        print("修复完成!")
        print("=" * 60)
        print(f"📊 统计:")
        print(f"   找到脚本文件: {total_found}")
        print(f"   已修复 (新增记录): {total_fixed}")
        print(f"   已存在 (跳过): {total_already_exists}")
        print(f"   错误: {total_errors}")
        print()

        if total_fixed > 0:
            print("✅ 修复成功! 请刷新前端页面查看测试脚本")
        elif total_found == 0:
            print("⚠️  未找到任何测试脚本文件")
        else:
            print("ℹ️  所有脚本文件已有数据库记录")


if __name__ == "__main__":
    asyncio.run(fix_web_test_scripts())
