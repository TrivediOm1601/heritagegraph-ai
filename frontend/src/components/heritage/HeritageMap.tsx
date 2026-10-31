import { useEffect, useRef, useState } from 'react';
import * as L from 'leaflet';
import 'leaflet/dist/leaflet.css';

import type { HeritageRoute, HeritageSite } from '../../types/archive';

interface HeritageMapProps {
  sites: HeritageSite[];
  /** Sites currently in the route (always drawn individually). */
  selectedIds: string[];
  /** The site whose details are open; the map flies to it. */
  focusedId?: string | null;
  /**
   * A key identifying the active admin/region filter. When it changes the
   * map fits the matching sites, so choosing a state zooms to that state;
   * an empty value after a filter returns the view to all of India.
   */
  fitKey?: string | null;
  /** Increment to return the map to the full India view (reset filters). */
  resetToken?: number;
  /** Called when a marker or its "View details" button is activated. */
  onOpen: (siteId: string) => void;
  route?: HeritageRoute | null;
}

/** India's national extent, used for the initial view. */
export const INDIA_BOUNDS: L.LatLngBoundsExpression = [
  [6.5, 68.0],
  [35.8, 97.5],
];

export const INITIAL_CENTER: [number, number] = [22.8, 80.2];
export const INITIAL_ZOOM = 4;

/** Above this zoom every site is drawn individually. */
export const CLUSTER_MAX_ZOOM = 5;

/** Category colour: distinct, accessible on the ivory map background. */
const CATEGORY_COLORS: Record<string, string> = {
  'Forts and Palaces': '#934027',
  Temples: '#146a66',
  Mosques: '#2f5d8a',
  Churches: '#7a4b8f',
  Monasteries: '#b1502e',
  Stepwells: '#a3552b',
  'Archaeological Sites': '#6f6247',
  'Ancient Cities': '#8a6a2f',
  Caves: '#3f6b52',
  Museums: '#5b6462',
  Memorials: '#773725',
  'Cultural Landscapes': '#4d7a3a',
  'Traditional Architecture': '#9a6b4f',
  'Other Heritage Sites': '#a4926a',
};

export function categoryColor(category: string): string {
  return CATEGORY_COLORS[category] ?? '#a4926a';
}

/** Category names that have a colour, in the documented order. */
export const MAP_CATEGORIES = Object.keys(CATEGORY_COLORS);

export interface SiteCluster {
  key: string;
  latitude: number;
  longitude: number;
  sites: HeritageSite[];
}

/**
 * Group sites into a coarse lat/lon grid at low zoom.
 *
 * This is deliberately lightweight: the curated national dataset is bounded,
 * and a grid keeps the map responsive without a clustering plugin. Selected
 * (route) sites are excluded by the caller so they always stay visible.
 */
export function clusterHeritageSites(sites: HeritageSite[], zoom: number): SiteCluster[] {
  const grid = zoom <= 3 ? 5 : zoom <= 4 ? 3 : zoom <= CLUSTER_MAX_ZOOM ? 1.5 : 0;
  const clusters = new Map<string, SiteCluster>();

  for (const site of sites) {
    if (site.latitude == null || site.longitude == null) continue;
    if (grid === 0) {
      clusters.set(`site:${site.id}`, {
        key: `site:${site.id}`,
        latitude: site.latitude,
        longitude: site.longitude,
        sites: [site],
      });
      continue;
    }
    const cellLat = Math.floor(site.latitude / grid) * grid + grid / 2;
    const cellLon = Math.floor(site.longitude / grid) * grid + grid / 2;
    const key = `cluster:${cellLat.toFixed(2)}:${cellLon.toFixed(2)}`;
    const existing = clusters.get(key);
    if (existing) {
      existing.sites.push(site);
    } else {
      clusters.set(key, {
        key,
        latitude: cellLat,
        longitude: cellLon,
        sites: [site],
      });
    }
  }

  return Array.from(clusters.values()).sort((a, b) => b.sites.length - a.sites.length);
}

