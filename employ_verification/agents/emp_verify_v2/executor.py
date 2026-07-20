"""
Employee Verification v2 Executor — Thin subclass of BaseA2UIExecutor.
"""

from agents._base.base_executor import BaseA2UIExecutor


class EmpVerifyV2Executor(BaseA2UIExecutor):
    """A2A executor for the Employee Verification v2 Agent."""
    AGENT_CONFIG_NAME = "emp_verify_v2"
