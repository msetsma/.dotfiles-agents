/**
 * pi-quiet — compact transcript rendering for Pi.
 *
 * Three levers:
 *   1. Tools: every tool call renders as one line plus a semantic outcome.
 *   2. Windows: alt+o opens a scrollable overlay with the full output/diff.
 *   3. Modes: /quiet switches quiet | normal | verbose, persisted to quiet.json.
 *
 * Errors are always shown, even in quiet mode.
 */

import type { ExtensionAPI, ExtensionContext, Theme } from "@earendil-works/pi-coding-agent";
import {
	Box,
	Key,
	type KeyId,
	ScrollView,
	Text,
	type TUI,
	matchesKey,
} from "@earendil-works/pi-tui";
import { type QuietConfig, type QuietMode, isQuietMode, loadConfig, saveConfig } from "../src/config.ts";
import {
	type ToolResultLike,
	classifyDiffLine,
	describeCall,
	detailText,
	resultSummary,
} from "../src/summaries.ts";

/** A tool call retained for the detail overlay. */
interface Recent {
	id: string;
	name: string;
	args: unknown;
	result?: ToolResultLike;
	isError: boolean;
	cwd: string;
	order: number;
}

export default function quiet(pi: ExtensionAPI) {
	const config: QuietConfig = loadConfig();
	let mode: QuietMode = config.mode;
	let cwd = process.cwd();

	const recent = new Map<string, Recent>();
	let order = 0;

	function enforceLimit(): void {
		while (recent.size > config.recentLimit) {
			let oldestKey: string | undefined;
			let oldest = Number.POSITIVE_INFINITY;
			for (const [key, value] of recent) {
				if (value.order < oldest) {
					oldest = value.order;
					oldestKey = key;
				}
			}
			if (oldestKey === undefined) break;
			recent.delete(oldestKey);
		}
	}

	function remember(id: string, name: string, patch: Partial<Recent>): void {
		const existing = recent.get(id);
		if (existing) {
			if (patch.args !== undefined) existing.args = patch.args;
			if (patch.result !== undefined) existing.result = patch.result;
			if (patch.isError !== undefined) existing.isError = patch.isError;
			if (patch.cwd !== undefined) existing.cwd = patch.cwd;
			return;
		}
		recent.set(id, {
			id,
			name,
			args: patch.args,
			result: patch.result,
			isError: patch.isError ?? false,
			cwd: patch.cwd ?? cwd,
			order: order++,
		});
		enforceLimit();
	}

	function recentList(): Recent[] {
		return [...recent.values()].sort((a, b) => a.order - b.order);
	}

	function styleDetail(body: string, theme: Theme, toolName: string): string {
		const isDiff = toolName === "edit";
		return body
			.split("\n")
			.map((line) => {
				if (!isDiff) return theme.fg("toolOutput", line);
				switch (classifyDiffLine(line)) {
					case "added":
						return theme.fg("toolDiffAdded", line);
					case "removed":
						return theme.fg("toolDiffRemoved", line);
					default:
						return theme.fg("toolDiffContext", line);
				}
			})
			.join("\n");
	}

	// ── Tools: compact renderers for every tool (built-in and MCP) ────────────
	pi.registerToolRenderer((toolName, next) => {
		const native = next();

		return {
			renderShell: native?.renderShell,

			renderCall(args, theme, context) {
				if (mode === "normal" && native?.renderCall) {
					return native.renderCall(args, theme, context);
				}
				const { label, detail } = describeCall(toolName, args, context.cwd);
				const head = theme.fg("toolTitle", theme.bold(label));
				return new Text(detail ? `${head} ${theme.fg("accent", detail)}` : head, 0, 0);
			},

			renderResult(result, options, theme, context) {
				if (mode === "normal" && native?.renderResult) {
					return native.renderResult(result, options, theme, context);
				}

				remember(context.toolCallId, toolName, {
					args: context.args,
					result: result as ToolResultLike,
					isError: context.isError,
					cwd: context.cwd,
				});

				if (options.isPartial) {
					return new Text(theme.fg("warning", "…"), 0, 0);
				}

				const summary = resultSummary(toolName, context.args, result as ToolResultLike, context.isError);

				if (mode === "verbose") {
					const body = detailText(toolName, context.args, result as ToolResultLike, context.cwd);
					return new Text(styleDetail(body, theme, toolName), 0, 0);
				}

				if (summary.isError && config.alwaysShowErrors) {
					return new Text(theme.fg("error", `✗ ${summary.text}`), 0, 0);
				}

				const preview = options.expanded ? config.previewLines : 0;
				if (preview > 0) {
					const lines = detailText(toolName, context.args, result as ToolResultLike, context.cwd).split("\n");
					const shown = lines.slice(0, preview).join("\n");
					const hidden = lines.length - preview;
					let out = styleDetail(shown, theme, toolName);
					if (hidden > 0) {
						out += `\n${theme.fg("muted", `… ${hidden} more lines — ${config.detailKey} for detail`)}`;
					}
					return new Text(out, 0, 0);
				}

				const color = summary.isError ? "error" : "toolOutput";
				return new Text(`${theme.fg("muted", "→ ")}${theme.fg(color, summary.text)}`, 0, 0);
			},
		};
	});

	// ── Windows: detail overlay for the most recent tool calls ────────────────
	async function openDetail(ctx: ExtensionContext): Promise<void> {
		if (ctx.mode !== "tui") {
			ctx.ui.notify("quiet: the detail overlay needs the interactive TUI", "error");
			return;
		}
		const items = recentList();
		if (items.length === 0) {
			ctx.ui.notify("quiet: no tool calls in this session yet", "info");
			return;
		}
		await ctx.ui.custom<void>(
			(tui, theme, _keybindings, done) => new DetailOverlay(tui, theme, items, config, done),
			{
				overlay: true,
				overlayOptions: { width: "92%", maxHeight: "80%", anchor: "center", margin: 1 },
			},
		);
	}

	pi.registerShortcut(config.detailKey as KeyId, {
		description: "pi-quiet: open the tool detail overlay",
		handler: async (ctx) => {
			await openDetail(ctx);
		},
	});

	// ── Modes ─────────────────────────────────────────────────────────────────
	function setMode(next: QuietMode, ctx: ExtensionContext): void {
		mode = next;
		saveConfig({ ...config, mode: next });
		ctx.ui.notify(
			next === "quiet"
				? `quiet: compact tools · ${config.detailKey} opens detail`
				: `quiet: mode ${next}`,
			"info",
		);
	}

	pi.registerCommand("quiet", {
		description: "Compact transcript: /quiet [quiet|normal|verbose|detail|status]",
		handler: async (args, ctx) => {
			const sub = args.trim().split(/\s+/).filter(Boolean)[0] ?? "status";
			if (sub === "detail" || sub === "show") {
				await openDetail(ctx);
				return;
			}
			if (isQuietMode(sub)) {
				setMode(sub, ctx);
				return;
			}
			if (sub === "status") {
				ctx.ui.notify(`quiet: mode ${mode} · ${config.detailKey} opens detail`, "info");
				return;
			}
			ctx.ui.notify("usage: /quiet [quiet|normal|verbose|detail|status]", "warning");
		},
	});

	// ── State ─────────────────────────────────────────────────────────────────
	pi.on("session_start", async (_event, ctx) => {
		cwd = ctx.cwd;
	});

	pi.on("tool_execution_start", async (event) => {
		if (event.parentToolCallId) return;
		remember(event.toolCallId, event.toolName, { args: event.args });
	});

	pi.on("tool_execution_end", async (event) => {
		if (event.parentToolCallId) return;
		remember(event.toolCallId, event.toolName, {
			result: event.result as ToolResultLike,
			isError: event.isError,
		});
	});
}