function popupContent(site: HeritageSite, onOpen: (siteId: string) => void): HTMLElement {
  const wrapper = document.createElement('div');
  wrapper.className = 'max-w-[16rem] text-sm';

  const title = document.createElement('p');
  title.className = 'font-semibold text-teal-800';
  title.textContent = site.name;
  wrapper.appendChild(title);

  const location = document.createElement('p');
  location.className = 'mt-0.5 text-xs text-gray-600';
  location.textContent = [site.city, site.state].filter(Boolean).join(', ');
  wrapper.appendChild(location);

  const meta = document.createElement('p');
  meta.className = 'mt-0.5 text-xs text-gray-600';
  meta.textContent = [site.category, site.period].filter(Boolean).join(' · ');
  wrapper.appendChild(meta);

  if (site.description) {
    const description = document.createElement('p');
    description.className = 'mt-1 line-clamp-3 text-xs leading-relaxed';
    description.textContent = site.description;
    wrapper.appendChild(description);
  }

  const coordinates = document.createElement('p');
  coordinates.className = 'mt-1 text-[11px] text-gray-500';
  coordinates.textContent =
    site.latitude != null && site.longitude != null
      ? `${site.latitude.toFixed(4)}, ${site.longitude.toFixed(4)} · ${site.coordinateAccuracy} coordinates`
      : 'No published coordinate — not plotted';
  wrapper.appendChild(coordinates);

  const button = document.createElement('button');
  button.type = 'button';
  button.className =
    'mt-2 rounded-md bg-teal-600 px-2 py-1 text-xs font-medium text-ivory hover:bg-teal-700';
  button.textContent = 'View details';
  button.addEventListener('click', () => onOpen(site.id));
  wrapper.appendChild(button);

  return wrapper;
}

function clusterIcon(count: number): L.DivIcon {
  return L.divIcon({
    className: 'heritage-cluster',
    html: `<span>${count}</span>`,
    iconSize: [38, 38],
    iconAnchor: [19, 19],
  });
}

/**
 * Leaflet map of the curated India-wide heritage dataset.
 *
 * The initial view fits the national extent. At low zoom, nearby unselected
 * sites are grouped into a count bubble (a lightweight grid cluster); route
 * sites stay individually visible. Markers are drawn only for sites with a
 * real published coordinate, and the list beside the map is the accessible
 * representation of the same data.
 */
