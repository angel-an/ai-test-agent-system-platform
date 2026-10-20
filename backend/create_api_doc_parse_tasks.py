"""一次性建表：api_doc_parse_tasks（幂等）。

本地 alembic 版本链已断裂（DB head=006，与 0015+ 全命名链脱节），
无法安全 alembic upgrade。此脚本用 CREATE TABLE IF NOT EXISTS 幂等建表，
与 0018_api_doc_parse_tasks.py migration 保持一致，供立即使用。
"""

from __future__ import annotations

import psycopg2

from app.config.settings import settings

DDL = """
CREATE TABLE IF NOT EXISTS api_doc_parse_tasks (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    attachment_id UUID REFERENCES attachments(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    doc_type VARCHAR(30) NOT NULL DEFAULT 'excel_mx',
    result JSONB,
    error TEXT,
    created_by VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_api_doc_parse_tasks_project_id ON api_doc_parse_tasks(project_id);
CREATE INDEX IF NOT EXISTS ix_api_doc_parse_tasks_attachment_id ON api_doc_parse_tasks(attachment_id);
CREATE INDEX IF NOT EXISTS ix_api_doc_parse_tasks_status ON api_doc_parse_tasks(status);
"""

DSN = settings.postgres_sync_url


def main() -> None:
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(DDL)
    cur.execute("SELECT to_regclass('api_doc_parse_tasks')")
    print("api_doc_parse_tasks:", "OK" if cur.fetchone()[0] else "FAILED")
    conn.close()


if __name__ == "__main__":
    main()
