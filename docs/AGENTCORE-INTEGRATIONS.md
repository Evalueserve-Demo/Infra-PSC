# AgentCore deployment and external dependencies

Stages #260, #320, #330–#360 are **NOT deployed** by this source bundle. The Python contract modules are included, but AgentCore Runtime, Identity, Policy, Memory, and Gateway require explicit supported provider resources, packaging, auth configuration, and tool schemas. Do not substitute empty Terraform resources or claim these integrations work.

To complete: confirm AWS AgentCore provider/API version, ZIP entrypoint and upload workflow, invoke payload schema, IAM runtime roles, memory retention and isolation, identity provider, gateway authentication and MCP target definitions, and approved model IDs. Then implement resources and integration tests.

External APIs: Graph app tenant/client secret and mailbox, SAP Ariba API, SAP S/4HANA API, PR/ITAR API, investigation data source and human-review identity provider. No real URLs, credentials or API contracts were supplied.
