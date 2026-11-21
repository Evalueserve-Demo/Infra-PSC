"""The build/<name>/ folders Terraform zips: one per source_dir in the *.tf files, written from lambda/."""

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("build_packages", ROOT / "scripts" / "build_packages.py")
bp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bp)


def fake_deps(root: pathlib.Path) -> pathlib.Path:
    for module in bp.DEPENDENCY_MODULES:
        (root / module).mkdir(parents=True)
        (root / module / "__init__.py").write_text("")
    return root


@pytest.mark.parametrize("name", bp.expected_builds())
def test_every_terraform_build_folder_has_source_under_lambda(name):
    assert bp.entry_file(bp.source_folder(name)).is_file()


def test_the_terraform_asks_for_the_folders_the_repository_has():
    assert len(bp.expected_builds()) >= 18


def test_a_self_contained_lambda_is_just_its_entry_file(tmp_path):
    folder = bp.build("spvr_tool", tmp_path, None, None)

    assert sorted(p.name for p in folder.iterdir()) == ["lambda_function.py"]


def test_a_lambda_that_uses_the_database_needs_the_dependencies(tmp_path):
    with pytest.raises(SystemExit, match="needs psycopg and pypdf"):
        bp.build("email_ingestion", tmp_path, None, None)


def test_the_dependencies_sit_beside_the_entry_file(tmp_path):
    folder = bp.build("reconciler", tmp_path / "out", fake_deps(tmp_path / "deps"), None)

    assert (folder / "lambda_function.py").is_file()
    assert (folder / "psycopg" / "__init__.py").is_file()


def test_an_agent_needs_the_bundle(tmp_path):
    with pytest.raises(SystemExit, match="agent-bundle"):
        bp.build("classification_agent", tmp_path, None, None)


def test_an_agent_is_the_bundle_with_its_own_main(tmp_path):
    import zipfile
    bundle = tmp_path / "bundle.zip"
    with zipfile.ZipFile(bundle, "w") as z:
        z.writestr("strands/__init__.py", "")
        z.writestr("main.py", "old")
        z.writestr("payment_query_platform/old.py", "stale")

    folder = bp.build("classification_agent", tmp_path / "out", None, bundle)

    assert (folder / "strands" / "__init__.py").is_file()
    assert (folder / "main.py").read_text(encoding="utf-8") != "old"
    assert sorted(p.name for p in folder.glob("*.py")) == ["main.py"]   # an agent is that one file
    assert not (folder / "payment_query_platform").exists()             # the stale copy in the bundle is dropped


def test_the_root_cause_build_folder_comes_from_the_root_cause_tool(tmp_path):
    assert bp.source_folder("root_cause_map_tool").name == "root_cause_tool"


def test_a_lambda_carries_only_the_modules_it_imports(tmp_path):
    folder = bp.build("results_report", tmp_path, fake_deps(tmp_path / "deps"), None)
    carried = sorted(p.name for p in folder.glob("*.py"))

    assert carried == ["database.py", "lambda_function.py", "results.py", "text.py"]   # results.py is its own


def test_a_lambda_borrows_what_another_lambda_owns(tmp_path):
    folder = bp.build("reconciler", tmp_path, fake_deps(tmp_path / "deps"), None)
    carried = {p.name for p in folder.glob("*.py")}

    assert {"ingest_mail.py", "open_case.py"} <= carried          # its own
    assert {"database.py", "graph_client.py", "escalation.py"} <= carried   # owned by other folders
    assert "invoke_agent.py" not in carried                       # not needed, so not carried


def test_a_module_may_live_in_only_one_folder(tmp_path, monkeypatch):
    extra = bp.LAMBDAS / "results-report" / "text.py"
    extra.write_text("")
    try:
        with pytest.raises(SystemExit, match="keep one copy"):
            bp.module_files()
    finally:
        extra.unlink()


@pytest.mark.parametrize("name", [n for n in bp.expected_builds() if n not in bp.AGENT_FOLDERS])
def test_every_built_lambda_imports_from_its_own_folder_alone(name, tmp_path):
    """The built folder, not the repository, must hold everything the handler imports."""
    import os, subprocess, sys
    folder = bp.build(name, tmp_path / "out", fake_deps(tmp_path / "deps"), None)
    # Stand-ins for the packages Lambda supplies, so the import does not depend on this machine's.
    code = (
        "import sys, importlib.util\n"
        "for n in ('psycopg', 'psycopg.types', 'psycopg.types.json', 'pypdf', 'boto3', 'botocore', 'botocore.config', 'botocore.exceptions', 'pg8000', 'pg8000.dbapi'):\n"
        "    sys.modules.setdefault(n, __import__('unittest.mock').mock.MagicMock())\n"
        "sys.path[:] = [p for p in sys.path if p and 'site-packages' not in p] + [sys.argv[1]]\n"
        "import lambda_function\n"
        "assert callable(lambda_function.lambda_handler)\n"
    )
    result = subprocess.run([sys.executable, "-c", code, str(folder)], capture_output=True, text=True, cwd=folder,
                            env={**os.environ, "PYTHONPATH": "", "AWS_DEFAULT_REGION": "us-east-1"})

    assert result.returncode == 0, result.stderr
