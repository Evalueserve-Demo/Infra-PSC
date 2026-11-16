# Build and validation scripts

Add deterministic packaging scripts for Lambda and AgentCore ZIP artifacts.

## build_packages.py

Writes the `build/<name>/` folders that Terraform zips with `archive_file`, from
the code in `lambda/`. Run it before `terraform apply`, or before the deploy
script in `infra/`. `--deps` and `--agent-bundle` are explained at the top of the
script.

## rotate_graph_secret.py

Replaces the Microsoft Graph client secret in Secrets Manager. It asks for the new
value, so the secret is never written to a file or a command line.
