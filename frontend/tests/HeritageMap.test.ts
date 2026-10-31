import { describe, expect, it } from 'vitest';

import { CLUSTER_MAX_ZOOM, clusterHeritageSites } from '../src/components/heritage/HeritageMap';
import type { HeritageSite } from '../src/types/archive';

function site(overrides: Partial<HeritageSite>): HeritageSite {
  return {
    id: 'site',
    name: 'Site',
    alternativeNames: [],
    region: 'West India',
    state: 'Gujarat',
    isUnionTerritory: false,
    category: 'Stepwells',
    periodGroup: 'Medieval',
    period: '15th century CE',
    unescoStatus: false,
    latitude: 23.0,
    longitude: 72.0,
    coordinateAccuracy: 'approximate',
    verificationStatus: 'unverified',
    sourceRefs: [],
    dataset: 'curated-demo-india',
    storyCount: 0,
    storyCategories: [],
    featuredStories: [],
    ...overrides,
  };
}

describe('clusterHeritageSites', () => {
  it('never invents a marker for a site without published coordinates', () => {
    const clusters = clusterHeritageSites(
      [
        site({ id: 'located', latitude: 23.8589, longitude: 72.1019 }),
        site({ id: 'unlocated', latitude: null, longitude: null }),
      ],
      4,
    );

    const ids = clusters.flatMap((cluster) => cluster.sites.map((item) => item.id));
    expect(ids).toEqual(['located']);
    expect(ids).not.toContain('unlocated');
  });

  it('groups nearby sites at national zoom and separates them when zoomed in', () => {
    const sites = [
      site({ id: 'rani-ki-vav', latitude: 23.8589, longitude: 72.1019 }),
      site({ id: 'adalaj-stepwell', latitude: 23.1667, longitude: 72.5833 }),
    ];

    const national = clusterHeritageSites(sites, 4);
    expect(national).toHaveLength(1);
    expect(national[0].sites).toHaveLength(2);

    const local = clusterHeritageSites(sites, CLUSTER_MAX_ZOOM + 1);
    expect(local).toHaveLength(2);
    expect(local.every((cluster) => cluster.sites.length === 1)).toBe(true);
  });

  it('produces stable, deterministic cluster keys', () => {
    const sites = [
      site({ id: 'a', latitude: 12.9, longitude: 77.5 }),
      site({ id: 'b', latitude: 13.1, longitude: 77.7 }),
    ];
    expect(clusterHeritageSites(sites, 3)).toEqual(clusterHeritageSites(sites, 3));
  });
});
