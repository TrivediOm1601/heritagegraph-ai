import { useEffect, useMemo, useState } from 'react';

import { HeritageMap, MAP_CATEGORIES, categoryColor } from '../components/heritage/HeritageMap';
import { StoryLibrary } from '../components/heritage/StoryLibrary';
import { StoryReader } from '../components/heritage/StoryReader';
import { buildHeritageRoute, listHeritageSites } from '../services/archiveApi';
import { ApiError } from '../services/api';
import type {
  HeritageRoute,
  HeritageSite,
  HeritageSitesResponse,
  HeritageStateFacet,
} from '../types/archive';

type LoadState =
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'ready'; data: HeritageSitesResponse };

type SortKey = 'name' | 'state' | 'category' | 'period';

const PAGE_SIZE = 10;
const ALL_STATES = '';

interface MapPageProps {
  /** Open the existing subject overview (search + related topics + graph). */
  onOpenSubject?: (query: string) => void;
  /** Open the archive studio with this query pre-filled. */
  onSearchArchives?: (query: string) => void;
}

function locationLabel(site: HeritageSite): string {
  const local = [site.city, site.district].filter(
    (part, index, all) => part && all.indexOf(part) === index,
  );
  return [local.join(', '), site.state].filter(Boolean).join(' · ');
}

function sortSites(sites: HeritageSite[], sort: SortKey): HeritageSite[] {
  const copy = [...sites];
  if (sort === 'state') {
    return copy.sort(
      (a, b) => a.state.localeCompare(b.state) || a.name.localeCompare(b.name),
    );
  }
  if (sort === 'category') {
    return copy.sort(
      (a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name),
    );
  }
  if (sort === 'period') {
    return copy.sort((a, b) => {
      if (!a.periodGroup && !b.periodGroup) return a.name.localeCompare(b.name);
      if (!a.periodGroup) return 1;
      if (!b.periodGroup) return -1;
      return (
        a.periodGroup.localeCompare(b.periodGroup) || a.name.localeCompare(b.name)
      );
    });
  }
  return copy.sort((a, b) => a.name.localeCompare(b.name));
}

function matchesQuery(site: HeritageSite, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [
    site.name,
    ...(site.alternativeNames ?? []),
    site.city,
    site.district,
    site.state,
    site.category,
    site.period,
  ]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(needle));
}

