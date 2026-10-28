import type { NavItem } from './AppHeader';
import { AppHeader } from './AppHeader';

interface AppLayoutProps {
  children: React.ReactNode;
  /** Navigation items rendered in the header; defaults to the app's primary routes. */
  navItems?: NavItem[];
  /** Path used to highlight the current navigation item (e.g. "#history"). */
  activeHref?: string;
  /** Session controls rendered in the header. */
  sessionSlot?: React.ReactNode;
}

/**
 * Shared page shell: sticky header plus a constrained, responsive content
 * column. Every page should render inside this component so the header,
 * gutters and background stay identical across the app.
 */
export function AppLayout({ children, navItems, activeHref, sessionSlot }: AppLayoutProps) {
  return (
    <div className="flex min-h-screen flex-col bg-ivory-50">
      <AppHeader navItems={navItems} activeHref={activeHref} sessionSlot={sessionSlot} />

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 sm:py-10 lg:px-8">
        {children}
      </main>
    </div>
  );
}

export default AppLayout;