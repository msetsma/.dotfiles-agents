# .dotfiles-agents task runner
.PHONY: help sync agent-sync agent-check agent-outdated agent-update

help: ## list targets
	@printf "\nUsage: make <target>\n\nTargets:\n\n"
	@grep -E '^[a-z][a-z-]*:.*##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  %-15s %s\n", $$1, $$2}'
	@printf "\n"

sync: agent-sync ## render + merge the catalog into every agent
agent-sync: ## render the catalog into every agent
	bin/agent-sync

agent-check: ## dry-run: show what would change
	bin/agent-sync --dry-run

agent-outdated: ## report newer npm / uv / git versions
	bin/agent-update

agent-update: ## apply updates, then re-sync
	bin/agent-update --apply
