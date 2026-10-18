"use strict";
(function (root) {
  const CENTER = {latitude: 39.9526, longitude: -75.1652};
  const GRADES = {regular_gas: "regular", midgrade_gas: "midgrade", premium_gas: "premium", diesel: "diesel"};
  function distanceMiles(latitude, longitude) {
    const radians = n => n * Math.PI / 180;
    const a = Math.sin(radians(latitude - CENTER.latitude) / 2) ** 2 + Math.cos(radians(CENTER.latitude)) * Math.cos(radians(latitude)) * Math.sin(radians(longitude - CENTER.longitude) / 2) ** 2;
    return 3958.7613 * 2 * Math.asin(Math.sqrt(Math.min(1, a)));
  }
  function validPrice(value) {return value !== null && value !== "" && Number.isFinite(Number(value)) && Number(value) >= 0.5 && Number(value) <= 15;}
  function validCoordinates(station) {return typeof station.latitude === "number" && typeof station.longitude === "number" && Number.isFinite(station.latitude) && Number.isFinite(station.longitude) && Math.abs(station.latitude) <= 90 && Math.abs(station.longitude) <= 180;}
  function gasbuddy(snapshot) {
    if (!snapshot || !Array.isArray(snapshot.stations) || typeof snapshot.collected_at !== "string") throw new Error("GasBuddy snapshot is missing or invalid.");
    const stations = snapshot.stations.filter(validCoordinates).map(item => {
      const prices = {};
      for (const observation of item.prices || []) {
        const grade = GRADES[observation.fuel_grade];
        if (!grade || !["cash", "credit"].includes(observation.payment_type) || !validPrice(observation.price)) continue;
        prices[grade] ||= {};
        prices[grade][observation.payment_type] = {price: Number(observation.price).toFixed(3), reported_at: observation.reported_at};
      }
      return {id: `gasbuddy-${item.id}`, brand: item.name || "Unspecified", name: item.name || "Unnamed station",
        address: item.address?.line1 || "", city: item.address?.locality || "", state: item.address?.region || "", postal_code: item.address?.postalCode || "",
        latitude: item.latitude, longitude: item.longitude, distance_miles: distanceMiles(item.latitude, item.longitude), prices,
        source_url: item.source_url, collected_at: snapshot.collected_at, membership_required: null, fuel_hours: []};
    });
    return {kind: "gasbuddy", label: "GasBuddy export", center: CENTER, stations, collected_at: snapshot.collected_at,
      stale_after_hours: snapshot.stale_after_hours || 24, payment_types: ["credit", "cash"], csv_url: "gasbuddy/prices.csv",
      scope: `Saved ${snapshot.search_area || "Philadelphia"} search · ${stations.length} exported stations across the search area.`,
      coverage: `${snapshot.scope || "One saved search-result page."} ${snapshot.returned_on_page ?? stations.length} results were returned on this page${snapshot.reported_total != null ? ` out of ${snapshot.reported_total} reported matches` : ""}${snapshot.has_more_pages ? "; more pages are available but are not included" : ""}.`,
      sources: [{name: "GasBuddy JSON export", status: "ok", stations: stations.length, collected_at: snapshot.collected_at}]};
  }
  function retailers(snapshot) {
    if (!snapshot || !Array.isArray(snapshot.stations)) throw new Error("Retailer snapshot is missing or invalid.");
    const stations = snapshot.stations.filter(validCoordinates).map(item => {
      const prices = {};
      for (const [grade, price] of Object.entries(item.prices || {})) if (validPrice(price)) prices[grade] = {published: {price: Number(price).toFixed(3), reported_at: item.price_updated_at}};
      return {...item, prices};
    });
    return {kind: "retailers", label: "Retailer export", center: snapshot.center || CENTER, stations, collected_at: snapshot.generated_at,
      stale_after_hours: 24, payment_types: ["published"], csv_url: "prices.csv",
      scope: `Published prices within ${snapshot.radius_miles ?? 30} miles of Center City${snapshot.state_filter ? ` · ${snapshot.state_filter} only` : ""}.`,
      coverage: snapshot.coverage || "Saved retailer observations.", sources: snapshot.sources || []};
  }
  function observation(station, grade, payment) {return station.prices[grade]?.[payment] || null;}
  function freshness(item, threshold = 24, now = Date.now()) {
    if (!item?.reported_at) return {label: "Report time unknown", stale: false, unknown: true};
    const reported = Date.parse(item.reported_at);
    if (!Number.isFinite(reported)) return {label: "Report time unknown", stale: false, unknown: true};
    const hours = (now - reported) / 3600000;
    if (hours < 0) return {label: "Report time is in the future", stale: false, unknown: true};
    const stale = hours > threshold, age = hours < 1 ? "less than 1h" : `${Math.floor(hours)}h`;
    return {label: `${stale ? "Stale · " : ""}reported ${age} ago`, stale, unknown: false};
  }
  const api = {gasbuddy, retailers, observation, freshness, distanceMiles};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.RouteData = api;
})(typeof window !== "undefined" ? window : globalThis);