/** Searchable state/UT picker: all 36 options, each with its record count. */
function StatePicker({
  value,
  states,
  onChange,
}: {
  value: string;
  states: HeritageStateFacet[];
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');

  const visible = states.filter((state) =>
    state.name.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const selected = states.find((state) => state.name === value);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <div className="relative">
      <span className="text-xs font-medium text-ink-700">State / Union Territory</span>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="State or Union Territory"
        onClick={() => setOpen((current) => !current)}
        className="mt-1 flex w-full items-center justify-between gap-2 rounded-lg border border-ivory-300 bg-ivory-50 px-2 py-1.5 text-left text-sm text-ink-900 focus:border-teal-500 focus:outline-none"
      >
        <span className="truncate">
          {selected ? selected.name : 'All states and UTs'}
        </span>
        <span aria-hidden="true" className="text-ink-500">
          ▾
        </span>
      </button>

      {open ? (
        <div className="absolute z-[600] mt-1 w-72 rounded-lg border border-ivory-300 bg-ivory p-2 shadow-lg">
          <label className="block text-xs font-medium text-ink-700">
            Search states and UTs
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Gujarat, Ladakh, Delhi…"
              className="mt-1 w-full rounded-md border border-ivory-300 bg-ivory-50 px-2 py-1 text-sm font-normal text-ink-900 focus:border-teal-500 focus:outline-none"
            />
          </label>
          <ul role="listbox" aria-label="States and Union Territories" className="mt-2 max-h-64 overflow-auto">
            <li>
              <button
                type="button"
                role="option"
                aria-selected={value === ALL_STATES}
                onClick={() => {
                  onChange(ALL_STATES);
                  setOpen(false);
                  setQuery('');
                }}
                className="flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left text-sm hover:bg-ivory-100"
              >
                <span>All states and UTs</span>
                <span className="text-xs text-ink-500">{states.reduce((sum, item) => sum + item.count, 0)}</span>
              </button>
            </li>
            {visible.map((state) => (
              <li key={state.name}>
                <button
                  type="button"
                  role="option"
                  aria-selected={value === state.name}
                  onClick={() => {
                    onChange(state.name);
                    setOpen(false);
                    setQuery('');
                  }}
                  className={
                    value === state.name
                      ? 'flex w-full items-center justify-between gap-2 rounded bg-teal-600 px-2 py-1 text-left text-sm text-ivory'
                      : 'flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left text-sm hover:bg-ivory-100'
                  }
                >
                  <span className="truncate">
                    {state.name}
                    {state.isUnionTerritory ? (
                      <span
                        className={
                          value === state.name
                            ? 'ml-1 text-[10px] uppercase text-teal-100'
                            : 'ml-1 text-[10px] uppercase text-ink-500'
                        }
                      >
                        UT
                      </span>
                    ) : null}
                  </span>
                  <span className={value === state.name ? 'text-xs text-teal-100' : 'text-xs text-ink-500'}>
                    {state.count}
                  </span>
                </button>
              </li>
            ))}
            {visible.length === 0 ? (
              <li className="px-2 py-2 text-sm text-ink-500">No state matches that search.</li>
            ) : null}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function SiteThumbnail({ site }: { site: HeritageSite }) {
  if (site.imageUrl) {
    return (
      <img
        src={site.imageUrl}
        alt=""
        loading="lazy"
        className="h-12 w-12 shrink-0 rounded-md border border-ivory-300 object-cover"
        title={[site.imageCredit, site.imageLicense].filter(Boolean).join(' · ')}
      />
    );
  }
  return (
    <span
      aria-hidden="true"
      className="flex h-12 w-12 shrink-0 items-center justify-center rounded-md border border-ivory-300 font-display text-lg"
      style={{ color: categoryColor(site.category), backgroundColor: '#f7f1e1' }}
    >
      {site.name.slice(0, 1)}
    </span>
  );
}

/**
 * Explore India's Heritage.
 *
 * The curated national dataset only. Coordinates are published figures marked
 * approximate, a site without a coordinate is never plotted, and the route
 * builder is explicitly a straight-line estimate rather than road routing.
 * Details link into the existing subject overview and archive search, so the
 * map is an entry point to the rest of HeritageGraph AI, not a separate silo.
 */
export function MapPage({ onOpenSubject, onSearchArchives }: MapPageProps) {
  const [state, setState] = useState<LoadState>({ phase: 'loading' });
  const [selectedState, setSelectedState] = useState(ALL_STATES);
  const [region, setRegion] = useState('');
  const [category, setCategory] = useState('');
  const [periodGroup, setPeriodGroup] = useState('');
  const [unescoOnly, setUnescoOnly] = useState(false);
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<SortKey>('name');
  const [page, setPage] = useState(1);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [route, setRoute] = useState<HeritageRoute | null>(null);
  const [routeBusy, setRouteBusy] = useState(false);
  const [routeError, setRouteError] = useState<string | null>(null);
  const [routeStale, setRouteStale] = useState(false);
  const [mapResetToken, setMapResetToken] = useState(0);
  /** Empty = all; otherwise only sites with a published story in that category. */
  const [storyCategory, setStoryCategory] = useState('');
  /** When set, the dedicated story reading view replaces the map page body. */
  const [storyId, setStoryId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listHeritageSites({ limit: 500 }).then(
      (data) => {
        if (!cancelled) setState({ phase: 'ready', data });
      },
      (err: unknown) => {
        if (!cancelled) {
          setState({
            phase: 'error',
            message:
              err instanceof ApiError ? err.message : 'Could not load the heritage map.',
          });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const data = state.phase === 'ready' ? state.data : null;
  const sites = data?.sites ?? [];
  const states = data?.facets.states ?? [];
  const regions = data?.facets.regions ?? [];
  const categories = data?.facets.categories ?? [];
  const periods = data?.facets.periods ?? [];
  const storyCategories = data?.facets.storyCategories ?? [];
  const summary = data?.summary;

  const filtered = useMemo(
    () =>
      sites.filter(
        (site) =>
          (!selectedState || site.state === selectedState) &&
          (!region || site.region === region) &&
          (!category || site.category === category) &&
          (!periodGroup || site.periodGroup === periodGroup) &&
          (!unescoOnly || site.unescoStatus) &&
          (!storyCategory || site.storyCategories.includes(storyCategory)) &&
          matchesQuery(site, query),
      ),
    [sites, selectedState, region, category, periodGroup, unescoOnly, storyCategory, query],
  );

  const sorted = useMemo(() => sortSites(filtered, sort), [filtered, sort]);
  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const pageSites = sorted.slice(
    (currentPage - 1) * PAGE_SIZE,
    currentPage * PAGE_SIZE,
  );
  const focused = focusedId ? sites.find((site) => site.id === focusedId) ?? null : null;
  const mapFitKey = [
    selectedState,
    region,
    category,
    periodGroup,
    unescoOnly ? 'UNESCO' : '',
    storyCategory ? `story:${storyCategory}` : '',
  ]
    .filter(Boolean)
    .join('|');

  const visibleIds = useMemo(() => new Set(filtered.map((site) => site.id)), [filtered]);
  const selectedSites = selectedIds
    .map((id) => sites.find((site) => site.id === id))
    .filter((site): site is HeritageSite => Boolean(site));

  const changeFilter = (apply: () => void) => {
    apply();
    setPage(1);
  };

  const resetFilters = () => {
    setSelectedState(ALL_STATES);
    setRegion('');
    setCategory('');
    setPeriodGroup('');
    setUnescoOnly(false);
    setStoryCategory('');
    setQuery('');
    setSort('name');
    setPage(1);
    // Close the open site too: the reset must return the map to the national
    // view instead of leaving the previous selection focused.
    setFocusedId(null);
    setMapResetToken((value) => value + 1);
  };

  const openSite = (siteId: string) => {
    setFocusedId(siteId);
    if (!visibleIds.has(siteId)) {
      // The site is reachable from the map even when the current filters hide
      // it from the list; details still open, and the map flies to it.
      setFocusedId(siteId);
    }
  };

  const toggleRoute = (siteId: string) => {
    setRouteError(null);
    setRouteStale(Boolean(route));
    setSelectedIds((current) =>
      current.includes(siteId)
        ? current.filter((id) => id !== siteId)
        : [...current, siteId],
    );
  };

  const moveStop = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= selectedIds.length) return;
    setRouteError(null);
    setRouteStale(Boolean(route));
    setSelectedIds((current) => {
      const copy = [...current];
      [copy[index], copy[target]] = [copy[target], copy[index]];
      return copy;
    });
  };

  const buildRoute = async (order: 'selection' | 'distance') => {
    if (selectedIds.length < 2) {
      setRouteError('Select at least two sites to build a route.');
      return;
    }
    setRouteBusy(true);
    setRouteError(null);
    try {
      const built = await buildHeritageRoute(selectedIds, selectedIds[0], order);
      setRoute(built);
      setRouteStale(false);
    } catch (err) {
      setRouteError(err instanceof ApiError ? err.message : 'Could not build a route.');
    } finally {
      setRouteBusy(false);
    }
  };

  const clearRoute = () => {
    setSelectedIds([]);
    setRoute(null);
    setRouteError(null);
    setRouteStale(false);
  };

  // A story is a full reading view, not a modal: it gets the page, with an
  // explicit way back to the site it belongs to.
  if (storyId) {
    return (
      <StoryReader
        storyId={storyId}
        onBack={() => setStoryId(null)}
        onOpenStory={setStoryId}
      />
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <section className="rounded-2xl border border-ivory-300 bg-ivory-100 p-6 shadow-sm sm:p-8">
        <p className="text-xs font-semibold uppercase tracking-[0.25em] text-terracotta-600">
          Explore India&apos;s Heritage
        </p>
        <h1 className="mt-2 max-w-3xl font-display text-3xl leading-tight text-teal-800 sm:text-4xl">
          India&apos;s heritage, beautifully connected
        </h1>
        <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-700 sm:text-base">
          Explore India&apos;s extraordinary cultural and architectural heritage through an
          interactive map of monuments, temples, forts, stepwells, archaeological sites, and
          historic places. Discover their stories, explore their locations, and build heritage
          journeys across India.
        </p>
      </section>

      {state.phase === 'loading' ? (
        <p role="status" className="rounded-xl border border-ivory-300 bg-ivory p-5 text-sm text-ink-700">
          Loading the national heritage dataset…
        </p>
      ) : null}

      {state.phase === 'error' ? (
        <p role="alert" className="rounded-xl border border-terracotta-300 bg-terracotta-50 p-5 text-sm text-terracotta-800">
          {state.message}
        </p>
      ) : null}

      {data && summary ? (
        <>
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            {[
              { label: 'Heritage sites', value: summary.totalSites },
              {
                label: 'States & UTs',
                value: `${summary.statesRepresented} / 36`,
              },
              { label: 'Categories', value: summary.categories },
              {
                label: 'Sourced stories',
                value: summary.stories,
                note: `across ${summary.sitesWithStories} places`,
              },
              {
                label: 'Verified records',
                value: summary.verifiedRecords,
                note: `${summary.approximateCoordinates} with approximate coordinates`,
              },
            ].map((card) => (
              <div
                key={card.label}
                className="rounded-xl border border-ivory-300 bg-ivory p-4 shadow-sm"
              >
                <dt className="text-[11px] font-semibold uppercase tracking-[0.18em] text-ink-500">
                  {card.label}
                </dt>
                <dd className="mt-1 font-display text-2xl text-teal-800">{card.value}</dd>
                {card.note ? (
                  <dd className="mt-0.5 text-[11px] text-ink-500">{card.note}</dd>
                ) : null}
              </div>
            ))}
          </dl>

          <p className="rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-2 text-xs text-ink-500">
            {data.notice} This is a curated demonstration dataset (
            <code>dataset: {data.dataset}</code>), not user-contributed or verified archival
            material.
          </p>

          <div className="grid gap-5 lg:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
            <div className="flex min-w-0 flex-col gap-4">
              <section
                aria-label="Heritage filters"
                className="rounded-xl border border-ivory-300 bg-ivory p-4 shadow-sm"
              >
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  <StatePicker
                    value={selectedState}
                    states={states}
                    onChange={(value) => changeFilter(() => setSelectedState(value))}
                  />
                  <label className="text-xs font-medium text-ink-700">
                    Region
                    <select
                      value={region}
                      onChange={(event) =>
                        changeFilter(() => setRegion(event.target.value))
                      }
                      className="mt-1 block w-full rounded-lg border border-ivory-300 bg-ivory-50 px-2 py-1.5 text-sm font-normal text-ink-900 focus:border-teal-500 focus:outline-none"
                    >
                      <option value="">All regions</option>
                      {regions.map((item) => (
                        <option key={item.name} value={item.name}>
                          {item.name} ({item.count})
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs font-medium text-ink-700">
                    Category
                    <select
                      value={category}
                      onChange={(event) =>
                        changeFilter(() => setCategory(event.target.value))
                      }
                      className="mt-1 block w-full rounded-lg border border-ivory-300 bg-ivory-50 px-2 py-1.5 text-sm font-normal text-ink-900 focus:border-teal-500 focus:outline-none"
                    >
                      <option value="">All categories</option>
                      {categories.map((item) => (
                        <option key={item.name} value={item.name}>
                          {item.name} ({item.count})
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs font-medium text-ink-700">
                    Historical period
                    <select
                      value={periodGroup}
                      onChange={(event) =>
                        changeFilter(() => setPeriodGroup(event.target.value))
                      }
                      className="mt-1 block w-full rounded-lg border border-ivory-300 bg-ivory-50 px-2 py-1.5 text-sm font-normal text-ink-900 focus:border-teal-500 focus:outline-none"
                    >
                      <option value="">All periods</option>
                      {periods.map((item) => (
                        <option key={item.name} value={item.name}>
                          {item.name} ({item.count})
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs font-medium text-ink-700">
                    Stories &amp; Legends
                    <select
                      value={storyCategory}
                      onChange={(event) =>
                        changeFilter(() => setStoryCategory(event.target.value))
                      }
                      className="mt-1 block w-full rounded-lg border border-ivory-300 bg-ivory-50 px-2 py-1.5 text-sm font-normal text-ink-900 focus:border-teal-500 focus:outline-none"
                    >
                      <option value="">Any story tradition</option>
                      {storyCategories.map((item) => (
                        <option key={item.name} value={item.name}>
                          {item.label} ({item.count})
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="flex flex-col justify-end gap-1">
                    <label className="flex items-center gap-2 text-xs font-medium text-ink-700">
                      <input
                        type="checkbox"
                        checked={unescoOnly}
                        onChange={(event) =>
                          changeFilter(() => setUnescoOnly(event.target.checked))
                        }
                        className="h-4 w-4 rounded border-ivory-300 text-teal-600 focus:ring-teal-500"
                      />
                      UNESCO only
                    </label>
                    <button
                      type="button"
                      onClick={resetFilters}
                      className="rounded-lg border border-ivory-300 px-3 py-1.5 text-xs font-medium text-ink-700 hover:bg-ivory-100"
                    >
                      Reset filters
                    </button>
                  </div>
                </div>
                <p className="mt-2 text-xs text-ink-500" role="status">
                  {filtered.length} of {summary.totalSites} sites match
                  {selectedState ? ` · ${selectedState}` : ''}
                  {region ? ` · ${region}` : ''}
                  {category ? ` · ${category}` : ''}
                  {periodGroup ? ` · ${periodGroup}` : ''}
                  {storyCategory
                    ? ` · stories: ${storyCategories.find((item) => item.name === storyCategory)?.label ?? storyCategory}`
                    : ''}
                  {unescoOnly ? ' · UNESCO only' : ''}.
                </p>
              </section>

              <HeritageMap
                sites={filtered}
                selectedIds={selectedIds}
                focusedId={focusedId}
                fitKey={mapFitKey}
                resetToken={mapResetToken}
                onOpen={openSite}
                route={route}
              />

              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-ink-500">
                <p>
                  Markers use published coordinates marked{' '}
                  <strong>approximate</strong>. Missing coordinates are not plotted.
                </p>
                <details className="rounded-lg border border-ivory-300 bg-ivory px-2 py-1">
                  <summary className="cursor-pointer text-xs font-medium text-ink-700">
                    Category colours
                  </summary>
                  <ul className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1">
                    {MAP_CATEGORIES.filter((item) =>
                      categories.some((facet) => facet.name === item),
                    ).map((item) => (
                      <li key={item} className="flex items-center gap-1.5">
                        <span
                          aria-hidden="true"
                          className="inline-block h-2.5 w-2.5 rounded-full"
                          style={{ backgroundColor: categoryColor(item) }}
                        />
                        {item}
                      </li>
                    ))}
                  </ul>
                </details>
              </div>

              <section
                aria-labelledby="map-details-heading"
                className="rounded-xl border border-ivory-300 bg-ivory p-4 shadow-sm"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h2 id="map-details-heading" className="font-display text-lg text-teal-800">
                    Site details
                  </h2>
                  {focused ? (
                    <button
                      type="button"
                      onClick={() => setFocusedId(null)}
                      className="text-xs text-ink-500 hover:text-terracotta-700"
                    >
                      Close
                    </button>
                  ) : null}
                </div>

                {!focused ? (
                  <p className="mt-2 text-sm text-ink-500">
                    Select a marker or a site from the list to read its details, open its
                    subject page, or search your archives for it.
                  </p>
                ) : (
                  <article className="mt-2 flex flex-col gap-3">
                    <div>
                      <h3 className="font-display text-xl text-teal-800">{focused.name}</h3>
                      {focused.alternativeNames.length > 0 ? (
                        <p className="text-xs text-ink-500">
                          Also known as {focused.alternativeNames.join(', ')}
                        </p>
                      ) : null}
                      <p className="mt-0.5 text-sm text-ink-700">
                        {locationLabel(focused)}
                      </p>
                    </div>

                    <ul className="flex flex-wrap gap-1.5 text-xs">
                      <li className="rounded-full bg-ivory-200 px-2 py-0.5">{focused.category}</li>
                      {focused.period ? (
                        <li className="rounded-full bg-ivory-200 px-2 py-0.5">
                          {focused.period}
                        </li>
                      ) : (
                        <li className="rounded-full bg-ivory-200 px-2 py-0.5">
                          period not recorded
                        </li>
                      )}
                      {focused.architecturalStyle ? (
                        <li className="rounded-full bg-ivory-200 px-2 py-0.5">
                          {focused.architecturalStyle}
                        </li>
                      ) : null}
                      {focused.unescoStatus ? (
                        <li className="rounded-full bg-teal-600 px-2 py-0.5 text-ivory">
                          UNESCO {focused.unescoYear ?? ''}
                        </li>
                      ) : null}
                      <li className="rounded-full border border-terracotta-300 px-2 py-0.5 text-terracotta-700">
                        {focused.verificationStatus} record
                      </li>
                    </ul>

                    {focused.description ? (
                      <p className="text-sm leading-relaxed text-ink-700">
                        {focused.description}
                      </p>
                    ) : null}

                    <p className="text-xs text-ink-500">
                      {focused.latitude != null && focused.longitude != null
                        ? `${focused.latitude.toFixed(4)}, ${focused.longitude.toFixed(4)} · `
                        : 'No published coordinate · '}
                      {focused.coordinateAccuracy} coordinates
                      {focused.coordinateSource ? ` — ${focused.coordinateSource}` : ''}
                    </p>

                    {focused.sourceRefs.length > 0 ? (
                      <ul className="flex flex-wrap gap-3 text-xs">
                        {focused.sourceRefs.map((ref) => (
                          <li key={ref.url}>
                            <a
                              href={ref.url}
                              target="_blank"
                              rel="noreferrer noopener"
                              className="font-medium text-teal-700 underline-offset-2 hover:underline"
                            >
                              {ref.label}
                            </a>
                          </li>
                        ))}
                      </ul>
                    ) : focused.sourceUrl ? (
                      <a
                        href={focused.sourceUrl}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="text-xs font-medium text-teal-700 underline-offset-2 hover:underline"
                      >
                        Source article
                      </a>
                    ) : null}

                    <StoryLibrary
                      siteId={focused.id}
                      siteName={focused.name}
                      onOpenStory={setStoryId}
                    />

                    <div className="flex flex-wrap gap-2">
                      {onOpenSubject ? (
                        <button
                          type="button"
                          onClick={() => onOpenSubject(focused.name)}
                          className="rounded-lg bg-teal-600 px-3 py-2 text-sm font-medium text-ivory hover:bg-teal-700"
                        >
                          Open subject page
                        </button>
                      ) : null}
                      {onSearchArchives ? (
                        <button
                          type="button"
                          onClick={() => onSearchArchives(focused.name)}
                          className="rounded-lg border border-teal-600 px-3 py-2 text-sm font-medium text-teal-700 hover:bg-teal-50"
                        >
                          Search my archives
                        </button>
                      ) : null}
                      <button
                        type="button"
                        onClick={() => toggleRoute(focused.id)}
                        className="rounded-lg border border-ivory-300 px-3 py-2 text-sm font-medium text-ink-700 hover:bg-ivory-100"
                      >
                        {selectedIds.includes(focused.id)
                          ? 'Remove from route'
                          : 'Add to route'}
                      </button>
                    </div>
                  </article>
                )}
              </section>
            </div>

            <div className="flex min-w-0 flex-col gap-4">
              <section
                aria-labelledby="route-heading"
                className="rounded-xl border border-ivory-300 bg-ivory p-4 shadow-sm"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h2 id="route-heading" className="font-display text-lg text-teal-800">
                    Heritage route
                  </h2>
                  <span className="text-xs text-ink-500">
                    {selectedIds.length} selected
                  </span>
                </div>

                {selectedSites.length === 0 ? (
                  <p className="mt-1 text-sm text-ink-500">
                    Add sites with <strong>Add to route</strong> to plan a journey across states.
                  </p>
                ) : (
                  <ol className="mt-2 flex flex-col gap-1">
                    {selectedSites.map((site, index) => (
                      <li
                        key={site.id}
                        className="flex items-center gap-1 rounded-lg border border-ivory-300 bg-ivory-50 px-2 py-1.5"
                      >
                        <span className="w-5 text-xs text-ink-500">{index + 1}.</span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium text-ink-900">
                            {site.name}
                          </span>
                          <span className="block truncate text-[11px] text-ink-500">
                            {site.state}
                          </span>
                        </span>
                        <button
                          type="button"
                          aria-label={`Move ${site.name} up`}
                          disabled={index === 0}
                          onClick={() => moveStop(index, -1)}
                          className="rounded px-1 text-xs text-ink-500 hover:bg-ivory-200 disabled:opacity-30"
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          aria-label={`Move ${site.name} down`}
                          disabled={index === selectedSites.length - 1}
                          onClick={() => moveStop(index, 1)}
                          className="rounded px-1 text-xs text-ink-500 hover:bg-ivory-200 disabled:opacity-30"
                        >
                          ↓
                        </button>
                        <button
                          type="button"
                          aria-label={`Remove ${site.name} from the route`}
                          onClick={() => toggleRoute(site.id)}
                          className="rounded px-1 text-xs text-ink-500 hover:text-terracotta-700"
                        >
                          ✕
                        </button>
                      </li>
                    ))}
                  </ol>
                )}

                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => void buildRoute('selection')}
                    disabled={routeBusy || selectedIds.length < 2}
                    className="rounded-lg bg-teal-600 px-3 py-2 text-sm font-medium text-ivory hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {routeBusy ? 'Building…' : `Build route (${selectedIds.length})`}
                  </button>
                  <button
                    type="button"
                    onClick={() => void buildRoute('distance')}
                    disabled={routeBusy || selectedIds.length < 2}
                    className="rounded-lg border border-ivory-300 px-3 py-2 text-sm font-medium text-ink-700 hover:bg-ivory-100 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Shortest order
                  </button>
                  <button
                    type="button"
                    onClick={clearRoute}
                    disabled={selectedIds.length === 0 && !route}
                    className="rounded-lg border border-ivory-300 px-3 py-2 text-sm font-medium text-ink-700 hover:bg-ivory-100 disabled:opacity-50"
                  >
                    Clear
                  </button>
                </div>

                {routeError ? (
                  <p role="alert" className="mt-2 text-sm text-terracotta-700">
                    {routeError}
                  </p>
                ) : null}

                {routeStale ? (
                  <p className="mt-2 text-xs text-terracotta-700">
                    The selected stops changed — build the route again to update distances.
                  </p>
                ) : null}

                {route ? (
                  <div className="mt-3 rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-3">
                    <h3 className="font-display text-base text-teal-800">
                      Approximate heritage route · {route.totalDistanceKm} km
                    </h3>
                    <p className="text-[11px] text-ink-500">
                      {route.ordering === 'selection'
                        ? 'Stops follow the order you selected.'
                        : 'Stops follow a nearest-neighbour ordering.'}
                    </p>
                    <ol className="mt-2 flex flex-col gap-1.5">
                      {route.stops.map((stop, index) => (
                        <li key={stop.id} className="text-sm">
                          <span className="font-medium text-ink-900">
                            {index + 1}. {stop.name}
                          </span>
                          <span className="ml-1 text-xs text-ink-500">
                            {stop.state}
                            {index > 0
                              ? ` · ${route.legs[index - 1]?.distanceKm ?? '?'} km straight-line from the previous stop`
                              : ''}
                          </span>
                        </li>
                      ))}
                    </ol>
                    <p className="mt-2 text-xs text-ink-500">{route.notice}</p>
                  </div>
                ) : null}
              </section>

              <section
                aria-labelledby="map-sites-heading"
                className="rounded-xl border border-ivory-300 bg-ivory p-4 shadow-sm"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h2 id="map-sites-heading" className="font-display text-lg text-teal-800">
                    Heritage sites
                  </h2>
                  <span className="text-xs text-ink-500">{sorted.length} shown</span>
                </div>

                <div className="mt-2 flex flex-col gap-2">
                  <label className="text-xs font-medium text-ink-700">
                    Search within sites
                    <input
                      value={query}
                      onChange={(event) => changeFilter(() => setQuery(event.target.value))}
                      placeholder="Name, city, state or alias"
                      className="mt-1 w-full rounded-lg border border-ivory-300 bg-ivory-50 px-3 py-1.5 text-sm font-normal text-ink-900 placeholder:text-ink-500 focus:border-teal-500 focus:outline-none"
                    />
                  </label>
                  <label className="text-xs font-medium text-ink-700">
                    Sort
                    <select
                      value={sort}
                      onChange={(event) => {
                        setSort(event.target.value as SortKey);
                        setPage(1);
                      }}
                      className="mt-1 w-full rounded-lg border border-ivory-300 bg-ivory-50 px-2 py-1.5 text-sm font-normal text-ink-900 focus:border-teal-500 focus:outline-none"
                    >
                      <option value="name">Alphabetical</option>
                      <option value="state">State</option>
                      <option value="category">Category</option>
                      <option value="period">Historical period</option>
                    </select>
                  </label>
                </div>

                <ul className="mt-3 flex flex-col gap-2">
                  {pageSites.map((site) => {
                    const inRoute = selectedIds.includes(site.id);
                    const isFocused = focusedId === site.id;
                    return (
                      <li
                        key={site.id}
                        data-testid={`site-card-${site.id}`}
                        data-site-card="true"
                        className={
                          isFocused
                            ? 'rounded-lg border border-teal-600 bg-teal-50 p-2'
                            : 'rounded-lg border border-ivory-300 bg-ivory-50 p-2'
                        }
                      >
                        <button
                          type="button"
                          onClick={() => openSite(site.id)}
                          className="flex w-full items-start gap-2 text-left"
                        >
                          <SiteThumbnail site={site} />
                          <span className="min-w-0 flex-1">
                            <span className="flex flex-wrap items-center gap-1">
                              <span className="text-sm font-medium text-ink-900">
                                {site.name}
                              </span>
                              {site.unescoStatus ? (
                                <span className="rounded-full bg-teal-600 px-1.5 text-[10px] font-semibold uppercase text-ivory">
                                  UNESCO
                                </span>
                              ) : null}
                            </span>
                            <span className="mt-0.5 block truncate text-xs text-ink-500">
                              {locationLabel(site)}
                            </span>
                            <span className="mt-0.5 block text-xs text-ink-500">
                              {site.category} · {site.period ?? 'period not recorded'}
                            </span>
                          </span>
                        </button>
                        <div className="mt-1 flex items-center justify-between gap-2">
                          <span className="text-[11px] text-ink-500">
                            {site.coordinateAccuracy} coordinates
                          </span>
                          <button
                            type="button"
                            aria-pressed={inRoute}
                            onClick={() => toggleRoute(site.id)}
                            className={
                              inRoute
                                ? 'rounded-md bg-teal-600 px-2 py-1 text-[11px] font-medium text-ivory'
                                : 'rounded-md border border-ivory-300 px-2 py-1 text-[11px] font-medium text-ink-700 hover:bg-ivory-100'
                            }
                          >
                            {inRoute ? 'In route' : 'Add to route'}
                          </button>
                        </div>
                      </li>
                    );
                  })}
                  {pageSites.length === 0 ? (
                    <li className="rounded-lg border border-dashed border-ivory-300 px-3 py-6 text-center text-sm text-ink-500">
                      No heritage site matches these filters.
                      <button
                        type="button"
                        onClick={resetFilters}
                        className="mt-2 block w-full text-xs font-medium text-teal-700 hover:underline"
                      >
                        Reset all filters
                      </button>
                    </li>
                  ) : null}
                </ul>

                <div className="mt-3 flex items-center justify-between gap-2 text-xs text-ink-500">
                  <span>
                    {sorted.length === 0
                      ? 'Nothing to page'
                      : `${(currentPage - 1) * PAGE_SIZE + 1}–${Math.min(
                          currentPage * PAGE_SIZE,
                          sorted.length,
                        )} of ${sorted.length}`}
                  </span>
                  <span className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => setPage((value) => Math.max(1, value - 1))}
                      disabled={currentPage <= 1}
                      className="rounded-md border border-ivory-300 px-2 py-1 disabled:opacity-40"
                    >
                      Previous
                    </button>
                    <span aria-label="Page">
                      {currentPage} / {pageCount}
                    </span>
                    <button
                      type="button"
                      onClick={() => setPage((value) => Math.min(pageCount, value + 1))}
                      disabled={currentPage >= pageCount}
                      className="rounded-md border border-ivory-300 px-2 py-1 disabled:opacity-40"
                    >
                      Next
                    </button>
                  </span>
                </div>
              </section>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

export default MapPage;
