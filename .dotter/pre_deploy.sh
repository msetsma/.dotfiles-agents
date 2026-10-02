#!/usr/bin/env bash
# dotter pre-deploy hook: regenerate generated/ and merge the catalog into the
# shared agent configs. Running `dotter` therefore keeps every agent in sync in
# one step - merge first, then deploy the pure-MCP symlinks.
#
# NOTE: dotter executes hooks from a copy under .dotter/cache/, so BASH_SOURCE
# does not point back into the repo. Resolve the repo from AGENT_HOME (default
# ~/.agentdots) instead of from this file's location.
set -euo pipefail

exec "${AGENT_HOME:-$HOME/.agentdots}/bin/agent-sync"
