"""CASE: callbacks stored in instance fields, lists of hooks, bound methods as values, staticmethod/alias class attributes.
WHY: the callee is read from the heap (field or list), not from a name.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> Cfg.method
  <module> -> Svc.__init__
  <module> -> Svc.add_hook
  <module> -> Svc.fire
  <module> -> Svc.run
  <module> -> log
  Svc.fire -> Svc.tick
  Svc.fire -> retry
  Svc.run -> log
"""


def log(m):
    return m


def retry():
    return 1


class Svc:
    def __init__(self, cb):
        self.cb = cb
        self.hooks = []

    def run(self):
        return self.cb(1)

    def add_hook(self, h):
        self.hooks.append(h)

    def fire(self):
        for h in self.hooks:
            h()

    def tick(self):
        return 0


class Cfg:
    handler = staticmethod(log)

    def method(self):
        return 0

    alias = method


s = Svc(log)
s.run()
s.add_hook(retry)
s.add_hook(s.tick)
s.fire()
c = Cfg()
c.handler(1)
c.alias()
obj = Svc(print)
obj.cb = log
obj.run()
