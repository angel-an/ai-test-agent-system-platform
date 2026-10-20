# -*- coding: utf-8 -*-
"""deprecated=true 废弃标记的共享判定（2026-09-29 复核：查询/执行层必须过滤）。

口径：默认排除、显式可查——执行链路与指标统计排除；默认列表/历史报告保留可见。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.deprecation import filter_active, is_deprecated


def _case(identifier: str, *, deprecated: bool | None = None):
    fields = {}
    if deprecated is not None:
        fields["deprecated"] = deprecated
    return SimpleNamespace(identifier=identifier, custom_fields=fields or None)


class TestDeprecation:
    def test_missing_field_means_not_deprecated(self):
        assert is_deprecated(_case("TC-1")) is False
        assert is_deprecated(_case("TC-2", deprecated=False)) is False

    def test_deprecated_true(self):
        assert is_deprecated(_case("TC-3", deprecated=True)) is True

    def test_filter_active(self):
        cases = [
            _case("A"),
            _case("B", deprecated=True),
            _case("C", deprecated=False),
        ]
        assert [c.identifier for c in filter_active(cases)] == ["A", "C"]

    def test_filter_active_empty_and_none_fields(self):
        assert filter_active([]) == []
        # custom_fields 为 None 的对象不报错
        assert filter_active([SimpleNamespace(identifier="X", custom_fields=None)]) != []
