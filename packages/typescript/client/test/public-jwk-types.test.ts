import path from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, expectTypeOf, it } from "vitest";

import type { AgentContext, HostContext, PublicJwk, ResolvedAgentKey } from "../src/index.js";

/**
 * Server code often compiles without the DOM lib, where `JsonWebKey` does not
 * exist: the declarations then fail to compile, or (with `skipLibCheck`) the
 * key types silently become `any`. This test emits the package's declarations
 * in memory, as the build does, and type-checks a consumer against them with
 * `lib: ["es2022"]` only and `skipLibCheck: false`. Nothing is written to disk.
 */
const CLIENT_ROOT = fileURLToPath(new URL("..", import.meta.url));
const DECLARATION_DIR = path.join(CLIENT_ROOT, "no-dom-declarations");
const CONSUMER_FILE = path.join(DECLARATION_DIR, "consumer.ts");
const CONSUMER = `
import type { AgentContext, HostContext, PublicJwk, ResolvedAgentKey } from "./index.js";

const key: PublicJwk = { kty: "OKP", crv: "Ed25519", x: "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo" };
export const resolved: ResolvedAgentKey = { publicKey: key, usable: true };
export const agentKey: PublicJwk = ({} as AgentContext).publicJwk;
export const hostKey: PublicJwk = ({} as HostContext).publicJwk;
// @ts-expect-error a key type that resolved to any would accept a number
export const notAKey: PublicJwk = 42;
`;

const SHARED_OPTIONS: ts.CompilerOptions = {
  target: ts.ScriptTarget.ES2022,
  module: ts.ModuleKind.NodeNext,
  moduleResolution: ts.ModuleResolutionKind.NodeNext,
  strict: true,
};

/** The package's `.d.ts` files, built with the DOM lib as `tshy` does, keyed by absolute path. */
function emitDeclarations(): Map<string, string> {
  const files = new Map<string, string>();
  const program = ts.createProgram([path.join(CLIENT_ROOT, "src/index.ts")], {
    ...SHARED_OPTIONS,
    lib: ["lib.es2022.d.ts", "lib.dom.d.ts"],
    skipLibCheck: true,
    declaration: true,
    emitDeclarationOnly: true,
    rootDir: path.join(CLIENT_ROOT, "src"),
    outDir: DECLARATION_DIR,
  });
  program.emit(undefined, (fileName, text) => files.set(path.resolve(fileName), text));
  return files;
}

/** Type errors in the in-memory declarations and the consumer, compiled with no DOM lib. */
function noDomTypeErrors(files: Map<string, string>): string[] {
  files.set(CONSUMER_FILE, CONSUMER);
  const options: ts.CompilerOptions = { ...SHARED_OPTIONS, lib: ["lib.es2022.d.ts"], types: ["node"], skipLibCheck: false, noEmit: true };
  const host = ts.createCompilerHost(options);
  const disk = { fileExists: host.fileExists, readFile: host.readFile, getSourceFile: host.getSourceFile, directoryExists: host.directoryExists };
  host.fileExists = (file) => files.has(path.resolve(file)) || disk.fileExists(file);
  host.readFile = (file) => files.get(path.resolve(file)) ?? disk.readFile(file);
  host.directoryExists = (dir) => path.resolve(dir).startsWith(DECLARATION_DIR) || (disk.directoryExists?.(dir) ?? false);
  host.getSourceFile = (file, language, ...rest) => {
    const text = files.get(path.resolve(file));
    return text === undefined ? disk.getSourceFile(file, language, ...rest) : ts.createSourceFile(file, text, language);
  };
  const program = ts.createProgram([CONSUMER_FILE], options, host);
  return ts
    .getPreEmitDiagnostics(program)
    .filter((diagnostic) => diagnostic.file !== undefined && files.has(path.resolve(diagnostic.file.fileName)))
    .map((diagnostic) => `${path.relative(DECLARATION_DIR, diagnostic.file!.fileName)}: ${ts.flattenDiagnosticMessageText(diagnostic.messageText, " ")}`);
}

describe("PublicJwk: the public key type needs no DOM lib", () => {
  it("compiles the package declarations and a key-handling consumer without the DOM lib", () => {
    expect(noDomTypeErrors(emitDeclarations())).toEqual([]);
  }, 60_000);

  it("keeps JsonWebKey values from the DOM lib assignable both ways", () => {
    expectTypeOf<JsonWebKey>().toExtend<PublicJwk>();
    expectTypeOf<PublicJwk>().toExtend<JsonWebKey>();
    expectTypeOf<ResolvedAgentKey["publicKey"]>().toEqualTypeOf<PublicJwk>();
    expectTypeOf<AgentContext["publicJwk"]>().toEqualTypeOf<PublicJwk>();
    expectTypeOf<HostContext["publicJwk"]>().toEqualTypeOf<PublicJwk>();
  });
});
