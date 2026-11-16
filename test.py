def f(x):
    return x+1

def f1(x):
    return f(x)

print(f1(0))

def f(x):
    return x+2

print(f1(0))

def f2(x):
    return f(x)
