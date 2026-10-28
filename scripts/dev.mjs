#!/usr/bin/env node
/**
 * One command for the whole local application: `npm run dev`.
 *
 * Starts, supervises and stops the two services this project needs:
 *
 *   backend   Python/FastAPI, launched through `backend/scripts/run_server.py`
 *             so `.env` and `.vision_key` are resolved the documented way
 *             (never implicitly in demo mode);
 *   frontend  the Vite dev server, which proxies `/api` to the backend on
 *             its chosen port, so the browser only ever talks to one origin.
 *
 * What it guarantees (the reasons it exists rather than two terminals):
 *
 * * logs from both services interleaved with `[backend]` / `[frontend]`
 *   prefixes, and full logs kept in `.dev-logs/`;
 * * port conflicts are detected before start-up, reported, and resolved by
 *   moving to the next free port (never a silent switch — the URL printed at
 *   the end is the URL that works);
 * * start-up is *verified*: the backend must answer `/api/health` and the
 *   frontend must answer HTTP 200 before the banner is printed;
 * * a service that dies during start-up fails the command with that service's
 *   last log lines instead of leaving a half-running app behind;
 * * Ctrl+C (or an unexpected exit) stops both process trees;
 * * the configuration is checked first and missing vision credentials are
 *   reported loudly, with the exact variables to set (the app still starts —
 *   analysis answers 503 with an actionable message until they exist, unless
 *   you pass `--demo` for labelled fixtures).
 *
 * Flags: `--demo` (labelled fixture pipeline), `--no-frontend`, `--no-backend`,
 * `--host <host>` (default 127.0.0.1), `--backend-port`, `--frontend-port`.
 */

import { spawn } from 'node:child_process';
import { createWriteStream, mkdirSync, readFileSync } from 'node:fs';
import path from 'node:path';

import {
  BACKEND_DIR,
  FRONTEND_DIR,
  LOG_DIR,
  choosePort,
  displayCommand,
  frontendInstalled,
  killTree,
  pipeWithPrefix,
  venvPython,
  viteBin,
  waitForHttp,
} from './runtime.mjs';

const argv = process.argv.slice(2);
const has = (name) => argv.includes(name);
function value(name, fallback) {
  const index = argv.indexOf(name);
  return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
}

const DEMO = has('--demo');
const HOST = value('--host', '127.0.0.1');
const START_BACKEND = !has('--no-backend');
const START_FRONTEND = !has('--no-frontend');

const children = [];
let shuttingDown = false;

function log(label, line) {
  process.stdout.write(`${label.padEnd(9)} ${line}\n`);
}

function record(label, line) {
  log(label, line);
}

function fail(message, detail) {
  process.stderr.write(`\n✗ ${message}\n`);
  if (detail) process.stderr.write(`${detail}\n`);
  shutdown(1);
}

function shutdown(code = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of children) killTree(child);
  // Give the OS a moment to reap the trees before we exit.
  setTimeout(() => process.exit(code), 400);
}

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    if (!shuttingDown) process.stdout.write('\nStopping services…\n');
    shutdown(0);
  });
}
process.on('exit', () => {
  for (const child of children) killTree(child);
});

/** Start a child, tee its output to console + file, and track it. */
function start(label, command, args, options = {}) {
  const child = spawn(command, args, {
    cwd: options.cwd,
    env: options.env,
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: process.platform !== 'win32',
  });
  const logPath = path.join(LOG_DIR, `${label}.log`);
  const file = createWriteStream(logPath, { flags: 'w' });
  child.stdout?.on('data', (chunk) => file.write(chunk));
  child.stderr?.on('data', (chunk) => file.write(chunk));
  child.on('close', () => file.end());
  pipeWithPrefix(child, `[${label}]`, (_, line) => record(label, line));
  children.push(child);
  return child;
}

