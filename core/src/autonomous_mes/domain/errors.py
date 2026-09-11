class DomainError(Exception):
    """Base class for expected domain rule violations."""


class InvalidTransition(DomainError):
    pass


class ValidationError(DomainError):
    pass


class NotFound(DomainError):
    pass


class Forbidden(DomainError):
    pass


class IdempotencyConflict(DomainError):
    pass
