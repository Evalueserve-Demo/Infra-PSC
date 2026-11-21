# PSC #310 Investigation Invocation Lambda: sets the switches, then runs invoke_agent.py beside it.
import os

"""#310 Investigation Invocation Lambda.
Takes a case from SQS2 and runs the investigation: one agent call per invoice,
saving the result to the database, the Outlook colour and the draft reply.
Needs boto3, psycopg and psycopg-binary, on Python 3.12.

Terraform #310 has to set AURORA_HOST, AURORA_DB, AURORA_USER, GRAPH_SECRET_ID
and GRAPH_API_BASE_URL, and give the function database and network access.
AGENT_RUNTIME_ARN is filled in from INVESTIGATION_RUNTIME_ARN.
ENABLE_INVESTIGATION_INVOCATION turns the Lambda on or off.
"""


def lambda_handler(event, context=None):
    if os.getenv('ENABLE_INVESTIGATION_INVOCATION', 'true').lower() != 'true':
        raise RuntimeError('Investigation invocation disabled by configuration')
    os.environ.setdefault('AGENT_RUNTIME_ARN', os.getenv('INVESTIGATION_RUNTIME_ARN', ''))
    if not os.environ['AGENT_RUNTIME_ARN']:
        raise RuntimeError('Missing AGENT_RUNTIME_ARN / INVESTIGATION_RUNTIME_ARN')
    from invoke_agent import lambda_handler as developer_handler
    return developer_handler(event, context)
