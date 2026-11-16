__all__ = ["public", "Thing"]


def public():
    return 1


def _private():
    return 2


def not_exported():
    return 3


class Thing:
    def go(self):
        return _private()
