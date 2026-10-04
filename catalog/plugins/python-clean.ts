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
//
// Two registered tools give the agent escape hatches:
//   python_clean_skip      lifts the gate for one file after the user approves an
//                          off-topic skip (calls `bin/clean-python --waive`).
//   python_clean_refactor  fixes the file in a fresh child session, so ruff noise
//                          never lands in the main session's context. Configure
//                          the child's agent with the plugin `refactorAgent` option.
import { spawnSync } from "node:child_process"

const HOOK = `${process.env.HOME}/.dotfiles-agents/bin/clean-python`
const MAX_PROMPTS = 3
const REFACTOR_TIMEOUT_MS = 5 * 60 * 1000

function digest(text: string): string {
  let h = 0
  for (let i = 0; i < text.length; i++) h = (Math.imul(31, h) + text.charCodeAt(i)) | 0
  return String(h)
}

function isPython(file: unknown): file is string {
  return typeof file === "string" && /\.pyi?$/.test(file)
}

function runGate(file: string, cwd?: string) {
  return spawnSync(HOOK, [file], { encoding: "utf8", cwd })
}

export default {
  id: "dotfiles-agents.python-clean",
  async setup(ctx: any) {
    const cwd = ctx.location?.directory

    // Record a user-approved "off-topic" skip for one file.
    await safe(async () =>
      ctx.tool.transform((editor: any) => {
        editor.add({
          name: "python_clean_skip",
          description:
            "Skip the python-clean ruff gate for one Python file whose findings are off-topic. " +
            "Ask the user for permission first; only call this once they agree.",
          input: {
            type: "object",
            properties: {
              file: { type: "string", description: "Path to the Python file to skip." },
              reason: { type: "string", description: "Why the findings are off-topic for this task." },
            },
            required: ["file", "reason"],
            additionalProperties: false,
          },
          execute: async (input: any) => {
            const file = input?.file
            if (!isPython(file)) {
              return { content: "python_clean_skip: a Python `file` path is required." }
            }
            const waived = spawnSync(HOOK, ["--waive", file], { encoding: "utf8", cwd })
            const note = (waived.stderr || waived.stdout || "").trim()
            return { content: note || `Waived ${file} for its current findings.` }
          },
        })
      }),
    )

    // Hand the fix to a fresh child session, off the main conversation.
    await safe(async () =>
      ctx.tool.transform((editor: any) => {
        editor.add({
          name: "python_clean_refactor",
          description:
            "Fix remaining ruff findings for one Python file in a fresh child session, keeping the " +
            "work out of the main conversation. Use when you would rather not fix it inline.",
          input: {
            type: "object",
            properties: {
              file: { type: "string", description: "Path to the Python file to refactor." },
            },
            required: ["file"],
            additionalProperties: false,
          },
          execute: async (input: any, context: any) => {
            const file = input?.file
            if (!isPython(file)) {
              return { content: "python_clean_refactor: a Python `file` path is required." }
            }
            const caller = context?.sessionID
            if (typeof caller === "string" && (await safeGet(ctx, `refactor-child:${caller}`))) {
              return { content: "python_clean_refactor: refusing to nest refactors; fix the file in place." }
            }
            const result = runGate(file, cwd)
            if (result.status === 0) {
              return { content: `${file} already passes ruff; nothing to refactor.` }
            }
            const report = (result.stderr || result.stdout || "").trim()
            return { content: await refactorInChild(ctx, file, report, cwd) }
          },
        })
      }),
    )

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
      if (!isPython(file)) return

      const result = runGate(file, cwd)
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
            "",
            "Escape hatches, only if they fit:",
            "- If this file is genuinely off-topic for the user's task, ask the user for permission and, if they agree, call python_clean_skip with the file and a reason.",
            "- If you would rather fix it in a fresh, isolated context, call python_clean_refactor with this file.",
          ].join("\n"),
        })
      })
    })
  },
}

async function refactorInChild(
  ctx: any,
  file: string,
  report: string,
  cwd?: string,
): Promise<string> {
  const guardKey = `refactor:${file}`
  if (await safeGet(ctx, guardKey)) {
    return `A refactor for ${file} is already in progress.`
  }
  await safe(() => ctx.storage.set(guardKey, { started: Date.now() }))

  try {
    const input: any = { title: `ruff fix: ${file}` }
    const agent = ctx.options?.refactorAgent
    if (typeof agent === "string" && agent) input.agent = agent

    const created = await ctx.session.create(input)
    const childID = created?.id
    if (typeof childID !== "string") return "Could not create a refactor session."
    await safe(() => ctx.storage.set(`refactor-child:${childID}`, true))

    const prompt = [
      "Fix ruff findings in exactly one file, isolated from the main conversation.",
      "",
      `File: ${file}`,
      "",
      "Findings that ruff's auto-fix could not resolve:",
      report,
      "",
      "Fix the code itself; a `# noqa` suppression is not accepted (the gate ignores it).",
      "Edit only this file, and do not call python_clean_refactor.",
      `Verify with: ${HOOK} ${JSON.stringify(file)} -- it must exit 0.`,
    ].join("\n")
    await ctx.session.prompt({ sessionID: childID, text: prompt })

    let timedOut = false
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), REFACTOR_TIMEOUT_MS)
    try {
      await ctx.session.wait({ sessionID: childID }, { signal: controller.signal })
    } catch {
      timedOut = true
    } finally {
      clearTimeout(timer)
    }

    if (timedOut) {
      await safe(() => ctx.session.interrupt({ sessionID: childID, continue: false }))
      return `Refactor session timed out after ${REFACTOR_TIMEOUT_MS / 1000}s; ${file} may still fail ruff.`
    }

    const check = runGate(file, cwd)
    if (check.status === 0) return `Refactor session fixed ${file}.`
    const detail = (check.stderr || check.stdout || "").trim()
    return `Refactor session finished but ${file} still fails ruff:\n${detail}`
  } finally {
    await safe(() => ctx.storage.remove(guardKey))
  }
}

async function safeGet(ctx: any, key: string): Promise<unknown> {
  try {
    return await ctx.storage.get(key)
  } catch {
    return undefined
  }
}

async function safe(fn: () => unknown): Promise<void> {
  try {
    await fn()
  } catch {
    // Enforcement is best-effort; a storage or session failure must not break
    // the tool result the model already received.
  }
}
