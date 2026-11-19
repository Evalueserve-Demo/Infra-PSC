"""Check that every Lambda, agent and tool in the repo can be loaded. No AWS is used.

    python scripts/check_lambdas.py

For each file it compiles the code and then imports it, which runs the loader that
makes the embedded modules available, so a module that is missing or broken fails
here and not on the first invocation. The Lambdas are loaded twice, once reading
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
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", os.environ["AWS_DEFAULT_REGION"])


def forget_embedded() -> None:
    """Drop what the previous file registered, so each file is checked on its own."""
    for name in [n for n in sys.modules if n == "payment_query_platform" or n.startswith("payment_query_platform.")]:
        del sys.modules[name]
    sys.meta_path[:] = [f for f in sys.meta_path if type(f).__name__ != "_PSCEmbeddedLoader"]


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
    for path in sorted((ROOT / "tools").glob("*/handler.py")):
        checks.append((path, f"tools/{path.parent.name}", None))
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
            sys.path.insert(0, str(ROOT / "tools" / "_shared"))
            sys.path.insert(0, str(ROOT))
            try:
                load(path, label)
            finally:
                sys.path[:2] = []
            print(f"PASS  {label}")
        except ModuleNotFoundError as exc:
            if (exc.name or "").startswith("payment_query_platform"):
                failed += 1
                print(f"FAIL  {label}: an embedded module is missing: {exc.name}")
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
