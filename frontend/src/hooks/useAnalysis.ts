import { useCallback, useRef, useState } from 'react';

import {
  ApiError,
  submitAnalysis as submitAnalysisRequest,
  validateImageFile,
  type SubmitAnalysisSuccess,
} from '../services/api';

/** Lifecycle phases of an analysis submission. */
export type AnalysisPhase = 'idle' | 'submitting' | 'success' | 'error';

export interface UseAnalysisResult {
  /** Current lifecycle phase. */
  phase: AnalysisPhase;
  /** Error message when `phase === 'error'`. */
  error: string | null;
  /** Server result when `phase === 'success'`. */
  submission: SubmitAnalysisSuccess | null;
  /** True while a submission is in flight. */
  isSubmitting: boolean;
  /**
   * Submits a file for analysis. Resolves with the server result on
   * success, or null when the submission was rejected (invalid file,
   * in-flight duplicate, or backend/transport error — see `error`).
   */
  submitAnalysis: (file: File) => Promise<SubmitAnalysisSuccess | null>;
  /** Returns the hook to the idle state (clears error and result). */
  reset: () => void;
}

/**
 * A2 — analysis submission lifecycle.
 *
 * Wraps `services/api.submitAnalysis` with UI state:
 *  - guards against accidental duplicate submissions with a ref-based
 *    in-flight flag (race-safe, unlike state alone);
 *  - re-validates the file in the browser before any network call;
 *  - never enters `success` unless the request resolved, so a backend
 *    error can never be displayed as success.
 */
export function useAnalysis(): UseAnalysisResult {
  const [phase, setPhase] = useState<AnalysisPhase>('idle');
  const [error, setError] = useState<string | null>(null);
  const [submission, setSubmission] = useState<SubmitAnalysisSuccess | null>(null);
  const inFlightRef = useRef(false);

  const submitAnalysis = useCallback(
    async (file: File): Promise<SubmitAnalysisSuccess | null> => {
      // Duplicate guard: ignore submissions while one is already running.
      if (inFlightRef.current) return null;

      const problem = validateImageFile(file);
      if (problem) {
        setSubmission(null);
        setError(problem);
        setPhase('error');
        return null;
      }

      inFlightRef.current = true;
      setSubmission(null);
      setError(null);
      setPhase('submitting');

      try {
        const result = await submitAnalysisRequest(file);
        setSubmission(result);
        setError(null);
        setPhase('success');
        return result;
      } catch (err) {
        setSubmission(null);
        setError(
          err instanceof ApiError
            ? err.message
            : 'Something went wrong while uploading. Please try again.',
        );
        setPhase('error');
        return null;
      } finally {
        inFlightRef.current = false;
      }
    },
    [],
  );

  const reset = useCallback(() => {
    setPhase('idle');
    setError(null);
    setSubmission(null);
  }, []);

  return {
    phase,
    error,
    submission,
    isSubmitting: phase === 'submitting',
    submitAnalysis,
    reset,
  };
}

export default useAnalysis;