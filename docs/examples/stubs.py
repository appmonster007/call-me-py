import functools

def square(x):
    return x * x

def by_len(s):
    return len(s)

def add(a, b):
    return a + b

nums = list(map(square, [1, 2, 3]))
words = sorted(["bb", "a"], key=by_len)
inc = functools.partial(add, 1)
inc(41)
