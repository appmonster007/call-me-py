import b


def fa():
    return b.fb()


def ping(n):
    return n if n <= 0 else b.pong(n - 1)
