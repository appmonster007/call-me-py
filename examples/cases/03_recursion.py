"""CASE: self recursion, mutual recursion, recursion over a data structure.
WHY: call graph must contain cycles; fixpoint must terminate.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> fact
  <module> -> is_even
  <module> -> walk
  fact -> fact
  is_even -> is_odd
  is_odd -> is_even
  walk -> walk
"""


def fact(n):
    return 1 if n <= 1 else n * fact(n - 1)


def is_even(n):
    return True if n == 0 else is_odd(n - 1)


def is_odd(n):
    return False if n == 0 else is_even(n - 1)


def walk(tree):
    for child in tree.get("kids", []):
        walk(child)


fact(3)
is_even(3)
walk({"kids": [{}]})
