import type { Metadata } from 'next';
import type { ReactNode } from 'react';

import { EXTERNAL_LINK_FOCUS_CLASS, OpensInNewTabHint } from '@/components/links/opens-in-new-tab';
// tests/adapters/a2a/test_extension_page.py checks this file against AsapExtensionParams.
import extension from '@/lib/a2a/asap-extension-v1.json';
import { githubBlobMain } from '@/lib/landing/dist-loop-links';
import { cn } from '@/lib/utils';

const TITLE = 'ASAP manifest extension for A2A, v1';
const GUIDE_URL = githubBlobMain('docs/guides/interoperability.md');
const METADATA_EXAMPLE = { metadata: { [extension.uri]: { skill_id: 'web_research' } } };
const CODE = 'rounded bg-zinc-800 px-1 py-0.5 font-mono text-sm text-indigo-300';
const LINK = cn('text-indigo-400 underline underline-offset-2', EXTERNAL_LINK_FOCUS_CLASS);

/** Meaning of each field of the JSON file, by name. `Backticks` render as code. */
const MEANINGS: Record<string, string> = {
  agent_urn: 'The agent’s ASAP URN, the `id` of its ASAP manifest.',
  asap_endpoint: 'URL of the agent’s ASAP endpoint, for ASAP messages over HTTP.',
  events_endpoint: 'URL of the agent’s WebSocket endpoint for streamed events.',
  asap_version: 'The ASAP protocol version that the agent supports, for example `0.1`.',
  state_persistence: 'Whether the agent supports state snapshots. ASAP always writes it.',
  skill_schemas: 'The JSON Schemas of each skill, by skill `id`. Lists only skills that have one.',
  input_schema: 'JSON Schema of the skill’s input.',
  output_schema: 'JSON Schema of the skill’s output.',
};

export const metadata: Metadata = {
  title: `${TITLE} | ASAP Protocol`,
  description:
    'An optional A2A v1.0 Agent Card extension that carries ASAP manifest data: the agent URN, the ASAP endpoint, and the skill schemas.',
  alternates: { canonical: extension.uri },
};

export const dynamic = 'force-static';

type Field = { name: string; type: string; required: boolean; default?: boolean };

function C({ children }: { children: ReactNode }) {
  return <code className={CODE}>{children}</code>;
}

function withCode(text: string): ReactNode[] {
  return text.split('`').map((part, i) => (i % 2 ? <C key={i}>{part}</C> : part));
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-4">
      <h2 className="text-2xl font-bold text-white">{title}</h2>
      {children}
    </section>
  );
}

function CodeBlock({ value }: { value: unknown }) {
  return (
    <pre className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900 p-4 font-mono text-sm text-zinc-300">
      <code>{JSON.stringify(value, null, 2)}</code>
    </pre>
  );
}

