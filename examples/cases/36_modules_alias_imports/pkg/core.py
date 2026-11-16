from .util.helpers import double
from .util import helpers as hp


def run(n):
    return double(n) + hp.twice(n)


class Engine:
    def start(self):
        return run(1)
