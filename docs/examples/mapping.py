import functools

def log(fn):
    @functools.wraps(fn)
    def wrapper(*args):
        return fn(*args)
    return wrapper

class Greeter:
    def greet(self, name):
        fmt = lambda s: s.title()
        return fmt(name)

@log
def main():
    Greeter().greet("ada")

main()
