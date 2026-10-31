import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { MapPage } from '../src/pages/MapPage';
import type {
  HeritageRoute,
  HeritageSite,
  HeritageSitesResponse,
  HeritageStateFacet,
} from '../src/types/archive';

const listHeritageSites = vi.fn();
const buildHeritageRoute = vi.fn();
const listSiteStories = vi.fn();
const getStory = vi.fn();

vi.mock('../src/services/archiveApi', () => ({
  listHeritageSites: (...args: unknown[]) => listHeritageSites(...args),
  buildHeritageRoute: (...args: unknown[]) => buildHeritageRoute(...args),
  listSiteStories: (...args: unknown[]) => listSiteStories(...args),
  getStory: (...args: unknown[]) => getStory(...args),
  askStories: vi.fn(),
  submitStory: vi.fn(),
}));

vi.mock('../src/components/heritage/HeritageMap', () => ({
  MAP_CATEGORIES: [
    'Forts and Palaces',
    'Temples',
    'Mosques',
    'Churches',
    'Monasteries',
    'Stepwells',
    'Archaeological Sites',
    'Ancient Cities',
    'Caves',
    'Museums',
    'Memorials',
    'Cultural Landscapes',
    'Traditional Architecture',
    'Other Heritage Sites',
  ],
  categoryColor: () => '#146a66',
  HeritageMap: ({
    sites,
    selectedIds,
    focusedId,
    fitKey,
    resetToken,
  }: {
    sites: HeritageSite[];
    selectedIds: string[];
    focusedId?: string | null;
    fitKey?: string | null;
    resetToken?: number;
  }) => (
    <div
      data-testid="heritage-map"
      data-site-count={sites.length}
      data-route={selectedIds.join(',')}
      data-focused={focusedId ?? ''}
      data-fit-key={fitKey ?? ''}
      data-reset-token={resetToken ?? 0}
    />
  ),
}));

const STATES: HeritageStateFacet[] = [
  ['Andhra Pradesh', 'South India', false, 0],
  ['Arunachal Pradesh', 'Northeast India', false, 1],
  ['Assam', 'Northeast India', false, 0],
  ['Bihar', 'East India', false, 0],
  ['Chhattisgarh', 'Central India', false, 0],
  ['Goa', 'West India', false, 1],
  ['Gujarat', 'West India', false, 2],
  ['Haryana', 'North India', false, 0],
  ['Himachal Pradesh', 'North India', false, 0],
  ['Jharkhand', 'East India', false, 0],
  ['Karnataka', 'South India', false, 1],
  ['Kerala', 'South India', false, 0],
  ['Madhya Pradesh', 'Central India', false, 1],
  ['Maharashtra', 'West India', false, 0],
  ['Manipur', 'Northeast India', false, 0],
  ['Meghalaya', 'Northeast India', false, 0],
  ['Mizoram', 'Northeast India', false, 1],
  ['Nagaland', 'Northeast India', false, 0],
  ['Odisha', 'East India', false, 1],
  ['Punjab', 'North India', false, 1],
  ['Rajasthan', 'North India', false, 1],
  ['Sikkim', 'Northeast India', false, 0],
  ['Tamil Nadu', 'South India', false, 0],
  ['Telangana', 'South India', false, 0],
  ['Tripura', 'Northeast India', false, 0],
  ['Uttar Pradesh', 'North India', false, 1],
  ['Uttarakhand', 'North India', false, 0],
  ['West Bengal', 'East India', false, 0],
  ['Andaman and Nicobar Islands', 'South India', true, 1],
  ['Chandigarh', 'North India', true, 0],
  ['Dadra and Nagar Haveli and Daman and Diu', 'West India', true, 0],
  ['Delhi', 'North India', true, 1],
  ['Jammu and Kashmir', 'North India', true, 0],
  ['Ladakh', 'North India', true, 1],
  ['Lakshadweep', 'South India', true, 0],
  ['Puducherry', 'South India', true, 0],
].map(([name, region, isUnionTerritory, count]) => ({
  name: name as string,
  region: region as string,
  isUnionTerritory: isUnionTerritory as boolean,
  count: count as number,
}));

