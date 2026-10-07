/**
 * Pure formatting logic for the quiet transcript.
 *
 * Everything here returns plain (unstyled) strings so it can be unit tested
 * with `node --test` without loading Pi or the terminal UI. The extension
 * applies theme colors on top of these values.
 */

export interface ContentBlock {
	type: string;
	text?: string;
}

export interface ToolResultLike {
	content?: ContentBlock[];
	details?: unknown;
}

export interface CallDescriptor {
	/** Tool verb, e.g. `read` or `$`. */
	label: string;
	/** The salient argument, e.g. `src/server.ts:12-40`. */
	detail: string;
}

export interface ResultSummary {
	text: string;
	isError: boolean;
}

function asRecord(value: unknown): Record<string, unknown> {
	return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

/** Shorten a path relative to the working directory, falling back to `~`. */
export function relPath(path: unknown, cwd: string): string {
	if (typeof path !== "string" || path.length === 0) return ".";
	if (!path.startsWith("/")) return path;
	const home = process.env.HOME ?? "";
	if (cwd && path.startsWith(`${cwd}/`)) return path.slice(cwd.length + 1);
	if (cwd && path === cwd) return ".";
	if (home && path.startsWith(`${home}/`)) return `~${path.slice(home.length)}`;
	if (path.startsWith("/private/var/") || path.startsWith("/var/")) return path;
	return path;
}

export function truncate(text: string, max: number): string {
	const oneLine = text.replace(/\s+/g, " ").trim();
	if (oneLine.length <= max) return oneLine;
	return `${oneLine.slice(0, Math.max(0, max - 1))}…`;
}

/** Model-facing text of a tool result, blocks joined by newlines. */
export function textOf(result: ToolResultLike | undefined): string {
	const blocks = result?.content ?? [];
	return blocks
		.filter((block) => block?.type === "text" && typeof block.text === "string")
		.map((block) => block.text as string)
		.join("\n");
}

export function hasImage(result: ToolResultLike | undefined): boolean {
	return (result?.content ?? []).some((block) => block?.type === "image");
}

/** Number of lines, ignoring a single trailing newline. */
export function lineCount(text: string): number {
	if (!text) return 0;
	const body = text.endsWith("\n") ? text.slice(0, -1) : text;
	if (body.length === 0) return 0;
	return body.split("\n").length;
}

/** Number of non-blank lines, for command output and listings. */
export function nonBlankCount(text: string): number {
	if (!text) return 0;
	return text.split("\n").filter((line) => line.trim().length > 0).length;
}

export function firstLine(text: string, max = 140): string {
	const line = text.split("\n").find((candidate) => candidate.trim().length > 0) ?? "";
	return truncate(line, max);
}

/** `+N −M` counts for a unified patch or display diff. */
export function diffStat(patch: string): { added: number; removed: number } {
	let added = 0;
	let removed = 0;
	for (const line of patch.split("\n")) {
		if (line.startsWith("+++") || line.startsWith("---")) continue;
		if (line.startsWith("+")) added += 1;
		else if (line.startsWith("-")) removed += 1;
	}
	return { added, removed };
}

function rangeSuffix(args: Record<string, unknown>): string {
	const offset = typeof args.offset === "number" ? args.offset : undefined;
	const limit = typeof args.limit === "number" ? args.limit : undefined;
	if (offset === undefined && limit === undefined) return "";
	const start = offset ?? 1;
	if (limit === undefined || limit <= 0) return `:${start}-`;
	return `:${start}-${start + limit - 1}`;
}

const PREFERRED_ARG_KEYS = ["path", "file", "command", "pattern", "query", "url", "name", "prompt", "text"];

/** Best-effort one-line descriptor for a tool call. */
export function describeCall(toolName: string, args: unknown, cwd: string): CallDescriptor {
	const a = asRecord(args);
	const path = relPath(a.path ?? a.file, cwd);
	switch (toolName) {
		case "read":
			return { label: "read", detail: `${path}${rangeSuffix(a)}` };
		case "bash":
			return { label: "$", detail: truncate(String(a.command ?? ""), 120) };
		case "powershell":
			return { label: ">", detail: truncate(String(a.command ?? ""), 120) };
		case "write": {
			const lines = typeof a.content === "string" ? lineCount(a.content) : 0;
			return { label: "write", detail: lines > 0 ? `${path} (${lines} lines)` : path };
		}
		case "edit":
			return { label: "edit", detail: path };
		case "grep": {
			let detail = `/${truncate(String(a.pattern ?? ""), 60)}/`;
			if (typeof a.path === "string") detail += ` ${relPath(a.path, cwd)}`;
			if (typeof a.glob === "string") detail += ` (${a.glob})`;
			return { label: "grep", detail };
		}
		case "find": {
			let detail = truncate(String(a.pattern ?? ""), 60);
			if (typeof a.path === "string") detail += ` in ${relPath(a.path, cwd)}`;
			return { label: "find", detail };
		}
		case "ls":
			return { label: "ls", detail: relPath(a.path ?? ".", cwd) };
		default: {
			for (const key of PREFERRED_ARG_KEYS) {
				const value = a[key];
				if (typeof value === "string" && value.length > 0) {
					return { label: toolName, detail: truncate(value, 100) };
				}
			}
			const first = Object.values(a).find((value) => typeof value === "string" && value.length > 0);
			return { label: toolName, detail: typeof first === "string" ? truncate(first, 100) : "" };
		}
	}
}

function detailsOf(result: ToolResultLike | undefined): Record<string, unknown> {
	return asRecord(result?.details);
}

/** Compact, single-line outcome for a finished tool call. */
export function resultSummary(
	toolName: string,
	args: unknown,
	result: ToolResultLike | undefined,
	isError: boolean,
): ResultSummary {
	const text = textOf(result);
	if (isError) {
		return { text: `error: ${firstLine(text) || "failed"}`, isError: true };
	}

	const details = detailsOf(result);
	const a = asRecord(args);

	switch (toolName) {
		case "read": {
			if (hasImage(result)) return { text: "image", isError: false };
			let out = `${lineCount(text)} lines`;
			if (details.truncation) out += " (truncated)";
			return { text: out, isError: false };
		}
		case "bash":
		case "powershell": {
			const parts: string[] = [];
			const exit = /exit code:\s*(\d+)/i.exec(text);
			if (exit) parts.push(exit[1] === "0" ? "exit 0" : `exit ${exit[1]}`);
			else parts.push("done");
			const withoutExit = text
				.split("\n")
				.filter((line) => !/^\s*exit code:\s*\d+\s*$/i.test(line))
				.join("\n");
			const lines = nonBlankCount(withoutExit);
			if (lines > 0) parts.push(`${lines} lines`);
			if (details.truncation) parts.push("truncated");
			return { text: parts.join(" · "), isError: false };
		}
		case "write": {
			const lines = typeof a.content === "string" ? lineCount(a.content) : 0;
			return { text: lines > 0 ? `wrote ${lines} lines` : "written", isError: false };
		}
		case "edit": {
			const patch = typeof details.patch === "string" ? details.patch : typeof details.diff === "string" ? details.diff : "";
			if (patch) {
				const stat = diffStat(patch);
				return { text: `+${stat.added} −${stat.removed}`, isError: false };
			}
			const edits = Array.isArray(a.edits) ? a.edits.length : 0;
			return { text: edits > 0 ? `${edits} edit${edits === 1 ? "" : "s"}` : "edited", isError: false };
		}
		case "grep": {
			let out = `${nonBlankCount(text)} matches`;
			if (details.truncation || details.linesTruncated || details.matchLimitReached) out += " (truncated)";
			return { text: out, isError: false };
		}
		case "find": {
			let out = `${nonBlankCount(text)} files`;
			if (details.truncation || details.resultLimitReached) out += " (truncated)";
			return { text: out, isError: false };
		}
		case "ls": {
			let out = `${nonBlankCount(text)} entries`;
			if (details.truncation || details.entryLimitReached) out += " (truncated)";
			return { text: out, isError: false };
		}
		default: {
			if (!text) return { text: "ok", isError: false };
			const lines = nonBlankCount(text);
			return { text: lines > 1 ? `${lines} lines` : truncate(text, 100), isError: false };
		}
	}
}

/** Full, unstyled detail body used by the overlay and the verbose mode. */
export function detailText(toolName: string, args: unknown, result: ToolResultLike | undefined, cwd: string): string {
	const a = asRecord(args);
	const details = detailsOf(result);
	const text = textOf(result);

	switch (toolName) {
		case "bash":
		case "powershell": {
			const command = String(a.command ?? "");
			const body = text.trimEnd();
			const note = typeof details.fullOutputPath === "string" ? `\n\n[full output: ${details.fullOutputPath}]` : "";
			return `$ ${command}\n\n${body}${note}`.trimEnd();
		}
		case "edit": {
			const patch = typeof details.patch === "string" ? details.patch : typeof details.diff === "string" ? details.diff : text;
			return `${relPath(a.path, cwd)}\n\n${patch}`.trimEnd();
		}
		case "read":
		case "write":
			return `${relPath(a.path, cwd)}\n\n${text}`.trimEnd();
		case "grep":
		case "find":
		case "ls": {
			const descriptor = describeCall(toolName, args, cwd);
			return `${descriptor.label} ${descriptor.detail}\n\n${text}`.trimEnd();
		}
		default: {
			if (text) return text.trimEnd();
			const json = JSON.stringify(a, null, 2);
			return json && json !== "{}" ? json : "(no detail)";
		}
	}
}

/**
 * Prefix a line with `+`/`-`/space so the extension can color diff bodies.
 * Returns the raw line plus a semantic kind; non-diff text is `context`.
 */
export function classifyDiffLine(line: string): "added" | "removed" | "meta" | "context" {
	if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@") || line.startsWith("diff ")) return "meta";
	if (line.startsWith("+")) return "added";
	if (line.startsWith("-")) return "removed";
	return "context";
}
