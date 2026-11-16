import os.path as osp
import numpy as np
import app
from app import run, Engine as E
from app.utils import text as txt
from app.utils.text import *
from math import *
from os import *


def main():
    run(3)
    e = E()
    E.start(e)
    print(txt.slugify("A B"), shout("x"), osp.join("a", "b"))
    np.linalg.norm([1, 2])
    app.helpers.double(2)
    r = pi * sqrt(4)  # names from `from math import *`
    path.exists("x")  # ambiguous: math.path or os.path
    return app.core.missing


def shadow(np):
    return np.zeros(3)  # `np` is a parameter here, not numpy


main()
