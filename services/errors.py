"""Domain errors that map 1:1 onto the `{error:{code,message,request_id}}` envelope."""

from __future__ import annotations


class DomainError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def not_found(code: str = "NOT_FOUND", message: str = "Resource not found") -> DomainError:
    return DomainError(404, code, message)


def conflict(code: str, message: str) -> DomainError:
    return DomainError(409, code, message)


def forbidden(code: str = "FORBIDDEN", message: str = "Forbidden") -> DomainError:
    return DomainError(403, code, message)
