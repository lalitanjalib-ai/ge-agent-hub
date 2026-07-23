"""
Employee Verification Google OAuth v3 Executor — direct Google OBO (no STS).
"""

from agents._base.base_executor import BaseA2UIExecutor


class EmpVerifyGoogleOAuthV3Executor(BaseA2UIExecutor):
    """A2A executor for Employee Verification Google OAuth v3."""
    AGENT_CONFIG_NAME = "emp_verify_google_oauth_v3"
