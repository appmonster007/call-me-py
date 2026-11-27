def square(x):
    return x * x

def double(x):
    return x * 2

def ident(f):
    return f

ident(square)(3)
g = ident(double)
