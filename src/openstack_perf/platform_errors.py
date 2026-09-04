class PlatformValidationError(ValueError):
    """Raised for validation failures whose messages are safe to retain."""


def platform_failure_message(context: str, error: Exception) -> str:
    """Describe platform failures without retaining external exception text."""
    if isinstance(error, PlatformValidationError):
        return f"{context} failed: {error}"
    return f"{context} failed: {type(error).__name__}"
