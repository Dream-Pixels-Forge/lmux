#!/usr/bin/env node
/**
 * test.mjs — Build and run lmux unit tests.
 *
 * Usage: node scripts/test.mjs [--unit] [--interactive]
 */
import { execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const BUILD = join(ROOT, "build");
const TESTS = join(ROOT, "tests");

const unitOnly = process.argv.includes("--unit");
const interactive = process.argv.includes("--interactive");
const sanitize = process.argv.includes("--sanitize");
const CC = process.env.CC || "gcc";

/* Ensure core library is built. If sanitizing, rebuild core with sanitizers. */
if (sanitize || !existsSync(join(BUILD, "liblmux_core.a"))) {
    console.log("Building core library" + (sanitize ? " (with ASan/UBSan)" : "") + "...");
    execSync(`node scripts/build-core.mjs${sanitize ? " --sanitize" : ""}`, { stdio: "inherit", cwd: ROOT });
}

let overallResult = 0;

/* Define test programs: { name, sources } */
const testPrograms = [
    { name: "test_model",  sources: ["test_model.c"] },
    { name: "test_osc",    sources: ["test_osc.c"] },
    { name: "test_config", sources: ["test_config.c"] },
    { name: "test_fuzz",   sources: ["test_fuzz.c"] },
];

/* ── Compile and run each test ─────────────── */
for (const t of testPrograms) {
    const binary = join(BUILD, t.name);
    const sources = t.sources.map(s => join(TESTS, s)).join(" ");
    const cmd = [
        CC,
        "-std=c17", "-Wall", "-Wextra",
        sanitize ? "-fsanitize=address,undefined -fno-omit-frame-pointer" : "",
        "-I", join(ROOT, "include"),
        "-o", binary,
        sources,
        join(BUILD, "liblmux_core.a"),
        "-lm",
        sanitize ? "-fsanitize=address,undefined" : "",
    ].filter(Boolean).join(" ");

    console.log(`\n--- ${t.name} ---`);
    try {
        execSync(cmd, { stdio: "inherit", cwd: ROOT });
        execSync(binary, { stdio: "inherit", cwd: ROOT });
    } catch (e) {
        console.error(`✗ ${t.name} FAILED (exit ${e.status})`);
        overallResult = 1;
    }
}

/* ── CLI smoke test ────────────────────────── */
console.log("\n--- CLI smoke test ---");
try {
    execSync(`${join(BUILD, "lmux")} version`, { stdio: "inherit" });
    execSync(`${join(BUILD, "lmux")} help`, { stdio: "inherit" });
    console.log("✓ CLI help works");
} catch (e) {
    console.error(`✗ CLI test FAILED (exit ${e.status})`);
    overallResult = 1;
}

console.log("\n" + (overallResult === 0 ? "✓ All tests passed" : "✗ Some tests failed"));
process.exit(overallResult);
