# Python 编码规范 — 模块命名约束

## 1. 规则分类

### 1.1 第一类：Python 标准库冲突（高风险）

**规则**：自定义模块/包的名称**绝对禁止**与 Python 标准库模块同名。

**后果**：会导致导入遮蔽（shadowing），第三方库（如 SQLAlchemy）在 `import platform` 时会错误加载你的模块，引发 `AttributeError` 等难以排查的错误。

**检查方式**：
```bash
# 从项目根目录运行
python backend/scripts/check_stdlib_conflicts.py backend/app
```

**常见冲突名示例**：
```
platform, sys, os, io, json, re, types, copy, site, code,
string, signal, select, socket, threading, logging, email,
http, html, xml, csv, contextlib, datetime, enum,
pathlib, uuid, random, hashlib, base64, tempfile, subprocess,
shutil, glob, fnmatch, inspect, importlib, pkgutil, warnings,
traceback, typing, collections, functools, itertools, operator,
numbers, decimal, fractions, math, statistics, calendar, time
```

**注意**：`config` **不是** Python 标准库模块名（标准库对应的是 `configparser`），因此不属于第一类冲突。但如果项目内部约定避免使用，可作为第二类（项目保留名）处理。

### 1.2 第二类：项目保留名（中风险）

**规则**：项目可自定义一组保留名，扫描器支持通过 `--reserve-names` 参数检查。

**后果**：不会导致导入遮蔽，但可能造成语义混淆或内部约定不一致。

**检查方式**：
```bash
python backend/scripts/check_stdlib_conflicts.py backend/app --reserve-names config,utils,core
```

**当前项目状态**：
- `backend/app/config/` 存在，但 `config` 不是标准库名，因此标准库扫描器不会报错
- 是否将 `config` 列为项目保留名，由团队另行约定

## 2. 命名建议

| 业务含义 | ❌ 避免（标准库冲突） | ✅ 推荐 |
|---------|---------------------|--------|
| 测试平台 | `platform` | `test_platform`, `qa_platform` |
| 系统配置 | — | `app_config`, `settings` |
| 日志模块 | `logging` | `app_logging`, `log_utils` |
| 文件IO | `io` | `file_io`, `storage` |
| JSON处理 | `json` | `json_utils`, `serialization` |
| HTTP客户端 | `http` | `http_client`, `api_client` |

## 3. 问题排查

### 症状：AttributeError: module 'xxx' has no attribute 'yyy'

如果看到类似错误：

```
AttributeError: module 'platform' has no attribute 'python_implementation'
(consider renaming '.../platform/__init__.py' since it has the same name
as the standard library module named 'platform')
```

**原因**：自定义包与 Python 标准库同名，导致标准库被遮蔽。

**解决步骤**：
1. 重命名冲突的包/模块
2. 全局搜索所有引用该模块的导入语句并更新
3. 删除旧的 `.pyc` 缓存文件和 `__pycache__` 目录
4. 运行检查脚本确认无残留冲突

## 4. 自动化检查

### 本地检查（提交前必跑）

```bash
# 标准库冲突检查（必跑）
python backend/scripts/check_stdlib_conflicts.py backend/app

# 如项目有保留名约定，追加参数
python backend/scripts/check_stdlib_conflicts.py backend/app --reserve-names config,utils
```

### 单元测试

```bash
# 扫描器自身的单元测试
python -m pytest backend/tests/scripts/test_check_stdlib_conflicts.py -v
```

### pre-commit 接入（待配置）

在 `.pre-commit-config.yaml` 中添加本地钩子调用上述命令，可在提交前自动拦截冲突。

## 5. 相关文件

- `backend/scripts/check_stdlib_conflicts.py` — 命名冲突检查脚本
- `backend/tests/scripts/test_check_stdlib_conflicts.py` — 扫描器单元测试
