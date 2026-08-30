#!/usr/bin/env node
import { rmSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const BUILD = join(ROOT, "build");

rmSync(BUILD, { recursive: true, force: true });
console.log("✓ cleaned");
