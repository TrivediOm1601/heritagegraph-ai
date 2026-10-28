/**
 * Shared helpers for the root scripts (dev / setup / test / browser check).
 *
 * Everything here is dependency-free: the repository ships no root
 * node_modules, and a fresh clone should be able to run `npm run dev` after
 * `npm run setup` without installing a process manager or task runner.
 */

import { existsSync } from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const BACKEND_DIR = path.join(ROOT, 'backend');
export const FRONTEND_DIR = path.join(ROOT, 'frontend');
export const LOG_DIR = path.join(ROOT, '.dev-logs');

/** The backend virtual-environment interpreter, or null when it is missing. */
export function venvPython() {
  const candidates =
    process.platform === 'win32'
      ? [path.join(BACKEND_DIR, '.venv', 'Scripts', 'python.exe')]
      : [
          path.join(BACKEND_DIR, '.venv', 'bin', 'python3'),
          path.join(BACKEND_DIR, '.venv', 'bin', 'python'),
        ];
  return candidates.find((candidate) => existsSync(candidate)) ?? null;
}

/** A python launcher usable to create a virtual environment. */
export function systemPythonCandidates() {
  return process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python'];
}

/** Vite's CLI entry inside the frontend's own node_modules. */
export function viteBin() {
  const bin = path.join(FRONTEND_DIR, 'node_modules', 'vite', 'bin', 'vite.js');
  return existsSync(bin) ? bin : null;
}

export function frontendInstalled() {
  return existsSync(path.join(FRONTEND_DIR, 'node_modules'));
}

/** npm's executable name for this platform. */
export function npmCommand() {
  return process.platform === 'win32' ? 'npm.cmd' : 'npm';
}

/** True when a TCP port can be bound on `host`. */
export function portFree(port, host = '127.0.0.1') {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.unref();
    server.on('error', () => resolve(false));
    server.listen({ port, host }, () => server.close(() => resolve(true)));
  });
}

/**
 * Find a usable port starting at `preferred`.
 *
 * Returns `{ port, conflict }` — `conflict` is true when the preferred port
 * was taken, so the caller can say so out loud instead of silently moving.
 */
export async function choosePort(preferred, { host = '127.0.0.1', attempts = 20 } = {}) {
  for (let offset = 0; offset < attempts; offset += 1) {
    const port = preferred + offset;
    // eslint-disable-next-line no-await-in-loop
    if (await portFree(port, host)) return { port, conflict: offset > 0, preferred };
  }
  throw new Error(
    `no free port between ${preferred} and ${preferred + attempts - 1} on ${host}`,
  );
}

/** Poll a URL until it answers 2xx (or throw with `what` in the message). */
export async function waitForHttp(url, { timeoutMs = 60_000, what = url } = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastError = 'no response yet';
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { redirect: 'manual' });
      if (response.ok || response.status === 304) return response;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error.message ?? String(error);
    }
    // eslint-disable-next-line no-await-in-loop
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`timed out waiting for ${what} (${lastError})`);
}

/** Stream a child's output to the console with a prefix, and to a log file. */
export function pipeWithPrefix(child, label, writeLine) {
  const forward = (chunk) => {
    for (const line of String(chunk).split(/\r?\n/)) {
      if (line.trim()) writeLine(label, line);
    }
  };
  child.stdout?.on('data', forward);
  child.stderr?.on('data', forward);
}

/** Stop a child and everything it spawned. */
export function killTree(child) {
  if (!child || child.exitCode !== null || child.signalCode) return;
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      process.kill(-child.pid, 'SIGTERM');
    }
  } catch {
    /* already gone */
  }
}

/** Quote an argument for the shell (used only for display). */
export function displayCommand(command, args) {
  const quote = (value) => (/\s/.test(value) ? `"${value}"` : value);
  return [quote(command), ...args.map(quote)].join(' ');
}
