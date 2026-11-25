"""CASE: operators and protocols that call dunder methods implicitly.
WHY: +, ==, <, len, [], iter, str, bool, in, with, f-string, call syntax all dispatch to methods.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> Iter.__next__
  <module> -> Vec.__add__
  <module> -> Vec.__bool__
  <module> -> Vec.__call__
  <module> -> Vec.__contains__
  <module> -> Vec.__enter__
  <module> -> Vec.__eq__
  <module> -> Vec.__exit__
  <module> -> Vec.__getitem__
  <module> -> Vec.__init__
  <module> -> Vec.__iter__
  <module> -> Vec.__len__
  <module> -> Vec.__lt__
  <module> -> Vec.__str__
  Vec.__add__ -> Vec.__init__
  Vec.__getitem__ -> Vec.at
  Vec.__iter__ -> Iter.__init__
  Vec.__str__ -> Vec.fmt
"""


class Iter:
    def __init__(self, n):
        self.n = n

    def __next__(self):
        if self.n <= 0:
            raise StopIteration
        self.n -= 1
        return self.n


class Vec:
    def __init__(self, x):
        self.x = x

    def __add__(self, o):
        return Vec(self.x + o.x)

    def __eq__(self, o):
        return self.x == o.x

    def __hash__(self):
        return 0

    def __lt__(self, o):
        return self.x < o.x

    def __len__(self):
        return self.x

    def __getitem__(self, i):
        return self.at(i)

    def at(self, i):
        return i

    def __iter__(self):
        return Iter(self.x)

    def __str__(self):
        return self.fmt()

    def fmt(self):
        return "V"

    def __bool__(self):
        return True

    def __contains__(self, v):
        return True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __call__(self, y):
        return y


v = Vec(1)
w = Vec(2)
v + w
v == w
sorted([w, v])
len(v)
v[0]
list(v)
str(v)
bool(v)
1 in v
v(3)
with v:
    pass
f"{v}"
