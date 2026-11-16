# classification AgentCore runtime

The runtime's code is `main.py`, generated from the application package: the same file serves the
classification and extraction runtimes, and `AGENT_ROLE` picks the job. `scripts/build_packages.py`
copies it into `build/classification_agent/` with the agent dependencies; Terraform zips that folder.
