import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

import { AuthPanel, type AuthMode } from '../components/auth/AuthPanel';
import { AppLayout } from '../components/layout/AppLayout';
import type { NavItem } from '../components/layout/AppHeader';
import { ImageUpload } from '../components/upload/ImageUpload';
import { useAuth } from '../hooks/useAuth';
import { useSubjectRoute } from '../hooks/useSubjectRoute';
import { AnalysisPage } from '../pages/AnalysisPage';
import { ArchivePage } from '../pages/ArchivePage';
import { HistoryPage } from '../pages/HistoryPage';
import { HomePage } from '../pages/HomePage';
import { MapPage } from '../pages/MapPage';
import { ResearchPage } from '../pages/ResearchPage';

/** Top-level views; the URL hash makes each one linkable/refreshable. */
type View = 'home' | 'archive' | 'map' | 'research' | 'history';

const NAV_ITEMS: NavItem[] = [
  { label: 'Home', href: '#home' },
  { label: 'Archive', href: '#archive' },
  { label: 'Map', href: '#map' },
  { label: 'Research', href: '#research' },
  { label: 'History', href: '#history' },
];

const VIEWS: View[] = ['home', 'archive', 'map', 'research', 'history'];

function readView(): View {
  if (typeof window === 'undefined') return 'home';
  const hash = window.location.hash.replace('#', '');
  return (VIEWS as string[]).includes(hash) ? (hash as View) : 'home';
}

/**
 * HeritageGraph AI — application root.
 *
 * Five views share one shell: the historical search + image analysis home
 * page, the Heritage Archive Studio, the curated heritage map, the research
 * workspace, and the personal history. Navigation is hash-based and the
 * selected archive lives in `?archive=`, so every view is linkable and
 * survives a refresh.
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
      url.hash = next === 'home' ? '' : next;
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

  // From a heritage site to the archive studio, with the site name pre-filled
  // as the archive search (`?q=`). `archive` is cleared so the search runs
  // across the account's archives rather than a stale selection.
  const openArchiveSearch = useCallback(
    (query: string) => {
      const url = new URL(window.location.href);
      url.searchParams.set('q', query);
      url.searchParams.delete('archive');
      url.hash = 'archive';
      window.history.pushState(
        { view: 'archive' },
        '',
        `${url.pathname}${url.search}${url.hash}`,
      );
      setView('archive');
    },
    [],
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

  /** An authenticated view: show the session check, the panel, or the page. */
  const gated = (reason: string, render: () => ReactNode) => {
    if (auth.phase === 'loading') {
      return (
        <p role="status" className="rounded-xl border border-ivory-300 bg-ivory p-5 text-sm text-ink-700">
          Checking your session…
        </p>
      );
    }
    if (auth.phase === 'authenticated') return render();
    return (
      <div className="flex flex-col gap-4">
        {auth.phase === 'error' ? (
          <p
            role="alert"
            className="rounded-lg border border-terracotta-300 bg-terracotta-50 px-3 py-2 text-sm text-terracotta-700"
          >
            {auth.error}
          </p>
        ) : null}
        {authPanel(reason)}
      </div>
    );
  };

  const renderView = () => {
    if (view === 'history') {
      return gated('Sign in to see your analysis history and export it as a PDF.', () => (
        <HistoryPage onOpenAnalysis={openAnalysis} onOpenSearch={openSearch} />
      ));
    }
    if (view === 'archive') {
      return gated('Sign in to build an archive, upload material and search its extracted text.', () => (
        <ArchivePage />
      ));
    }
    if (view === 'research') {
      return gated('Sign in to keep research collections, notes and reports.', () => (
        <ResearchPage />
      ));
    }
    if (view === 'map') {
      return (
        <MapPage onOpenSubject={openSearch} onSearchArchives={openArchiveSearch} />
      );
    }
    return (
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
    );
  };

  return (
    <AppLayout navItems={NAV_ITEMS} activeHref={`#${view}`} sessionSlot={sessionSlot}>
      {renderView()}
    </AppLayout>
  );
}