export function HeritageMap({
  sites,
  selectedIds,
  focusedId,
  fitKey,
  resetToken = 0,
  onOpen,
  route,
}: HeritageMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markersRef = useRef<L.LayerGroup | null>(null);
  const routeRef = useRef<L.Polyline | null>(null);
  const markerRefs = useRef(new Map<string, L.CircleMarker>());
  const onOpenRef = useRef(onOpen);
  onOpenRef.current = onOpen;
  const [zoom, setZoom] = useState(INITIAL_ZOOM);
  const previousFitKey = useRef<string | null>(null);
  /** Last site the map flew to, so filter changes cannot re-trigger the flight. */
  const lastFocusedRef = useRef<string | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || mapRef.current) return;

    const map = L.map(container, {
      center: INITIAL_CENTER,
      zoom: INITIAL_ZOOM,
      minZoom: 3,
      maxZoom: 18,
      scrollWheelZoom: false,
      worldCopyJump: true,
    });
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);

    markersRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;
    map.fitBounds(INDIA_BOUNDS, { padding: [16, 16], animate: false });
    map.on('zoomend', () => setZoom(map.getZoom()));
    // The panel can mount before its final width is known.
    window.setTimeout(() => map.invalidateSize(), 0);

    return () => {
      map.remove();
      mapRef.current = null;
      markersRef.current = null;
      routeRef.current = null;
      markerRefs.current.clear();
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const group = markersRef.current;
    if (!map || !group) return;
    group.clearLayers();
    markerRefs.current.clear();

    const selected = new Set(selectedIds);
    const located = sites.filter((site) => site.latitude != null && site.longitude != null);
    const selectedSites = located.filter((site) => selected.has(site.id));
    const unselected = located.filter((site) => !selected.has(site.id));

    for (const site of selectedSites) {
      const marker = L.circleMarker([site.latitude as number, site.longitude as number], {
        radius: 9,
        color: '#622f21',
        weight: 3,
        fillColor: categoryColor(site.category),
        fillOpacity: 0.95,
      });
      marker.bindPopup(popupContent(site, (id) => onOpenRef.current(id)));
      marker.bindTooltip(`${site.name} (in route)`, { direction: 'top', offset: [0, -6] });
      marker.on('click', () => onOpenRef.current(site.id));
      marker.addTo(group);
      markerRefs.current.set(site.id, marker);
    }

    for (const cluster of clusterHeritageSites(unselected, zoom)) {
      if (cluster.sites.length === 1) {
        const site = cluster.sites[0];
        const focused = focusedId === site.id;
        const marker = L.circleMarker(
          [site.latitude as number, site.longitude as number],
          {
            radius: focused ? 10 : 6.5,
            color: focused ? '#115653' : '#fffff0',
            weight: focused ? 3 : 2,
            fillColor: categoryColor(site.category),
            fillOpacity: 0.9,
          },
        );
        marker.bindPopup(popupContent(site, (id) => onOpenRef.current(id)));
        marker.bindTooltip(site.name, { direction: 'top', offset: [0, -6] });
        marker.on('click', () => onOpenRef.current(site.id));
        marker.addTo(group);
        markerRefs.current.set(site.id, marker);
      } else {
        const marker = L.marker([cluster.latitude, cluster.longitude], {
          icon: clusterIcon(cluster.sites.length),
          keyboard: true,
          title: `${cluster.sites.length} heritage sites — zoom in`,
          alt: `${cluster.sites.length} heritage sites`,
        });
        marker.bindTooltip(`${cluster.sites.length} sites — zoom in`, {
          direction: 'top',
          offset: [0, -14],
        });
        marker.on('click', () => {
          const bounds = L.latLngBounds(
            cluster.sites.map((site) => [site.latitude as number, site.longitude as number]),
          );
          map.fitBounds(bounds, { padding: [32, 32], maxZoom: 8, animate: false });
        });
        marker.addTo(group);
      }
    }

    // The marker was just rebuilt, so reopen the focused site's popup to keep
    // it in sync with the details panel after a filter or zoom change. The
    // map itself is not moved here — that is the focus effect below.
    if (focusedId) markerRefs.current.get(focusedId)?.openPopup();
  }, [sites, selectedIds, focusedId, zoom]);

  // Filter-driven view: fit the matching records. This is what makes a state
  // selection zoom to that state while the national view stays the default.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (!fitKey) {
      if (previousFitKey.current) {
        map.fitBounds(INDIA_BOUNDS, { padding: [16, 16], animate: false });
      }
      previousFitKey.current = '';
      return;
    }
    previousFitKey.current = fitKey;
    const located = sites.filter(
      (site) => site.latitude != null && site.longitude != null,
    );
    if (located.length === 0) return;
    if (located.length === 1) {
      map.flyTo([located[0].latitude as number, located[0].longitude as number], 7, {
        duration: 0.5,
      });
      return;
    }
    const bounds = L.latLngBounds(
      located.map((site) => [site.latitude as number, site.longitude as number]),
    );
    map.fitBounds(bounds, { padding: [32, 32], maxZoom: 8, animate: false });
  }, [fitKey, sites]);

  // Reset filters: return to the full national extent, whatever zoom the
  // route or a previous state selection left behind.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || resetToken === 0) return;
    previousFitKey.current = '';
    map.fitBounds(INDIA_BOUNDS, { padding: [16, 16], animate: false });
  }, [resetToken]);

  // Focus from the list: move the map to the site. Runs only when the
  // focused site changes — `sites` is in the dependency list so a site that
  // arrives with the dataset can still be focused, but a filter or zoom
  // change must not drag the view back to the last selected site.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !focusedId) {
      lastFocusedRef.current = null;
      return;
    }
    if (lastFocusedRef.current === focusedId) return;
    const site = sites.find((item) => item.id === focusedId);
    if (!site || site.latitude == null || site.longitude == null) return;
    lastFocusedRef.current = focusedId;
    map.flyTo([site.latitude, site.longitude], Math.max(map.getZoom(), 7), {
      duration: 0.6,
    });
  }, [focusedId, sites]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (routeRef.current) {
      routeRef.current.remove();
      routeRef.current = null;
    }
    if (!route || route.stops.length < 2) return;

    const points: Array<[number, number]> = [];
    for (const stop of route.stops) {
      if (stop.latitude != null && stop.longitude != null) {
        points.push([stop.latitude, stop.longitude]);
      }
    }
    if (points.length < 2) return;
    routeRef.current = L.polyline(points, {
      color: '#b1502e',
      weight: 3,
      opacity: 0.85,
      dashArray: '7 6',
    }).addTo(map);
    map.fitBounds(routeRef.current.getBounds(), { padding: [32, 32], animate: false });
  }, [route]);

  return (
    <div
      ref={containerRef}
      role="application"
      aria-label="Heritage map of India with curated sites"
      className="h-[26rem] w-full rounded-xl border border-ivory-300 bg-ivory-100 sm:h-[34rem] lg:h-[38rem]"
    />
  );
}

export default HeritageMap;
