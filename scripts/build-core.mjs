#!/usr/bin/env node
/**
 * build-core.mjs — Compile the lmux core C library (liblmux_core.a).
 *
 * Builds:
 *   src/core/model.c    — Workspace/surface/pane model, JSON dispatch, snapshot
 *   src/core/osc.c      — OSC 9/99/777 terminal notification parser
 *   src/core/server.c   — Unix domain socket JSON protocol server
 *
 * Output: build/liblmux_core.a (static library)
 *
 * Usage: node scripts/build-core.mjs [--debug] [--sanitize]
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
const CFLAGS = [
    "-std=c17",
    "-Wall", "-Wextra", "-Wpedantic",
    "-fPIC",
    debug ? "-g -O0" : "-O2 -DNDEBUG",
    sanitize ? "-fsanitize=address,undefined -fno-omit-frame-pointer" : "",
    `-I${join(ROOT, "include")}`,
].filter(Boolean).join(" ");

const sources = [
    join(ROOT, "src", "core", "model.c"),
    join(ROOT, "src", "core", "osc.c"),
    join(ROOT, "src", "core", "server.c"),
    join(ROOT, "src", "core", "config.c"),
];

if (!existsSync(BUILD)) mkdirSync(BUILD, { recursive: true });

const objects = [];
for (const src of sources) {
    const basename = src.replace(/\.c$/, "");
    const obj = join(BUILD, `${basename.split("/").pop()}.o`);
    const cmd = `${CC} ${CFLAGS} -c "${src}" -o "${obj}"`;
    console.log(`cc: ${basename.split("/").pop()}.c`);
    execSync(cmd, { stdio: "inherit", cwd: ROOT });
    objects.push(obj);
}

/* Archive into static library. */
const ar = process.env.AR || "ar";
const lib = join(BUILD, "liblmux_core.a");
execSync(`${ar} rcs "${lib}" ${objects.join(" ")}`, { stdio: "inherit", cwd: ROOT });
console.log(`\n✓ ${lib}`);
