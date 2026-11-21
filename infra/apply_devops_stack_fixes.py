"""Bring the us-east-2 stack up to date with this repository, without Terraform.

The stack was first created with Terraform. This script makes the same changes
that `terraform apply` would make from the .tf files here, but through the AWS
API, so it can be run from this machine. The steps match a normal deploy:

    1. python scripts/build_packages.py ...      write the build/<name>/ folders
    2. python infra/apply_devops_stack_fixes.py   push them to AWS
    3. switch things on in order and send a test mail (see docs/handover/README.md)

The code is zipped from build/ the same way Terraform's archive_file does it,
so the same bytes reach AWS here and at the client. A later `terraform apply`
puts everything back under Terraform.

What it changes (each step can be run alone with --only):

    iam              the database and secret access the email-processing and
                     investigation Lambdas lacked, and sf2 calling only the
                     intake Lambda
    env              the variable names the Lambda code reads, and the VPC for
                     the investigation Lambda
    code             the Lambda code, from the build/ folders
    gateway          the application's tool names and schemas on his gateway
                     targets, so the agent finds the tools its prompt expects
    agents           the application's own Strands agents in the classification,
                     extraction and investigation runtimes, from the build/
                     folders, with the model access (and, for investigation,
                     the gateway access) their roles lacked
    state-machines   sf1 checks message_id; sf2 runs the application's intake
                     flow: the intake Lambda classifies and extracts, and queue
                     2 gets the case as the investigation reads it

One more step runs only when asked for with --only new-lambdas: it creates the
three Lambdas added in files 395 to 397 (intake-agents, reconciler, results-report) as
*-test copies, so a later `terraform apply` can create its own. --remove-new
deletes them again.

Nothing changes unless --apply is given. Every run first saves what it is about
to touch to the backup folder; --restore puts that back.

    python infra/apply_devops_stack_fixes.py
    python infra/apply_devops_stack_fixes.py --apply
    python infra/apply_devops_stack_fixes.py --restore <backup folder>
"""

import argparse
import ast
import copy
import datetime
import io
import json
import pathlib
import re
import sys
import time
import urllib.request
import zipfile

import boto3

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import build_packages  # noqa: E402  the build step shared with the client's Terraform

PREFIX = "boeing-helpdesk-paymentstatuscheck-dev"
ENDPOINT = "/runtime-endpoint/*"
ENV_READ = re.compile(r"""(?:os\.environ\[\s*|os\.environ\.get\(\s*|os\.getenv\(\s*)['"]([A-Z][A-Z0-9_]+)['"]""")

# The Lambdas whose code is replaced, and the lambda/ folder each comes from.
CODE = {
    "email-ingestion": "email-ingestion",
    "email-processing": "email-processing",
    "attachment-processing": "attachment-processing",
    "investigation-invocation": "investigation-invocation",
    "mail-preprocessing": "mail-preprocessing",
    "identifier-validation": "identifier-validation",
    "draft-builder-tool": "draft-builder-tool",
    "payment-rules-tool": "payment_rules_tool",
    "root-cause-map-tool": "root_cause_tool",
    "s4hana-tool": "s4hana-tool",
    "spvr-tool": "spvr_tool",
}
TOUCHED = sorted(set(CODE) | {"email-ingestion"})
STATE_MACHINES = ("sf1", "sf2")

# The AgentCore runtimes run the application's own agents: the runtime name, the file in
# lambda/ that becomes its main.py, and the environment that differs between them.
AGENTS = {
    "classification": ("psc_dev_classification", "agents/classification/main.py", {"AGENT_ROLE": "classification"}),
    "extraction": ("psc_dev_extraction", "agents/extraction/main.py", {"AGENT_ROLE": "extraction"}),
    "investigation": ("psc_dev_investigation", "agents/investigation/main.py", {}),
}
GATEWAY_NAME = "psc-dev-investigation-gateway"
ENABLE_INVESTIGATION = False
UPDATE_FIELDS = ("description", "roleArn", "networkConfiguration", "protocolConfiguration", "environmentVariables",
                 "authorizerConfiguration", "lifecycleConfiguration", "requestHeaderConfiguration")

def as_list(value):
    return value if isinstance(value, list) else [value]


class Stack:
    def __init__(self, region: str):
        self.region = region
        self.lam = boto3.client("lambda", region_name=region)
        self.iam = boto3.client("iam")
        self.sfn = boto3.client("stepfunctions", region_name=region)
        self.rds = boto3.client("rds", region_name=region)
        self.s3 = boto3.client("s3", region_name=region)
        self.agentcore = boto3.client("bedrock-agentcore-control", region_name=region)
        self.account = boto3.client("sts").get_caller_identity()["Account"]
        ingestion = self.config("email-ingestion")
        self.ingestion_env = ingestion.get("Environment", {}).get("Variables", {})
        self.vpc = {
            "SubnetIds": ingestion["VpcConfig"]["SubnetIds"],
            "SecurityGroupIds": ingestion["VpcConfig"]["SecurityGroupIds"],
        }
        proxy_arn = self.rds.describe_db_proxies()["DBProxies"][0]["DBProxyArn"]
        self.db_user = self.ingestion_env["AURORA_USER"]
        self.rds_db_arn = f"arn:aws:rds-db:{region}:{self.account}:dbuser:{proxy_arn.split(':')[6]}/{self.db_user}"

    def name(self, short: str) -> str:
        return f"{PREFIX}-{short}"

    def config(self, short: str) -> dict:
        return self.lam.get_function_configuration(FunctionName=self.name(short))

    def role_of(self, short: str) -> str:
        return self.config(short)["Role"].split("/")[-1]

    def sm_arn(self, short: str) -> str:
        return f"arn:aws:states:{self.region}:{self.account}:stateMachine:{self.name(short)}"

    def wait(self, short: str) -> None:
        self.lam.get_waiter("function_updated_v2").wait(FunctionName=self.name(short))


