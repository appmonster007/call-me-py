"""CASE: exception classes with __init__, raise in callee, handlers, finally, suppress.
WHY: raising constructs an exception object (implicit __init__ call); handlers are control flow.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> on_error
  <module> -> risky
  <module> -> safe
  AppError.__init__ -> code_for
  risky -> AppError.__init__
  safe -> cleanup
  safe -> on_error
  safe -> risky
"""
import contextlib


def code_for(m):
    return 1


class AppError(Exception):
    def __init__(self, msg):
        super().__init__(msg)
        self.code = code_for(msg)


def risky(n):
    if n:
        raise AppError("x")
    return n


def on_error(e):
    return e


def cleanup():
    pass


def safe(n):
    try:
        return risky(n)
    except AppError as e:
        return on_error(e)
    finally:
        cleanup()


safe(0)
safe(1)
with contextlib.suppress(AppError):
    risky(1)
try:
    risky(1)
except (AppError, ValueError) as e:
    on_error(e)
