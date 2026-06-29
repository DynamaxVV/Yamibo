from __future__ import annotations

from typing import Any


class YamiboError(Exception):
    """Base application error."""


class JobNotFound(YamiboError):
    """Raised when a job cannot be found."""


class LeaseNotAcquired(YamiboError):
    """Raised when a worker cannot acquire a job lease."""


class RemoteFetchError(YamiboError):
    """Raised when remote HTML cannot be fetched."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class LoginRequiredError(RemoteFetchError):
    """Raised when Yamibo returns a login-required page."""


class RemoteMaintenanceError(RemoteFetchError):
    """Raised when Yamibo is in maintenance mode."""


class RemoteAccessPausedError(RemoteFetchError):
    """Raised when remote access is paused after anti-bot detection."""


class UnexpectedPageError(RemoteFetchError):
    """Raised when fetched HTML is not the expected thread detail page."""


class ThreadPermissionRequiredError(UnexpectedPageError):
    """Raised when a thread page requires a higher read permission."""

    def __init__(self, message: str, *, required_permission: int | None = None, details: dict[str, Any] | None = None):
        merged_details = dict(details or {})
        if required_permission is not None:
            merged_details["required_permission"] = required_permission
        super().__init__(message, details=merged_details)
        self.required_permission = required_permission
