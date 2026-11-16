"""Parse Python files, tag every function with its file name, and print the AST."""
import ast
import sys
from pathlib import Path

FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)

# ast.dump(..., include_attributes=True) prints every name listed in a node
# class's _attributes (lineno, col_offset, ...). Adding "filename" there makes
# our extra attribute show up in the dump next to the line numbers.
for node_class in FUNCTION_NODES:
    node_class._attributes += ("filename",)  # pylint: disable=protected-access


def parse_file(path: Path) -> ast.Module:
    """Parse one file and store its name on every function node."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):  # ast.walk also reaches nested functions
        if isinstance(node, FUNCTION_NODES):
            setattr(node, "filename", str(path))
    return tree


def main() -> None:
    """Parse every file given on the command line (default: examples/example.py)."""
    paths = [Path(p) for p in sys.argv[1:]] or [Path("examples/example.py")]
    for path in paths:
        tree = parse_file(path)
        print(ast.dump(tree, indent=4, include_attributes=True))


if __name__ == "__main__":
    main()
