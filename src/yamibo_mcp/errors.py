from __future__ import annotations


class YamiboError(Exception):
    """Base application error."""


class JobNotFound(YamiboError):
    """Raised when a job cannot be found."""


class LeaseNotAcquired(YamiboError):
    """Raised when a worker cannot acquire a job lease."""


class RemoteFetchError(YamiboError):
    """Raised when remote HTML cannot be fetched."""


class LoginRequiredError(RemoteFetchError):
    """Raised when Yamibo returns a login-required page."""


class RemoteMaintenanceError(RemoteFetchError):
    """Raised when Yamibo is in maintenance mode."""


class UnexpectedPageError(RemoteFetchError):
    """Raised when fetched HTML is not the expected thread detail page."""
