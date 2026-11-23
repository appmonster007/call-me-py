import functools


def square(x):
    return x * x


def double(x):
    return x * 2


def apply(f, v):
    return f(v)


def compose(f, g):
    def h(x):
        return f(g(x))
    return h


def make_adder(n):
    return lambda x: x + n


def logged(fn):
    def wrapper(*args):
        return fn(*args)
    return wrapper


@logged
def greet(name):
    return "hi " + name


def unused():
    return square(0)


class Base:
    def __init__(self):
        self.cb = double

    def run(self):
        return self.step()

    def step(self):
        return self.cb(1)


class Child(Base):
    def __init__(self):
        super().__init__()

    def step(self):
        return square(3)


def main():
    apply(square, 3)
    h = compose(square, double)
    h(2)
    make_adder(5)(1)
    handlers = {"sq": square, "db": double}
    handlers["sq"](4)
    list(map(double, [1, 2]))
    sorted([3, 1], key=square)
    functools.reduce(apply2, [1, 2])
    greet("a")
    Child().run()


def apply2(a, b):
    return a + b


main()