function pythonCheck() {
  return new Promise((resolve) => {
    const python = venvPython();
    if (!python) {
      resolve({ ok: false, output: 'backend/.venv is missing — run `npm run setup` first.' });
      return;
    }
    const check = spawn(python, ['-m', 'scripts.run_server', '--check'], {
      cwd: BACKEND_DIR,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let output = '';
    check.stdout.on('data', (chunk) => (output += chunk));
    check.stderr.on('data', (chunk) => (output += chunk));
    check.on('close', (code) => resolve({ ok: code === 0, output: output.trim() }));
  });
}

async function main() {
  mkdirSync(LOG_DIR, { recursive: true });

  if (!venvPython()) {
    fail(
      'the backend virtual environment is missing',
      'Run `npm run setup` once (it creates backend/.venv, installs both dependency sets and prepares the database), then `npm run dev` again.',
    );
    return;
  }
  if (START_FRONTEND && !frontendInstalled()) {
    fail(
      'frontend dependencies are not installed',
      'Run `npm run setup` once, then `npm run dev` again.',
    );
    return;
  }

  // 1 — configuration, before anything is started.
  const config = await pythonCheck();
  process.stdout.write('\nConfiguration check (backend/scripts/run_server.py --check)\n');
  for (const line of config.output.split('\n')) log('  ', line);
  if (!DEMO && !config.ok) {
    process.stdout.write(
      '\n! Vision credentials are not configured, so image analysis cannot run yet.\n' +
        '  Set VISION_API_BASE_URL / VISION_API_KEY / VISION_MODEL (backend/.env or the shell)\n' +
        '  — see .env.example for a verified free-tier example and `npm run dev -- --demo`\n' +
        '  to explore the labelled fixture pipeline instead. Everything else works now.\n',
    );
  }
  process.stdout.write('\n');

  // 2 — ports.
  let backendPort = Number(value('--backend-port', '8000'));
  let frontendPort = Number(value('--frontend-port', '5173'));

  return startAll();

  async function startAll() {
    if (START_BACKEND) {
      const chosen = await choosePort(backendPort, { host: '127.0.0.1' });
      if (chosen.conflict) {
        log('', `port ${chosen.preferred} is in use — starting the backend on ${chosen.port}`);
      }
      backendPort = chosen.port;
    }
    if (START_FRONTEND) {
      const chosen = await choosePort(frontendPort, { host: HOST });
      if (chosen.conflict) {
        log('', `port ${chosen.preferred} is in use — starting the frontend on ${chosen.port}`);
      }
      frontendPort = chosen.port;
    }

    const backendOrigin = `http://127.0.0.1:${backendPort}`;

    if (START_BACKEND) {
      const python = venvPython();
      const args = ['-m', 'scripts.run_server', '--host', '127.0.0.1', '--port', String(backendPort)];
      if (DEMO) args.push('--demo');
      log('', `$ ${displayCommand(python, args)}`);
      const backend = start('backend', python, args, {
        cwd: BACKEND_DIR,
        env: { ...process.env, HG_BACKEND_ORIGIN: backendOrigin },
      });
      backend.on('close', (code) => {
        if (!shuttingDown) {
          fail(`the backend stopped (exit code ${code}) — stopping the frontend too`, tail('backend'));
        }
      });
    }

    if (START_FRONTEND) {
      const vite = viteBin();
      const args = ['--host', HOST, '--port', String(frontendPort), '--strictPort'];
      log('', `$ ${displayCommand('node', [vite ?? 'vite', ...args])}`);
      const frontend = start('frontend', process.execPath, [vite, ...args], {
        cwd: FRONTEND_DIR,
        // The dev server proxies /api to this origin, so the browser keeps
        // talking to a single origin whatever port the backend landed on.
        env: { ...process.env, HG_BACKEND_ORIGIN: backendOrigin },
      });
      frontend.on('close', (code) => {
        if (!shuttingDown) {
          fail(`the frontend stopped (exit code ${code}) — stopping the backend too`, tail('frontend'));
        }
      });
    }

    // 3 — verify, then report.
    try {
      if (START_BACKEND) {
        await waitForHttp(`${backendOrigin}/api/health`, {
          timeoutMs: 60_000,
          what: 'the backend health endpoint',
        });
      }
      if (START_FRONTEND) {
        await waitForHttp(`http://${HOST}:${frontendPort}/`, {
          timeoutMs: 60_000,
          what: 'the frontend dev server',
        });
      }
    } catch (error) {
      fail(error.message, tail(START_BACKEND ? 'backend' : 'frontend'));
      return;
    }

    const lines = [
      '',
      'HeritageGraph AI is running',
      '',
      START_FRONTEND ? `  App          http://${HOST}:${frontendPort}` : null,
      START_BACKEND ? `  Backend API  ${backendOrigin}` : null,
      START_BACKEND ? `  API docs     ${backendOrigin}/docs` : null,
      START_BACKEND ? `  Self-check   ${backendOrigin}/api/diagnostics` : null,
      `  Mode         ${DEMO ? 'DEMO (labelled fixture pipeline)' : 'LIVE (real providers)'}`,
      `  Logs         ${path.relative(process.cwd(), LOG_DIR)}/backend.log, ${path.relative(process.cwd(), LOG_DIR)}/frontend.log`,
      '',
      START_FRONTEND
        ? '  Open the App URL above in any browser — the frontend calls /api on its own origin,\n' +
          '  so there is no CORS or cross-origin step to fail.'
        : null,
      '  Press Ctrl+C to stop both services.',
      '',
    ].filter(Boolean);
    process.stdout.write(`${lines.join('\n')}\n`);
  }
}

/** Last lines of a service's log file, for a failure report. */
function tail(label) {
  try {
    // Read synchronously: this only runs on the failure path.
    const file = path.join(LOG_DIR, `${label}.log`);
    return `--- ${file} (last lines) ---\n${readFileSync(file, 'utf8')
      .trim()
      .split('\n')
      .slice(-15)
      .join('\n')}`;
  } catch {
    return `(no ${label} log captured)`;
  }
}

main().catch((error) => fail(error.message ?? String(error)));
