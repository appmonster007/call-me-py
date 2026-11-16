import re


def slugify(s):
    return re.sub(r"\W+", "-", s).lower()


def shout(s):
    return s.upper() + "!"
