
# =========================================================
# 330 - AGENTCORE IDENTITY
# =========================================================
#
# Authentication strategy:
#
# 1. AgentCore Runtime workload identity is managed
#    automatically by AWS.
#
# 2. Investigation Invocation Lambda uses IAM
#    authentication to invoke the AgentCore Runtime.
#
# 3. AgentCore Gateway will use IAM (SigV4)
#    inbound authorization in stage #360.
#
# 4. Enterprise tool authentication will be configured
#    after the client confirms the authentication contract.
#
# 5. No OAuth credential provider is created here.
#
# 6. No placeholder credentials, ARNs or secrets.
#
# =========================================================

