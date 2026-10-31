import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { StoryReader } from '../src/components/heritage/StoryReader';

const getStory = vi.fn();
const askStories = vi.fn();
const submitStory = vi.fn();

vi.mock('../src/services/archiveApi', () => ({
  getStory: (...args: unknown[]) => getStory(...args),
  askStories: (...args: unknown[]) => askStories(...args),
  submitStory: (...args: unknown[]) => submitStory(...args),
}));

const authState = { phase: 'anonymous' as string };
vi.mock('../src/hooks/useAuth', () => ({
  useAuth: () => ({
    phase: authState.phase,
    user: authState.phase === 'authenticated' ? { id: 'u1', email: 'a@b.c', displayName: 'A' } : null,
    error: null,
    busy: false,
    login: vi.fn(),
    signup: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
    formError: null,
    clearFormError: vi.fn(),
  }),
}));

const SOURCE = {
  id: 'src-1',
  title: 'Konark Sun Temple',
  author: 'Wikipedia contributors',
  sourceType: 'secondary-reference',
  published: '2026',
  url: 'https://en.wikipedia.org/wiki/Konark_Sun_Temple',
  excerpt: null,
  locator: 'Legend section',
  licence: 'CC BY-SA 4.0',
};

const STORY = {
  id: 'konark-origin',
  siteId: 'konark-sun-temple',
  siteName: 'Sun Temple, Konark',
  siteState: 'Odisha',
  title: 'Why was the Sun Temple built? Two traditions',
  summary: 'A temple tradition and a scholarly account answer the same question.',
  narrative: 'Two accounts answer the same question.\n\nThe traditions are kept separate.',
  category: 'mythology',
  categoryLabel: 'Mythology',
  narrativeType: 'conflicting-accounts',
  narrativeTypeLabel: 'Conflicting accounts',
  tradition: 'Odia temple tradition and scholarship',
  language: 'en',
  languageLabel: 'English',
  community: null,
  period: 'c. 1250 CE',
  verificationStatus: 'source-checked',
  provenance: 'Curated from the cited sources',
  isCommunity: false,
  versionCount: 2,
  sourceCount: 1,
  culturalBackground: 'Konark remains a living pilgrimage site.',
  relatedEntities: [],
  relatedSites: [],
  sources: [SOURCE],
  versions: [
    {
      id: 'konark-origin-samba',
      label: "Temple tradition: Samba's cure",
      summary: 'Samba was cured by Surya and built the temple in thanksgiving.',
      narrative: 'The Madala Panji preserves the account.',
      tradition: 'Madala Panji',
      language: 'en',
      community: 'Odia temple tradition',
      differences: 'Places the founder in sacred narrative.',
      verificationStatus: 'source-checked',
      sources: [SOURCE],
    },
    {
      id: 'konark-origin-narasimha',
      label: 'Scholarly account: Narasimhadeva I',
      summary: 'Epigraphic evidence dates the temple to Narasimhadeva I.',
      narrative: null,
      tradition: 'Art-historical scholarship',
      language: 'en',
      community: null,
      differences: 'Attributes construction to a named king and a date.',
      verificationStatus: 'source-checked',
      sources: [],
    },
  ],
  relatedStories: [
    {
      id: 'konark-wheels',
      siteId: 'konark-sun-temple',
      title: 'Reading the twenty-four wheels',
      summary: 'Interpretations of the wheels.',
      category: 'architecture',
      categoryLabel: 'Architecture',
      narrativeType: 'scholarly-interpretation',
      narrativeTypeLabel: 'Scholarly interpretation',
      tradition: null,
      language: 'en',
      languageLabel: 'English',
      community: null,
      period: null,
      verificationStatus: 'source-checked',
      provenance: 'Curated',
      isCommunity: false,
      versionCount: 0,
      sourceCount: 1,
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  authState.phase = 'anonymous';
  getStory.mockResolvedValue(STORY);
});

describe('StoryReader', () => {
  it('presents alternative traditions separately with their own sources', async () => {
    render(<StoryReader storyId="konark-origin" onBack={vi.fn()} onOpenStory={vi.fn()} />);

    expect(
      await screen.findByRole('heading', { level: 2, name: STORY.title }),
    ).toBeInTheDocument();
    expect(screen.getByText('Alternative traditions')).toBeInTheDocument();
    expect(screen.getByText("Temple tradition: Samba's cure")).toBeInTheDocument();
    expect(screen.getByText('Scholarly account: Narasimhadeva I')).toBeInTheDocument();
    expect(screen.getByText(/Places the founder in sacred narrative/)).toBeInTheDocument();
    expect(screen.getAllByText(/Differs from the other accounts/)).toHaveLength(2);
    expect(screen.getByText('Community: Odia temple tradition')).toBeInTheDocument();
    expect(screen.getByText('Sources & evidence')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'Open source' })[0]).toHaveAttribute(
      'href',
      'https://en.wikipedia.org/wiki/Konark_Sun_Temple',
    );
    expect(screen.getByText('Reading the twenty-four wheels')).toBeInTheDocument();
  });

  it('answers only from stored stories and shows the sources consulted', async () => {
    askStories.mockResolvedValue({
      answer: '**Why was the Sun Temple built? Two traditions** (Conflicting accounts): …',
      matchedStories: [STORY],
      citations: ['Konark Sun Temple — Wikipedia contributors (https://en.wikipedia.org/wiki/Konark_Sun_Temple)'],
      limitation: 'This answer draws on 1 of 3 stored stories.',
    });
    render(<StoryReader storyId="konark-origin" onBack={vi.fn()} onOpenStory={vi.fn()} />);
    await screen.findByRole('heading', { level: 2, name: STORY.title });

    fireEvent.change(
      screen.getByRole('textbox', { name: "Ask about this place's stories" }),
      {
        target: { value: 'Are there multiple versions of the origin story?' },
      },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));

    expect(await screen.findByTestId('story-answer')).toHaveTextContent('Two traditions');
    expect(screen.getByText('Sources consulted')).toBeInTheDocument();
    expect(screen.getByText(/This answer draws on 1 of 3 stored stories/)).toBeInTheDocument();
    expect(askStories).toHaveBeenCalledWith(
      'konark-sun-temple',
      'Are there multiple versions of the origin story?',
    );
  });

  it('keeps contribution behind sign-in and submits with consent', async () => {
    const { rerender } = render(
      <StoryReader storyId="konark-origin" onBack={vi.fn()} onOpenStory={vi.fn()} />,
    );
    await screen.findByRole('heading', { level: 2, name: STORY.title });
    expect(screen.getByText(/Sign in from the header to submit a story/)).toBeInTheDocument();

    authState.phase = 'authenticated';
    submitStory.mockResolvedValue({
      story: { ...STORY, id: 'community-x', isCommunity: true },
      status: 'pending',
      message: 'Thank you. The contribution is stored as pending.',
    });
    rerender(<StoryReader storyId="konark-origin" onBack={vi.fn()} onOpenStory={vi.fn()} />);
    expect(screen.queryByText(/Sign in from the header/)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Story title'), {
      target: { value: 'A local account of the wheels' },
    });
    fireEvent.change(screen.getByLabelText('The narrative'), {
      target: { value: 'A contributor describes how the wheels were explained to them as a child.' },
    });
    fireEvent.click(screen.getByLabelText(/I have the contributor's permission/));
    fireEvent.click(screen.getByRole('button', { name: 'Submit for review' }));

    await waitFor(() =>
      expect(submitStory).toHaveBeenCalledWith(
        'konark-sun-temple',
        expect.objectContaining({
          title: 'A local account of the wheels',
          language: 'en',
          consent: true,
        }),
      ),
    );
    expect(await screen.findByText(/stored as pending/)).toBeInTheDocument();
  });
});
