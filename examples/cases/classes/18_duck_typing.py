"""CASE: unrelated classes with the same method name; hasattr guard.
WHY: no inheritance links them; every class with that method name is a candidate.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> maybe
  <module> -> talk
  maybe -> Robot.charge
  talk -> Duck.speak
  talk -> Robot.speak
"""


class Duck:
    def speak(self):
        return "quack"


class Robot:
    def speak(self):
        return "beep"

    def charge(self):
        return 1


class Rock:
    pass


def talk(x):
    return x.speak()


def maybe(x):
    if hasattr(x, "charge"):
        return x.charge()


for o in (Duck(), Robot()):
    talk(o)
for o in (Duck(), Robot(), Rock()):
    maybe(o)
