#!/usr/bin/env python3
"""
运行 LangGraph PostgreSQL 迁移脚本

按顺序执行 storage/migrations/ 下的所有 .sql 文件
"""

import os
import sys
import glob
import re
from pathlib import Path

try:
    import psycopg
except ImportError:
    print("ERROR: 请先安装 psycopg: pip install psycopg")
    sys.exit(1)

# 数据库连接配置（从 .env 读取或硬编码）
POSTGRES_URI = os.environ.get("POSTGRES_URI", "postgres://postgres:postgres@127.0.0.1:5432/langgraph_platform_db")
MIGRATIONS_DIR = Path(__file__).parent / "storage" / "migrations"


def split_sql_statements(sql: str) -> list[str]:
    """将多语句 SQL 拆分为单个语句列表"""
    statements = []
    current = []
    in_dollar_quote = False
    dollar_tag = ""
    
    lines = sql.splitlines()
    for line in lines:
        stripped = line.strip()
        # 跳过空行和注释
        if not stripped or stripped.startswith("--"):
            continue
        
        current.append(line)
        
        # 检测美元引号开始/结束
        if not in_dollar_quote:
            match = re.search(r'\$(\w*)\$', stripped)
            if match and stripped.count('$' + match.group(1) + '$') % 2 == 1:
                in_dollar_quote = True
                dollar_tag = match.group(1)
        else:
            if '$' + dollar_tag + '$' in stripped:
                in_dollar_quote = False
                dollar_tag = ""
        
        # 如果行以分号结尾且不在美元引号内，则作为一个完整语句
        if not in_dollar_quote and stripped.endswith(';'):
            stmt = '\n'.join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
    
    # 处理最后没有分号的语句
    if current:
        stmt = '\n'.join(current).strip()
        if stmt:
            statements.append(stmt)
    
    return statements


def run_migrations():
    print(f"开始运行迁移到: {POSTGRES_URI}")
    
    # 获取所有 .sql 迁移文件并排序
    sql_files = sorted(glob.glob(str(MIGRATIONS_DIR / "*.sql")))
    
    if not sql_files:
        print(f"ERROR: 未找到迁移文件: {MIGRATIONS_DIR}")
        sys.exit(1)
    
    print(f"发现 {len(sql_files)} 个迁移文件")
    
    conn = psycopg.connect(POSTGRES_URI, autocommit=True)
    try:
        with conn.cursor() as cur:
            # 创建迁移记录表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            
            for sql_file in sql_files:
                version = Path(sql_file).stem
                
                # 检查是否已执行
                cur.execute("SELECT 1 FROM schema_migrations WHERE version = %s", (version,))
                if cur.fetchone():
                    print(f"  SKIP (已执行): {version}")
                    continue
                
                print(f"  RUN: {version}")
                
                with open(sql_file, "r", encoding="utf-8") as f:
                    sql = f.read()
                
                statements = split_sql_statements(sql)
                
                try:
                    for stmt in statements:
                        if not stmt.strip():
                            continue
                        cur.execute(stmt)
                    
                    cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
                    print(f"  OK: {version} ({len(statements)} 条语句)")
                except Exception as e:
                    print(f"  FAIL: {version} - {e}")
                    raise
    finally:
        conn.close()
    
    print("\n所有迁移执行完成！")


if __name__ == "__main__":
    run_migrations()