/** Scrollable overlay showing one tool call's full output, cycling with [ ]. */
class DetailOverlay {
	focused = false;

	private index: number;
	private readonly items: Recent[];
	private readonly theme: Theme;
	private readonly config: QuietConfig;
	private readonly done: (result: void) => void;
	private readonly tui: TUI;
	private readonly root: Box;
	private readonly header: Text;
	private readonly body: Text;
	private readonly scroll: ScrollView;

	constructor(tui: TUI, theme: Theme, items: Recent[], config: QuietConfig, done: (result: void) => void) {
		this.tui = tui;
		this.theme = theme;
		this.items = items;
		this.config = config;
		this.done = done;
		this.index = items.length - 1;

		this.header = new Text("", 0, 0);
		this.body = new Text("", 0, 0);
		this.scroll = new ScrollView(this.body, {
			follow: "none",
			scrollbar: "auto",
			scrollbarTrackStyle: (text) => theme.fg("scrollbarTrack", text),
			scrollbarThumbStyle: (text) => theme.fg("scrollbarThumb", text),
		});
		const hint = new Text(
			theme.fg("dim", ` ↑↓/jk scroll · [ ] prev/next · esc close · ${items.length} calls`),
			0,
			0,
		);

		this.root = new Box(1, 1, (text) => theme.bg("customMessageBg", text));
		this.root.addChild(this.header);
		this.root.addChild(this.scroll);
		this.root.addChild(hint);

		this.renderCurrent();
	}

