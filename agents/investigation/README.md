# investigation AgentCore runtime

The runtime's code is `main.py`, generated from the application package. It calls the payment tools through
the AgentCore Gateway. `scripts/build_packages.py` copies it into `build/investigation_agent/`; Terraform zips that folder.
