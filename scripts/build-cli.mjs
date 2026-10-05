#!/usr/bin/env node
/**
 * build-cli.mjs — Compile the lmux CLI binary.
 *
 * Links against liblmux_core.a (must be built first via build-core.mjs).
 *
 * Output: build/lmux (statically linked CLI binary)
 *
 * Usage: node scripts/build-cli.mjs [--debug] [--sanitize]
 */

import { execSync } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const BUILD = join(ROOT, "build");

const debug = process.argv.includes("--debug");
const sanitize = process.argv.includes("--sanitize");
const CC = process.env.CC || "gcc";

/* Detect libsystemd availability (same logic as build-core.mjs) */
let haveSystemd = false;
let systemdLibs = "";
try {
    const pkgLibs = execSync("pkg-config --libs libsystemd 2>/dev/null", { encoding: "utf8" }).trim();
    if (pkgLibs) {
        haveSystemd = true;
        systemdLibs = pkgLibs;
    }
} catch { /* libsystemd not available */ }

// -fsanitize has to be passed to BOTH the compile and the link. Forgetting it
// at link time against an ASan-instrumented liblmux_core.a fails with
// "undefined reference to __asan_init", so the sanitizer CI job could not
// produce a usable binary at all (issue #14).
const SAN = sanitize ? "-fsanitize=address,undefined -fno-omit-frame-pointer" : "";
const LDFLAGS = [
    debug ? "-g" : "",
    SAN,
    "-pthread",
    haveSystemd ? systemdLibs : "",
].filter(Boolean).join(" ");
const CFLAGS = [
    "-std=c17",
    "-Wall", "-Wextra",
    debug ? "-g -O0" : "-O2 -DNDEBUG",
    SAN,
    `-I${join(ROOT, "include")}`,
].filter(Boolean).join(" ");

if (!existsSync(BUILD)) mkdirSync(BUILD, { recursive: true });

const lib = join(BUILD, "liblmux_core.a");
if (!existsSync(lib)) {
    console.error("Error: liblmux_core.a not found. Run 'node scripts/build-core.mjs' first.");
    process.exit(1);
}

const source = join(ROOT, "src", "cli", "main.c");
const output = join(BUILD, "lmux");

const cmd = `${CC} ${CFLAGS} -o "${output}" "${source}" "${lib}" ${LDFLAGS}`;
console.log(`cc: main.c -> lmux`);
execSync(cmd, { stdio: "inherit", cwd: ROOT });

console.log(`\n✓ ${output}`);