function site(overrides: Partial<HeritageSite> = {}): HeritageSite {
  return {
    id: 'site',
    name: 'Site',
    alternativeNames: [],
    region: 'North India',
    state: 'Delhi',
    isUnionTerritory: true,
    district: null,
    city: null,
    category: 'Memorials',
    periodGroup: 'Early Modern',
    period: '17th century CE',
    architecturalStyle: 'Mughal',
    unescoStatus: false,
    unescoYear: null,
    latitude: 28.0,
    longitude: 77.0,
    coordinateSource: 'Published coordinates (Wikipedia), rounded to 4 decimals',
    coordinateAccuracy: 'approximate',
    verificationStatus: 'unverified',
    description: 'A curated demonstration record.',
    sourceUrl: 'https://en.wikipedia.org/wiki/Site',
    sourceRefs: [{ label: 'Wikipedia', url: 'https://en.wikipedia.org/wiki/Site' }],
    imageUrl: null,
    imageCredit: null,
    imageLicense: null,
    dataset: 'curated-demo-india',
    storyCount: 0,
    storyCategories: [],
    featuredStories: [],
    ...overrides,
  };
}

const SITES: HeritageSite[] = [
  site({
    id: 'taj-mahal',
    name: 'Taj Mahal',
    state: 'Uttar Pradesh',
    region: 'North India',
    city: 'Agra',
    district: 'Agra',
    category: 'Memorials',
    periodGroup: 'Early Modern',
    period: '17th century CE',
    unescoStatus: true,
    unescoYear: 1983,
    latitude: 27.1751,
    longitude: 78.0421,
  }),
  site({
    id: 'rani-ki-vav',
    name: 'Rani ki Vav',
    alternativeNames: ["Queen's Stepwell"],
    state: 'Gujarat',
    region: 'West India',
    city: 'Patan',
    district: 'Patan',
    category: 'Stepwells',
    periodGroup: 'Early Medieval',
    period: '11th century CE',
    unescoStatus: true,
    unescoYear: 2014,
    latitude: 23.8589,
    longitude: 72.1019,
    storyCount: 2,
    storyCategories: ['architecture', 'history'],
  }),
  site({
    id: 'adalaj-stepwell',
    name: 'Adalaj Stepwell',
    state: 'Gujarat',
    region: 'West India',
    category: 'Stepwells',
    periodGroup: 'Medieval',
    period: '1499 CE',
    latitude: 23.1667,
    longitude: 72.5833,
  }),
  site({
    id: 'hampi',
    name: 'Hampi',
    state: 'Karnataka',
    region: 'South India',
    category: 'Ancient Cities',
    periodGroup: 'Medieval',
    period: '14th–16th century CE',
    unescoStatus: true,
    unescoYear: 1986,
    latitude: 15.335,
    longitude: 76.46,
  }),
  site({
    id: 'konark',
    name: 'Sun Temple, Konark',
    state: 'Odisha',
    region: 'East India',
    category: 'Temples',
    periodGroup: 'Medieval',
    period: '13th century CE',
    unescoStatus: true,
    unescoYear: 1984,
    latitude: 19.8876,
    longitude: 86.0945,
  }),
  site({
    id: 'tawang',
    name: 'Tawang Monastery',
    state: 'Arunachal Pradesh',
    region: 'Northeast India',
    category: 'Monasteries',
    periodGroup: 'Early Modern',
    period: '17th century CE',
    latitude: 27.586,
    longitude: 91.859,
  }),
  site({
    id: 'sanchi',
    name: 'Sanchi Stupa',
    state: 'Madhya Pradesh',
    region: 'Central India',
    category: 'Archaeological Sites',
    periodGroup: 'Ancient',
    period: '3rd century BCE onwards',
    unescoStatus: true,
    unescoYear: 1989,
    latitude: 23.4794,
    longitude: 77.7397,
  }),
  site({
    id: 'qutub-minar',
    name: 'Qutub Minar',
    state: 'Delhi',
    region: 'North India',
    category: 'Archaeological Sites',
    periodGroup: 'Medieval',
    period: '12th–13th century CE',
    unescoStatus: true,
    unescoYear: 1993,
    latitude: 28.5245,
    longitude: 77.1855,
  }),
  site({
    id: 'jaisalmer-fort',
    name: 'Jaisalmer Fort',
    state: 'Rajasthan',
    region: 'North India',
    category: 'Forts and Palaces',
    periodGroup: 'Medieval',
    period: '12th century CE',
    unescoStatus: true,
    unescoYear: 2013,
    latitude: 26.9124,
    longitude: 70.9126,
  }),
  site({
    id: 'vangchhia',
    name: 'Vangchhia Menhirs',
    state: 'Mizoram',
    region: 'Northeast India',
    category: 'Archaeological Sites',
    periodGroup: null,
    period: null,
    latitude: 23.05,
    longitude: 93.3,
  }),
  site({
    id: 'cellular-jail',
    name: 'Cellular Jail',
    state: 'Andaman and Nicobar Islands',
    region: 'South India',
    isUnionTerritory: true,
    category: 'Memorials',
    periodGroup: 'Colonial',
    period: '19th–20th century CE',
    latitude: 11.674,
    longitude: 92.748,
  }),
  site({
    id: 'golden-temple',
    name: 'Golden Temple',
    alternativeNames: ['Harmandir Sahib'],
    state: 'Punjab',
    region: 'North India',
    category: 'Other Heritage Sites',
    periodGroup: 'Early Modern',
    period: '16th century CE onwards',
    latitude: 31.62,
    longitude: 74.8765,
  }),
  site({
    id: 'basilica-bom-jesus',
    name: 'Basilica of Bom Jesus',
    state: 'Goa',
    region: 'West India',
    category: 'Churches',
    periodGroup: 'Early Modern',
    period: '16th–17th century CE',
    unescoStatus: true,
    unescoYear: 1986,
    latitude: 15.5009,
    longitude: 73.9116,
  }),
  site({
    id: 'unlocated-site',
    name: 'Unlocated Heritage Record',
    state: 'Ladakh',
    region: 'North India',
    category: 'Traditional Architecture',
    periodGroup: null,
    period: null,
    latitude: null,
    longitude: null,
  }),
];

