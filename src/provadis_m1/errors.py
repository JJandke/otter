"""Fixed public error messages: never include upstream text or exception details."""

from enum import IntEnum


class ExitCode(IntEnum):
    AUTHENTICATION = 2
    NETWORK = 3
    INVALID_RESPONSE = 4
    SUSPICIOUS_RESPONSE = 5
    CONFIGURATION = 6
    SERVICE = 7
    BOOTSTRAP = 8


class M1Error(Exception):
    def __init__(self, category: str, code: ExitCode, reason: str):
        self.category = category
        self.code = code
        self.reason = reason
        self.diagnostics: dict | None = None
        super().__init__(reason)


class AuthenticationError(M1Error):
    def __init__(self, reason: str = "authentication_required"):
        super().__init__("authentication", ExitCode.AUTHENTICATION, reason)


class BootstrapError(M1Error):
    def __init__(self):
        super().__init__("bootstrap", ExitCode.BOOTSTRAP, "portal_did_not_issue_timetable_bearer")


class NetworkError(M1Error):
    def __init__(self, reason: str = "network_request_failed"):
        super().__init__("network", ExitCode.NETWORK, reason)


class InvalidResponse(M1Error):
    def __init__(self, reason: str):
        super().__init__("invalid_response", ExitCode.INVALID_RESPONSE, reason)


class SuspiciousResponse(M1Error):
    def __init__(self, reason: str = "empty_timetable"):
        super().__init__("suspicious_response", ExitCode.SUSPICIOUS_RESPONSE, reason)


class ConfigurationError(M1Error):
    def __init__(self, reason: str):
        super().__init__("configuration", ExitCode.CONFIGURATION, reason)


class ServiceError(M1Error):
    def __init__(self, reason: str):
        super().__init__("service", ExitCode.SERVICE, reason)
