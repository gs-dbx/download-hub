"""Regression guards for persistent admin-mutation auditing."""

import ast
from pathlib import Path


_MAIN = Path(__file__).resolve().parent.parent / "src" / "app" / "main.py"
_READ_ONLY_POST_HANDLERS = {"admin_preview"}


def test_every_admin_mutation_handler_writes_config_audit():
    """Every persistent admin handler must call the config-audit writer."""
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    mutation_handlers = set()
    for node in functions.values():
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            method = decorator.func
            route = decorator.args[0]
            if (
                isinstance(method, ast.Attribute)
                and isinstance(method.value, ast.Name)
                and method.value.id == "app"
                and method.attr == "post"
                and isinstance(route, ast.Constant)
                and str(route.value).startswith("/admin/")
                and node.name not in _READ_ONLY_POST_HANDLERS
            ):
                mutation_handlers.add(node.name)

    assert mutation_handlers, "No persistent admin mutation handlers discovered"

    missing = []
    for name in sorted(mutation_handlers):
        calls = {
            node.func.id
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        if "_log_config_audit" not in calls:
            missing.append(name)
    assert not missing, f"Admin mutation handler(s) omit config audit: {missing}"
