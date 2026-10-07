/**
 * Quiet-mode configuration, stored next to Pi's other agent-directory files
 * (`~/.pi/agent/quiet.json`, alongside `zentui.json`).
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export type QuietMode = "quiet" | "normal" | "verbose";

export interface QuietConfig {
	/** Default rendering mode for new sessions. */
	mode: QuietMode;
	/** Keybinding that opens the detail overlay. */
	detailKey: string;
	/** How many recent tool calls the overlay can cycle through. */
	recentLimit: number;
	/** Lines of body shown inline when a tool row is expanded (0 disables). */
	previewLines: number;
	/** Always render one-line errors even in quiet mode. */
	alwaysShowErrors: boolean;
}

export const DEFAULT_CONFIG: QuietConfig = {
	mode: "quiet",
	detailKey: "alt+o",
	recentLimit: 40,
	previewLines: 0,
	alwaysShowErrors: true,
};

export const MODES: QuietMode[] = ["quiet", "normal", "verbose"];

export function isQuietMode(value: unknown): value is QuietMode {
	return typeof value === "string" && (MODES as string[]).includes(value);
}

/** Resolve Pi's agent directory without importing the host package. */
export function agentDir(): string {
	const override = process.env.PI_CODING_AGENT_DIR;
	if (override && override.length > 0) return override;
	return join(homedir(), ".pi", "agent");
}

export function configPath(): string {
	return join(agentDir(), "quiet.json");
}

function coerce(raw: Record<string, unknown>): QuietConfig {
	const config: QuietConfig = { ...DEFAULT_CONFIG };
	if (isQuietMode(raw.mode)) config.mode = raw.mode;
	if (typeof raw.detailKey === "string" && raw.detailKey.length > 0) config.detailKey = raw.detailKey;
	if (typeof raw.recentLimit === "number" && Number.isFinite(raw.recentLimit)) {
		config.recentLimit = Math.max(1, Math.min(500, Math.floor(raw.recentLimit)));
	}
	if (typeof raw.previewLines === "number" && Number.isFinite(raw.previewLines)) {
		config.previewLines = Math.max(0, Math.min(500, Math.floor(raw.previewLines)));
	}
	if (typeof raw.alwaysShowErrors === "boolean") config.alwaysShowErrors = raw.alwaysShowErrors;
	return config;
}

export function loadConfig(): QuietConfig {
	const path = configPath();
	if (!existsSync(path)) return { ...DEFAULT_CONFIG };
	try {
		const parsed = JSON.parse(readFileSync(path, "utf8"));
		return coerce(parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {});
	} catch {
		return { ...DEFAULT_CONFIG };
	}
}

export function saveConfig(config: QuietConfig): void {
	const path = configPath();
	mkdirSync(dirname(path), { recursive: true });
	writeFileSync(path, `${JSON.stringify(config, null, 2)}\n`, "utf8");
}
