from pathlib import Path


def _list_modules():
    mod_dir = Path(__file__).parent
    modules = []
    for file in mod_dir.rglob("*.py"):
        if not file.is_file() or file.name == "__init__.py":
            continue
        relative = file.relative_to(mod_dir)
        module_path = (
            str(relative.with_suffix(""))
            .replace("\\", ".")
            .replace("/", ".")
        )
        modules.append(module_path)
    return modules


all_modules = frozenset(sorted(_list_modules()))
