#!/usr/bin/env node
/**
 * First-time setup: `npm run setup`.
 *
 * Safe and repeatable — it never deletes anything and never writes a
 * credential. Steps:
 *
 *   1. check the runtimes (Node, Python) and say exactly what is missing;
 *   2. create `backend/.venv` if it does not exist, and install the backend
 *      requirements into it;
 *   3. install the frontend dependencies (only when they are missing);
 *   4. create `backend/.env` from `backend/.env.example` when absent — local,
 *      non-secret defaults only, with the provider variables left commented
 *      out so nothing is ever "configured" by accident;
 *   5. run the database migrations (idempotent; `--seed` also loads the
 *      curated development records);
 *   6. print the next commands.
 *
 * Flags: `--seed`, `--force-deps` (reinstall both dependency sets).
 */

import { spawnSync } from 'node:child_process';
import { copyFileSync, existsSync } from 'node:fs';
import path from 'node:path';

import {
  BACKEND_DIR,
  FRONTEND_DIR,
  ROOT,
  displayCommand,
  frontendInstalled,
  npmCommand,
  systemPythonCandidates,
  venvPython,
} from './runtime.mjs';

const argv = process.argv.slice(2);
const WANT_SEED = argv.includes('--seed');
const FORCE_DEPS = argv.includes('--force-deps');

const MIN_NODE_MAJOR = 20;

function say(message = '') {
  process.stdout.write(`${message}\n`);
}

function run(command, args, options = {}) {
  say(`\n$ ${displayCommand(command, args)}`);
  const result = spawnSync(command, args, {
    cwd: options.cwd ?? ROOT,
    stdio: 'inherit',
    shell: options.shell ?? false,
    env: { ...process.env, ...(options.env ?? {}) },
  });
  if (result.status !== 0) {
    throw new Error(
      `command failed (exit ${result.status ?? 'signal ' + result.signal}): ${displayCommand(command, args)}`,
    );
  }
}

function findSystemPython() {
  for (const candidate of systemPythonCandidates()) {
    const probe = spawnSync(candidate, ['--version'], { stdio: 'pipe', shell: false });
    if (probe.status === 0) {
      return { command: candidate, version: String(probe.stdout || probe.stderr).trim() };
    }
  }
  return null;
}

function main() {
  say('HeritageGraph AI — setup');
  say('========================');

  // 1 — runtimes.
  const nodeMajor = Number(process.versions.node.split('.')[0]);
  if (nodeMajor < MIN_NODE_MAJOR) {
    throw new Error(
      `Node ${process.versions.node} is too old: Vite 8 needs Node ${MIN_NODE_MAJOR}+ ` +
        '(20.19+ or 22.12+ recommended). Install a newer Node and re-run `npm run setup`.',
    );
  }
  say(`  node   ${process.versions.node} (ok)`);

  let python = venvPython();
  if (!python) {
    const system = findSystemPython();
    if (!system) {
      throw new Error(
        'Python 3.11+ was not found. Install it, make sure `python --version` works, ' +
          'then re-run `npm run setup`.',
      );
    }
    say(`  python ${system.version} (creating backend/.venv)`);
    run(system.command, ['-m', 'venv', '.venv'], { cwd: BACKEND_DIR });
    python = venvPython();
    if (!python) throw new Error('the virtual environment could not be created in backend/.venv');
  } else {
    const probe = spawnSync(python, ['--version'], { stdio: 'pipe' });
    say(`  python ${String(probe.stdout).trim()} (existing backend/.venv)`);
  }

  // 2 — backend dependencies.
  run(python, ['-m', 'pip', 'install', '--upgrade', 'pip'], { cwd: BACKEND_DIR });
  run(python, ['-m', 'pip', 'install', '-r', 'requirements.txt'], { cwd: BACKEND_DIR });

  // 3 — frontend dependencies.
  if (FORCE_DEPS || !frontendInstalled()) {
    run(npmCommand(), ['install', '--prefix', 'frontend'], { shell: process.platform === 'win32' });
  } else {
    say('\nfrontend dependencies already installed (use --force-deps to reinstall)');
  }

  // 4 — backend/.env (non-secret local defaults), never overwriting.
  const envFile = path.join(BACKEND_DIR, '.env');
  const envExample = path.join(BACKEND_DIR, '.env.example');
  if (existsSync(envFile)) {
    say(`\nbackend/.env already exists — left untouched`);
  } else if (existsSync(envExample)) {
    copyFileSync(envExample, envFile);
    say(`\ncreated backend/.env from backend/.env.example (local defaults, no credentials)`);
  } else {
    say(`\n! backend/.env.example is missing — create backend/.env yourself (see .env.example)`);
  }

  // 5 — database migrations (idempotent). Runs through the same launcher the
  // app uses, so the environment resolution (backend/.env, sanitised PORT,
  // DATABASE_URL default) has exactly one implementation.
  const migrateArgs = ['-m', 'scripts.run_server', '--migrate'];
  if (WANT_SEED) migrateArgs.push('--seed');
  run(python, migrateArgs, { cwd: BACKEND_DIR });

  say('\nSetup complete. Next:');
  say('  npm run dev                 # start backend + frontend together');
  say('  npm run dev -- --demo       # same, with the labelled fixture pipeline');
  say('  npm test                    # backend + frontend test suites');
  say('  npm run build               # production frontend build');
  say('\nFor real image analysis, set VISION_API_BASE_URL / VISION_API_KEY / VISION_MODEL');
  say('in backend/.env (see .env.example for a verified free-tier example), then `npm run dev`.');
  say('`npm run dev` prints a configuration summary and names any missing variable.');
}

try {
  main();
} catch (error) {
  say(`\n✗ setup failed: ${error.message ?? error}`);
  process.exit(1);
}
