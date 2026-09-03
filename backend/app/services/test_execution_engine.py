"""
统一测试执行引擎（兼容层）

本模块保留原有的公共 API（TestExecutionService、ExecutionResult、ScriptExecutor），
内部实现已迁移到 app.services.execution 包中的 ScriptExecutionEngine。

如需扩展新的脚本执行器，请在 app.services.execution.executors 中注册。
"""
"""
andan
"""


import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.schemas.enums import JobStatus
from app.services.execution.engine import ScriptExecutionEngine
# pragma: no cover  MC80OmFIVnBZMlhscm9ua3VMazZPR1ZwVkE9PTpmZTZhZjBiNA==

logger = logging.getLogger(__name__)

# fmt: off  MS80OmFIVnBZMlhscm9ua3VMazZPR1ZwVkE9PTpmZTZhZjBiNA==

@dataclass
class ExecutionResult:
    """单个脚本执行结果（保留以兼容既有导入）"""

    success: bool
    status: JobStatus
    duration_ms: int = 0
    error_message: Optional[str] = None
    report_path: Optional[str] = None
    result_summary: Dict[str, Any] = field(default_factory=dict)
    detail_run_id: Optional[str] = None


class ScriptExecutor(ABC):
    """脚本执行器抽象基类（保留以兼容既有导入）"""

    @abstractmethod
    async def execute(
        self,
        script_id: UUID,
        config: Dict[str, Any],
    ) -> ExecutionResult:
        """执行单个脚本"""
        ...

    @abstractmethod
    async def cancel(self) -> None:
        """取消当前执行"""
        ...
# noqa  Mi80OmFIVnBZMlhscm9ua3VMazZPR1ZwVkE9PTpmZTZhZjBiNA==


class TestExecutionService:
    """统一测试执行服务（委托给 ScriptExecutionEngine）"""

    def __init__(self, mongodb=None):
        self._engine = ScriptExecutionEngine(mongodb=mongodb)
# type: ignore  My80OmFIVnBZMlhscm9ua3VMazZPR1ZwVkE9PTpmZTZhZjBiNA==

    async def execute_run(
        self,
        test_run_id: UUID,
        trigger: str = "manual",
    ) -> Dict[str, Any]:
        """
        执行整个测试运行。

        委托给 ScriptExecutionEngine.execute_run，保持 API 兼容。
        """
        return await self._engine.execute_run(test_run_id, trigger=trigger)

    async def cancel_run(self, test_run_id: UUID) -> None:
        """取消测试运行"""
        await self._engine.cancel_run(test_run_id)
