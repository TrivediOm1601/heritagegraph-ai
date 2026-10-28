import { useState } from 'react';
import type { FormEvent } from 'react';

import type { SignupInput } from '../../services/accountApi';

export type AuthMode = 'login' | 'signup';

interface AuthPanelProps {
  mode: AuthMode;
  onModeChange: (mode: AuthMode) => void;
  onSubmit: (input: SignupInput) => Promise<boolean>;
  /** Credential error from the hook (generic for login by design). */
  error: string | null;
  /** True while a request is in flight. */
  busy: boolean;
  onDismissError?: () => void;
  /** Optional context line, e.g. "Sign in to see your history". */
  reason?: string;
}

/** Client-side mirror of the backend policy (the server is authoritative). */
export const MIN_PASSWORD_LENGTH = 8;

/**
 * R7 — sign-in / sign-up panel.
 *
 * Kept intentionally small and consistent with the app's card style. It
 * validates before any request (email shape, password length) and shows the
 * server's message verbatim: login failures are generic on purpose, so the
 * copy never implies which field was wrong.
 */
export function AuthPanel({
  mode,
  onModeChange,
  onSubmit,
  error,
  busy,
  onDismissError,
  reason,
}: AuthPanelProps) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);

  const isSignup = mode === 'signup';
  const message = localError ?? error;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLocalError(null);
    onDismissError?.();

    const trimmedEmail = email.trim();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(trimmedEmail)) {
      setLocalError('Enter a valid email address, for example name@example.org.');
      return;
    }
    if (isSignup && password.length < MIN_PASSWORD_LENGTH) {
      setLocalError(`Use at least ${MIN_PASSWORD_LENGTH} characters for your password.`);
      return;
    }
    if (!password) {
      setLocalError('Enter your password.');
      return;
    }

    await onSubmit({
      email: trimmedEmail,
      password,
      ...(isSignup && displayName.trim() ? { displayName: displayName.trim() } : {}),
    });
  };

  return (
    <section
      aria-labelledby="auth-heading"
      className="rounded-xl border border-ivory-300 bg-ivory p-5 shadow-sm sm:p-6"
    >
      <h2 id="auth-heading" className="font-display text-lg text-teal-800">
        {isSignup ? 'Create your account' : 'Sign in'}
      </h2>
      <p className="mt-1 text-sm text-ink-500">
        {reason ??
          (isSignup
            ? 'Accounts keep your analyses private: only you can reopen or export them.'
            : 'Sign in to upload images, keep a history and export PDFs.')}
      </p>

      <div className="mt-4 flex gap-1 rounded-lg bg-ivory-100 p-1" role="tablist" aria-label="Account action">
        {(['login', 'signup'] as const).map((option) => (
          <button
            key={option}
            type="button"
            role="tab"
            aria-selected={mode === option}
            onClick={() => {
              setLocalError(null);
              onDismissError?.();
              onModeChange(option);
            }}
            className={
              mode === option
                ? 'flex-1 rounded-md bg-teal-600 px-3 py-1.5 text-sm font-medium text-ivory'
                : 'flex-1 rounded-md px-3 py-1.5 text-sm font-medium text-ink-700 transition-colors hover:bg-ivory-200'
            }
          >
            {option === 'login' ? 'Sign in' : 'Sign up'}
          </button>
        ))}
      </div>

      <form onSubmit={submit} className="mt-4 flex flex-col gap-3" noValidate>
        <div>
          <label htmlFor="auth-email" className="block text-sm font-medium text-ink-700">
            Email
          </label>
          <input
            id="auth-email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="mt-1 w-full rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-sm text-ink-900 placeholder:text-ink-500 focus:border-teal-500 focus:outline-none"
            placeholder="name@example.org"
            required
          />
        </div>

        {isSignup ? (
          <div>
            <label htmlFor="auth-name" className="block text-sm font-medium text-ink-700">
              Display name <span className="font-normal text-ink-500">(optional)</span>
            </label>
            <input
              id="auth-name"
              type="text"
              autoComplete="name"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              maxLength={120}
              className="mt-1 w-full rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-sm text-ink-900 placeholder:text-ink-500 focus:border-teal-500 focus:outline-none"
              placeholder="How should we address you?"
            />
          </div>
        ) : null}

        <div>
          <label htmlFor="auth-password" className="block text-sm font-medium text-ink-700">
            Password
          </label>
          <input
            id="auth-password"
            type="password"
            autoComplete={isSignup ? 'new-password' : 'current-password'}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="mt-1 w-full rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-sm text-ink-900 placeholder:text-ink-500 focus:border-teal-500 focus:outline-none"
            placeholder={isSignup ? `At least ${MIN_PASSWORD_LENGTH} characters` : 'Your password'}
            required
          />
        </div>

        {message ? (
          <div
            role="alert"
            className="rounded-lg border border-terracotta-300 bg-terracotta-50 px-3 py-2 text-sm text-terracotta-700"
          >
            {message}
          </div>
        ) : null}

        <button
          type="submit"
          disabled={busy}
          className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-ivory transition-colors hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy
            ? isSignup
              ? 'Creating account…'
              : 'Signing in…'
            : isSignup
              ? 'Create account'
              : 'Sign in'}
        </button>
      </form>
    </section>
  );
}

export default AuthPanel;
