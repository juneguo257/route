"use strict";
const embeddedRetailers = JSON.parse(document.getElementById("route-data").textContent);
const byId = id => document.getElementById(id);
const timestamp = value => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString("en-US", {timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short"}) : "unknown";
const money = price => `$${Number(price).toFixed(3)}`;
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
const safeLink = value => {try {const url = new URL(value);return url.protocol === "https:" ? escapeHTML(url.href) : "#";} catch {return "#";}};
const priceMarkup = price => {const [whole, decimal] = Number(price).toFixed(3).split(".");return `$${whole}.${decimal.slice(0, 2)}<sup>${decimal[2]}</sup>`;};
const paymentLabel = value => ({credit: "Credit", cash: "Cash", published: "Published · payment unspecified"}[value] || value);
const datasets = {}, errors = {};
let active = null, map = null, layer = null, markers = new Map(), selected = null, visible = [];
const selectedObservation = station => RouteData.observation(station, byId("grade").value, byId("payment").value);
if (typeof L !== "undefined") {
  map = L.map("map").setView([39.9526, -75.1652], 10);
  let tileFailures = 0;
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).on("tileerror", () => {if (++tileFailures === 3) showMapError("Map tiles could not load. Station prices and links are still available in the list.");}).addTo(map);
  layer = L.layerGroup().addTo(map);
  L.circleMarker([39.9526, -75.1652], {radius: 5, color: "#385b45", weight: 2, fillColor: "#fff", fillOpacity: 1}).bindTooltip("Center City · distance reference").addTo(map);
} else showMapError("The map library could not load. Connect to the internet and reload to display OpenStreetMap.");

function showMapError(message) {byId("map-error").textContent = message;byId("map-error").hidden = false;}
function setOptions(id, values, allLabel) {
  const select = byId(id); select.replaceChildren();
  if (allLabel) {const option = document.createElement("option");option.value = "all";option.textContent = allLabel;select.append(option);}
  for (const value of values) {const option = document.createElement("option");option.value = value;option.textContent = id === "payment" ? paymentLabel(value) : value;select.append(option);}
}
function fitResults() {
  if (map && visible.length) {map.closePopup();map.fitBounds(L.latLngBounds(visible.map(s => [s.latitude, s.longitude])).extend([39.9526, -75.1652]), {padding: [55, 65], maxZoom: 12});}
}
function selectStation(id, fly = true) {
  selected = id;
  for (const button of document.querySelectorAll(".station")) button.setAttribute("aria-pressed", String(button.dataset.id === id));
  for (const [markerId, marker] of markers) marker.getElement()?.querySelector(".price-pill")?.classList.toggle("selected", markerId === id);
  const station = visible.find(s => s.id === id), marker = markers.get(id);
  if (marker && fly) {map.setView([station.latitude, station.longitude], Math.max(map.getZoom(), 12));marker.openPopup();}
}
function popup(station) {
  const osmURL = `https://www.openstreetmap.org/?mlat=${station.latitude}&mlon=${station.longitude}#map=17/${station.latitude}/${station.longitude}`;
  const grades = Object.entries(station.prices).flatMap(([grade, payments]) => Object.entries(payments).map(([payment, observation]) => {
    const fresh = RouteData.freshness(observation, active.stale_after_hours);
    return `<div class="popup-observation"><span>${escapeHTML(grade)} · ${escapeHTML(paymentLabel(payment))}: <strong>${money(observation.price)}</strong></span><br><span class="${fresh.stale ? "stale-text" : ""}">${escapeHTML(fresh.label)}${observation.reported_at ? ` · ${timestamp(observation.reported_at)}` : ""}</span></div>`;
  })).join("");
  const hours = station.fuel_hours?.length ? `<details><summary>Fuel station hours</summary>${station.fuel_hours.map(escapeHTML).join("<br>")}</details>` : "";
  const membership = station.membership_required === true ? "Membership required" : "Access requirements not supplied by source";
  return `<h2>${escapeHTML(station.name)}</h2><p>${escapeHTML(station.address)}<br>${escapeHTML(station.city)}, ${escapeHTML(station.state)} ${escapeHTML(station.postal_code)}</p><div class="popup-prices">${grades}</div><p>${membership} · USD / US gallon<br>${station.distance_miles.toFixed(1)} miles from Center City (straight line)</p><p>Collected ${timestamp(station.collected_at)}<br>Pump prices may differ.</p>${hours}<p><a target="_blank" rel="noopener" href="${safeLink(station.source_url)}">${active.kind === "gasbuddy" ? "GasBuddy" : "Retailer"} source ↗</a> · <a target="_blank" rel="noopener" href="${osmURL}">OpenStreetMap ↗</a></p>`;
}
function render() {
  const brand = byId("brand").value, state = byId("state").value;
  visible = (active?.stations || []).filter(s => selectedObservation(s) && (brand === "all" || s.brand === brand) && (state === "all" || s.state === state));
  visible.sort((a,b) => byId("sort").value === "distance" ? a.distance_miles - b.distance_miles : Number(selectedObservation(a).price) - Number(selectedObservation(b).price));
  byId("count").textContent = `${visible.length} ${visible.length === 1 ? "station" : "stations"}`;
  byId("results").replaceChildren();layer?.clearLayers();markers = new Map();
  byId("map-basis").textContent = active ? `${paymentLabel(byId("payment").value)} prices · ${active.label}` : "Snapshot unavailable";
  const cheapest = Math.min(...visible.map(s => Number(selectedObservation(s).price)));
  for (const station of visible) {
    const observation = selectedObservation(station), fresh = RouteData.freshness(observation, active.stale_after_hours);
    const button = document.createElement("button");button.type = "button";button.className = "station";button.dataset.id = station.id;
    button.setAttribute("aria-pressed", String(station.id === selected));
    button.setAttribute("aria-label", `${station.name}, ${money(observation.price)} per gallon, ${paymentLabel(byId("payment").value)}, ${station.city}, ${station.state}${station.membership_required ? ", membership required" : ""}`);
    button.innerHTML = `<span class="station-top"><span class="station-name">${escapeHTML(station.name)}</span><span class="station-price">${priceMarkup(observation.price)}</span></span><span class="station-address">${escapeHTML(station.address)}<br>${escapeHTML(station.city)}, ${escapeHTML(station.state)}</span><span class="station-meta">${station.distance_miles.toFixed(1)} mi from Center City${station.membership_required ? " · Membership required" : ""}<br><span class="${fresh.stale ? "stale-text" : ""}">${escapeHTML(fresh.label)}</span> · Collected ${timestamp(station.collected_at)}</span>`;
    button.addEventListener("click", () => selectStation(station.id));byId("results").append(button);
    if (map) {
      const marker = L.marker([station.latitude, station.longitude], {
        icon: L.divIcon({className: "price-pin", html: `<span class="price-pill${Number(observation.price) === cheapest ? " best" : ""}${station.id === selected ? " selected" : ""}">${money(observation.price)}</span>`, iconSize: [70, 34], iconAnchor: [35, 17]}),
        title: `${station.name} · ${money(observation.price)} · ${paymentLabel(byId("payment").value)}`, alt: `${station.name} fuel price`,
      }).bindPopup(popup(station), {maxWidth: 280, maxHeight: 220}).on("click", () => selectStation(station.id, false)).addTo(layer);
      markers.set(station.id, marker);
    }
  }
  if (!visible.length) {
    const empty = document.createElement("p");empty.className="empty";
    empty.textContent = !active ? "This snapshot is unavailable. Choose another data source or export a new snapshot." : "No published prices match these filters. Try another grade, payment basis, or brand.";
    byId("results").append(empty);
  }
}
function chooseSource() {
  const key = byId("source").value; active = datasets[key] || null; selected = null;
  for (const id of ["grade", "payment", "brand", "state", "sort"]) byId(id).disabled = !active;
  setOptions("payment", active?.payment_types || []);
  setOptions("brand", [...new Set((active?.stations || []).map(s => s.brand))].sort(), "All brands");
  setOptions("state", [...new Set((active?.stations || []).map(s => s.state))].sort(), "All states");
  byId("scope").textContent = active?.scope || "Saved prices unavailable.";
  byId("coverage").textContent = active?.coverage || errors[key] || "No exported data found.";
  byId("snapshot-status").textContent = active ? `Saved snapshot · ${active.kind === "retailers" ? "generated" : "collected"} ${timestamp(active.collected_at)}` : "Snapshot could not be loaded";
  byId("snapshot-status").classList.toggle("stale-text", !active);
  byId("price-notes").textContent = !active ? "" : active.kind === "gasbuddy" ? `Cash and credit observations are shown separately. Reports older than ${active.stale_after_hours} hours are marked stale. Access requirements are not supplied in this export. Pump prices may differ.` : "These retailer stations require membership to buy fuel. Payment basis and price update times are not supplied. Pump prices may differ.";
  byId("generated").textContent = "This page reads a saved export when opened. Reload after a new export to see updated data. No background price polling.";
  byId("sources").replaceChildren();
  for (const source of active?.sources || []) {
    const row = document.createElement("li");row.textContent = source.status === "ok" ? `${source.name}: ${source.stations} stations · collected ${timestamp(source.collected_at)}` : `${source.name}: unavailable (${source.error})`;byId("sources").append(row);
  }
  const download = document.querySelector(".download");download.hidden = !active;if (active) download.href = active.csv_url;
  render();fitResults();
}
for (const id of ["grade", "payment", "brand", "state", "sort"]) byId(id).addEventListener("change", () => {render();fitResults();});
byId("source").addEventListener("change", chooseSource);byId("fit").addEventListener("click", fitResults);
async function start() {
  byId("count").textContent = "Loading saved prices…";
  for (const id of ["source", "grade", "payment", "brand", "state", "sort"]) byId(id).disabled = true;
  document.querySelector(".download").hidden = true;
  try {datasets.retailers = RouteData.retailers(embeddedRetailers);} catch (error) {errors.retailers = error.message;}
  try {
    // Same-origin file only. Never contact GasBuddy from the frontend.
    const response = await fetch("gasbuddy/stations.json", {cache: "no-store"});
    if (!response.ok) throw new Error(`Saved GasBuddy export returned HTTP ${response.status}.`);
    datasets.gasbuddy = RouteData.gasbuddy(await response.json());
  } catch (error) {errors.gasbuddy = error.message;}
  byId("source").disabled = false;chooseSource();
}
start();
