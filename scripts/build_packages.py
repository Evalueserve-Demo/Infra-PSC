"""Assemble the build/<name>/ folders that Terraform zips with archive_file.

Every Lambda and agent in the Terraform reads its code from build/<name>/. This
script writes those folders from lambda/ and agents/, so the same folders feed Terraform at
the client and the deploy script here.

    build/<name>/lambda_function.py     a Lambda (main.py for an agent)
    build/<name>/<module>.py             the modules it imports, directly or through others: its own, and the ones
                                         it borrows from other Lambdas' and agents' folders (each lives in one place)
    build/<name>/<dependencies>         the Linux build of psycopg and pypdf, where the code uses them
    build/<name>/<agent bundle>         for an agent: Strands and the rest, built for AgentCore

The folders it writes are the ones named by `source_dir` in the *.tf files.

    python scripts/build_packages.py --deps <psycopg and pypdf for Linux> --agent-bundle <bundle.zip>
    python scripts/build_packages.py --deps <deps> --agent-bundle <bundle.zip> email_ingestion reconciler

--deps is a folder holding psycopg, psycopg_binary and pypdf built for Lambda's
platform, which pip will not resolve on Windows:

    pip install --target <deps> --platform manylinux2014_x86_64 \\
        --python-version 3.12 --implementation cp --only-binary=:all: \\
        --no-deps psycopg psycopg-binary typing_extensions tzdata pypdf

--agent-bundle is the zip of the agents' dependencies built for AgentCore's ARM64
Python runtime. Only the agents use it.
"""

import argparse
import ast
import os
import pathlib
import re
import shutil
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
LAMBDAS = ROOT / "lambda"
DEPENDENCY_MODULES = ("psycopg", "pypdf")
# Application folders an older agent bundle may still hold; the code now sits beside main.py.
OLD_APP_DIRS = ("payment_query_platform/", "task2_payment_agent/", "email_agents/")
AGENTS = ROOT / "agents"
# A Terraform build folder whose lambda/ folder is named differently.
FOLDER_NAMES = {"root_cause_map_tool": "root_cause_tool"}
# The agent runtimes keep their code under agents/.
AGENT_FOLDERS = {"classification_agent": "classification", "extraction_agent": "extraction",
                 "investigation_agent": "investigation"}
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def long_path(path: pathlib.Path) -> pathlib.Path:
    """The path in a form Windows accepts beyond 260 characters; the agent bundle has deep folders."""
    if os.name != "nt":
        return path
    text = str(path.resolve())
    return pathlib.Path(text if text.startswith("\\\\?\\") else "\\\\?\\" + text)


ENTRY_NAMES = ("lambda_function.py", "main.py", "handler.py")


def module_files() -> dict[str, pathlib.Path]:
    """Every module that sits beside a handler under lambda/ and agents/, by name.

    A module lives once, in the folder of the Lambda or agent it belongs to, and the others that
    import it get it copied into their package. So a name must not appear in two folders.
    """
    found: dict[str, pathlib.Path] = {}
    for folder in sorted([*LAMBDAS.iterdir(), *AGENTS.iterdir()]):
        for path in sorted(folder.glob("*.py")) if folder.is_dir() else []:
            if path.name in ENTRY_NAMES:
                continue
            if path.stem in found:
                raise SystemExit(f"{path.name} is in both {found[path.stem].parent} and {folder}: keep one copy")
            found[path.stem] = path
    return found


def imports_of(code: str) -> set[str]:
    """The top-level name of every module the code imports, anywhere in it (lazy imports too)."""
    found = set()
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module.split(".")[0])
    return found


