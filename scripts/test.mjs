#!/usr/bin/env node
/**
 * `npm test` — both test suites, one command.
 *
 * Runs the backend suite with the project's own virtual environment and the
 * frontend suite with vitest, prints a summary, and exits non-zero if either
 * fails. Extra flags: `--backend-only`, `--frontend-only`, and anything after
 * `--` is forwarded to pytest / vitest respectively.
 *
 * Mocked vs live: the suites use mocks/simulated providers by default. Real
 * provider calls only run behind the opt-in gates documented in the README
 * (HERITAGE_LIVE_PROVIDER_TESTS=1, HERITAGE_LIVE_SOURCE_TESTS=1).
 */

import { spawnSync } from 'node:child_process';

import {
  BACKEND_DIR,
  ROOT,
  displayCommand,
  frontendInstalled,
  npmCommand,
  venvPython,
} from './runtime.mjs';

const argv = process.argv.slice(2);
const separator = argv.indexOf('--');
const ownArgs = separator === -1 ? argv : argv.slice(0, separator);
const forwarded = separator === -1 ? [] : argv.slice(separator + 1);
const BACKEND_ONLY = ownArgs.includes('--backend-only');
const FRONTEND_ONLY = ownArgs.includes('--frontend-only');

const results = [];

function run(label, command, args, cwd) {
  process.stdout.write(`\n=== ${label} ===\n$ ${displayCommand(command, args)}\n`);
  const started = Date.now();
  const result = spawnSync(command, args, {
    cwd,
    stdio: 'inherit',
    shell: process.platform === 'win32' && command === npmCommand(),
  });
  const seconds = ((Date.now() - started) / 1000).toFixed(1);
  results.push({
    label,
    ok: result.status === 0,
    detail: result.status === 0 ? `passed in ${seconds}s` : `failed (exit ${result.status ?? result.signal})`,
  });
  return result.status === 0;
}

let ok = true;

if (!FRONTEND_ONLY) {
  const python = venvPython();
  if (!python) {
    results.push({ label: 'backend', ok: false, detail: 'skipped: backend/.venv is missing (run `npm run setup`)' });
    ok = false;
  } else {
    ok = run('backend (pytest)', python, ['-m', 'pytest', '-q', ...forwarded], BACKEND_DIR) && ok;
  }
}

if (!BACKEND_ONLY) {
  if (!frontendInstalled()) {
    results.push({ label: 'frontend', ok: false, detail: 'skipped: frontend/node_modules is missing (run `npm run setup`)' });
    ok = false;
  } else {
    ok = run('frontend (vitest)', npmCommand(), ['--prefix', 'frontend', 'run', 'test', ...forwarded], ROOT) && ok;
  }
}

process.stdout.write('\n=== summary ===\n');
for (const result of results) {
  process.stdout.write(`  ${result.ok ? '✓' : '✗'} ${result.label.padEnd(18)} ${result.detail}\n`);
}
process.stdout.write(`\n${ok ? 'all suites passed' : 'FAILED — see the output above'}\n`);
process.exit(ok ? 0 : 1);
