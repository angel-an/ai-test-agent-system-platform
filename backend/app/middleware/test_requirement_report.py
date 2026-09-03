"""Tests for RequirementReportMiddleware."""

import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import hashlib
from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage

from app.middleware.requirement_report import (
    RequirementReportMiddleware,
    _extract_step1_section,
    _ai_message_text,
    _infer_requirement_name,
    _safe_filename,
    _persist_report,
    _seen,
    _EXPORTS_DIR,
)


class TestExtractStep1Section:
    def test_no_step1_heading(self):
        text = "Some random text without step 1 heading"
        section, missing = _extract_step1_section(text)
        assert section is None
        assert missing == []

    def test_step1_only_heading_too_short(self):
        text = "## Step 1: 需求理解\n"
        section, missing = _extract_step1_section(text)
        assert section is None  # Too short (< 200 chars)

    def test_step1_with_step2(self):
        text = """## Step 1: 需求理解

### Step 1a: 文档结构确认
确认文档结构完整。

### Step 1b: 功能点提取
提取了 5 个功能点。

### Step 1c: 需求识别
识别了显性和隐性需求。

### Step 1d: 范围声明
In Scope: 用户管理模块
Out of Scope: 支付模块

### Step 1e: 用例预估
预估 20 个测试用例。

### Step 1.5: 自检
自检通过。

### 待澄清问题清单
1. 问题一

### 功能测试矩阵
| 功能点 | 优先级 |
|-------|-------|
| 登录 | 高 |

## Step 2: 测试策略

继续下一步。
"""
        section, missing = _extract_step1_section(text)
        assert section is not None
        assert len(section) >= 200
        assert "## Step 1: 需求理解" in section
        assert "## Step 2" not in section  # Should stop before Step 2
        assert missing == []  # All subsections present

    def test_step1_without_step2(self):
        text = """## Step 1: 需求理解

### Step 1a: 文档结构确认
确认文档结构完整，包含所有章节。

### Step 1b: 功能点提取
提取了 5 个功能点：登录、注册、个人中心、设置、帮助。

### Step 1c: 需求识别
识别了显性和隐性需求，包括功能性需求和非功能性需求。

### Step 1d: 范围声明
In Scope: 用户管理模块、订单管理模块
Out of Scope: 支付模块、第三方集成

### Step 1e: 用例预估
预估 20 个测试用例，按功能点分布。

### Step 1.5: 自检
自检通过，所有检查项均满足。

### 待澄清问题清单
1. 问题一：关于用户权限的具体定义

### 功能测试矩阵
| 功能点 | 优先级 |
|-------|-------|
| 登录 | 高 |
"""
        section, missing = _extract_step1_section(text)
        assert section is not None
        assert "## Step 2" not in section  # No Step 2 in input, should go to end

    def test_missing_subsections(self):
        text = """## Step 1: 需求理解

### Step 1a: 文档结构确认
确认文档结构完整，包含所有章节和段落，确保文档的可读性和完整性。文档应当包含清晰的需求描述、功能规格说明以及相关的业务规则定义。每个章节都应该有明确的标题和编号，方便后续引用和跟踪。

### Step 1b: 功能点提取
提取了核心功能点，包括用户管理模块的注册登录功能、订单处理模块的创建查询功能、数据统计模块的报表导出功能等关键业务功能点共计十五个。每个功能点都对应具体的业务场景和用户需求。

## Step 2: 测试策略

接下来进行测试策略制定，包括测试范围界定、测试方法选择、测试资源分配以及测试进度安排等关键内容，确保测试活动能够覆盖所有核心功能点并按时完成交付。
"""
        section, missing = _extract_step1_section(text)
        assert section is not None
        assert len(missing) > 0
        assert "Step 1c" in missing
        assert "Step 1d" in missing
        assert "Step 1e" in missing


class TestAiMessageText:
    def test_string_content(self):
        msg = AIMessage(content="Hello world")
        assert _ai_message_text(msg) == "Hello world"

    def test_list_content(self):
        msg = AIMessage(content=["Hello", "world"])
        assert _ai_message_text(msg) == "Hello\nworld"

    def test_dict_content(self):
        msg = AIMessage(content=[{"text": "Hello"}, {"text": "world"}])
        assert _ai_message_text(msg) == "Hello\nworld"

    def test_mixed_content(self):
        msg = AIMessage(content=["Hello", {"text": "world"}])
        assert _ai_message_text(msg) == "Hello\nworld"


