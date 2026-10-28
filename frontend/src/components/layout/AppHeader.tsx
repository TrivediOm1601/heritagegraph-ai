export interface NavItem {
  label: string;
  href: string;
}

interface AppHeaderProps {
  /** Navigation items to render; defaults to the app's primary routes. */
  navItems?: NavItem[];
  /** Path used to mark the current item as active. */
  activeHref?: string;
  /** Session controls (signed-in user + sign out, or the sign-in state). */
  sessionSlot?: React.ReactNode;
}

const DEFAULT_NAV_ITEMS: NavItem[] = [
  { label: 'Home', href: '/' },
  { label: 'Analysis', href: '/analysis' },
];

/**
 * Resolves the path used to highlight the active nav item. Callers can pass
 * `activeHref` explicitly (e.g. once client-side routing lands); otherwise
 * the current location is used so navigation state stays correct without a
 * router.
 */
function resolveCurrentPath(activeHref?: string): string {
  if (activeHref !== undefined) return activeHref;
  return typeof window !== 'undefined' ? window.location.pathname : '/';
}

/** Small node-and-edge mark standing in for the knowledge graph logo. */
function BrandMark() {
  return (
    <span
      aria-hidden="true"
      className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-teal-600 text-ivory shadow-sm transition-colors group-hover:bg-teal-700"
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="h-5 w-5"
      >
        <circle cx="6" cy="7" r="2.4" />
        <circle cx="18" cy="6.5" r="2.4" />
        <circle cx="12" cy="17.5" r="2.4" />
        <path d="M8.2 8.3 10.5 15.6M15.9 8.5 13.5 15.6M8.3 7.3h7.3" />
      </svg>
    </span>
  );
}

/**
 * Application header: brand, tagline and primary navigation.
 * Stays visible while scrolling and collapses gracefully on narrow
 * viewports — the tagline is hidden below `sm` and the brand text
 * truncates instead of pushing the nav off screen.
 */
export function AppHeader({
  navItems = DEFAULT_NAV_ITEMS,
  activeHref,
  sessionSlot,
}: AppHeaderProps) {
  const currentPath = resolveCurrentPath(activeHref);

  return (
    <header className="sticky top-0 z-40 border-b border-ivory-300 bg-ivory-100/95 backdrop-blur supports-[backdrop-filter]:bg-ivory-100/90">
      <div className="mx-auto flex h-16 w-full max-w-6xl items-center justify-between gap-3 px-4 sm:gap-6 sm:px-6 lg:px-8">
        <a
          href="/"
          className="group flex min-w-0 items-center gap-3"
          aria-label="HeritageGraph AI home"
        >
          <BrandMark />
          <span className="min-w-0">
            <span className="block truncate font-display text-lg font-semibold tracking-tight text-teal-800">
              HeritageGraph <span className="text-terracotta-600">AI</span>
            </span>
            <span className="hidden truncate text-xs text-ink-500 sm:block">
              Connect the dots behind every historical image
            </span>
          </span>
        </a>

        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
        <nav aria-label="Primary" className="flex items-center gap-1 sm:gap-2">
          {navItems.map((item) => {
            const isActive = item.href === currentPath;
            return (
              <a
                key={item.href}
                href={item.href}
                aria-current={isActive ? 'page' : undefined}
                className={
                  isActive
                    ? 'rounded-lg bg-teal-600 px-2.5 py-1.5 text-sm font-medium text-ivory transition-colors hover:bg-teal-700 sm:px-3'
                    : 'rounded-lg px-2.5 py-1.5 text-sm font-medium text-ink-700 transition-colors hover:bg-ivory-200 hover:text-teal-800 sm:px-3'
                }
              >
                {item.label}
              </a>
            );
          })}
        </nav>
        {sessionSlot ? <div className="flex items-center">{sessionSlot}</div> : null}
        </div>
      </div>
    </header>
  );
}

export default AppHeader;