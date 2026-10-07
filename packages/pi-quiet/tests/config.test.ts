import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, test } from "node:test";
import { DEFAULT_CONFIG, agentDir, configPath, isQuietMode, loadConfig, saveConfig } from "../src/config.ts";

const dir = mkdtempSync(join(tmpdir(), "pi-quiet-"));
const previous = process.env.PI_CODING_AGENT_DIR;
process.env.PI_CODING_AGENT_DIR = dir;

after(() => {
	if (previous === undefined) delete process.env.PI_CODING_AGENT_DIR;
	else process.env.PI_CODING_AGENT_DIR = previous;
	rmSync(dir, { recursive: true, force: true });
});

test("agentDir honours PI_CODING_AGENT_DIR", () => {
	assert.equal(agentDir(), dir);
	assert.equal(configPath(), join(dir, "quiet.json"));
});

test("loadConfig returns defaults when the file is absent", () => {
	assert.deepEqual(loadConfig(), DEFAULT_CONFIG);
});

test("saveConfig then loadConfig round-trips valid values", () => {
	saveConfig({ ...DEFAULT_CONFIG, mode: "verbose", recentLimit: 10, previewLines: 4 });
	const loaded = loadConfig();
	assert.equal(loaded.mode, "verbose");
	assert.equal(loaded.recentLimit, 10);
	assert.equal(loaded.previewLines, 4);
});

test("loadConfig coerces out-of-range and unknown values", () => {
	saveConfig({ ...DEFAULT_CONFIG, mode: "verbose", recentLimit: -5, previewLines: 9999 });
	const loaded = loadConfig();
	assert.equal(loaded.mode, "verbose");
	assert.equal(loaded.recentLimit, 1);
	assert.equal(loaded.previewLines, 500);
});

test("isQuietMode guards the mode union", () => {
	assert.equal(isQuietMode("quiet"), true);
	assert.equal(isQuietMode("normal"), true);
	assert.equal(isQuietMode("verbose"), true);
	assert.equal(isQuietMode("loud"), false);
	assert.equal(isQuietMode(undefined), false);
});
