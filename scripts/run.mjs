#!/usr/bin/env node
/**
 * run.mjs — Build and run lmux CLI.
 *
 * Usage: node scripts/run.mjs [--json] <command> [args...]
 */

import { execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const BUILD = join(ROOT, "build");

/* Ensure core library is built. */
const lib = join(BUILD, "liblmux_core.a");
if (!existsSync(lib)) {
    console.log("Building core library...");
    execSync("node scripts/build-core.mjs", { stdio: "inherit", cwd: ROOT });
}

/* Ensure CLI is built. */
const cli = join(BUILD, "lmux");
if (!existsSync(cli)) {
    console.log("Building CLI...");
    execSync("node scripts/build-cli.mjs", { stdio: "inherit", cwd: ROOT });
}

/* Forward args. */
const args = process.argv.slice(2);
execSync(`"${cli}" ${args.map(a => `"${a}"`).join(" ")}`, { stdio: "inherit", cwd: ROOT });
