import numpy as np


class Base:
    def start(self):
        return np.sqrt(self.go())

    def go(self):
        return 1
