"""Check that every Lambda, agent and tool in the repo can be loaded. No AWS is used.

    python scripts/check_lambdas.py

For each file it compiles the code and then imports it, with the Lambda and agent folders on the path, since a module may live in another's folder, so a
module that is missing or broken fails here and not on the first invocation. Which
modules each deployed package carries is checked by tests/test_build_packages.py. The Lambdas are loaded twice, once reading
mail from Outlook (the default) and once with MAIL_SOURCE=s3. The agents are
imported only when bedrock-agentcore is installed, since their main.py needs it.

Prints one line per file and exits with 1 if any failed. Needs the packages the
Lambdas import: boto3 and psycopg[binary] (and pypdf for attachment processing).
"""

import importlib.util
import os
import pathlib
import sys

sys.dont_write_bytecode = True

ROOT = pathlib.Path(__file__).resolve().parent.parent
HANDLER_ROOTS = tuple(str(ROOT / d).replace(os.sep, "/") + "/" for d in ("lambda", "agents"))
HANDLER_FOLDERS = [p for d in ("lambda", "agents") for p in sorted((ROOT / d).iterdir()) if p.is_dir()]
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", os.environ["AWS_DEFAULT_REGION"])


def forget_embedded() -> None:
    """Drop the modules the previous file imported from its folder, so each file is checked on its own."""
    for name in [n for n, m in sys.modules.items()
                 if str(getattr(m, "__file__", "") or "").replace(os.sep, "/").startswith(HANDLER_ROOTS)]:
        del sys.modules[name]


def load(path: pathlib.Path, label: str) -> None:
    forget_embedded()
    spec = importlib.util.spec_from_file_location("check_" + label.replace("-", "_").replace("/", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if path.name == "lambda_function.py" and not callable(getattr(module, "lambda_handler", None)):
        raise AttributeError("no lambda_handler")


def main() -> int:
    failed = skipped = 0
    checks = []
    for path in sorted((ROOT / "lambda").glob("*/lambda_function.py")):
        for mode in ("graph", "s3"):
            checks.append((path, f"{path.parent.name} [MAIL_SOURCE={mode}]", mode))
    agents_ok = importlib.util.find_spec("bedrock_agentcore") is not None
    for path in sorted((ROOT / "agents").glob("*/main.py")):
        checks.append((path, f"agents/{path.parent.name}", "agent"))

    for path, label, mode in checks:
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")  # in memory: writes no .pyc
            if mode == "agent" and not agents_ok:
                print(f"SKIP  {label}: compiled, not imported (bedrock-agentcore is not installed)")
                continue
            if mode in ("graph", "s3"):
                os.environ["MAIL_SOURCE"] = mode
            else:
                os.environ.pop("MAIL_SOURCE", None)
            sys.path[:0] = [*(str(p) for p in HANDLER_FOLDERS)]
            try:
                load(path, label)
            finally:
                del sys.path[:len(HANDLER_FOLDERS)]
            print(f"PASS  {label}")
        except ModuleNotFoundError as exc:
            if any(list((ROOT / d).glob("*/" + (exc.name or "?") + ".py")) for d in ("lambda", "agents")):
                failed += 1
                print(f"FAIL  {label}: one of its own modules is missing from its folder: {exc.name}")
            else:  # a package this machine lacks, not a fault in the code
                skipped += 1
                print(f"SKIP  {label}: needs the package '{exc.name}' (pip install {exc.name})")
        except Exception as exc:
            failed += 1
            print(f"FAIL  {label}: {type(exc).__name__}: {str(exc)[:160]}")
    print(f"\n{len(checks) - failed - skipped} of {len(checks)} checks passed, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