	private current(): Recent {
		return this.items[this.index] as Recent;
	}

	private renderCurrent(): void {
		const item = this.current();
		const call = describeCall(item.name, item.args, item.cwd);
		const position = `${this.index + 1}/${this.items.length}`;
		const status = item.result === undefined ? "running…" : item.isError ? "error" : "ok";
		const statusColor = item.isError ? "error" : item.result === undefined ? "warning" : "success";

		this.header.setText(
			`${this.theme.fg("accent", this.theme.bold(` ${call.label} `))}` +
				`${this.theme.fg("text", call.detail)}  ` +
				`${this.theme.fg(statusColor, status)}  ` +
				`${this.theme.fg("muted", position)}`,
		);

		const body =
			item.result === undefined
				? "(no result yet)"
				: detailText(item.name, item.args, item.result, item.cwd);
		this.body.setText(this.style(body, item.name));
		this.scroll.scrollToStart();
		this.tui.requestRender();
	}

	private style(body: string, toolName: string): string {
		const isDiff = toolName === "edit";
		return body
			.split("\n")
			.map((line) => {
				if (!isDiff) return this.theme.fg("toolOutput", line);
				switch (classifyDiffLine(line)) {
					case "added":
						return this.theme.fg("toolDiffAdded", line);
					case "removed":
						return this.theme.fg("toolDiffRemoved", line);
					default:
						return this.theme.fg("toolDiffContext", line);
				}
			})
			.join("\n");
	}

	private move(delta: number): void {
		this.index = Math.max(0, Math.min(this.items.length - 1, this.index + delta));
		this.renderCurrent();
	}

	handleInput(data: string): void {
		if (matchesKey(data, Key.escape) || data === "q") {
			this.done(undefined as unknown as void);
			return;
		}
		if (matchesKey(data, Key.leftbracket) || matchesKey(data, Key.left) || data === "h") {
			this.move(-1);
			return;
		}
		if (matchesKey(data, Key.rightbracket) || matchesKey(data, Key.right) || data === "l") {
			this.move(1);
			return;
		}

		const page = Math.max(1, this.scroll.viewportHeight - 1);
		if (matchesKey(data, Key.up) || data === "k") {
			this.scroll.scrollBy(-1);
			this.tui.requestRender();
			return;
		}
		if (matchesKey(data, Key.down) || data === "j") {
			this.scroll.scrollBy(1);
			this.tui.requestRender();
			return;
		}
		if (matchesKey(data, Key.pageUp)) {
			this.scroll.scrollBy(-page);
			this.tui.requestRender();
			return;
		}
		if (matchesKey(data, Key.pageDown)) {
			this.scroll.scrollBy(page);
			this.tui.requestRender();
			return;
		}
		if (matchesKey(data, Key.home)) {
			this.scroll.scrollToStart();
			this.tui.requestRender();
			return;
		}
		if (matchesKey(data, Key.end)) {
			this.scroll.scrollToEnd();
			this.tui.requestRender();
		}
	}

	render(width: number): string[] {
		return this.root.render(width);
	}

	invalidate(): void {
		this.root.invalidate();
	}
}
