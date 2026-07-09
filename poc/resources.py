"""
Mock cloud resource data for the local A2UI sample agent.

From the ADK + A2UI Codelab:
https://codelabs.developers.google.com/next26/adk-a2ui
"""

RESOURCES = [
    {
        "name": "auth-service",
        "type": "Cloud Run",
        "region": "us-west1",
        "status": "healthy",
        "cpu": "2 vCPU",
        "memory": "1 GiB",
        "instances": 3,
        "url": "https://auth-service-abc123.run.app",
        "last_deployed": "2026-04-18T14:22:00Z",
    },
    {
        "name": "events-db",
        "type": "Cloud SQL",
        "region": "us-east1",
        "status": "warning",
        "tier": "db-custom-8-32768",
        "storage": "500 GB SSD",
        "connections": 195,
        "version": "PostgreSQL 16",
        "issue": "Storage usage at 92%",
        "usage_percent": 92,
    },
    {
        "name": "analytics-pipeline",
        "type": "Cloud Run",
        "region": "us-west1",
        "status": "error",
        "cpu": "2 vCPU",
        "memory": "4 GiB",
        "instances": 0,
        "url": "https://analytics-pipeline-ghi789.run.app",
        "last_deployed": "2026-04-10T16:45:00Z",
        "issue": "CrashLoopBackOff: OOM killed",
    },
]


_pending_resources: list[dict] | None = None
_pending_detail: dict | None = None
_pending_profile: dict | None = None

USER_PROFILE = {
    "avatar": (
        "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=150&h=150&fit=crop"
    ),
    "name": "Sarah Chen",
    "username": "@sarahchen",
    "bio": "Product Designer at Tech Co. Creating delightful experiences.",
    "followers": 12400,
    "following": 892,
    "posts": 347,
    "followText": "Follow",
}


def find_resource(name: str) -> dict | None:
    """Look up a resource by name."""
    for resource in RESOURCES:
        if resource["name"] == name:
            return dict(resource)
    return None


def get_resources() -> list[dict]:
    """Get all cloud resources in the current project."""
    global _pending_resources
    _pending_resources = list(RESOURCES)
    return _pending_resources


def consume_pending_resources() -> list[dict] | None:
    """Return resources fetched by the latest get_resources call, once."""
    global _pending_resources
    pending = _pending_resources
    _pending_resources = None
    return pending


def get_resource_details(name: str) -> dict:
    """Get detailed information for a single cloud resource by name."""
    global _pending_detail
    resource = find_resource(name)
    if resource is None:
        return {"error": f"Resource '{name}' was not found."}
    _pending_detail = resource
    return resource


def consume_pending_detail() -> dict | None:
    """Return resource details fetched by get_resource_details, once."""
    global _pending_detail
    pending = _pending_detail
    _pending_detail = None
    return pending


def get_user_profile() -> dict:
    """Get the current user's profile for the KpmgUserProfile widget."""
    global _pending_profile
    _pending_profile = dict(USER_PROFILE)
    return _pending_profile


def consume_pending_profile() -> dict | None:
    """Return profile fetched by get_user_profile, once."""
    global _pending_profile
    pending = _pending_profile
    _pending_profile = None
    return pending
