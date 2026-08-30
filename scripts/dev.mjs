#!/usr/bin/env node
/**
 * dev.mjs — Development mode: build core, watch for changes.
 *
 * Usage: node scripts/dev.mjs
 */

import { execSync } from "node:child_process";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { watch } from "node:fs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");

const WATCH_DIRS = [
    join(ROOT, "src"),
    join(ROOT, "include"),
];

function build() {
    try {
        console.log("\n=== Building ===\n");
        execSync("node scripts/build-core.mjs", { stdio: "inherit", cwd: ROOT });
        execSync("node scripts/build-cli.mjs", { stdio: "inherit", cwd: ROOT });
        console.log("\n✓ build complete");
    } catch (e) {
        console.error("\n✗ build failed");
    }
}

build();

const watchers = WATCH_DIRS.map((dir) =>
    watch(dir, { recursive: true }, (event, filename) => {
        if (filename && (filename.endsWith(".c") || filename.endsWith(".h"))) {
            console.log(`\n[change] ${filename}`);
            build();
        }
    })
);

process.on("SIGINT", () => {
    watchers.forEach((w) => w.close());
    process.exit(0);
});

console.log("\nWatching for changes... (Ctrl+C to exit)");
