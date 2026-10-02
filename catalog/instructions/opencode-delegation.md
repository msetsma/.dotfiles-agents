# Delegation: fan out by default

Speed matters more than token cost here. Parallelize aggressively.

## Rules

1. Before starting any task with 2 or more parts, split it into independent pieces and launch one subagent per piece with the `subagent` tool.
2. Put ALL `subagent` calls in ONE message. Separate messages run one after another and waste time.
3. Fan out wide. 3 to 6 subagents at once is normal. If you're unsure whether a piece is worth delegating, delegate it.
4. Use `explore` for any reading, searching, or "where is X / how does Y work" question. Use `general` for edits, running commands, and multi-step work.
5. Parallel edits are fine as long as each subagent owns a different set of files. Name the exact files each one may touch so they never overlap.
6. While subagents run, don't redo their work yourself. Wait for the results, then combine them.

## Fan out when

- You'd otherwise search or read more than 2 files
- The task touches more than one module, directory, or concern
- You're researching several questions or options
- You're making the same kind of change in several places (one subagent per file or group of files)
- You're running independent checks (tests, lint, typecheck, build)

## Do it yourself when

- It's a single edit to a file you already know
- Each step depends on the previous step's result

## Writing subagent prompts

Subagents can't see this conversation. Each prompt must include:
- The goal, in one sentence
- The exact files or directories to look at or change
- What to return: a short summary, the file:line locations, or the changes made. No long dumps.
