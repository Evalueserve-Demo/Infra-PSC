"""Run the tests against the code inside the deployed Lambda files.

Each Lambda under lambda/ carries the modules it needs as text. This puts those
modules on the import path under their normal names (payment_query_platform.*),
so the tests exercise what is actually deployed and not a separate source folder.
"""

import ast
import base64
import importlib.abc
import io
import zipfile
import importlib.util
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MARKER = "# The Lambda's own code:\n"
# The header names the module a file is the packaging of: "# Entry module: X" or "# PSC: single-file packaging of X."
ORIGIN = re.compile(r"# (?:Entry module: |PSC: single-file packaging of )(payment_query_platform(?:\.\w+)*)")

# The tests for tools/ need that folder, which is not in every checkout.
collect_ignore = [] if (ROOT / "tools").is_dir() else ["test_apis.py", "test_rules.py"]
# These two describe the earlier design, where the text of an attachment is read as the file is
# stored. attachment-processing now stores the file and reads the text in a later step
# (EXTRACT_CLEAN), so they would fail against the deployed code and are left out until rewritten.
collect_ignore += ["test_attachment_store.py", "test_attachments.py"]

MODULES: dict[str, str] = {}
PACKAGES: set[str] = set()
CONFLICTS: list[str] = []


def same(a: str, b: str) -> bool:
    return a.replace("\r\n", "\n").strip() == b.replace("\r\n", "\n").strip()


def add(name: str, code: str, where: str) -> None:
    if name in MODULES and not same(MODULES[name], code):
        CONFLICTS.append(f"{name} differs in {where}")
    MODULES.setdefault(name, code)


def add_zip(encoded: str, where: str) -> None:
    """The investigation Lambda carries the package as a base85 zip."""
    archive = zipfile.ZipFile(io.BytesIO(base64.b85decode(encoded)))
    for entry in archive.namelist():
        if not entry.endswith(".py"):
            continue
        parts = entry[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
            PACKAGES.add(".".join(parts))
        add(".".join(parts), archive.read(entry).decode("utf-8"), where)


def collect() -> None:
    for path in sorted([*(ROOT / "lambda").glob("*/*.py"), *(ROOT / "agents").glob("*/*.py")]):
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        # A single-file Lambda ends with the entry module's own code, after the marker.
        origin = ORIGIN.search(text)
        if origin and MARKER in text:
            add(origin.group(1), text.split(MARKER, 1)[1], path.parent.name)
        for node in ast.parse(text).body:
            if not (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)):
                continue
            if node.targets[0].id == "_PSC_MODULES":
                for name, code in ast.literal_eval(node.value).items():
                    add(name, code, path.parent.name)
            elif node.targets[0].id == "_PSC_PACKAGES":
                PACKAGES.update(ast.literal_eval(node.value))
            elif node.targets[0].id == "_PACKAGE_B85":
                add_zip("".join(ast.literal_eval(node.value)), path.parent.name)


class Embedded(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in MODULES:
            return None
        return importlib.util.spec_from_loader(fullname, self, is_package=fullname in PACKAGES)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        exec(compile(MODULES[module.__name__], f"<lambda:{module.__name__}>", "exec"), module.__dict__)


collect()
# attachment-processing is one file whose sections share a namespace, so its attachment_text and
# store_attachment code is not a module of its own. The whole file stands in for both names.
_attachments = ROOT / "lambda" / "attachment-processing" / "lambda_function.py"
if _attachments.is_file():
    _text = _attachments.read_text(encoding="utf-8").replace("\r\n", "\n")
    for _alias in ("payment_query_platform.attachment_text", "payment_query_platform.ingestion.store_attachment"):
        MODULES.setdefault(_alias, _text)
# Every parent of a registered module is a package, even when it has no code of its own.
for _name in list(MODULES):
    _parts = _name.split(".")
    for _i in range(1, len(_parts)):
        _parent = ".".join(_parts[:_i])
        PACKAGES.add(_parent)
        MODULES.setdefault(_parent, "")
sys.meta_path.insert(0, Embedded())
