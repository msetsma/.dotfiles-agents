import assert from "node:assert/strict";
import { test } from "node:test";
import {
	classifyDiffLine,
	describeCall,
	detailText,
	diffStat,
	lineCount,
	nonBlankCount,
	relPath,
	resultSummary,
	truncate,
} from "../src/summaries.ts";

const CWD = "/home/dev/app";

test("relPath shortens paths under cwd and home", () => {
	process.env.HOME = "/home/dev";
	assert.equal(relPath("/home/dev/app/src/x.ts", CWD), "src/x.ts");
	assert.equal(relPath("/home/dev/other/x.ts", CWD), "~/other/x.ts");
	assert.equal(relPath("src/x.ts", CWD), "src/x.ts");
	assert.equal(relPath(undefined, CWD), ".");
	assert.equal(relPath(CWD, CWD), ".");
});

test("truncate collapses whitespace and clips", () => {
	assert.equal(truncate("a   b\n c", 20), "a b c");
	assert.equal(truncate("0123456789", 5), "0123…");
});

test("lineCount and nonBlankCount", () => {
	assert.equal(lineCount("a\nb\nc\n"), 3);
	assert.equal(lineCount(""), 0);
	assert.equal(nonBlankCount("a\n\n\nb\n"), 2);
});

test("describeCall builds compact descriptors for built-ins", () => {
	assert.deepEqual(describeCall("read", { path: "/home/dev/app/a.ts", offset: 10, limit: 5 }, CWD), {
		label: "read",
		detail: "a.ts:10-14",
	});
	assert.deepEqual(describeCall("bash", { command: "npm test" }, CWD), { label: "$", detail: "npm test" });
	assert.deepEqual(describeCall("edit", { path: "/home/dev/app/a.ts" }, CWD), { label: "edit", detail: "a.ts" });
	assert.equal(describeCall("write", { path: "/home/dev/app/a.ts", content: "1\n2\n3" }, CWD).detail, "a.ts (3 lines)");
	assert.equal(describeCall("grep", { pattern: "verify", path: "/home/dev/app/src", glob: "*.ts" }, CWD).detail, "/verify/ src (*.ts)");
	assert.equal(describeCall("ls", { path: "/home/dev/app/src" }, CWD).detail, "src");
});

test("describeCall falls back for unknown and MCP tools", () => {
	assert.deepEqual(describeCall("mcp__jira__search", { query: "flaky test" }, CWD), {
		label: "mcp__jira__search",
		detail: "flaky test",
	});
	assert.deepEqual(describeCall("custom", {}, CWD), { label: "custom", detail: "" });
});

test("resultSummary reports read line counts and truncation", () => {
	const result = { content: [{ type: "text", text: "a\nb\nc" }], details: {} };
	assert.deepEqual(resultSummary("read", {}, result, false), { text: "3 lines", isError: false });
	const truncated = { content: [{ type: "text", text: "a\nb" }], details: { truncation: { truncated: true } } };
	assert.equal(resultSummary("read", {}, truncated, false).text, "2 lines (truncated)");
	assert.equal(resultSummary("read", {}, { content: [{ type: "image" }] }, false).text, "image");
});

test("resultSummary counts edit diff stats", () => {
	const patch = "--- a\n+++ b\n@@ -1 +1,2 @@\n-old\n+new\n+extra";
	const result = { content: [{ type: "text", text: "ok" }], details: { patch } };
	assert.deepEqual(diffStat(patch), { added: 2, removed: 1 });
	assert.equal(resultSummary("edit", {}, result, false).text, "+2 −1");
});

test("resultSummary keeps errors visible and concise", () => {
	const result = { content: [{ type: "text", text: "EBUSY: resource busy\nsecond line" }] };
	const summary = resultSummary("bash", {}, result, true);
	assert.equal(summary.isError, true);
	assert.equal(summary.text, "error: EBUSY: resource busy");
});

test("resultSummary handles bash, grep and find", () => {
	assert.equal(resultSummary("bash", {}, { content: [{ type: "text", text: "exit code: 0\nok" }] }, false).text, "exit 0 · 1 lines");
	assert.equal(resultSummary("grep", {}, { content: [{ type: "text", text: "a\nb\n" }] }, false).text, "2 matches");
	assert.equal(resultSummary("find", {}, { content: [{ type: "text", text: "x\ny\nz" }] }, false).text, "3 files");
});

test("detailText includes command and full body for bash", () => {
	const result = { content: [{ type: "text", text: "line1\nline2" }] };
	assert.equal(detailText("bash", { command: "ls -la" }, result, CWD), "$ ls -la\n\nline1\nline2");
});

test("detailText prefers the unified patch for edits", () => {
	const result = { content: [{ type: "text", text: "ignored" }], details: { patch: "@@\n-a\n+b" } };
	assert.equal(detailText("edit", { path: "/home/dev/app/a.ts" }, result, CWD), "a.ts\n\n@@\n-a\n+b");
});

test("detailText falls back to args JSON for unknown tools", () => {
	assert.equal(detailText("custom", { a: 1 }, {}, CWD), '{\n  "a": 1\n}');
	assert.equal(detailText("custom", {}, { content: [{ type: "text", text: "hi" }] }, CWD), "hi");
});

test("classifyDiffLine distinguishes diff rows", () => {
	assert.equal(classifyDiffLine("+++ b"), "meta");
	assert.equal(classifyDiffLine("--- a"), "meta");
	assert.equal(classifyDiffLine("+added"), "added");
	assert.equal(classifyDiffLine("-removed"), "removed");
	assert.equal(classifyDiffLine(" context"), "context");
});
