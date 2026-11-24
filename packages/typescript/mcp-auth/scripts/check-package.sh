#!/usr/bin/env bash
set -euo pipefail

package_dir=$(mktemp -d)
trap 'rm -rf "$package_dir"' EXIT

pnpm pack --pack-destination "$package_dir"
installed_dir="$package_dir/node_modules/@asap-protocol/mcp-auth"
mkdir -p "$installed_dir"
tar -xzf "$package_dir/"*.tgz --strip-components=1 -C "$installed_dir"
# Use the workspace's installed dependencies with the files from the tarball.
ln -s "$PWD/node_modules" "$installed_dir/node_modules"

cd "$package_dir"
node --input-type=module -e 'import assert from "node:assert/strict"; import { createMcpAuthMiddleware } from "@asap-protocol/mcp-auth"; assert.equal(typeof createMcpAuthMiddleware, "function")'
node --input-type=commonjs -e 'const assert = require("node:assert/strict"); const { createMcpAuthMiddleware } = require("@asap-protocol/mcp-auth"); assert.equal(typeof createMcpAuthMiddleware, "function")'