const RESPONSE: HeritageSitesResponse = {
  sites: SITES,
  total: SITES.length,
  limit: 500,
  offset: 0,
  dataset: 'curated-demo-india',
  notice: 'Curated demonstration dataset covering India.',
  summary: {
    totalSites: 14,
    statesRepresented: 13,
    unionTerritoriesRepresented: 4,
    categories: 10,
    verifiedRecords: 0,
    approximateCoordinates: 13,
    unescoSites: 8,
    stories: 2,
    sitesWithStories: 1,
  },
  facets: {
    states: STATES,
    regions: [
      { name: 'North India', count: 6 },
      { name: 'South India', count: 2 },
      { name: 'East India', count: 1 },
      { name: 'West India', count: 3 },
      { name: 'Central India', count: 1 },
      { name: 'Northeast India', count: 2 },
    ],
    categories: [
      { name: 'Forts and Palaces', count: 1 },
      { name: 'Temples', count: 1 },
      { name: 'Churches', count: 1 },
      { name: 'Monasteries', count: 1 },
      { name: 'Stepwells', count: 2 },
      { name: 'Archaeological Sites', count: 3 },
      { name: 'Ancient Cities', count: 1 },
      { name: 'Memorials', count: 2 },
      { name: 'Traditional Architecture', count: 1 },
      { name: 'Other Heritage Sites', count: 1 },
    ],
    periods: [
      { name: 'Ancient', count: 1 },
      { name: 'Early Medieval', count: 1 },
      { name: 'Medieval', count: 4 },
      { name: 'Early Modern', count: 4 },
      { name: 'Colonial', count: 1 },
    ],
    storyCategories: [
      { name: 'history', label: 'History', count: 1 },
      { name: 'architecture', label: 'Architecture', count: 1 },
    ],
  },
};