class TestInferRequirementName:
    def test_from_human_message_attachment(self):
        section = "some content"
        msg = HumanMessage(
            content="test",
            additional_kwargs={
                "attachments": [
                    {"metadata": {"filename": "user_login_requirements.docx"}}
                ]
            }
        )
        name = _infer_requirement_name(section, [msg])
        assert name == "user_login_requirements"

    def test_from_section_title(self):
        section = "**需求名称**：用户登录系统\n其他内容"
        name = _infer_requirement_name(section, [])
        assert name == "用户登录系统"

    def test_fallback(self):
        section = "just some content"
        name = _infer_requirement_name(section, [])
        assert name == "需求名称"


class TestSafeFilename:
    def test_normal_chars(self):
        assert _safe_filename("hello world") == "hello world"

    def test_invalid_chars(self):
        assert _safe_filename("hello/world:test") == "hello_world_test"

    def test_empty(self):
        assert _safe_filename("") == "需求分析"


class TestPersistReport:
    def setup_method(self):
        # Clear cache before each test
        _seen.clear()

    def test_persist_new_report(self):
        section = """## Step 1: 需求理解

### Step 1a: 文档结构确认
确认文档结构完整，包含所有章节。

### Step 1b: 功能点提取
提取了 5 个功能点：登录、注册、个人中心、设置、帮助。

### Step 1c: 需求识别
识别了显性和隐性需求，包括功能性需求和非功能性需求。

### Step 1d: 范围声明
In Scope: 用户管理模块、订单管理模块
Out of Scope: 支付模块、第三方集成

### Step 1e: 用例预估
预估 20 个测试用例，按功能点分布。

### Step 1.5: 自检
自检通过，所有检查项均满足。

### 待澄清问题清单
1. 问题一：关于用户权限的具体定义

### 功能测试矩阵
| 功能点 | 优先级 |
|-------|-------|
| 登录 | 高 |
"""
        result = _persist_report("thread_123", section, "测试需求", [])
        assert result is not None
        assert result.endswith(".md")
        assert "测试需求" in result

        # Verify file was created
        output_path = _EXPORTS_DIR / result
        assert output_path.exists()

        # Clean up
        output_path.unlink()

    def test_deduplication(self):
        section = """## Step 1: 需求理解

### Step 1a: 文档结构确认
确认文档结构完整，包含所有章节。

### Step 1b: 功能点提取
提取了 5 个功能点：登录、注册、个人中心、设置、帮助。

### Step 1c: 需求识别
识别了显性和隐性需求，包括功能性需求和非功能性需求。

### Step 1d: 范围声明
In Scope: 用户管理模块、订单管理模块
Out of Scope: 支付模块、第三方集成

### Step 1e: 用例预估
预估 20 个测试用例，按功能点分布。

### Step 1.5: 自检
自检通过，所有检查项均满足。

### 待澄清问题清单
1. 问题一：关于用户权限的具体定义

### 功能测试矩阵
| 功能点 | 优先级 |
|-------|-------|
| 登录 | 高 |
"""
        result1 = _persist_report("thread_123", section, "测试需求", [])
        assert result1 is not None

        # Same content, same thread - should be deduplicated
        result2 = _persist_report("thread_123", section, "测试需求", [])
        assert result2 is None  # Deduplicated

        # Clean up
        if result1:
            output_path = _EXPORTS_DIR / result1
            if output_path.exists():
                output_path.unlink()


class TestMiddleware:
    def test_init(self):
        mw = RequirementReportMiddleware()
        assert mw is not None

    def test_after_model_no_messages(self):
        mw = RequirementReportMiddleware()
        result = mw.after_model({}, None)
        assert result is None  # Should not crash

    def test_after_model_no_ai_message(self):
        mw = RequirementReportMiddleware()
        state = {"messages": [HumanMessage(content="Hello")]}
        result = mw.after_model(state, None)
        assert result is None  # Should not crash

    def test_after_model_with_step1(self):
        mw = RequirementReportMiddleware()
        content = """## Step 1: 需求理解

### Step 1a: 文档结构确认
确认文档结构完整，包含所有章节。

### Step 1b: 功能点提取
提取了 5 个功能点：登录、注册、个人中心、设置、帮助。

### Step 1c: 需求识别
识别了显性和隐性需求，包括功能性需求和非功能性需求。

### Step 1d: 范围声明
In Scope: 用户管理模块、订单管理模块
Out of Scope: 支付模块、第三方集成

### Step 1e: 用例预估
预估 20 个测试用例，按功能点分布。

### Step 1.5: 自检
自检通过，所有检查项均满足。

### 待澄清问题清单
1. 问题一：关于用户权限的具体定义

### 功能测试矩阵
| 功能点 | 优先级 |
|-------|-------|
| 登录 | 高 |
"""
        state = {"messages": [AIMessage(content=content)]}
        result = mw.after_model(state, None)
        assert result is None  # Should not crash


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
