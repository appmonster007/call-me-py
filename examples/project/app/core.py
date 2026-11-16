import json  # unused
import numpy as np
from .helpers import double as dbl
from .utils.base import Base
from .utils import text


def run(n):
    return np.sqrt(dbl(n))


class Engine(Base):
    def go(self):
        return text.slugify("x")
