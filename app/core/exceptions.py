class AppError(Exception):
    """Base application error."""


class ValidationAppError(AppError):
    """Raised for validation-related failures."""


class GuardrailBlockedError(AppError):
    """Raised when a guardrail blocks a request or response."""


class ModelOutputParsingError(AppError):
    """Raised when model output cannot be parsed into expected schema."""


class UpstreamServiceError(AppError):
    """Raised for upstream provider failures."""


class NodeExecutionError(AppError):
    """Raised when a graph node fails in execution."""