const STORY = {
  id: 'rani-ki-vav-udayamati',
  siteId: 'rani-ki-vav',
  siteName: 'Rani ki Vav',
  siteState: 'Gujarat',
  title: 'The stepwell Queen Udayamati commissioned',
  summary: 'A stepwell attributed to Queen Udayamati in the 11th century.',
  narrative: 'Rani ki Vav descends through seven levels.\n\nIt was excavated in the 20th century.',
  category: 'history',
  categoryLabel: 'History',
  narrativeType: 'historically-documented',
  narrativeTypeLabel: 'Historically documented',
  tradition: 'Chaulukya court tradition',
  language: 'en',
  languageLabel: 'English',
  community: null,
  period: '11th century CE',
  verificationStatus: 'source-checked',
  provenance: 'Curated from the cited sources',
  isCommunity: false,
  versionCount: 0,
  sourceCount: 2,
  culturalBackground: 'Stepwells combined water storage and ritual space.',
  relatedEntities: [],
  relatedSites: [],
  sources: [
    {
      id: 'src-1',
      title: 'Rani ki Vav',
      author: 'Wikipedia contributors',
      sourceType: 'secondary-reference',
      published: '2026',
      url: 'https://en.wikipedia.org/wiki/Rani_ki_Vav',
      excerpt: null,
      locator: 'Lead',
      licence: 'CC BY-SA 4.0',
    },
  ],
  versions: [],
  relatedStories: [],
};

function mapRoute(siteIds: string[], order: 'selection' | 'distance' = 'selection'): HeritageRoute {
  const stops = siteIds
    .map((id) => SITES.find((item) => item.id === id))
    .filter((item): item is HeritageSite => Boolean(item));
  return {
    stops,
    legs: stops.slice(0, -1).map((stop, index) => ({
      from: stop.id,
      to: stops[index + 1].id,
      distanceKm: 100,
    })),
    totalDistanceKm: 100 * (stops.length - 1),
    distanceMethod: 'great-circle',
    ordering: order,
    notice: 'Approximate straight-line route, not a road route.',
    excludedSiteIds: [],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  listHeritageSites.mockResolvedValue(RESPONSE);
  listSiteStories.mockResolvedValue({
    siteId: 'site',
    siteName: 'Site',
    total: 0,
    stories: [],
    categories: [],
    notice: 'Stories are presented as separate traditions.',
  });
  getStory.mockResolvedValue(null);
});

