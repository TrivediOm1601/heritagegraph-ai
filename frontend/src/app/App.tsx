import { useCallback, useEffect, useState } from 'react';

import { AuthPanel, type AuthMode } from '../components/auth/AuthPanel';
import { AppLayout } from '../components/layout/AppLayout';
import type { NavItem } from '../components/layout/AppHeader';
import { ImageUpload } from '../components/upload/ImageUpload';
import { useAuth } from '../hooks/useAuth';
import { useSubjectRoute } from '../hooks/useSubjectRoute';
import { AnalysisPage } from '../pages/AnalysisPage';
import { HistoryPage } from '../pages/HistoryPage';
import { HomePage } from '../pages/HomePage';

/** The two top-level views; the URL hash makes each one linkable/refreshable. */
type View = 'home' | 'history';

const NAV_ITEMS: NavItem[] = [
  { label: 'Home', href: '#home' },
  { label: 'History', href: '#history' },
];

function readView(): View {
  if (typeof window === 'undefined') return 'home';
  return window.location.hash.replace('#', '') === 'history' ? 'history' : 'home';
}

/**
 * R7 — application root.
 *
 * Adds two things to the A1/A5 shell without changing the search or analysis
 * experience:
 *
 * * a **session-aware header** (sign in / signed-in user + sign out) and a
 *   gated upload area — uploading, history and export all require an account;
 * * a second view, **History**, addressable through `#history` so a refresh
 *   or the browser's back/forward buttons return to the same place.
 *
 * The home view keeps its original layout: hero + search, the uploader, and
 * the analysis results section underneath.
 */
export default function App() {
  const [activeAnalysisId, setActiveAnalysisId] = useState<string | null>(null);
  const [view, setView] = useState<View>(readView);
  const [authMode, setAuthMode] = useState<AuthMode>('login');
  const auth = useAuth();
  const { open } = useSubjectRoute();

  // Hash navigation: links, Back/Forward and direct URLs all drive the view.
  useEffect(() => {
    const onHashChange = () => setView(readView());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const goTo = useCallback((next: View) => {
    setView(next);
    if (typeof window !== 'undefined') {
      const url = new URL(window.location.href);
      url.hash = next === 'history' ? 'history' : '';
      window.history.pushState({ view: next }, '', `${url.pathname}${url.search}${url.hash}`);
    }
  }, []);

  // Keep Back/Forward in sync with pushState (which fires no hashchange).
  useEffect(() => {
    const onPopState = () => setView(readView());
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const openAnalysis = useCallback(
    (analysisId: string) => {
      setActiveAnalysisId(analysisId);
      goTo('home');
      window.setTimeout(() => {
        document.getElementById('analysis-section')?.scrollIntoView?.({
          behavior: 'smooth',
          block: 'start',
        });
      }, 0);
    },
    [goTo],
  );

  const openSearch = useCallback(
    (query: string) => {
      open(query);
      goTo('home');
    },
    [goTo, open],
  );

  const sessionSlot =
    auth.phase === 'authenticated' && auth.user ? (
      <div className="flex items-center gap-2">
        <span
          className="hidden max-w-[12rem] truncate text-sm text-ink-700 sm:inline"
          title={auth.user.email}
        >
          {auth.user.displayName || auth.user.email}
        </span>
        <button
          type="button"
          onClick={() => void auth.logout()}
          disabled={auth.busy}
          className="rounded-lg border border-ivory-300 px-2.5 py-1.5 text-sm font-medium text-ink-700 transition-colors hover:bg-ivory-200 disabled:opacity-50"
        >
          Sign out
        </button>
      </div>
    ) : null;

  const authPanel = (reason: string) => (
    <AuthPanel
      mode={authMode}
      onModeChange={setAuthMode}
      onSubmit={authMode === 'signup' ? auth.signup : auth.login}
      error={auth.formError}
      busy={auth.busy}
      onDismissError={auth.clearFormError}
      reason={reason}
    />
  );

  return (
    <AppLayout navItems={NAV_ITEMS} activeHref={`#${view}`} sessionSlot={sessionSlot}>
      {view === 'history' ? (
        auth.phase === 'loading' ? (
          <p role="status" className="rounded-xl border border-ivory-300 bg-ivory p-5 text-sm text-ink-700">
            Checking your session…
          </p>
        ) : auth.phase === 'authenticated' ? (
          <HistoryPage onOpenAnalysis={openAnalysis} onOpenSearch={openSearch} />
        ) : (
          <div className="flex flex-col gap-4">
            {auth.phase === 'error' ? (
              <p
                role="alert"
                className="rounded-lg border border-terracotta-300 bg-terracotta-50 px-3 py-2 text-sm text-terracotta-700"
              >
                {auth.error}
              </p>
            ) : null}
            {authPanel('Sign in to see your analysis history and export it as a PDF.')}
          </div>
        )
      ) : (
        <>
          <HomePage />

          <div className="mt-6 sm:mt-8">
            {auth.phase === 'loading' ? (
              <p role="status" className="rounded-xl border border-ivory-300 bg-ivory p-5 text-sm text-ink-700">
                Checking your session…
              </p>
            ) : auth.phase === 'authenticated' ? (
              <ImageUpload onSubmitted={(result) => setActiveAnalysisId(result.analysisId)} />
            ) : (
              authPanel('Sign in to upload an image, keep its analysis in your history and export it.')
            )}
          </div>

          <div id="analysis-section" className="mt-6 scroll-mt-4 sm:mt-8">
            <AnalysisPage analysisId={activeAnalysisId} />
          </div>
        </>
      )}
    </AppLayout>
  );
}
