def square(x):
    return x * x

def double(x):
    return x * 2

def apply(f, v):
    return f(v)

def pick(flag):
    return square if flag else double

apply(square, 1)
apply(double, 2)
pick(True)(3)
