def f():
    pass

def g():
    pass

a = [g]
b = a
b.append(f)
a[0]()