def needed_files(entry: pathlib.Path) -> list[pathlib.Path]:
    """The modules of ours the entry file needs, found by following imports.

    Its own folder first, then the folders of the other Lambdas and agents it borrows from.
    Everything else (boto3, psycopg, strands ...) is a dependency, not ours.
    """
    ours, found, todo = module_files(), {}, [entry]
    while todo:
        for name in imports_of(todo.pop().read_text(encoding="utf-8")):
            beside = entry.parent / (name + ".py")
            path = beside if beside.is_file() and beside.name not in ENTRY_NAMES else ours.get(name)
            if path is not None and path not in found:
                found[path] = True
                todo.append(path)
    return sorted(found)


def expected_builds() -> list[str]:
    names = set()
    for tf in ROOT.glob("*.tf"):
        names |= set(re.findall(r'source_dir\s*=\s*"\$\{path\.module\}/build/(\w+)"', tf.read_text(encoding="utf-8")))
    return sorted(names)


def source_folder(name: str) -> pathlib.Path:
    if name in AGENT_FOLDERS:
        return AGENTS / AGENT_FOLDERS[name]
    if name in FOLDER_NAMES:
        return LAMBDAS / FOLDER_NAMES[name]
    for folder in LAMBDAS.iterdir():
        if folder.is_dir() and folder.name.replace("-", "_") == name:
            return folder
    raise SystemExit(f"No folder under lambda/ for the Terraform build folder {name}")


def entry_file(folder: pathlib.Path) -> pathlib.Path:
    for name in ("lambda_function.py", "main.py"):
        if (folder / name).is_file():
            return folder / name
    raise SystemExit(f"{folder} holds neither lambda_function.py nor main.py")


def build(name: str, out: pathlib.Path, deps: pathlib.Path | None, bundle: pathlib.Path | None) -> pathlib.Path:
    entry = entry_file(source_folder(name))
    is_agent = entry.name == "main.py"
    target = out / name
    if target.exists():
        shutil.rmtree(long_path(target))
    long_path(target).mkdir(parents=True)

    if is_agent:
        if bundle is None:
            raise SystemExit(f"{name} is an agent: pass --agent-bundle")
        with zipfile.ZipFile(bundle) as z:
            for item in z.infolist():
                if item.filename == "main.py" or item.filename.startswith(OLD_APP_DIRS) or item.is_dir():
                    continue
                destination = long_path(target / item.filename)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(z.read(item.filename))

    code = entry.read_text(encoding="utf-8")
    needed = needed_files(entry)
    for path in needed:
        shutil.copy2(path, long_path(target / path.name))

    if not is_agent and any(module in code + "".join(p.read_text(encoding="utf-8") for p in needed) for module in DEPENDENCY_MODULES):
        if deps is None or not all((deps / module).is_dir() for module in DEPENDENCY_MODULES):
            # An empty or missing folder would build without complaint and fail on import.
            raise SystemExit(f"{name} needs psycopg and pypdf: pass --deps with both built for Lambda's platform")
        shutil.copytree(long_path(deps), long_path(target), ignore=IGNORE, dirs_exist_ok=True)

    shutil.copy2(entry, long_path(target / entry.name))
    return target


def size_mb(folder: pathlib.Path) -> float:
    return sum(p.stat().st_size for p in long_path(folder).rglob("*") if p.is_file()) / 1e6


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="build folders to write, default all of them")
    parser.add_argument("--deps", help="psycopg and pypdf built for Lambda's platform")
    parser.add_argument("--agent-bundle", help="the agents' dependency bundle built for AgentCore")
    parser.add_argument("--out", default=str(ROOT / "build"), help="where to write, default build/")
    args = parser.parse_args()

    known = expected_builds()
    unknown = [n for n in args.names if n not in known]
    if unknown:
        sys.exit(f"Not a build folder in the Terraform: {', '.join(unknown)}. Known: {', '.join(known)}")
    deps = pathlib.Path(args.deps) if args.deps else None
    bundle = pathlib.Path(args.agent_bundle) if args.agent_bundle else None
    for name in args.names or known:
        target = build(name, pathlib.Path(args.out), deps, bundle)
        print(f"{name:26} {size_mb(target):6.1f} MB")


if __name__ == "__main__":
    main()