describe('India Heritage Explorer', () => {
  it('opens with the national summary and all 36 states and UTs', async () => {
    render(<MapPage />);

    expect(await screen.findByText("India's heritage, beautifully connected")).toBeInTheDocument();
    expect(screen.getByText('14')).toBeInTheDocument();
    expect(screen.getByText('13 / 36')).toBeInTheDocument();
    expect(screen.getByText('10')).toBeInTheDocument();
    expect(screen.getByText(/not user-contributed or verified archival material/)).toBeInTheDocument();
    expect(screen.getByTestId('heritage-map')).toHaveAttribute('data-site-count', '14');

    fireEvent.click(screen.getByRole('button', { name: 'State or Union Territory' }));
    const options = within(
      screen.getByRole('listbox', { name: 'States and Union Territories' }),
    ).getAllByRole('option');
    // 36 states/UTs + the "all" option.
    expect(options).toHaveLength(37);
    expect(screen.getByRole('option', { name: /Ladakh/ })).toBeInTheDocument();
  });

  it('filters by state, region, category, period, UNESCO and combines them', async () => {
    render(<MapPage />);
    await screen.findByTestId('heritage-map');

    fireEvent.click(screen.getByRole('button', { name: 'State or Union Territory' }));
    const search = screen.getByLabelText('Search states and UTs');
    fireEvent.change(search, { target: { value: 'guj' } });
    fireEvent.click(screen.getByRole('option', { name: /Gujarat/ }));
    expect(screen.getByText('2 of 14 sites match · Gujarat.')).toBeInTheDocument();
    expect(screen.getByTestId('heritage-map')).toHaveAttribute('data-site-count', '2');
    expect(screen.getByTestId('heritage-map')).toHaveAttribute('data-fit-key', 'Gujarat');

    fireEvent.click(screen.getByRole('button', { name: /Reset filters/ }));
    expect(screen.getByTestId('heritage-map')).toHaveAttribute('data-fit-key', '');
    expect(screen.getByTestId('heritage-map')).toHaveAttribute('data-reset-token', '1');
    fireEvent.change(screen.getByLabelText('Region'), { target: { value: 'South India' } });
    expect(screen.getByText('2 of 14 sites match · South India.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Reset filters/ }));
    fireEvent.change(screen.getByLabelText('Category'), { target: { value: 'Stepwells' } });
    expect(screen.getByText('2 of 14 sites match · Stepwells.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Reset filters/ }));
    fireEvent.change(screen.getByLabelText('Historical period'), { target: { value: 'Medieval' } });
    expect(screen.getByText('5 of 14 sites match · Medieval.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Reset filters/ }));
    fireEvent.click(screen.getByLabelText('UNESCO only'));
    expect(screen.getByText(/8 of 14 sites match · UNESCO only\./)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Reset filters/ }));
    fireEvent.change(screen.getByLabelText('Region'), { target: { value: 'North India' } });
    fireEvent.change(screen.getByLabelText('Category'), { target: { value: 'Forts and Palaces' } });
    fireEvent.click(screen.getByLabelText('UNESCO only'));
    expect(screen.getByText('1 of 14 sites match · North India · Forts and Palaces · UNESCO only.')).toBeInTheDocument();
    expect(screen.getByText('Jaisalmer Fort')).toBeInTheDocument();
  });

  it('closes the focused site so Reset filters returns to the national view', async () => {
    render(<MapPage />);
    await screen.findByTestId('heritage-map');

    fireEvent.click(screen.getByText('Rani ki Vav'));
    expect(screen.getByTestId('heritage-map')).toHaveAttribute('data-focused', 'rani-ki-vav');

    fireEvent.click(screen.getByRole('button', { name: /Reset filters/ }));
    // A stale focus would fly the map back to the old site instead of India.
    expect(screen.getByTestId('heritage-map')).toHaveAttribute('data-focused', '');
    expect(screen.getByTestId('heritage-map')).toHaveAttribute('data-reset-token', '1');
    expect(screen.getByText('14 of 14 sites match.')).toBeInTheDocument();
  });

  it('searches within sites (including alternative names) and resets to an empty state', async () => {
    render(<MapPage />);
    await screen.findByTestId('heritage-map');

    fireEvent.change(screen.getByLabelText('Search within sites'), {
      target: { value: 'Harmandir' },
    });
    expect(screen.getByText('1 of 14 sites match.')).toBeInTheDocument();
    expect(screen.getByText('Golden Temple')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Search within sites'), {
      target: { value: 'no such place' },
    });
    expect(
      screen.getByText('No heritage site matches these filters.'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reset filters' }));
    expect(screen.getByText('14 of 14 sites match.')).toBeInTheDocument();
  });

  it('sorts and paginates the site list without loading every record at once', async () => {
    render(<MapPage />);
    await screen.findByTestId('heritage-map');

    const ids = () =>
      screen
        .getAllByTestId(/^site-card-/)
        .map((card) => card.getAttribute('data-testid')?.replace('site-card-', ''));
    expect(ids()).toHaveLength(10);
    expect(ids()[0]).toBe('adalaj-stepwell'); // alphabetical

    fireEvent.change(screen.getByLabelText('Sort'), { target: { value: 'state' } });
    expect(ids()[0]).toBe('cellular-jail'); // Andaman and Nicobar Islands
    expect(screen.getByText('1–10 of 14')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(ids()).toHaveLength(4);
    expect(screen.getByText('11–14 of 14')).toBeInTheDocument();
    expect(screen.getByText('Taj Mahal')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(ids()).toHaveLength(10);
  });

  it('opens site details and links to the subject page and archive search', async () => {
    const onOpenSubject = vi.fn();
    const onSearchArchives = vi.fn();
    render(<MapPage onOpenSubject={onOpenSubject} onSearchArchives={onSearchArchives} />);
    await screen.findByTestId('heritage-map');

    fireEvent.click(screen.getByText('Rani ki Vav'));
    expect(screen.getByRole('heading', { level: 3, name: 'Rani ki Vav' })).toBeInTheDocument();
    expect(screen.getByText(/Also known as Queen's Stepwell/)).toBeInTheDocument();
    expect(screen.getByText(/UNESCO 2014/)).toBeInTheDocument();
    expect(screen.getByText(/unverified record/)).toBeInTheDocument();
    expect(screen.getByTestId('heritage-map')).toHaveAttribute('data-focused', 'rani-ki-vav');

    fireEvent.click(screen.getByRole('button', { name: 'Open subject page' }));
    expect(onOpenSubject).toHaveBeenCalledWith('Rani ki Vav');

    fireEvent.click(screen.getByRole('button', { name: 'Search my archives' }));
    expect(onSearchArchives).toHaveBeenCalledWith('Rani ki Vav');
  });

  it('builds, reorders and removes a cross-state route', async () => {
    buildHeritageRoute.mockImplementation(
      async (ids: string[], _start: string, order: 'selection' | 'distance') =>
        mapRoute(ids, order),
    );
    render(<MapPage />);
    await screen.findByTestId('heritage-map');

    const card = (id: string) => screen.getByTestId(`site-card-${id}`);
    fireEvent.click(within(card('rani-ki-vav')).getByRole('button', { name: 'Add to route' }));
    fireEvent.click(within(card('hampi')).getByRole('button', { name: 'Add to route' }));
    fireEvent.click(within(card('sanchi')).getByRole('button', { name: 'Add to route' }));
    expect(screen.getByText('3 selected')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Move Sanchi Stupa up' }));
    fireEvent.click(screen.getByRole('button', { name: 'Build route (3)' }));

    await waitFor(() =>
      expect(buildHeritageRoute).toHaveBeenCalledWith(
        ['rani-ki-vav', 'sanchi', 'hampi'],
        'rani-ki-vav',
        'selection',
      ),
    );
    expect(
      await screen.findByText('Approximate heritage route · 200 km'),
    ).toBeInTheDocument();
    expect(screen.getByText(/Approximate straight-line route, not a road route\./)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Remove Hampi from the route' }));
    expect(screen.getByText('2 selected')).toBeInTheDocument();
    expect(screen.getByText(/selected stops changed/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(screen.getByText('0 selected')).toBeInTheDocument();
    expect(screen.queryByText(/Approximate heritage route/)).not.toBeInTheDocument();
  });

  it('does not plot a site without coordinates but keeps it in the list', async () => {
    render(<MapPage />);
    await screen.findByTestId('heritage-map');

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText('Unlocated Heritage Record')).toBeInTheDocument();
    // The map received the same records; it is the map's job to skip null coords.
    expect(screen.getByTestId('heritage-map')).toHaveAttribute('data-site-count', '14');
  });

  it('filters sites by story tradition and reads a story inside the app', async () => {
    listSiteStories.mockResolvedValue({
      siteId: 'rani-ki-vav',
      siteName: 'Rani ki Vav',
      total: 1,
      stories: [STORY],
      categories: [{ name: 'history', label: 'History', count: 1 }],
      notice: 'Stories are presented as separate traditions.',
    });
    getStory.mockResolvedValue(STORY);
    render(<MapPage />);
    await screen.findByTestId('heritage-map');

    fireEvent.change(screen.getByLabelText('Stories & Legends'), {
      target: { value: 'history' },
    });
    expect(screen.getByTestId('heritage-map')).toHaveAttribute('data-site-count', '1');
    expect(screen.getByTestId('heritage-map')).toHaveAttribute(
      'data-fit-key',
      'story:history',
    );
    expect(screen.getByText(/stories: History/)).toBeInTheDocument();

    fireEvent.click(screen.getByText('Rani ki Vav'));
    expect(
      await screen.findByRole('heading', { level: 4, name: /Stories & Legends/ }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText('The stepwell Queen Udayamati commissioned'),
    ).toBeInTheDocument();
    expect(screen.getByText('Historically documented')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Read full story' }));
    expect(
      await screen.findByRole('heading', {
        level: 2,
        name: 'The stepwell Queen Udayamati commissioned',
      }),
    ).toBeInTheDocument();
    expect(screen.getByText('Sources & evidence')).toBeInTheDocument();
    expect(screen.getByText(/Back to Rani ki Vav/)).toBeInTheDocument();
  });

  it('surfaces a load failure instead of an empty map', async () => {
    listHeritageSites.mockRejectedValue(new Error('offline'));
    render(<MapPage />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load the heritage map.');
    expect(screen.queryByTestId('heritage-map')).not.toBeInTheDocument();
  });
});
