# .dotfiles-agents task runner
.PHONY: sync agent-sync agent-check agent-outdated agent-update

sync: agent-sync
agent-sync:
	bin/agent-sync

agent-check:
	bin/agent-sync --dry-run

agent-outdated:
	bin/agent-update

agent-update:
	bin/agent-update --apply
