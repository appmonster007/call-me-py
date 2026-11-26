class Vec:
    def __init__(self, x):
        self.x = x

    def __add__(self, other):
        return Vec(self.x + other.x)

    def __iter__(self):
        return iter([self.x])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    @property
    def size(self):
        return 1

v = Vec(1) + Vec(2)
for x in v:
    pass
with v:
    pass
n = v.size
