"""
Employee Verification V2 Executor — Thin subclass of BaseA2UIExecutor.

All the A2A/A2UI logic lives in the base class. This file just declares
which config to use.
"""

from agents._base.base_executor import BaseA2UIExecutor


class EmployeeVerificationV2Executor(BaseA2UIExecutor):
    """A2A executor for the Employee Verification Agent V2."""
    AGENT_CONFIG_NAME = "employee_verification_v2"