function FieldTable({ label, fields }: { label: string; fields: readonly Field[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900/40">
      <table aria-label={label} className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-zinc-800 text-white">
            {['Name', 'Type', 'Required', 'Meaning'].map((head) => (
              <th key={head} className="h-10 px-4 font-medium">
                {head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {fields.map((field) => (
            <tr key={field.name} className="border-b border-zinc-800 align-top last:border-0">
              <td className="p-4">
                <C>{field.name}</C>
              </td>
              <td className="p-4">{field.type}</td>
              <td className="p-4 whitespace-nowrap">
                {field.required ? 'Required' : 'Optional'}
                {field.default !== undefined && `, default ${field.default}`}
              </td>
              <td className="min-w-64 p-4 text-zinc-400">{withCode(MEANINGS[field.name])}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function AsapExtensionPage() {
  return (
    <main className="bg-zinc-950 font-sans text-zinc-300">
      <article className="container mx-auto max-w-4xl space-y-12 px-4 py-16 leading-relaxed">
        <header className="space-y-4">
          <h1 className="text-3xl font-bold tracking-tight text-white md:text-5xl">{TITLE}</h1>
          <p>
            <code className={cn(CODE, 'break-all')}>{extension.uri}</code>
          </p>
          <p className="text-lg text-zinc-400">
            This URI names an extension of the A2A v1.0 Agent Card. ASAP (Async Simple Agent
            Protocol) is an agent-to-agent protocol that works with A2A. When ASAP generates an
            Agent Card from an ASAP manifest, it declares this extension in{' '}
            <C>capabilities.extensions[]</C>. Its <C>params</C> carry the manifest data that an
            Agent Card has no field for: the agent’s ASAP URN, its ASAP endpoint, and the JSON
            Schemas of its skills.
          </p>
        </header>
        <Section title="Is it required?">
          <p>
            No. ASAP declares the extension with <C>required</C> set to false. The JSON form of an
            A2A v1.0 card leaves out a false <C>required</C>, so the entry has no <C>required</C>{' '}
            key. A client that does not know the extension can ignore it and use the card like any
            other A2A v1.0 card. The extension changes no A2A field. ASAP reads the params to map
            the card back to the agent’s ASAP manifest. You need the extension URI in one case: to
            select a skill when you call an ASAP gateway (see below).
          </p>
        </Section>
        <Section title="Params">
          <p>
            <C>params</C> is a JSON object with snake_case names. A string param is never empty.
            ASAP leaves out an optional param that has no value and never writes <C>null</C>. When
            ASAP reads a card, it ignores params it does not know.
          </p>
          <FieldTable label="Params of the ASAP extension" fields={extension.params} />
          <h3 className="pt-4 text-lg font-semibold text-white">
            Each value of <C>skill_schemas</C>
          </h3>
          <FieldTable
            label="Fields of a skill_schemas value"
            fields={extension.skillSchemaFields}
          />
        </Section>
        <Section title="Example">
          <p>
            The extension object as ASAP writes it in a card, in <C>capabilities.extensions[]</C>:
          </p>
          <CodeBlock value={extension.example} />
        </Section>
        <Section title="Select a skill">
          <p>
            An A2A v1.0 message does not name a skill. An ASAP gateway reads it from the message{' '}
            <C>metadata</C>. The key is the extension URI. The value is an object with a string{' '}
            <C>skill_id</C> that names a skill of the card:
          </p>
          <CodeBlock value={METADATA_EXAMPLE} />
          <p>
            If the key is absent and the card has exactly one skill, that skill runs. The gateway
            refuses the message with JSON-RPC error <C>-32602</C> (invalid params) when the key is
            absent and the card has several skills, when the value is not an object with a string{' '}
            <C>skill_id</C>, or when the card does not list that skill. A malformed value never
            falls back to the only skill.
          </p>
        </Section>
        <Section title="Signatures">
          <p>
            The extension is part of the Agent Card, so an A2A v1.0 card signature covers its{' '}
            <C>params</C>. The signed payload is the canonical JSON form of the card without{' '}
            <C>signatures</C>. That form drops empty values such as <C>[]</C> and <C>{'{}'}</C>, and
            it cannot carry an integer above 2^53 − 1 exactly. No signature covers such a value.
            ASAP does not sign a card that holds one. When ASAP reads a card, it requires a valid
            signature by default, and it refuses a signed card whose params hold such a value.
          </p>
        </Section>
        <Section title="Versioning">
          <p>
            This URI names version 1 of the params. Readers ignore params they do not know, so an
            added optional param keeps this URI. A breaking change to the params gets a new URI.
          </p>
        </Section>
        <Section title="More">
          <p>
            The{' '}
            <a href={GUIDE_URL} target="_blank" rel="noopener noreferrer" className={LINK}>
              interoperability guide
              <OpensInNewTabHint />
            </a>{' '}
            shows A2A v1.0, MCP authorization (OAuth 2.1), and ASAP used together. It includes the
            field mapping between an Agent Card and an ASAP manifest.
          </p>
        </Section>
      </article>
    </main>
  );
}