# ---------------------------------------------------------------- backup / restore

def backup(stack: Stack, folder: pathlib.Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for short in TOUCHED:
        got = stack.lam.get_function(FunctionName=stack.name(short))
        (folder / f"lambda-{short}.json").write_text(json.dumps(got["Configuration"], indent=2, default=str))
        (folder / f"lambda-{short}.zip").write_bytes(urllib.request.urlopen(got["Code"]["Location"]).read())
    roles = {stack.role_of(s) for s in TOUCHED} | {stack.name("sf2-role"), stack.name("sf1-role")}
    roles |= {agent_runtime(stack, name)["roleArn"].split("/")[-1] for name, _, _ in AGENTS.values()}
    for role in sorted(roles):
        policies = {}
        for pname in stack.iam.list_role_policies(RoleName=role)["PolicyNames"]:
            policies[pname] = stack.iam.get_role_policy(RoleName=role, PolicyName=pname)["PolicyDocument"]
        (folder / f"role-{role}.json").write_text(json.dumps(policies, indent=2))
    for short in STATE_MACHINES:
        d = stack.sfn.describe_state_machine(stateMachineArn=stack.sm_arn(short))
        (folder / f"sfn-{short}.json").write_text(json.dumps(
            {"definition": json.loads(d["definition"]), "roleArn": d["roleArn"]}, indent=2))
    (folder / "gateway-targets.json").write_text(json.dumps(gateway_targets(stack), indent=2, default=str))
    for key, (name, _, _) in AGENTS.items():
        runtime = agent_runtime(stack, name)
        (folder / f"agent-{key}.json").write_text(json.dumps(runtime, indent=2, default=str))
        (folder / f"agent-{key}.zip").write_bytes(agent_zip(stack, runtime))
    print(f"backup saved to {folder}")


def restore(stack: Stack, folder: pathlib.Path) -> None:
    for target in json.loads((folder / "gateway-targets.json").read_text()):
        update_target(stack, target, target["targetConfiguration"])
        print(f"restored gateway target {target['name']}")
    for key in AGENTS:
        saved = json.loads((folder / f"agent-{key}.json").read_text())
        upload_agent(stack, saved, (folder / f"agent-{key}.zip").read_bytes())
        print(f"restored agent {key}")
    for path in sorted(folder.glob("role-*.json")):
        for pname, doc in json.loads(path.read_text()).items():
            stack.iam.put_role_policy(RoleName=path.stem[5:], PolicyName=pname, PolicyDocument=json.dumps(doc))
            print(f"restored policy {pname} on {path.stem[5:]}")
    for short in TOUCHED:
        cfg = json.loads((folder / f"lambda-{short}.json").read_text())
        stack.lam.update_function_configuration(
            FunctionName=stack.name(short), Environment=cfg.get("Environment", {"Variables": {}}),
            VpcConfig={"SubnetIds": cfg.get("VpcConfig", {}).get("SubnetIds", []),
                       "SecurityGroupIds": cfg.get("VpcConfig", {}).get("SecurityGroupIds", [])})
        stack.wait(short)
        stack.lam.update_function_code(FunctionName=stack.name(short), ZipFile=(folder / f"lambda-{short}.zip").read_bytes())
        stack.wait(short)
        print(f"restored Lambda {short}")
    for short in STATE_MACHINES:
        saved = json.loads((folder / f"sfn-{short}.json").read_text())
        stack.sfn.update_state_machine(stateMachineArn=stack.sm_arn(short), definition=json.dumps(saved["definition"]))
        print(f"restored state machine {short}")


# ---------------------------------------------------------------- agents

def agent_runtime(stack: Stack, name: str) -> dict:
    token = None
    while True:
        page = stack.agentcore.list_agent_runtimes(**({"nextToken": token} if token else {}))
        for found in page["agentRuntimes"]:
            if found["agentRuntimeName"] == name:
                return stack.agentcore.get_agent_runtime(agentRuntimeId=found["agentRuntimeId"])
        token = page.get("nextToken")
        if not token:
            raise SystemExit(f"No AgentCore runtime named {name}")


def agent_location(runtime: dict) -> tuple[str, str]:
    s3 = runtime["agentRuntimeArtifact"]["codeConfiguration"]["code"]["s3"]
    return s3["bucket"], s3["prefix"]


def agent_zip(stack: Stack, runtime: dict) -> bytes:
    bucket, key = agent_location(runtime)
    return stack.s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def build_folder(short: str) -> pathlib.Path:
    """The build/<name>/ folder Terraform zips for this Lambda or agent, written by scripts/build_packages.py."""
    folder = build_packages.ROOT / "build" / short.replace("-", "_")
    if not folder.is_dir():
        raise SystemExit(f"{folder} does not exist: run scripts/build_packages.py first")
    return folder


def zip_build(folder: pathlib.Path) -> bytes:
    """What Terraform's archive_file makes of a build folder: the same bytes go to AWS here and there."""
    root = build_packages.long_path(folder)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(root).as_posix())
    return buf.getvalue()


