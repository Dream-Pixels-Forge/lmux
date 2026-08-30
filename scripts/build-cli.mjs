#!/usr/bin/env node
/**
 * build-cli.mjs — Compile the lmux CLI binary.
 *
 * Links against liblmux_core.a (must be built first via build-core.mjs).
 *
 * Output: build/lmux (statically linked CLI binary)
 *
 * Usage: node scripts/build-cli.mjs [--debug]
 */

import { execSync } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const BUILD = join(ROOT, "build");

const debug = process.argv.includes("--debug");
const CC = process.env.CC || "gcc";
const LDFLAGS = [
    debug ? "-g" : "",
    "-pthread",
].join(" ").trim();
const CFLAGS = [
    "-std=c17",
    "-Wall", "-Wextra",
    debug ? "-g -O0" : "-O2 -DNDEBUG",
    `-I${join(ROOT, "include")}`,
].join(" ");

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
