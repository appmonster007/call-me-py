def inc(x):
    return x + 1

def dec(x):
    return x - 1

def wrap(f):
    def run(x):
        return f(x)
    return run

a = wrap(inc)
b = wrap(dec)
a(1)