def gateway_url(stack: Stack) -> str:
    for found in stack.agentcore.list_gateways()["items"]:
        if found["name"] == GATEWAY_NAME:
            return stack.agentcore.get_gateway(gatewayIdentifier=found["gatewayId"])["gatewayUrl"]
    raise SystemExit(f"No gateway named {GATEWAY_NAME}")


def patch_agent_role(stack: Stack, runtime: dict, model_id: str, gateway_id: str | None) -> list[tuple[str, str, dict, dict]]:
    """The runtime role with the streaming call and the agents' model, and the gateway for investigation."""
    role = runtime["roleArn"].split("/")[-1]
    profile = f"arn:aws:bedrock:*:{stack.account}:inference-profile/{model_id}"
    changes = []
    for pname in stack.iam.list_role_policies(RoleName=role)["PolicyNames"]:
        old = stack.iam.get_role_policy(RoleName=role, PolicyName=pname)["PolicyDocument"]
        new = copy.deepcopy(old)
        statements = as_list(new["Statement"])
        invoke = [s for s in statements if "bedrock:InvokeModel" in as_list(s["Action"])]
        for st in invoke:
            actions = as_list(st["Action"])
            if "bedrock:InvokeModelWithResponseStream" not in actions:
                actions.append("bedrock:InvokeModelWithResponseStream")
            st["Action"] = actions
            resources = [r for r in as_list(st["Resource"]) if "inference-profile/" not in r] + [profile]
            st["Resource"] = resources
        if not invoke and "execution" in pname:        # the investigation role had no model access at all
            statements.append({"Sid": "InvokeInvestigationModel", "Effect": "Allow",
                               "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                               "Resource": ["arn:aws:bedrock:*::foundation-model/*", profile]})
            if gateway_id:
                statements.append({"Sid": "CallToolsGateway", "Effect": "Allow", "Action": ["bedrock-agentcore:InvokeGateway"],
                                   "Resource": f"arn:aws:bedrock-agentcore:{stack.region}:{stack.account}:gateway/{gateway_id}"})
        new["Statement"] = statements
        if new != old:
            changes.append((role, pname, old, new))
    return changes


def upload_agent(stack: Stack, runtime: dict, data: bytes, env: dict | None = None) -> None:
    """Put the zip where the runtime reads it, and roll the runtime to a new version."""
    bucket, key = agent_location(runtime)
    stack.s3.put_object(Bucket=bucket, Key=key, Body=data)
    fields = {k: runtime[k] for k in UPDATE_FIELDS if k in runtime}
    if env is not None:
        fields["environmentVariables"] = env
    stack.agentcore.update_agent_runtime(
        agentRuntimeId=runtime["agentRuntimeId"], agentRuntimeArtifact=runtime["agentRuntimeArtifact"], **fields)
    for _ in range(60):
        status = stack.agentcore.get_agent_runtime(agentRuntimeId=runtime["agentRuntimeId"])["status"]
        if status == "READY":
            return
        if status.endswith("FAILED"):
            raise SystemExit(f"{runtime['agentRuntimeName']} update failed: {status}")
        time.sleep(10)
    raise SystemExit(f"{runtime['agentRuntimeName']} did not become READY in 10 minutes")


def norm(text: str) -> str:
    return text.replace("\r\n", "\n")


# ---------------------------------------------------------------- gateway

# The tool Lambda behind each gateway target, and its folder under lambda/, which holds its tool_schema.json.
TOOL_FOLDERS = {
    "s4hana-tool": "s4hana-tool", "spvr-tool": "spvr_tool", "payment-rules-tool": "payment_rules_tool",
    "root-cause-map-tool": "root_cause_tool", "draft-builder-tool": "draft-builder-tool",
}


def gateway_id(stack: Stack) -> str:
    for found in stack.agentcore.list_gateways()["items"]:
        if found["name"] == GATEWAY_NAME:
            return found["gatewayId"]
    raise SystemExit(f"No gateway named {GATEWAY_NAME}")


def gateway_targets(stack: Stack) -> list[dict]:
    gid, items, token = gateway_id(stack), [], None
    while True:
        page = stack.agentcore.list_gateway_targets(gatewayIdentifier=gid, **({"nextToken": token} if token else {}))
        items += page["items"]
        token = page.get("nextToken")
        if not token:
            return [stack.agentcore.get_gateway_target(gatewayIdentifier=gid, targetId=i["targetId"]) for i in items]


# Short names, because the gateway names each tool "<target>___<tool>" and Bedrock rejects a
# tool name over 64 characters. The s4hana target already has a short name and keeps it.
SHORT_TARGET_NAMES = {"spvr-tool": "spvr-target", "payment-rules-tool": "payment-rules-target",
                      "root-cause-map-tool": "root-cause-map-target", "draft-builder-tool": "draft-builder-target"}


def short_name(target: dict) -> str:
    function = target["targetConfiguration"]["mcp"]["lambda"]["lambdaArn"].split(":")[-1].replace(f"{PREFIX}-", "")
    return SHORT_TARGET_NAMES.get(function, target["name"])


def update_target(stack: Stack, target: dict, config: dict, name: str | None = None) -> None:
    stack.agentcore.update_gateway_target(
        gatewayIdentifier=gateway_id(stack), targetId=target["targetId"], name=name or target["name"],
        **({"description": target["description"]} if target.get("description") else {}),
        targetConfiguration=config, credentialProviderConfigurations=target["credentialProviderConfigurations"])


def wanted_target_config(target: dict, tools_dir: pathlib.Path) -> dict | None:
    lam = target["targetConfiguration"]["mcp"]["lambda"]
    folder = TOOL_FOLDERS.get(lam["lambdaArn"].split(":")[-1].replace(f"{PREFIX}-", ""))
    if not folder:
        return None
    schema = json.loads((tools_dir / folder / "tool_schema.json").read_text(encoding="utf-8"))
    return {"mcp": {"lambda": {"lambdaArn": lam["lambdaArn"], "toolSchema": {"inlinePayload": schema}}}}


# ---------------------------------------------------------------- iam

def patch_policy(doc: dict, stack: Stack, drop: tuple = (), add: tuple = ()) -> dict:
    """The policy with agent invoke paths added, some Lambdas dropped, statements added."""
    new = copy.deepcopy(doc)
    statements = as_list(new["Statement"])
    for st in statements:
        if "bedrock-agentcore:InvokeAgentRuntime" in as_list(st["Action"]):
            resources = as_list(st["Resource"])
            for arn in list(resources):
                if ":runtime/" in arn and not arn.endswith("*") and arn + ENDPOINT not in resources:
                    resources.append(arn + ENDPOINT)
            st["Resource"] = resources
        if "lambda:InvokeFunction" in as_list(st["Action"]) and drop:
            kept = [r for r in as_list(st["Resource"]) if not r.endswith(drop)]
            st["Resource"] = kept if len(kept) > 1 else (kept[0] if kept else st["Resource"])
    have = {a for st in statements for a in as_list(st["Action"])}
    for st in add:
        if not set(as_list(st["Action"])) <= have:
            statements.append(st)
    new["Statement"] = statements
    return new


def iam_changes(stack: Stack) -> list[tuple[str, str, dict, dict]]:
    secret = stack.ingestion_env["GRAPH_SECRET_ID"]
    kms = stack.config("attachment-processing")["Environment"]["Variables"]["KMS_KEY_ARN"]
    db = {"Sid": "DatabaseProxyIamAuth", "Effect": "Allow", "Action": ["rds-db:connect"], "Resource": stack.rds_db_arn}
    graph = {"Sid": "ReadGraphSecret", "Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"], "Resource": secret}
    decrypt = {"Sid": "DecryptApplicationSecrets", "Effect": "Allow", "Action": ["kms:Decrypt"], "Resource": kms}
    plan = [
        (stack.name("sf2-role"), (), ()),
        (stack.role_of("email-processing"), (), (db,)),
        (stack.role_of("investigation-invocation"), (), (graph, decrypt, db)),
    ]
    changes = []
    for role, drop, add in plan:
        for pname in stack.iam.list_role_policies(RoleName=role)["PolicyNames"]:
            old = stack.iam.get_role_policy(RoleName=role, PolicyName=pname)["PolicyDocument"]
            # new statements go into the policy that already holds the Lambda's main access
            adds = add if any(k in pname for k in ("access", "execution", "invocation")) else ()
            if role.endswith("sf2-role"):
                new = patch_sf2_policy(old, intake_lambda_arn(stack))
            else:
                new = patch_policy(old, stack, (), adds)
            if new != old:
                changes.append((role, pname, old, new))
    return changes


# ---------------------------------------------------------------- env

def env_changes(stack: Stack) -> list[tuple[str, dict, dict | None]]:
    e = stack.ingestion_env
    aurora = {k: e[k] for k in ("AURORA_HOST", "AURORA_DB", "AURORA_USER")}
    graph = {"GRAPH_API_BASE_URL": e["GRAPH_API_BASE_URL"], "GRAPH_SECRET_ID": e["GRAPH_SECRET_ID"]}
    want = {
        "email-processing": (aurora, None),
        "attachment-processing": (graph, None),
        "investigation-invocation": ({**aurora, **graph, "GRAPH_MAILBOX": e["GRAPH_MAILBOX"]}, stack.vpc),
    }
    out = []
    for short, (add, vpc) in want.items():
        cfg = stack.config(short)
        current = cfg.get("Environment", {}).get("Variables", {})
        merged = {**add, **current}                      # never overwrite what is set
        if short == "investigation-invocation" and ENABLE_INVESTIGATION:
            merged["ENABLE_INVESTIGATION_INVOCATION"] = "true"
        has_vpc = bool(cfg.get("VpcConfig", {}).get("SubnetIds"))
        if merged != current or (vpc and not has_vpc):
            out.append((short, merged, vpc if vpc and not has_vpc else None))
    return out


# ---------------------------------------------------------------- code

def env_reads(entry: pathlib.Path) -> set[str]:
    """The environment variables the handler reads, in its own file and in the modules it imports."""
    names = set(ENV_READ.findall(entry.read_text(encoding="utf-8")))
    for path in build_packages.needed_files(entry):
        names |= set(ENV_READ.findall(path.read_text(encoding="utf-8")))
    return names


def missing_env(stack: Stack, clone: pathlib.Path, short: str, extra: dict) -> list[str]:
    entry = clone / "lambda" / CODE[short] / "lambda_function.py"
    have = set(stack.config(short).get("Environment", {}).get("Variables", {})) | set(extra)
    return sorted(n for n in env_reads(entry) - have if not n.startswith("AWS_"))


# ---------------------------------------------------------------- state machines

def check_graph(definition: dict) -> None:
    states = definition["States"]
    targets = {definition["StartAt"]}
    for s in states.values():
        targets |= {s[k] for k in ("Next", "Default") if k in s}
        targets |= {c["Next"] for c in s.get("Choices", [])}
        targets |= {c["Next"] for c in s.get("Catch", [])}
    missing = targets - set(states)
    todo, seen = [definition["StartAt"]], set()
    while todo:
        n = todo.pop()
        if n in seen: continue
        seen.add(n)
        s = states[n]
        todo += [s[k] for k in ("Next", "Default") if k in s] + [c["Next"] for c in s.get("Choices", [])] + [c["Next"] for c in s.get("Catch", [])]
    if missing or set(states) - seen:
        raise SystemExit(f"invalid state machine: missing {sorted(missing)}, unreachable {sorted(set(states) - seen)}")


def with_unwrap(definition: dict) -> dict:
    """A pipe delivers an SQS batch as an array, even of one; a direct start passes the object."""
    if definition["StartAt"] == "UnwrapInput":
        return definition
    definition["States"]["UnwrapInput"] = {"Type": "Choice", "Choices": [
        {"Variable": "$[0]", "IsPresent": True, "Next": "TakeFirstItem"}], "Default": definition["StartAt"]}
    definition["States"]["TakeFirstItem"] = {"Type": "Pass", "InputPath": "$[0]", "Next": definition["StartAt"]}
    definition["StartAt"] = "UnwrapInput"
    return definition


def new_sf1(definition: dict) -> dict:
    text = json.dumps(definition).replace('"$.ingestion_id"', '"$.message_id"')
    text = text.replace("Required ingestion_id must be present", "Required message_id must be present")
    return with_unwrap(json.loads(text))


def intake_task(arn: str, role: str, nxt: str) -> dict:
    return {
        "Type": "Task", "Resource": "arn:aws:states:::lambda:invoke",
        "Parameters": {"FunctionName": arn, "Payload": {"role": role, "case.$": "$"}},
        "ResultSelector": {"case.$": "$.Payload"}, "OutputPath": "$.case",
        "Retry": [{"ErrorEquals": ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException",
                                   "Lambda.TooManyRequestsException", "RuntimeClientError"],
                   "IntervalSeconds": 3, "MaxAttempts": 6, "BackoffRate": 2}],
        "Catch": [{"ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "ProcessingFailed"}],
        "Next": nxt,
    }


def new_sf2(definition: dict, intake_arn: str) -> dict:
    """sf2 as 220-step-function-2.tf defines it: the intake Lambda classifies and extracts, and queue 2 gets the case."""
    old = definition["States"]
    publish = copy.deepcopy(old["PublishToSQS2"])
    publish["Parameters"]["MessageBody.$"] = "$"
    publish["ResultPath"] = None
    publish["Retry"] = [{"ErrorEquals": ["States.ALL"], **{k: v for k, v in old["PublishToSQS2"]["Retry"][0].items() if k != "ErrorEquals"}}]
    publish["Next"] = "ProcessingCompleted"
    return with_unwrap({
        "Comment": "PSC - SF2 Classification and Identifier Extraction",
        "StartAt": "ValidateInput",
        "States": {
            "ValidateInput": {"Type": "Choice", "Choices": [{"And": [
                {"Variable": "$.case_id", "IsPresent": True}, {"Variable": "$.message_id", "IsPresent": True}],
                "Next": "Classify"}], "Default": "InvalidInput"},
            "Classify": intake_task(intake_arn, "classification", "IsPaymentRelated"),
            "IsPaymentRelated": {"Type": "Choice", "Choices": [
                {"Variable": "$.investigate", "BooleanEquals": True, "Next": "ExtractIdentifiers"}], "Default": "NotInvestigated"},
            "ExtractIdentifiers": intake_task(intake_arn, "extraction", "PublishToSQS2"),
            "PublishToSQS2": publish,
            "ProcessingCompleted": {"Type": "Succeed"},
            "NotInvestigated": {"Type": "Succeed"},
            "InvalidInput": old["InvalidInput"],
            "ProcessingFailed": old["ProcessingFailed"],
        },
    })


def intake_lambda_arn(stack: Stack) -> str:
    """The intake Lambda Terraform creates, or the -test copy until it has."""
    for suffix in ("", SUFFIX):
        try:
            return stack.lam.get_function_configuration(FunctionName=f"{PREFIX}-intake-agents{suffix}")["FunctionArn"]
        except stack.lam.exceptions.ResourceNotFoundException:
            continue
    raise SystemExit("The intake-agents Lambda does not exist: run --only new-lambdas first")


def patch_sf2_policy(doc: dict, intake_arn: str) -> dict:
    """sf2 only calls the intake Lambda and publishes to queue 2: the agents are called by the Lambda."""
    keep = [s for s in as_list(doc["Statement"]) if s.get("Sid") in ("PublishToSQS2", "EncryptSQS2Messages")]
    invoke = {"Sid": "InvokeIntakeAgentsLambda", "Effect": "Allow", "Action": ["lambda:InvokeFunction"], "Resource": [intake_arn]}
    return {**doc, "Statement": [invoke, *keep]}


# ---------------------------------------------------------------- new Lambdas

# The Lambdas added in files 395 to 397. They are created here under a -test suffix, so a
# later `terraform apply` can create its own without an "already exists" error.
NEW = {
    "intake-agents": {"timeout": 300, "memory": 512},
    "reconciler": {"timeout": 300, "memory": 512},
    "results-report": {"timeout": 60, "memory": 256},
}
SUFFIX = "-test"


def new_names(stack: Stack, short: str) -> dict:
    base = f"{PREFIX}-{short}{SUFFIX}"
    return {"function": base, "role": f"{base}-role", "log": f"/aws/lambda/{base}"}


def results_bucket_name(stack: Stack) -> str:
    # Shorter than the Terraform name plus the suffix, which would pass S3's 63 characters.
    return f"{PREFIX}-{stack.account}-res{SUFFIX}"


def new_policy(stack: Stack, short: str, v: dict) -> dict:
    db = {"Effect": "Allow", "Action": ["rds-db:connect"], "Resource": stack.rds_db_arn}
    graph = {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"], "Resource": v["secret"]}
    metrics = {"Effect": "Allow", "Action": ["cloudwatch:PutMetricData"], "Resource": "*"}
    kms_use = {"Effect": "Allow", "Action": ["kms:GenerateDataKey", "kms:Decrypt"], "Resource": v["kms"]}
    statements = {
        "intake-agents": [
            graph, {"Effect": "Allow", "Action": ["kms:Decrypt"], "Resource": v["kms"]}, db,
            {"Effect": "Allow", "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
             "Resource": [v["classification_arn"], v["classification_arn"] + ENDPOINT,
                          v["extraction_arn"], v["extraction_arn"] + ENDPOINT]},
            {"Effect": "Allow", "Action": ["bedrock:ApplyGuardrail"], "Resource": v["guardrail_arn"]},
            {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": f"arn:aws:s3:::{v['attachments']}/*"},
            metrics],
        "reconciler": [
            graph, kms_use, db,
            {"Effect": "Allow", "Action": ["states:StartExecution"], "Resource": v["sf1_arn"]},
            {"Effect": "Allow", "Action": ["sqs:SendMessage"], "Resource": v["sqs1_arn"]},
            {"Effect": "Allow", "Action": ["lambda:InvokeFunction"], "Resource": v["ingestion_arn"]},
            metrics],
        "results-report": [
            db, {"Effect": "Allow", "Action": ["s3:PutObject"], "Resource": f"arn:aws:s3:::{v['results_bucket']}/*"}, kms_use],
    }[short]
    return {"Version": "2012-10-17", "Statement": statements}


def new_env(stack: Stack, short: str, v: dict, args) -> dict:
    e = stack.ingestion_env
    aurora = {k: e[k] for k in ("AURORA_HOST", "AURORA_DB", "AURORA_USER")}
    graph = {"GRAPH_API_BASE_URL": e["GRAPH_API_BASE_URL"], "GRAPH_SECRET_ID": e["GRAPH_SECRET_ID"], "GRAPH_MAILBOX": e["GRAPH_MAILBOX"]}
    if short == "intake-agents":
        return {**aurora, **graph, "ATTACHMENT_BUCKET": v["attachments"],
                "CLASSIFICATION_RUNTIME_ARN": v["classification_arn"], "EXTRACTION_RUNTIME_ARN": v["extraction_arn"],
                "GUARDRAIL_ID": v["guardrail_id"], "GUARDRAIL_VERSION": v["guardrail_version"]}
    if short == "reconciler":
        env = {**aurora, **graph, "STATE_MACHINE_ARN": v["sf1_arn"], "SQS1_QUEUE_URL": v["sqs1_url"],
               "INGESTION_FUNCTION": v["ingestion_name"],
               "INGESTION_CUTOFF_UTC": e.get("INGESTION_CUTOFF_UTC", "")}
        if args.reconcile_since:
            env["RECONCILE_SINCE"] = args.reconcile_since
        if args.reconcile_limit is not None:
            env["RECONCILE_LIMIT"] = str(args.reconcile_limit)
        return env
    return {**aurora, "RESULTS_BUCKET": v["results_bucket"]}


def new_values(stack: Stack) -> dict:
    attach = stack.config("attachment-processing")["Environment"]["Variables"]
    cls, ext = agent_runtime(stack, AGENTS["classification"][0]), agent_runtime(stack, AGENTS["extraction"][0])
    guard = stack.config("mail-preprocessing")["Environment"]["Variables"]   # his Terraform sets the guardrail here
    guardrail_id = guard["GUARDRAIL_ID"]
    sqs = boto3.client("sqs", region_name=stack.region)
    sqs1_url = sqs.get_queue_url(QueueName=stack.name("sqs1"))["QueueUrl"]
    return {
        "secret": stack.ingestion_env["GRAPH_SECRET_ID"], "kms": attach["KMS_KEY_ARN"], "attachments": attach["ATTACHMENT_BUCKET"],
        "classification_arn": cls["agentRuntimeArn"], "extraction_arn": ext["agentRuntimeArn"],
        "guardrail_id": guardrail_id, "guardrail_version": guard["GUARDRAIL_VERSION"],
        "guardrail_arn": f"arn:aws:bedrock:{stack.region}:{stack.account}:guardrail/{guardrail_id}",
        "sf1_arn": stack.sm_arn("sf1"), "sqs1_url": sqs1_url,
        "sqs1_arn": f"arn:aws:sqs:{stack.region}:{stack.account}:{stack.name('sqs1')}",
        "ingestion_name": stack.name("email-ingestion"),
        "ingestion_arn": f"arn:aws:lambda:{stack.region}:{stack.account}:function:{stack.name('email-ingestion')}",
        "results_bucket": results_bucket_name(stack),
    }


def ensure_results_bucket(stack: Stack, name: str, kms: str) -> None:
    try:
        stack.s3.head_bucket(Bucket=name)
        return
    except Exception:
        pass
    stack.s3.create_bucket(Bucket=name, CreateBucketConfiguration={"LocationConstraint": stack.region})
    stack.s3.put_public_access_block(Bucket=name, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    stack.s3.put_bucket_encryption(Bucket=name, ServerSideEncryptionConfiguration={"Rules": [{
        "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "aws:kms", "KMSMasterKeyID": kms}, "BucketKeyEnabled": True}]})


def deploy_new_lambdas(stack: Stack, args) -> None:
    v = new_values(stack)
    trust = json.dumps({"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]})
    baseline = f"arn:aws:iam::{stack.account}:policy/{PREFIX}-lambda-base"
    logs = boto3.client("logs", region_name=stack.region)
    for short, size in NEW.items():
        n = new_names(stack, short)
        try:
            role_arn = stack.iam.get_role(RoleName=n["role"])["Role"]["Arn"]
        except stack.iam.exceptions.NoSuchEntityException:
            role_arn = stack.iam.create_role(RoleName=n["role"], AssumeRolePolicyDocument=trust)["Role"]["Arn"]
            print(f"   created role {n['role']}")
        stack.iam.attach_role_policy(RoleName=n["role"], PolicyArn=baseline)
        stack.iam.put_role_policy(RoleName=n["role"], PolicyName="access", PolicyDocument=json.dumps(new_policy(stack, short, v)))
        if short == "results-report":
            ensure_results_bucket(stack, v["results_bucket"], v["kms"])
        package = zip_build(build_folder(short))
        settings = dict(Role=role_arn, Handler="lambda_function.lambda_handler", Runtime="python3.12",
                        Timeout=size["timeout"], MemorySize=size["memory"],
                        Environment={"Variables": new_env(stack, short, v, args)}, VpcConfig=stack.vpc)
        try:
            stack.lam.get_function(FunctionName=n["function"])
            stack.lam.update_function_configuration(FunctionName=n["function"], **settings)
            stack.lam.get_waiter("function_updated_v2").wait(FunctionName=n["function"])
            stack.lam.update_function_code(FunctionName=n["function"], ZipFile=package)
        except stack.lam.exceptions.ResourceNotFoundException:
            for _ in range(12):  # a new role takes a few seconds to be assumable
                try:
                    stack.lam.create_function(FunctionName=n["function"], Code={"ZipFile": package}, **settings)
                    break
                except stack.lam.exceptions.InvalidParameterValueException as exc:
                    if "role" not in str(exc).lower():
                        raise
                    time.sleep(5)
        stack.lam.get_waiter("function_active_v2").wait(FunctionName=n["function"])
        try:
            logs.create_log_group(logGroupName=n["log"])
        except logs.exceptions.ResourceAlreadyExistsException:
            pass
        logs.put_retention_policy(logGroupName=n["log"], retentionInDays=30)
        print(f"   {n['function']}: {len(package) / 1e6:.1f} MB deployed")


def remove_new_lambdas(stack: Stack) -> None:
    logs = boto3.client("logs", region_name=stack.region)
    for short in NEW:
        n = new_names(stack, short)
        for call, kwargs in ((stack.lam.delete_function, {"FunctionName": n["function"]}),
                             (logs.delete_log_group, {"logGroupName": n["log"]})):
            try:
                call(**kwargs)
            except Exception:
                pass
        try:
            stack.iam.detach_role_policy(RoleName=n["role"], PolicyArn=f"arn:aws:iam::{stack.account}:policy/{PREFIX}-lambda-base")
            stack.iam.delete_role_policy(RoleName=n["role"], PolicyName="access")
            stack.iam.delete_role(RoleName=n["role"])
        except Exception:
            pass
        print(f"   removed {n['function']}")
    bucket = results_bucket_name(stack)
    try:
        for page in stack.s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                stack.s3.delete_object(Bucket=bucket, Key=obj["Key"])
        stack.s3.delete_bucket(Bucket=bucket)
        print(f"   removed bucket {bucket}")
    except Exception:
        pass


# ---------------------------------------------------------------- run

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--clone", default=str(pathlib.Path(__file__).resolve().parent.parent),
                    help="the folder holding the Terraform files and lambda/ (this repository's root)")
    ap.add_argument("--region", default="us-east-2")
    ap.add_argument("--only", choices=["iam", "env", "code", "gateway", "agents", "state-machines", "new-lambdas"], action="append")
    ap.add_argument("--tools-dir", default=str(pathlib.Path(__file__).resolve().parent.parent / "lambda"),
                    help="gateway: the folder holding each tool Lambda's folder, with its tool_schema.json")
    ap.add_argument("--enable-investigation", action="store_true",
                    help="env: set ENABLE_INVESTIGATION_INVOCATION=true on the investigation Lambda")
    ap.add_argument("--agent-model", default="global.anthropic.claude-sonnet-4-6",
                    help="agents: the model every agent runs on, as BEDROCK_MODEL_ID")
    ap.add_argument("--remove-new", action="store_true", help="delete what the new-lambdas step created")
    ap.add_argument("--reconcile-since", help="new-lambdas: RECONCILE_SINCE for the reconciler, an ISO time")
    ap.add_argument("--reconcile-limit", type=int, help="new-lambdas: RECONCILE_LIMIT for the reconciler")
    ap.add_argument("--apply", action="store_true", help="make the changes; without it, only show them")
    ap.add_argument("--backup-dir", default=None)
    ap.add_argument("--restore", metavar="BACKUP_DIR")
    args = ap.parse_args()
    global ENABLE_INVESTIGATION
    ENABLE_INVESTIGATION = args.enable_investigation
    stack = Stack(args.region)
    if args.restore:
        return restore(stack, pathlib.Path(args.restore))
    if args.remove_new:
        return remove_new_lambdas(stack)
    steps = args.only or ["iam", "env", "code", "gateway", "agents", "state-machines"]
    clone = pathlib.Path(args.clone)
    folder = pathlib.Path(args.backup_dir or f"psc-backup-{datetime.datetime.now():%Y%m%d-%H%M%S}")
    backup(stack, folder)
    mode = "APPLYING" if args.apply else "DRY RUN (add --apply to change anything)"
    print(f"\n{mode} in {stack.region}, account {stack.account}\n")

    if "iam" in steps:
        print("== iam")
        for role, pname, old, new in iam_changes(stack):
            before = {s.get("Sid") or json.dumps(s, sort_keys=True) for s in as_list(old["Statement"])}
            print(f"   {role} / {pname}: statements {len(before)} -> {len(as_list(new['Statement']))}")
            if args.apply:
                stack.iam.put_role_policy(RoleName=role, PolicyName=pname, PolicyDocument=json.dumps(new))
        if args.apply:
            time.sleep(10)  # IAM changes take a few seconds to be seen

    if "env" in steps:
        print("== env")
        for short, merged, vpc in env_changes(stack):
            added = sorted(set(merged) - set(stack.config(short).get("Environment", {}).get("Variables", {})))
            print(f"   {short}: add {added}{' and attach to the VPC' if vpc else ''}")
            if args.apply:
                kwargs = {"VpcConfig": vpc} if vpc else {}
                stack.lam.update_function_configuration(FunctionName=stack.name(short), Environment={"Variables": merged}, **kwargs)
                stack.wait(short)

    if "code" in steps:
        print("== code (zipped from build/, as Terraform zips it)")
        added = {short: merged for short, merged, _ in env_changes(stack)}
        for short in CODE:
            data = zip_build(build_folder(short))
            lacking = missing_env(stack, clone, short, added.get(short, {}))
            print(f"   {short}: {len(data) / 1e6:.1f} MB from build/{short.replace('-', '_')}"
                  + (f"  WARNING env not set: {lacking}" if lacking else ""))
            if args.apply:
                stack.lam.update_function_code(FunctionName=stack.name(short), ZipFile=data)
                stack.wait(short)

    if "gateway" in steps:
        print("== gateway tools (the application's tool schemas)")
        tools_dir = pathlib.Path(args.tools_dir)
        for target in gateway_targets(stack):
            wanted = wanted_target_config(target, tools_dir)
            if wanted is None:
                print(f"   {target['name']}: no tool folder for its Lambda, left as it is")
                continue
            names = [t["name"] for t in wanted["mcp"]["lambda"]["toolSchema"]["inlinePayload"]]
            new_name = short_name(target)
            have = target["targetConfiguration"] == wanted and target["name"] == new_name
            print(f"   {target['name']}: {'already current' if have else f'name {new_name}, tools ' + str(names)}")
            if args.apply and not have:
                update_target(stack, target, wanted, new_name)

    if "agents" in steps:
        print("== agents (the application's own Strands agents)")
        gateway = gateway_url(stack)
        gateway_id = gateway.split("//")[1].split(".")[0]
        for key, (name, source, extra_env) in AGENTS.items():
            runtime = agent_runtime(stack, name)
            folder = build_folder(f"{key}_agent")
            built = build_packages.long_path(folder)
            # main.py and the modules beside it: what the runtime runs.
            local = {p.relative_to(built).as_posix(): norm(p.read_text(encoding="utf-8"))
                     for p in built.glob("*.py")}
            env = {"AWS_REGION": stack.region, "BEDROCK_MODEL_ID": args.agent_model, **extra_env}
            if key == "investigation":
                env["GATEWAY_URL"] = gateway
            with zipfile.ZipFile(io.BytesIO(agent_zip(stack, runtime))) as z:
                names = z.namelist()
                same_code = all(n in names and norm(z.read(n).decode("utf-8")) == text for n, text in local.items())
            up_to_date = any(n.startswith("strands/") for n in names) and same_code \
                and runtime.get("environmentVariables") == env
            policies = patch_agent_role(stack, runtime, args.agent_model, gateway_id if key == "investigation" else None)
            print(f"   {key}: {'already current' if up_to_date else 'deploy the agent from ' + source}"
                  f"; role policy changes: {[p for _, p, _, _ in policies] or 'none'}")
            if args.apply:
                for role, pname, _, new in policies:
                    stack.iam.put_role_policy(RoleName=role, PolicyName=pname, PolicyDocument=json.dumps(new))
                if policies:
                    time.sleep(10)
                if not up_to_date:
                    upload_agent(stack, runtime, zip_build(folder), env)

    if "new-lambdas" in (args.only or []):
        print("== new Lambdas (named *-test, see --remove-new)")
        for short in NEW:
            print(f"   {new_names(stack, short)['function']}")
        if args.apply:
            deploy_new_lambdas(stack, args)

    if "state-machines" in steps:
        print("== state machines")
        intake_arn = intake_lambda_arn(stack)
        for short, make in (("sf1", new_sf1), ("sf2", lambda d: new_sf2(d, intake_arn))):
            current = json.loads(stack.sfn.describe_state_machine(stateMachineArn=stack.sm_arn(short))["definition"])
            wanted = make(current)
            check_graph(wanted)
            print(f"   {short}: {'unchanged' if wanted == current else f'{len(current['States'])} -> {len(wanted['States'])} states'}")
            if args.apply and wanted != current:
                stack.sfn.update_state_machine(stateMachineArn=stack.sm_arn(short), definition=json.dumps(wanted))
    print("\ndone" if args.apply else "\nnothing changed")


if __name__ == "__main__":
    main()
