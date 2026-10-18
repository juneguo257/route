const test = require('node:test');
const assert = require('node:assert/strict');
const RouteData = require('../web/data.js');

const snapshot = () => ({collected_at: '2026-10-18T15:00:00Z', search_area: 'Philadelphia', reported_total: 1566, returned_on_page: 1, has_more_pages: true,
  stations: [{id: '42', name: 'Liberty', latitude: 39.95, longitude: -75.16, address: {line1: '123 Main St', locality: 'Philadelphia', region: 'PA', country: 'US'},
    prices: [{fuel_grade: 'regular_gas', payment_type: 'credit', price: '3.999', reported_at: '2026-10-18T14:00:00Z'},
      {fuel_grade: 'regular_gas', payment_type: 'cash', price: '3.899', reported_at: '2026-10-17T13:00:00Z'},
      {fuel_grade: 'diesel', payment_type: 'credit', price: '0', reported_at: null}]}]});

test('GasBuddy cash and credit remain separate, and zero prices are absent', () => {
  const data = RouteData.gasbuddy(snapshot()), station = data.stations[0];
  assert.equal(RouteData.observation(station, 'regular', 'credit').price, '3.999');
  assert.equal(RouteData.observation(station, 'regular', 'cash').price, '3.899');
  assert.equal(RouteData.observation(station, 'diesel', 'credit'), null);
  assert.equal(station.membership_required, null);
  assert.match(data.coverage, /1566/);
  assert.match(data.coverage, /not included/);
});

test('cash selection never substitutes a credit observation', () => {
  const input = snapshot();input.stations[0].prices = input.stations[0].prices.slice(0, 1);
  assert.equal(RouteData.observation(RouteData.gasbuddy(input).stations[0], 'regular', 'cash'), null);
});

test('freshness recalculates from report timestamp instead of trusting exported age', () => {
  const observation = {reported_at: '2026-10-17T13:00:00Z', age_hours: 1, freshness: 'recent'};
  const now = Date.parse('2026-10-18T15:00:00Z');
  assert.equal(RouteData.freshness(observation, 24, now).stale, true);
  assert.equal(RouteData.freshness({reported_at: null}, 24, now).unknown, true);
  assert.equal(RouteData.freshness({reported_at: 'bad date'}, 24, now).unknown, true);
  assert.equal(RouteData.freshness({reported_at: '2026-10-19T00:00:00Z'}, 24, now).unknown, true);
});

test('retailer observations do not become credit or cash prices', () => {
  const data = RouteData.retailers({stations: [{id: 'costco-1', brand: 'Costco', latitude: 39.95, longitude: -75.16, prices: {regular: '3.659'}, membership_required: true}]});
  assert.equal(RouteData.observation(data.stations[0], 'regular', 'published').price, '3.659');
  assert.equal(RouteData.observation(data.stations[0], 'regular', 'credit'), null);
  assert.equal(data.stations[0].membership_required, true);
});

test('missing and failed exports produce explicit errors; invalid coordinates are excluded', () => {
  assert.throws(() => RouteData.gasbuddy(null), /invalid/);
  assert.throws(() => RouteData.gasbuddy({stations: []}), /invalid/);
  const input = snapshot();input.stations[0].latitude = null;
  assert.equal(RouteData.gasbuddy(input).stations.length, 0);
  assert.equal(RouteData.distanceMiles(39.9526, -75.1652), 0);
});
