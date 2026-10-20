import sys, os
sys.path.insert(0, '.')
os.environ['EMBEDDING_BINDING_API_KEY'] = 'sk-6cwnCNPVwFMwPgZPk0ejyQ'

from app.config.settings import get_settings
get_settings.cache_clear()

import asyncio
from uuid import UUID
from app.config.database import async_session_factory
from app.services.knowledge_base_service import KnowledgeBaseService
from app.services.knowledge_indexer import KnowledgeIndexer
from app.models.knowledge_document import KnowledgeDocument, DocumentStatus
from sqlalchemy import select

async def full_setup():
    async with async_session_factory() as session:
        service = KnowledgeBaseService(session)

        # 检查是否已有空间
        result = await session.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.file_name.like('%小杨生煎%')
            )
        )
        existing = result.scalars().all()
        if existing:
            print(f"找到已有文档: {len(existing)} 个")
            for doc in existing:
                print(f"  - {doc.file_name} (space: {doc.space_id})")
            # 使用已有空间重新索引
            space_id = existing[0].space_id
        else:
            # 1. 创建知识空间
            from app.schemas.knowledge_base import KnowledgeSpaceCreate
            space = await service.create_space(
                project_identifier='PR-2',
                data=KnowledgeSpaceCreate(
                    name='小杨生煎-缺陷与需求知识库',
                    description='包含BUG列表和需求文档，用于测试用例生成时的RAG检索',
                    business_line='小杨生煎',
                ),
                created_by=UUID('00000000-0000-0000-0000-000000000001'),
            )
            space_id = space.id
            print(f"[1] 知识空间创建成功: {space_id}")

            # 2. 上传缺陷列表
            with open('D:/360Downloads/小杨生煎BUG列表0902.xlsx', 'rb') as f:
                bug_data = f.read()
            bug_doc = await service.upload_document(
                space_id=space_id,
                file_name='小杨生煎BUG列表0902.xlsx',
                file_data=bug_data,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                created_by=UUID('00000000-0000-0000-0000-000000000001'),
                title='小杨生煎BUG列表0902',
            )
            print(f"[2] 缺陷列表上传成功: {bug_doc.document_id}")

            # 3. 上传需求文档
            with open('D:/360Downloads/小杨生煎-B端储值充值.docx', 'rb') as f:
                req_data = f.read()
            req_doc = await service.upload_document(
                space_id=space_id,
                file_name='小杨生煎-B端储值充值.docx',
                file_data=req_data,
                content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                created_by=UUID('00000000-0000-0000-0000-000000000001'),
                title='小杨生煎-B端储值充值需求文档',
            )
            print(f"[3] 需求文档上传成功: {req_doc.document_id}")

        # 4. 索引所有文档
        result = await session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.space_id == space_id)
        )
        docs = result.scalars().all()
        print(f"\n[4] 开始索引 {len(docs)} 个文档...")

        indexer = KnowledgeIndexer(session)
        for doc in docs:
            if doc.status == DocumentStatus.INDEXED and doc.chunk_count > 0:
                print(f"  跳过已索引: {doc.file_name}")
                continue

            # 删除旧切片（如果有）
            from sqlalchemy import delete
            await session.execute(
                delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id)
            )
            doc.status = DocumentStatus.PENDING
            doc.chunk_count = 0
            await session.flush()

            success = await indexer.index_document(doc.id)
            status = "成功" if success else "失败"
            print(f"  索引 {doc.file_name}: {status}, 切片数: {doc.chunk_count}")

        await session.commit()
        print(f"\n[5] 全部完成，空间ID: {space_id}")
        return str(space_id)

from app.models.knowledge_chunk import KnowledgeChunk
space_id = asyncio.run(full_setup())
print(f"\n最终空间ID: {space_id}")
