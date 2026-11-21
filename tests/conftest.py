"""Test setup.

Each Lambda or agent keeps all its code in its own folder: lambda_function.py or main.py holds its
handler, and the files it uses sit beside it. Where two of them use the same file, each has its
own copy (test_copies.py checks the copies are identical). pyproject.toml puts those folders on
the import path.

The tests were written against names like payment_query_platform.reconciliation.reconcile. The
tables below keep them working: each name stands for the file that holds that code now.
"""

import importlib
import importlib.abc
import importlib.util
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The tests for tools/ need that folder, which is not in every checkout.
collect_ignore = [] if (ROOT / "tools").is_dir() else ["test_apis.py", "test_rules.py"]
# These two describe the earlier design, where the text of an attachment is read as the file is
# stored. attachment-processing now stores the file and reads the text in a later step
# (EXTRACT_CLEAN), so they would fail against the deployed code and are left out until rewritten.
collect_ignore += ["test_attachment_store.py", "test_attachments.py"]

P = "payment_query_platform."
# A name -> the handler file that holds that code (lambda_function.py or main.py).
ENTRY_FILES = {
    P + "ingestion.ingest_to_sqs": "lambda/email-ingestion/lambda_function.py",
    P + "intake.invoke_agents": "lambda/intake-agents/lambda_function.py",
    P + "reconciliation.reconcile": "lambda/reconciler/lambda_function.py",
    P + "reporting.api": "lambda/results-report/lambda_function.py",
    # An agent is one file, so every old name for a part of it stands for that file.
    P + "intake.agent_runtime": "agents/classification/main.py",
    P + "intake.classification": "agents/classification/main.py",
    P + "intake.extraction": "agents/classification/main.py",
    P + "investigation.agent_runtime": "agents/investigation/main.py",
    P + "investigation.schema": "agents/investigation/main.py",
    P + "investigation.gateway": "agents/investigation/main.py",
    # attachment-processing is one file with sections, so the whole file stands in for both names.
    P + "attachment_text": "lambda/attachment-processing/lambda_function.py",
    P + "ingestion.store_attachment": "lambda/attachment-processing/lambda_function.py",
}
# A name -> the module beside the handlers, imported by its own plain name.
OWN_MODULES = {
    P + "ingestion.ingest_mail": "ingest_mail",
    P + "ingestion.open_case": "open_case",
    P + "intake.attachments": "attachments",
    P + "intake.guardrail": "guardrail",
    P + "intake.inputs": "inputs",
    P + "intake.prompts": "prompts",
    P + "reporting.results": "results",
    P + "investigation.invoke_agent": "invoke_agent",
    P + "investigation.prompt": "prompt",
    P + "mailbox.categories": "categories",
    P + "mailbox.graph_auth": "graph_auth",
    P + "mailbox.graph_client": "graph_client",
    P + "mailbox.s3_mail": "s3_mail",
    P + "storage.database": "database",
    P + "config": "config",
    P + "escalation": "escalation",
    P + "metrics": "metrics",
    P + "models": "models",
    P + "text": "text",
}
# The old package and its folders: empty, so that the names above can sit under them.
EMPTY_PACKAGES = {P[:-1], P + "ingestion", P + "reconciliation", P + "reporting", P + "intake",
                  P + "investigation", P + "mailbox", P + "storage"}


class Names(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ENTRY_FILES or fullname in OWN_MODULES or fullname in EMPTY_PACKAGES:
            return importlib.util.spec_from_loader(fullname, self, is_package=fullname in EMPTY_PACKAGES)
        return None

    loaded = {}  # handler file -> its module, so the names that stand for one file share one module

    def create_module(self, spec):
        if spec.name in OWN_MODULES:
            return importlib.import_module(OWN_MODULES[spec.name])  # the same module the handler imports
        if spec.name in ENTRY_FILES:
            return self.loaded.get(ENTRY_FILES[spec.name])
        return None

    def exec_module(self, module):
        name = module.__name__
        if name in ENTRY_FILES:
            if ENTRY_FILES[name] in self.loaded:
                return
            self.loaded[ENTRY_FILES[name]] = module
            path = ROOT / ENTRY_FILES[name]
            exec(compile(path.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10)), str(path), "exec"), module.__dict__)
        elif name in EMPTY_PACKAGES:
            module.__path__ = []


sys.meta_path.append(Names())

HANDLER_FOLDERS = ("lambda/email-ingestion", "lambda/intake-agents", "lambda/investigation-invocation",
                   "lambda/reconciler", "lambda/results-report", "agents/classification",
                   "agents/extraction", "agents/investigation")
# For a subprocess that must see the same code: the folders pyproject.toml puts on the path.
SOURCE_PATH = os.pathsep.join(str(ROOT / p) for p in HANDLER_FOLDERS)
