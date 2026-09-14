"""
数据构建器

解析和构建请求数据，支持：
- ${stepN.field} 引用前序步骤数据
- ${random_*} 自动生成随机数据
- 环境变量注入
- 嵌套数据结构递归解析
"""

from __future__ import annotations

import os
import random
import re
import string
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.schemas.intent import DataConfig

# 随机数据生成器注册表
RANDOM_GENERATORS: Dict[str, Callable[[], Any]] = {
    "random_email": lambda: f"user_{_random_string(8)}@example.com",
    "random_phone": lambda: f"1{random.randint(3, 9)}{random.randint(100000000, 999999999)}",
    "random_uuid": lambda: str(uuid.uuid4()),
    "random_string": lambda: _random_string(10),
    "random_int": lambda: random.randint(1, 100000),
    "random_float": lambda: round(random.uniform(1.0, 100000.0), 2),
    "random_bool": lambda: random.choice([True, False]),
    "random_date": lambda: datetime.now(timezone.utc).isoformat(),
    "random_name": lambda: f"User_{_random_string(6)}",
    "random_id": lambda: f"ID{_random_string(10).upper()}",
}


def _random_string(length: int = 10) -> str:
    """生成随机字符串"""
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


class DataBuilder:
    """数据构建器 - 解析变量引用和随机数据"""

    # 变量引用模式：${step1.token} 或 ${global_var}
    STEP_REF_PATTERN = re.compile(r"\$\{step(\d+)\.([\w.]+)\}")
    VAR_REF_PATTERN = re.compile(r"\$\{(?!step\d+\.)([\w.]+)\}")
    RANDOM_PATTERN = re.compile(r"\$\{(random_\w+)\}")

    def __init__(
        self,
        step_data: Optional[Dict[str, Dict[str, Any]]] = None,
        variables: Optional[Dict[str, Any]] = None,
        environ: Optional[Dict[str, str]] = None,
    ):
        """
        Args:
            step_data: 步骤数据 {step_id: {field: value}}
            variables: 全局变量
            environ: 环境变量
        """
        self.step_data = step_data or {}
        self.variables = variables or {}
        self.environ = environ or os.environ

    def build(self, data: Optional[DataConfig]) -> Dict[str, Any]:
        """
        构建完整的请求数据

        Returns:
            {body, params, headers, path_vars} 所有值已解析
        """
        if not data:
            return {}

        result = {}

        if data.body is not None:
            result["body"] = self._resolve_value(data.body)
        if data.params is not None:
            result["params"] = self._resolve_value(data.params)
        if data.headers is not None:
            result["headers"] = self._resolve_value(data.headers)
        if data.path_vars is not None:
            result["path_vars"] = self._resolve_value(data.path_vars)

        return result

    def _resolve_value(self, value: Any) -> Any:
        """递归解析值中的变量引用"""
        if isinstance(value, str):
            return self._resolve_string(value)
        elif isinstance(value, dict):
            return {k: self._resolve_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._resolve_value(item) for item in value]
        else:
            return value

    def _resolve_string(self, s: str) -> str:
        """解析字符串中的变量引用"""
        # 1. 解析 ${stepN.field} 引用
        def step_replacer(match: re.Match) -> str:
            step_num = int(match.group(1))
            field_path = match.group(2)

            # step_num 是 1-based，转换为 step_id
            step_id = self._get_step_id_by_order(step_num)
            if not step_id:
                return match.group(0)  # 保留原样

            step_data = self.step_data.get(step_id, {})
            value = self._get_nested_value(step_data, field_path)
            return str(value) if value is not None else match.group(0)

        result = self.STEP_REF_PATTERN.sub(step_replacer, s)

        # 2. 解析 ${random_*} 随机数据
        def random_replacer(match: re.Match) -> str:
            generator_name = match.group(1)
            generator = RANDOM_GENERATORS.get(generator_name)
            if generator:
                return str(generator())
            return match.group(0)

        result = self.RANDOM_PATTERN.sub(random_replacer, result)

        # 3. 解析 ${variable} 普通变量
        def var_replacer(match: re.Match) -> str:
            var_name = match.group(1)

            # 先查全局变量
            if var_name in self.variables:
                return str(self.variables[var_name])

            # 再查环境变量
            if var_name in self.environ:
                return self.environ[var_name]

            return match.group(0)

        result = self.VAR_REF_PATTERN.sub(var_replacer, result)

        return result

    def _get_step_id_by_order(self, step_order: int) -> Optional[str]:
        """根据步骤序号获取步骤 ID"""
        # step_order 是 1-based
        # 这里假设 step_data 的 key 是 step_id，需要外部维护 step_order -> step_id 映射
        # 简化实现：按插入顺序取第 N 个
        step_ids = list(self.step_data.keys())
        if 1 <= step_order <= len(step_ids):
            return step_ids[step_order - 1]
        return None

    def _get_nested_value(self, data: Dict[str, Any], path: str) -> Any:
        """从嵌套字典中获取值"""
        parts = path.split(".")
        current = data

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None

        return current

    def update_step_data(self, step_id: str, data: Dict[str, Any]):
        """更新步骤数据（用于步骤间数据传递）"""
        if step_id not in self.step_data:
            self.step_data[step_id] = {}
        self.step_data[step_id].update(data)

    def set_variable(self, name: str, value: Any):
        """设置全局变量"""
        self.variables[name] = value

    def get_variable(self, name: str) -> Any:
        """获取变量值"""
        return self.variables.get(name) or self.environ.get(name)


def register_random_generator(name: str, generator: Callable[[], Any]):
    """注册自定义随机数据生成器"""
    RANDOM_GENERATORS[name] = generator


def get_random_generators() -> Dict[str, Callable[[], Any]]:
    """获取所有随机数据生成器"""
    return RANDOM_GENERATORS.copy()
