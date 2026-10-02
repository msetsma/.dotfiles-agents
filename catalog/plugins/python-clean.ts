// opencode plugin: run the shared Python gate after a write/edit.
//
// Deployed by agent-sync (symlinked into ~/.config/opencode/plugins/). The same
// gate runs as a Claude Code PostToolUse hook via bin/clean-python, so both
// clients enforce identical rules.
//
// After a Python write the plugin does two things:
//   1. Appends the gate's findings to the tool result, so the model sees them
//      in the same turn (reassigning `event.result` is the only mutation that
//      propagates through the V2 hook).
//   2. If anything is still broken, injects a session prompt itself. That makes
//      the fix mandatory: the agent cannot end the turn as if the write were
//      clean, because it gets a follow-up instruction with no human input.
//
// A per-file counter in plugin storage keeps a file that cannot be fixed (say
// an undefined name only the user can resolve) from looping forever: we keep
// re-prompting while the report changes, then hand the last report back and stop.
import { spawnSync } from "node:child_process"

const HOOK = `${process.env.HOME}/.agentdots/bin/clean-python`
const MAX_PROMPTS = 3

function digest(text: string): string {
  let h = 0
  for (let i = 0; i < text.length; i++) h = (Math.imul(31, h) + text.charCodeAt(i)) | 0
  return String(h)
}

export default {
  id: "agentdots.python-clean",
  async setup(ctx: any) {
    const cwd = ctx.location?.directory

    await ctx.tool.hook("execute.after", async (event: any) => {
      if (event.status !== "completed") return
      if (
        event.tool !== "write" &&
        event.tool !== "edit" &&
        event.tool !== "patch"
      ) {
        return
      }
      if (!event.result) return

      const input = event.input ?? {}
      const meta = event.result.metadata ?? {}
      const file =
        meta.files?.[0]?.file ??
        meta.file ??
        input.filePath ??
        input.filepath ??
        input.path
      if (typeof file !== "string" || !/\.pyi?$/.test(file)) return

      const result = spawnSync(HOOK, [file], { encoding: "utf8", cwd })
      const stateKey =
        event.sessionID ? `prompted:${event.sessionID}:${file}` : undefined

      if (result.status === 0) {
        if (stateKey) await safe(() => ctx.storage.remove(stateKey))
        return
      }

      const report = (result.stderr || result.stdout || "").trim()
      if (!report) return

      event.result = {
        ...event.result,
        content: [
          ...(event.result.content ?? []),
          { type: "text", text: report },
        ],
      }

      // Force the agent to act. Best-effort: enforcement must never turn a
      // successful write into a failed tool call.
      if (!stateKey || !event.sessionID) return
      await safe(async () => {
        const prev = (await ctx.storage.get(stateKey)) as
          | { digest?: string; count?: number }
          | undefined
        const count = (prev?.count ?? 0) + 1
        await ctx.storage.set(stateKey, { digest: digest(report), count })

        const progressed = prev?.digest !== digest(report)
        if (count > MAX_PROMPTS) return
        if (!progressed && count > 1) return
        if (typeof ctx.session?.prompt !== "function") return

        await ctx.session.prompt({
          sessionID: event.sessionID,
          text: [
            `clean-python ran after your write to ${file} and ruff could not auto-fix everything:`,
            "",
            report,
            "",
            "Fix the code itself (a `# noqa` suppression is ignored) and write the file again.",
          ].join("\n"),
        })
      })
    })
  },
}

async function safe(fn: () => unknown): Promise<void> {
  try {
    await fn()
  } catch {
    // Enforcement is best-effort; a storage or session failure must not break
    // the tool result the model already received.
  }
}
