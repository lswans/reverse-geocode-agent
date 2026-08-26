const EARTH_RADIUS_METERS = 6_371_008.8;

export const DEFAULT_GEOLOCATION_OPTIONS = Object.freeze({
  enableHighAccuracy: true,
  maximumAge: 3_000,
  timeout: 15_000,
});

export const DEFAULT_ANALYSIS_POLICY = Object.freeze({
  minDistanceMeters: 50,
  minIntervalMs: 15_000,
  maxAccuracyMeters: 75,
  maxForcedAccuracyMeters: 250,
});

function finiteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

export function haversineMeters(first, second) {
  const toRadians = (degrees) => degrees * Math.PI / 180;
  const lat1 = toRadians(first.latitude);
  const lat2 = toRadians(second.latitude);
  const deltaLat = lat2 - lat1;
  const deltaLon = toRadians(second.longitude - first.longitude);
  const sinLat = Math.sin(deltaLat / 2);
  const sinLon = Math.sin(deltaLon / 2);
  const a = sinLat * sinLat + Math.cos(lat1) * Math.cos(lat2) * sinLon * sinLon;
  return 2 * EARTH_RADIUS_METERS * Math.asin(Math.min(1, Math.sqrt(a)));
}

export function positionToFix(position, { now = Date.now(), maxAgeMs = 30_000 } = {}) {
  const coords = position?.coords;
  const timestamp = Number(position?.timestamp);
  if (!coords
      || !finiteNumber(coords.latitude)
      || !finiteNumber(coords.longitude)
      || !finiteNumber(coords.accuracy)
      || coords.latitude < -90
      || coords.latitude > 90
      || coords.longitude < -180
      || coords.longitude > 180
      || coords.accuracy < 0
      || !Number.isFinite(timestamp)
      || now - timestamp > maxAgeMs) {
    return null;
  }

  return Object.freeze({
    latitude: coords.latitude,
    longitude: coords.longitude,
    accuracy: coords.accuracy,
    altitude: finiteNumber(coords.altitude) ? coords.altitude : null,
    heading: finiteNumber(coords.heading) ? coords.heading : null,
    speed: finiteNumber(coords.speed) ? coords.speed : null,
    timestamp,
  });
}

export function shouldAnalyzeFix(
  fix,
  lastAnalyzedFix,
  {
    now = Date.now(),
    lastAnalyzedAt = 0,
    force = false,
    ...overrides
  } = {},
) {
  const policy = { ...DEFAULT_ANALYSIS_POLICY, ...overrides };
  if (!fix) return false;
  if (force) return fix.accuracy <= policy.maxForcedAccuracyMeters;
  if (fix.accuracy > policy.maxAccuracyMeters) return false;
  if (!lastAnalyzedFix) return true;
  if (now - lastAnalyzedAt < policy.minIntervalMs) return false;

  const requiredDistance = Math.max(policy.minDistanceMeters, fix.accuracy);
  return haversineMeters(lastAnalyzedFix, fix) >= requiredDistance;
}

export function accuracyCircleGeoJson(fix, pointCount = 64) {
  if (!fix || fix.accuracy <= 0) {
    return { type: 'FeatureCollection', features: [] };
  }

  const latitudeRadians = fix.latitude * Math.PI / 180;
  const angularDistance = fix.accuracy / EARTH_RADIUS_METERS;
  const coordinates = [];

  for (let index = 0; index <= pointCount; index += 1) {
    const bearing = index / pointCount * Math.PI * 2;
    const latitude = Math.asin(
      Math.sin(latitudeRadians) * Math.cos(angularDistance)
      + Math.cos(latitudeRadians) * Math.sin(angularDistance) * Math.cos(bearing),
    );
    const longitude = fix.longitude * Math.PI / 180 + Math.atan2(
      Math.sin(bearing) * Math.sin(angularDistance) * Math.cos(latitudeRadians),
      Math.cos(angularDistance) - Math.sin(latitudeRadians) * Math.sin(latitude),
    );
    coordinates.push([longitude * 180 / Math.PI, latitude * 180 / Math.PI]);
  }

  return {
    type: 'Feature',
    properties: { accuracy: fix.accuracy },
    geometry: { type: 'Polygon', coordinates: [coordinates] },
  };
}

export function locationError(error) {
  const code = Number(error?.code);
  if (code === 1) {
    return { fatal: true, message: 'Location permission was denied. Allow location access in your browser settings and try again.' };
  }
  if (code === 2) {
    return { fatal: false, message: 'Your location is temporarily unavailable. Move somewhere with a clearer signal and try again.' };
  }
  if (code === 3) {
    return { fatal: false, message: 'The location request timed out. Still listening for a better GPS fix…' };
  }
  return { fatal: false, message: 'The browser could not read your location.' };
}

export class LiveLocationTracker {
  constructor({
    geolocation = globalThis.navigator?.geolocation,
    onFix = () => {},
    onStatus = () => {},
    onError = () => {},
    now = () => Date.now(),
    options = DEFAULT_GEOLOCATION_OPTIONS,
  } = {}) {
    this.geolocation = geolocation;
    this.onFix = onFix;
    this.onStatus = onStatus;
    this.onError = onError;
    this.now = now;
    this.options = { ...DEFAULT_GEOLOCATION_OPTIONS, ...options };
    this.watchId = null;
  }

  get active() {
    return this.watchId !== null;
  }

  start() {
    if (this.active) return true;
    if (!this.geolocation?.watchPosition) {
      this.onError({ fatal: true, message: 'This browser does not support live location tracking.' });
      return false;
    }

    this.onStatus('requesting');
    this.watchId = this.geolocation.watchPosition(
      (position) => {
        const fix = positionToFix(position, { now: this.now() });
        if (!fix) return;
        this.onStatus('active');
        this.onFix(fix);
      },
      (error) => {
        const detail = locationError(error);
        this.onError(detail);
        if (detail.fatal) this.stop({ announce: false });
      },
      this.options,
    );
    return true;
  }

  stop({ announce = true } = {}) {
    if (this.watchId !== null && this.geolocation?.clearWatch) {
      this.geolocation.clearWatch(this.watchId);
    }
    this.watchId = null;
    if (announce) this.onStatus('stopped');
  }
}
