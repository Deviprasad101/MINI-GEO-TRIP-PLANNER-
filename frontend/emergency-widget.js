/**
 * GeoTrip Emergency Widget — hospitals FAB + live GPS nearest-3 map.
 * Auto-skips if #emergencyFab already exists (main_page, packages, booking).
 */
(function () {
    'use strict';

    if (document.getElementById('emergencyFab')) return;

    var CSV_URL = '/tirupati_main_data.csv';
    var OSRM_BASE = 'https://router.project-osrm.org/route/v1/driving';
    var OSRM_TRIP_BASE = 'https://router.project-osrm.org/trip/v1/driving';
    var GEO_OPTS = { enableHighAccuracy: true, timeout: 30000, maximumAge: 0 };

    var EMERGENCY_HOSPITAL_LATLNG_FALLBACK = {
        'SVIMS Hospital': { lat: 13.642478, lng: 79.405348 },
        'Ruia Government Hospital': { lat: 13.644965, lng: 79.405757 },
        'Apollo Hospital Tirupati': { lat: 13.623068, lng: 79.429942 },
        'Sri Chakra Hospital': { lat: 13.6360533, lng: 79.4210585 },
        'Aster Narayanadri Hospital': { lat: 13.62846, lng: 79.463813 },
        'Helios Hospital': { lat: 13.638177, lng: 79.423773 },
        'Venkataramana Hospital': { lat: 13.6353807, lng: 79.4199327 },
        'Suraksha Hospital': { lat: 13.635603, lng: 79.4204007 },
        'Mother Hospital': { lat: 13.6382376, lng: 79.4184239 },
        'Life Line Hospital': { lat: 13.6367942, lng: 79.4214374 },
        'Balaji Hospital': { lat: 13.6366949, lng: 79.4274731 },
        'Sree Ramadevi Hospital': { lat: 13.638003, lng: 79.4171204 },
        'Padmavathi Hospital': { lat: 13.6398667, lng: 79.4162768 },
        'Lotus Hospital': { lat: 13.6372, lng: 79.4215 },
        'Sai Sudha Hospital': { lat: 13.635545, lng: 79.4215715 },
        'Annapurna Hospital': { lat: 13.6349, lng: 79.4202 },
        'Medicover Hospital Tirupati': { lat: 13.6355541, lng: 79.4183153 },
        'Sankalpa Hospital': { lat: 13.6346953, lng: 79.420532 },
        "People's Hospital": { lat: 13.6316368, lng: 79.4231711 },
        'Cure Hospital': { lat: 13.622838, lng: 79.409767 }
    };

    var hospitalStubs = [];
    var hospitalGeocodeCache = {};
    var pkgMap = null;
    var pkgHospitalLayer = null;
    var emergencyAbort = null;

    function ensureMaterialFont() {
        if (document.querySelector('link[href*="Material+Symbols+Outlined"]')) return;
        var l = document.createElement('link');
        l.rel = 'stylesheet';
        l.href = 'https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0';
        document.head.appendChild(l);
    }

    function isAboutPage() {
        return document.body.classList.contains('about-page') ||
            (window.location.pathname || '').indexOf('/about') !== -1;
    }

    function hideEmergencyMap() {
        var mapSec = document.getElementById('gtEmergencyMapSection');
        if (mapSec) mapSec.classList.add('hidden');
    }

    function mountMapSection(node) {
        if (isAboutPage()) {
            node.classList.add('emergency-map-card--about-footer');
        }
        var anchor = document.getElementById('gtEmergencyMapAnchor');
        if (isAboutPage() && anchor) {
            anchor.appendChild(node);
        } else {
            document.body.appendChild(node);
        }
    }

    function relocateMapIfAbout() {
        var anchor = document.getElementById('gtEmergencyMapAnchor');
        var mapSec = document.getElementById('gtEmergencyMapSection');
        if (!anchor || !mapSec || !isAboutPage()) return;
        if (mapSec.parentElement !== anchor) {
            mapSec.classList.add('emergency-map-card--about-footer');
            anchor.appendChild(mapSec);
        }
    }

    function injectDom() {
        if (document.getElementById('emergencyFab')) return;
        if (!document.getElementById('gtEmergencyMapSection')) {
            var mapWrap = document.createElement('div');
            mapWrap.innerHTML =
                '<section class="emergency-map-card hidden" id="gtEmergencyMapSection" aria-label="Emergency hospital map">' +
                '<div class="emergency-map-card__head">' +
                '<span class="emergency-map-card__title">Nearest hospitals</span>' +
                '<button type="button" class="emergency-map-card__close" id="gtEmergencyMapClose" aria-label="Close map">×</button>' +
                '</div>' +
                '<p id="gtEmergencyMapStatus" class="emergency-map-card__status"></p>' +
                '<div class="emergency-map-legend" aria-hidden="true">' +
                '<span><i class="dot dot-you"></i> You</span>' +
                '<span><i class="dot dot-h"></i> Hospital 1–3</span>' +
                '</div>' +
                '<div id="gtEmergencyMap" aria-label="Emergency map"></div>' +
                '<div id="gtEmergencyMapChips" class="emergency-map-hospital-chips" hidden></div>' +
                '</section>';
            mountMapSection(mapWrap.firstChild);
        }
        var wrap = document.createElement('div');
        wrap.innerHTML =
            '<div id="emergencyBackdrop" class="emergency-panel-backdrop" aria-hidden="true"></div>' +
            '<div id="emergencyPanel" class="emergency-panel" role="dialog" aria-modal="true" aria-labelledby="emergencyTitle" hidden aria-hidden="true">' +
            '<div class="emergency-panel__head"><h2 id="emergencyTitle">Emergency - Hospitals</h2>' +
            '<button type="button" class="emergency-panel__close" id="emergencyCloseBtn" aria-label="Close">×</button></div>' +
            '<p class="emergency-panel__lead">Uses your <strong>live GPS location</strong> to show the 3 nearest hospitals with a driving route.</p>' +
            '<div id="emergencyListRoot" class="emergency-list"></div>' +
            '<div class="emergency-panel__foot">' +
            '<button type="button" class="btn-emergency-map" id="emergencyShowMapBtn">Show nearest 3 on map</button>' +
            '<p id="emergencyMapHint" class="emergency-hint" role="status"></p></div></div>' +
            '<button type="button" class="emergency-fab" id="emergencyFab" aria-haspopup="dialog" aria-controls="emergencyPanel" title="Emergency hospitals">' +
            '<span class="material-symbols-outlined" aria-hidden="true">local_hospital</span>Emergency</button>';
        while (wrap.firstChild) document.body.appendChild(wrap.firstChild);
    }

    function parseCSVLine(line) {
        var parts = line.split(',');
        if (parts.length < 6) return null;
        var hName = (parts[6] || '').trim();
        var hCat = (parts[7] || '').trim();
        if (!(parts[0] || '').trim() && hName && hCat.toLowerCase().indexOf('hospital') !== -1) {
            return { name: hName, category: hCat, lat: NaN, lng: NaN, timings: (parts[8] || '24 Hours').trim(), needsGeocode: true };
        }
        var name = (parts[0] || '').trim();
        var category = (parts[1] || '').trim();
        var lat = parseFloat(parts[2]);
        var lng = parseFloat(parts[3]);
        if (!name || isNaN(lat) || isNaN(lng)) return null;
        if ((category || '').toLowerCase().indexOf('hospital') === -1) return null;
        return { name: name, category: category, lat: lat, lng: lng, timings: (parts[5] || '24 Hours').trim() };
    }

    function haversineKm(lat1, lng1, lat2, lng2) {
        var R = 6371, toR = Math.PI / 180;
        var dLat = (lat2 - lat1) * toR, dLng = (lng2 - lng1) * toR;
        var a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * toR) * Math.cos(lat2 * toR) * Math.sin(dLng / 2) ** 2;
        return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
    }

    function geocodeNominatim(query, signal) {
        var url = 'https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + encodeURIComponent(query);
        return fetch(url, { signal: signal, headers: { Accept: 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (arr) {
                if (!arr || !arr[0]) return null;
                return { lat: parseFloat(arr[0].lat), lng: parseFloat(arr[0].lon), label: arr[0].display_name };
            })
            .catch(function () { return null; });
    }

    function geocodeHospital(h, signal) {
        if (hospitalGeocodeCache[h.name]) return Promise.resolve(hospitalGeocodeCache[h.name]);
        if (!isNaN(h.lat) && !isNaN(h.lng)) {
            var g = { name: h.name, lat: h.lat, lng: h.lng, timings: h.timings };
            hospitalGeocodeCache[h.name] = g;
            return Promise.resolve(g);
        }
        var fb = EMERGENCY_HOSPITAL_LATLNG_FALLBACK[h.name];
        if (fb) {
            var f = { name: h.name, lat: fb.lat, lng: fb.lng, timings: h.timings };
            hospitalGeocodeCache[h.name] = f;
            return Promise.resolve(f);
        }
        return geocodeNominatim(h.name + ', Tirupati, India', signal).then(function (geo) {
            if (!geo) return null;
            var r = { name: h.name, lat: geo.lat, lng: geo.lng, timings: h.timings };
            hospitalGeocodeCache[h.name] = r;
            return r;
        });
    }

    function geocodeAllHospitals(signal) {
        var stubs = hospitalStubs.length ? hospitalStubs : Object.keys(EMERGENCY_HOSPITAL_LATLNG_FALLBACK).map(function (n) {
            return { name: n, timings: '24 Hours' };
        });
        return Promise.all(stubs.map(function (h) { return geocodeHospital(h, signal); })).then(function (list) {
            return list.filter(Boolean);
        });
    }

    function fetchOsrmRoute(waypoints, signal) {
        if (!waypoints || waypoints.length < 2) return Promise.resolve(null);
        var path = waypoints.map(function (w) { return w[1] + ',' + w[0]; }).join(';');
        return fetch(OSRM_BASE + '/' + path + '?overview=full&geometries=geojson', { signal: signal })
            .then(function (r) { return r.json(); })
            .then(function (j) {
                if (!j || j.code !== 'Ok' || !j.routes || !j.routes[0]) return null;
                return j.routes[0].geometry.coordinates.map(function (c) { return [c[1], c[0]]; });
            })
            .catch(function () { return null; });
    }

    function fetchOsrmTripOrder(waypoints, signal) {
        if (!waypoints || waypoints.length < 3) return Promise.resolve(null);
        var path = waypoints.map(function (w) { return w[1] + ',' + w[0]; }).join(';');
        return fetch(OSRM_TRIP_BASE + '/' + path + '?source=first&destination=last&roundtrip=false&overview=false', { signal: signal })
            .then(function (r) { return r.json(); })
            .then(function (j) {
                if (!j || j.code !== 'Ok' || !Array.isArray(j.waypoints)) return null;
                var pairs = j.waypoints.map(function (wi, i) { return { inputIdx: i, visitIdx: wi.waypoint_index }; });
                pairs.sort(function (a, b) { return a.visitIdx - b.visitIdx; });
                return pairs.map(function (x) { return x.inputIdx; });
            })
            .catch(function () { return null; });
    }

    function ensureMap() {
        if (typeof L === 'undefined') return false;
        if (pkgMap) return true;
        var el = document.getElementById('gtEmergencyMap');
        if (!el) return false;
        pkgMap = L.map(el, { scrollWheelZoom: true }).setView([13.6285, 79.4192], 12);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap', maxZoom: 19
        }).addTo(pkgMap);
        pkgHospitalLayer = L.layerGroup().addTo(pkgMap);
        return true;
    }

    function renderEmergencyHospitalList() {
        var root = document.getElementById('emergencyListRoot');
        if (!root) return;
        var stubs = hospitalStubs.length ? hospitalStubs : Object.keys(EMERGENCY_HOSPITAL_LATLNG_FALLBACK).map(function (n) {
            return { name: n, timings: '24 Hours' };
        });
        root.innerHTML = '<ul>' + stubs.slice(0, 12).map(function (h) {
            return '<li><strong>' + h.name + '</strong><span class="meta">' + (h.timings || '24 Hours') + '</span></li>';
        }).join('') + '</ul>';
    }

    function openEmergencyPanel() {
        var panel = document.getElementById('emergencyPanel');
        var bd = document.getElementById('emergencyBackdrop');
        if (!panel || !bd) return;
        panel.hidden = false;
        panel.setAttribute('aria-hidden', 'false');
        panel.classList.add('is-open');
        bd.classList.add('is-open');
        bd.setAttribute('aria-hidden', 'false');
        renderEmergencyHospitalList();
        var hint = document.getElementById('emergencyMapHint');
        if (hint) hint.textContent = 'Allow location access when prompted — we use your exact live GPS.';
    }

    function closeEmergencyPanel() {
        var panel = document.getElementById('emergencyPanel');
        var bd = document.getElementById('emergencyBackdrop');
        if (panel) { panel.classList.remove('is-open'); panel.hidden = true; panel.setAttribute('aria-hidden', 'true'); }
        if (bd) { bd.classList.remove('is-open'); bd.setAttribute('aria-hidden', 'true'); }
    }

    function requestLiveGps(onOk, onErr) {
        if (!navigator.geolocation) {
            onErr('Geolocation is not supported on this device.');
            return;
        }
        navigator.geolocation.getCurrentPosition(
            function (pos) {
                onOk(pos.coords.latitude, pos.coords.longitude, pos.coords.accuracy);
            },
            function (err) {
                var msg = 'Could not get your live location.';
                if (err.code === 1) msg = 'Location permission denied. Enable GPS/location for this site in browser settings.';
                else if (err.code === 3) msg = 'Location request timed out. Try again outdoors or check GPS.';
                onErr(msg);
            },
            GEO_OPTS
        );
    }

    window.GeoTripEmergency = window.GeoTripEmergency || {};
    window.GeoTripEmergency.requestLiveGps = requestLiveGps;
    window.GeoTripEmergency.GEO_OPTS = GEO_OPTS;

    function showEmergencyNearestHospitals() {
        var btn = document.getElementById('emergencyShowMapBtn');
        var hint = document.getElementById('emergencyMapHint');
        var status = document.getElementById('gtEmergencyMapStatus');
        if (emergencyAbort) emergencyAbort.abort();
        emergencyAbort = new AbortController();
        var signal = emergencyAbort.signal;
        if (btn) btn.disabled = true;
        if (hint) hint.textContent = 'Requesting your live GPS location…';

        function fail(msg) {
            if (hint) hint.textContent = msg || 'Could not complete.';
            if (btn) btn.disabled = false;
        }

        function drawFromStart(startLat, startLng, accM) {
            var label = 'Your live location' + (accM ? ' (±' + Math.round(accM) + ' m)' : '');
            var compact = isAboutPage();
            var startSize = compact ? 28 : 34;
            var hospSize = compact ? 24 : 30;
            geocodeAllHospitals(signal).then(function (geoHospitals) {
                if (signal.aborted) return;
                if (btn) btn.disabled = false;
                if (!geoHospitals.length) { fail('Could not load hospitals.'); return; }
                geoHospitals.sort(function (a, b) {
                    return haversineKm(startLat, startLng, a.lat, a.lng) - haversineKm(startLat, startLng, b.lat, b.lng);
                });
                var top3 = geoHospitals.slice(0, 3);
                var mapSec = document.getElementById('gtEmergencyMapSection');
                var chips = document.getElementById('gtEmergencyMapChips');
                if (mapSec) mapSec.classList.remove('hidden');
                relocateMapIfAbout();
                if (chips) {
                    chips.hidden = true;
                    chips.innerHTML = '';
                }
                if (!ensureMap()) { fail('Map failed to load.'); return; }
                pkgHospitalLayer.clearLayers();
                L.marker([startLat, startLng], {
                    icon: L.divIcon({
                        className: 'pkg-em-start',
                        html: '<div style="width:' + startSize + 'px;height:' + startSize + 'px;border-radius:50%;background:#0f172a;color:#fff;display:flex;align-items:center;justify-content:center;font-size:' + (compact ? '12px' : '14px') + ';box-shadow:0 2px 6px rgba(0,0,0,0.25);">⌂</div>',
                        iconSize: [startSize, startSize], iconAnchor: [startSize / 2, startSize / 2]
                    }),
                    zIndexOffset: 900
                }).bindPopup('<b>' + label + '</b>').addTo(pkgHospitalLayer);
                top3.forEach(function (h, idx) {
                    var n = idx + 1;
                    var dist = haversineKm(startLat, startLng, h.lat, h.lng).toFixed(1);
                    L.marker([h.lat, h.lng], {
                        icon: L.divIcon({
                            className: 'pkg-hosp-icon',
                            html: '<div style="min-width:' + hospSize + 'px;height:' + hospSize + 'px;border-radius:50%;background:#735c00;color:#fff;font-weight:800;font-size:' + (compact ? '11px' : '12px') + ';display:flex;align-items:center;justify-content:center;border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,0.2);">' + n + '</div>',
                            iconSize: [hospSize, hospSize], iconAnchor: [hospSize / 2, hospSize / 2]
                        })
                    }).bindPopup('<b>' + n + '. ' + h.name + '</b><br>' + dist + ' km away').addTo(pkgHospitalLayer);
                });
                var seqPts = [[startLat, startLng]].concat(top3.map(function (h) { return [h.lat, h.lng]; }));
                fetchOsrmTripOrder(seqPts, signal).then(function (orderIdx) {
                    var orderedPts = orderIdx && orderIdx.length === seqPts.length
                        ? orderIdx.map(function (ix) { return seqPts[ix]; }) : seqPts;
                    fetchOsrmRoute(orderedPts, signal).then(function (coords) {
                        if (signal.aborted) return;
                        if (coords && coords.length >= 2) {
                            L.polyline(coords, { color: '#735c00', weight: compact ? 4 : 5, opacity: 0.92 }).addTo(pkgHospitalLayer);
                        } else {
                            L.polyline(orderedPts, { color: '#ca8a04', weight: compact ? 2 : 3, dashArray: '6 5' }).addTo(pkgHospitalLayer);
                        }
                        pkgMap.fitBounds(orderedPts, { padding: compact ? [28, 28] : [40, 40], maxZoom: compact ? 15 : 14 });
                        setTimeout(function () { pkgMap.invalidateSize(); }, compact ? 280 : 200);
                        if (hint) hint.textContent = 'Showing 3 nearest hospitals from your live location.';
                        if (status) status.textContent = 'Emergency: live GPS + 3 nearest hospitals.';
                        closeEmergencyPanel();
                        if (mapSec) mapSec.scrollIntoView({ behavior: 'smooth', block: compact ? 'nearest' : 'center' });
                    });
                });
            }).catch(function () { fail('Network error.'); });
        }

        requestLiveGps(
            function (lat, lng, acc) { drawFromStart(lat, lng, acc); },
            function (msg) { fail(msg); }
        );
    }

    function loadCsv() {
        return fetch(CSV_URL).then(function (res) {
            if (!res.ok) throw new Error('csv');
            return res.text();
        }).then(function (text) {
            hospitalStubs = [];
            text.trim().split(/\r?\n/).slice(1).forEach(function (line) {
                var row = parseCSVLine(line);
                if (row) hospitalStubs.push(row);
            });
        }).catch(function () { hospitalStubs = []; });
    }

    function init() {
        ensureMaterialFont();
        injectDom();
        relocateMapIfAbout();
        loadCsv();
        document.getElementById('emergencyFab').addEventListener('click', openEmergencyPanel);
        document.getElementById('emergencyCloseBtn').addEventListener('click', closeEmergencyPanel);
        document.getElementById('emergencyBackdrop').addEventListener('click', closeEmergencyPanel);
        document.getElementById('emergencyShowMapBtn').addEventListener('click', showEmergencyNearestHospitals);
        var mapClose = document.getElementById('gtEmergencyMapClose');
        if (mapClose) mapClose.addEventListener('click', hideEmergencyMap);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
