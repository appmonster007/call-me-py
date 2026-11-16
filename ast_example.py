import ast
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "examples/example.py")
tree = ast.parse(path.read_text(), filename=str(path))
print(ast.dump(tree, indent=4))
