// Temporary path-filter simulation. Not part of the CI pull requests.
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

export default defineConfig({
    plugins: [react()],
    test: {
        environment: 'jsdom',
        setupFiles: ['./vitest.setup.ts'],
        globals: true,
        include: ['src/**/*.test.ts?(x)'],
        exclude: ['tests/example.spec.ts', 'tests/browse.spec.ts'], // Playwright tests
        coverage: {
            provider: 'v8',
            reporter: ['text', 'text-summary'],
            include: ['src/**/*.{ts,tsx}'],
            exclude: ['src/**/*.d.ts'],
            thresholds: {
                lines: 45,
                branches: 38,
                functions: 30,
                statements: 44,
            },
        },
    },
    resolve: {
        alias: {
            '@': resolve(__dirname, './src'),
        },
    },
})
