import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import extension from '@/lib/a2a/asap-extension-v1.json';
import AsapExtensionPage, { dynamic, metadata } from '../page';

// tests/adapters/a2a/test_extension_page.py checks the JSON against AsapExtensionParams.
const EXTENSION_URI = 'https://asap-protocol.com/a2a/extensions/manifest/v1';
const GUIDE_URL =
  'https://github.com/asap-protocol/asap-protocol/blob/main/docs/guides/interoperability.md';

function codeBlock(value: unknown): HTMLElement {
  const text = JSON.stringify(value, null, 2);
  return screen.getByText((_, el) => el?.tagName === 'CODE' && el.textContent === text);
}

describe('AsapExtensionPage', () => {
  it('names the extension by its URI', () => {
    render(<AsapExtensionPage />);

    expect(extension.uri).toBe(EXTENSION_URI);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('ASAP manifest extension');
    expect(screen.getAllByText(EXTENSION_URI).length).toBeGreaterThan(0);
  });

  it('lists every field in order, with its type, whether it is required, and a meaning', () => {
    render(<AsapExtensionPage />);
    const expected = [extension.params, extension.skillSchemaFields];
    const tables = screen.getAllByRole('table');

    expect(tables).toHaveLength(expected.length);
    tables.forEach((table, index) => {
      const rows = within(table).getAllByRole('row').slice(1);
      expect(rows).toHaveLength(expected[index].length);
      expected[index].forEach((field, row) => {
        const cells = within(rows[row]).getAllByRole('cell');
        expect(cells[0]).toHaveTextContent(new RegExp(`^${field.name}$`));
        expect(cells[1]).toHaveTextContent(field.type);
        expect(cells[2]).toHaveTextContent(field.required ? 'Required' : 'Optional');
        if ('default' in field) expect(cells[2]).toHaveTextContent(`default ${field.default}`);
        expect(cells[3].textContent).not.toBe('');
      });
    });
  });

  it('shows the example and the skill selection metadata in code blocks', () => {
    render(<AsapExtensionPage />);

    expect(codeBlock(extension.example).parentElement?.tagName).toBe('PRE');
    const selection = { metadata: { [EXTENSION_URI]: { skill_id: 'web_research' } } };
    expect(codeBlock(selection).parentElement?.tagName).toBe('PRE');
  });

  it('links to the interoperability guide, with no CTA tracking', () => {
    render(<AsapExtensionPage />);

    const link = screen.getByRole('link', { name: /interoperability guide/i });
    expect(link).toHaveAttribute('href', GUIDE_URL);
    expect(link).not.toHaveAttribute('data-cta');
  });

  it('is static and declares the extension URI as its canonical URL', () => {
    expect(dynamic).toBe('force-static');
    expect(metadata.title).toBe('ASAP manifest extension for A2A, v1 | ASAP Protocol');
    expect(metadata.description).toContain('A2A v1.0');
    expect(metadata.alternates?.canonical).toBe(EXTENSION_URI);
  });
});
