# -*- coding: utf-8 -*-
"""平台服务层：需求质量门控与用例生成编排。"""

from .requirement_quality_service import (
    GenerationOutcome,
    RequirementQualityService,
    TestCaseGenerationService,
    build_compatibility_report,
    build_coverage_report,
    build_requirements_section,
    query_cases_by_feature,
    resolve_sole_feature_id,
    to_platform_case,
)

__all__ = [
    "GenerationOutcome",
    "RequirementQualityService",
    "TestCaseGenerationService",
    "build_requirements_section",
    "query_cases_by_feature",
    "build_coverage_report",
    "build_compatibility_report",
    "resolve_sole_feature_id",
    "to_platform_case",
]
