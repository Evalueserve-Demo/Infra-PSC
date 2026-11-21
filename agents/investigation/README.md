# investigation AgentCore runtime

The runtime's code is `main.py`, one self-contained file (it is deployed on its own). It calls the payment tools through
the AgentCore Gateway. `scripts/build_packages.py` copies it into `build/investigation_agent/`; Terraform zips that folder.
