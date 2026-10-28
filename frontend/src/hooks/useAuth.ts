import { useCallback, useEffect, useRef, useState } from 'react';

import {
  ApiError,
  getCurrentUser,
  login as loginRequest,
  logout as logoutRequest,
  signup as signupRequest,
  type AuthUser,
  type SignupInput,
} from '../services/accountApi';

/** Where the session state stands. */
export type AuthPhase = 'loading' | 'anonymous' | 'authenticated' | 'error';

export interface UseAuthResult {
  phase: AuthPhase;
  user: AuthUser | null;
  /** Message shown when the session could not be checked (phase 'error'). */
  error: string | null;
  /** True while a sign-in/sign-up/sign-out request is running. */
  busy: boolean;
  login: (input: { email: string; password: string }) => Promise<boolean>;
  signup: (input: SignupInput) => Promise<boolean>;
  logout: () => Promise<void>;
  /** Re-check the session (used after a 401 from another request). */
  refresh: () => Promise<void>;
  /** The last credential error, surfaced by the form. */
  formError: string | null;
  clearFormError: () => void;
}

/**
 * R7 — session state.
 *
 * The backend keeps the session in an HttpOnly cookie, so the client never
 * holds a token: it asks `/api/auth/me` on load and after every credential
 * action. A page refresh therefore restores the signed-in state, and an
 * expired session simply becomes "anonymous" on the next check.
 */
export function useAuth(): UseAuthResult {
  const [phase, setPhase] = useState<AuthPhase>('loading');
  const [user, setUser] = useState<AuthUser | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Guards against a slow initial check overwriting a newer sign-in.
  const generationRef = useRef(0);

  const load = useCallback(async () => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    try {
      const current = await getCurrentUser();
      if (generationRef.current !== generation) return;
      setUser(current);
      setPhase(current ? 'authenticated' : 'anonymous');
      setError(null);
    } catch (err) {
      if (generationRef.current !== generation) return;
      setUser(null);
      setError(
        err instanceof ApiError
          ? err.message
          : 'Could not check your sign-in state. Please try again.',
      );
      setPhase('error');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const login = useCallback(
    async (input: { email: string; password: string }): Promise<boolean> => {
      setBusy(true);
      setFormError(null);
      try {
        const signedIn = await loginRequest(input);
        generationRef.current += 1;
        setUser(signedIn);
        setPhase('authenticated');
        setError(null);
        return true;
      } catch (err) {
        setFormError(
          err instanceof ApiError ? err.message : 'Something went wrong. Please try again.',
        );
        return false;
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const signup = useCallback(async (input: SignupInput): Promise<boolean> => {
    setBusy(true);
    setFormError(null);
    try {
      const created = await signupRequest(input);
      generationRef.current += 1;
      setUser(created);
      setPhase('authenticated');
      setError(null);
      return true;
    } catch (err) {
      setFormError(
        err instanceof ApiError ? err.message : 'Something went wrong. Please try again.',
      );
      return false;
    } finally {
      setBusy(false);
    }
  }, []);

  const logout = useCallback(async () => {
    setBusy(true);
    try {
      await logoutRequest();
    } catch {
      // Even if the request failed, the local state must not keep claiming
      // an authenticated user; the cookie is cleared by the server on the
      // next successful call, and /me will report 401 if it already was.
    } finally {
      generationRef.current += 1;
      setUser(null);
      setPhase('anonymous');
      setBusy(false);
    }
  }, []);

  const clearFormError = useCallback(() => setFormError(null), []);

  return {
    phase,
    user,
    error,
    busy,
    login,
    signup,
    logout,
    refresh: load,
    formError,
    clearFormError,
  };
}

export default useAuth;
