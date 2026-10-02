// opencode plugin: run the shared Python gate after a write/edit.
//
// Deployed by agent-sync (symlinked into ~/.config/opencode/plugins/). The same
// gate runs as a Claude Code PostToolUse hook via bin/clean-python, so both
// clients enforce identical rules.
//
// The findings are appended to the tool result's `content`, which is what the
// model reads back - reassigning `event.result` is the only mutation that
// propagates through the V2 hook.
import { spawnSync } from "node:child_process"

const HOOK = `${process.env.HOME}/.agentdots/bin/clean-python`

export default {
  id: "agentdots.python-clean",
  async setup(ctx: any) {
    const cwd = ctx.location?.directory

    await ctx.tool.hook("execute.after", (event: any) => {
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
      if (result.status === 0) return

      const report = (result.stderr || result.stdout || "").trim()
      if (!report) return

      event.result = {
        ...event.result,
        content: [
          ...(event.result.content ?? []),
          { type: "text", text: report },
        ],
      }
    })
  },
}
