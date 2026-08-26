import test from 'node:test';
import assert from 'node:assert/strict';

import {
  LiveLocationTracker,
  accuracyCircleGeoJson,
  haversineMeters,
  locationError,
  positionToFix,
  shouldAnalyzeFix,
} from '../static/location-tracking.js';

const NOW = 1_000_000;

function position(latitude, longitude, accuracy = 10, timestamp = NOW) {
  return {
    timestamp,
    coords: { latitude, longitude, accuracy, altitude: null, heading: null, speed: null },
  };
}

test('haversineMeters measures nearby movement', () => {
  const distance = haversineMeters(
    { latitude: 40, longitude: -73 },
    { latitude: 40.001, longitude: -73 },
  );
  assert.ok(distance > 111 && distance < 112);
});

test('positionToFix accepts a fresh valid fix and rejects stale or invalid fixes', () => {
  const fix = positionToFix(position(40, -73, 12), { now: NOW });
  assert.equal(fix.latitude, 40);
  assert.equal(fix.accuracy, 12);
  assert.equal(positionToFix(position(40, -73, 12, NOW - 30_001), { now: NOW }), null);
  assert.equal(positionToFix(position(100, -73), { now: NOW }), null);
});

test('analysis policy accepts the first accurate fix', () => {
  const accurate = positionToFix(position(40, -73, 25), { now: NOW });
  const poor = positionToFix(position(40, -73, 120), { now: NOW });
  assert.equal(shouldAnalyzeFix(accurate, null, { now: NOW }), true);
  assert.equal(shouldAnalyzeFix(poor, null, { now: NOW }), false);
});

test('analysis policy requires both elapsed time and meaningful movement', () => {
  const previous = positionToFix(position(40, -73, 10), { now: NOW });
  const moved = positionToFix(position(40.001, -73, 10, NOW + 20_000), { now: NOW + 20_000 });
  const jitter = positionToFix(position(40.00001, -73, 10, NOW + 20_000), { now: NOW + 20_000 });

  assert.equal(shouldAnalyzeFix(moved, previous, { now: NOW + 10_000, lastAnalyzedAt: NOW }), false);
  assert.equal(shouldAnalyzeFix(jitter, previous, { now: NOW + 20_000, lastAnalyzedAt: NOW }), false);
  assert.equal(shouldAnalyzeFix(moved, previous, { now: NOW + 20_000, lastAnalyzedAt: NOW }), true);
});

test('forced analysis bypasses movement and time but still rejects unusable accuracy', () => {
  const previous = positionToFix(position(40, -73, 10), { now: NOW });
  const usable = positionToFix(position(40, -73, 200), { now: NOW });
  const unusable = positionToFix(position(40, -73, 300), { now: NOW });
  assert.equal(shouldAnalyzeFix(usable, previous, { force: true }), true);
  assert.equal(shouldAnalyzeFix(unusable, previous, { force: true }), false);
});

test('accuracy circle is closed and centered around the fix', () => {
  const fix = positionToFix(position(40, -73, 25), { now: NOW });
  const polygon = accuracyCircleGeoJson(fix, 16);
  const ring = polygon.geometry.coordinates[0];
  assert.equal(ring.length, 17);
  assert.deepEqual(ring[0], ring.at(-1));
});

test('LiveLocationTracker starts once, forwards fixes, and clears its watch', () => {
  let success;
  let requestedOptions;
  const cleared = [];
  const fixes = [];
  const statuses = [];
  const geolocation = {
    watchPosition(onSuccess, _onError, options) {
      success = onSuccess;
      requestedOptions = options;
      return 42;
    },
    clearWatch(id) { cleared.push(id); },
  };
  const tracker = new LiveLocationTracker({
    geolocation,
    now: () => NOW,
    onFix: (fix) => fixes.push(fix),
    onStatus: (status) => statuses.push(status),
  });

  assert.equal(tracker.start(), true);
  assert.equal(tracker.start(), true);
  success(position(40, -73));
  tracker.stop();

  assert.equal(requestedOptions.enableHighAccuracy, true);
  assert.equal(fixes.length, 1);
  assert.deepEqual(statuses, ['requesting', 'active', 'stopped']);
  assert.deepEqual(cleared, [42]);
});

test('location errors distinguish fatal permission denial from transient failures', () => {
  assert.equal(locationError({ code: 1 }).fatal, true);
  assert.equal(locationError({ code: 2 }).fatal, false);
  assert.equal(locationError({ code: 3 }).fatal, false);
});
