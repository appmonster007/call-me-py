"""CASE: circular imports (a imports b imports a) and mutual recursion across modules.
WHY: import order must not matter; the cycle is also a call cycle (ping <-> pong).
EXPECTED (ground truth from a traced run; caller -> callee):
  a.fa -> b.fb
  a.ping -> b.pong
  b.pong -> a.ping
  main.<module> -> main.main
  main.main -> a.fa
  main.main -> a.ping
"""
import a


def main():
    a.fa()
    a.ping(2)


main()
