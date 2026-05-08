/**
 * GeoTrip Planner — Recommendation Engine v2 (Pure JS, No Backend)
 *
 * Features:
 *   1. Content-Based Filtering — TF-IDF Cosine Similarity on tags
 *   2. Proximity + Route Optimisation — Haversine distance + nearest-neighbour sort
 *   3. Time-Aware Day Plan — uses Trip Details form (start time, end time) to
 *      only suggest places that are OPEN during the user's outing window
 *   4. Category / Theme Filtering
 */

class GeoTripRecommender {
    constructor() {
        this.places = [];
        this.tfidfMatrix = [];
        this.loaded = false;
    }

    // ─── Step 1: Load & Parse CSV ──────────────────────────────────────────────
    async loadCSV(csvUrl) {
        try {
            const res = await fetch(csvUrl);
            const text = await res.text();
            const lines = text.trim().split(/\r?\n/);

            this.places = [];
            for (let i = 1; i < lines.length; i++) {
                const cols = this._splitCSVLine(lines[i]);
                const name = (cols[0] || '').trim();
                const cat  = (cols[1] || '').trim();
                if (!name) continue;

                const lat = parseFloat(cols[2]);
                const lng = parseFloat(cols[3]);
                if (isNaN(lat) || isNaN(lng)) continue;

                const place = {
                    name,
                    category:    cat,
                    lat,
                    lng,
                    description: (cols[4] || '').trim(),
                    timings:     (cols[5] || '').trim(),
                    tags:        (cols[6] || '').trim().toLowerCase(),
                    rating:      parseFloat(cols[7]) || 4.0,
                };
                this.places.push(place);
            }

            this._buildTFIDF();
            this.loaded = true;
            console.log(`[Recommender v2] Loaded ${this.places.length} places.`);
        } catch (e) {
            console.error('[Recommender] CSV load failed:', e);
        }
    }

    _splitCSVLine(line) {
        const result = [];
        let inQuotes = false, current = '';
        for (const ch of line) {
            if (ch === '"') { inQuotes = !inQuotes; continue; }
            if (ch === ',' && !inQuotes) { result.push(current); current = ''; continue; }
            current += ch;
        }
        result.push(current);
        return result;
    }

    // ─── Step 2: TF-IDF ───────────────────────────────────────────────────────
    _tokenize(text) {
        return (text || '').toLowerCase().replace(/[^a-z0-9\s]/g, '').split(/\s+/).filter(Boolean);
    }

    _buildTFIDF() {
        const docs = this.places.map(p =>
            `${p.tags} ${p.category} ${this._tokenize(p.description).slice(0, 10).join(' ')}`
        );
        const vocabSet = new Set();
        docs.forEach(doc => this._tokenize(doc).forEach(t => vocabSet.add(t)));
        const vocab = Array.from(vocabSet);

        const tfMatrix = docs.map(doc => {
            const tokens = this._tokenize(doc);
            const tf = {};
            tokens.forEach(t => { tf[t] = (tf[t] || 0) + 1; });
            const total = tokens.length || 1;
            return vocab.map(v => (tf[v] || 0) / total);
        });

        const N = docs.length;
        const idf = vocab.map((v, vi) => {
            const df = tfMatrix.filter(row => row[vi] > 0).length;
            return Math.log((N + 1) / (df + 1)) + 1;
        });

        this.tfidfMatrix = tfMatrix.map(tfRow => {
            const tfidf = tfRow.map((tf, vi) => tf * idf[vi]);
            const norm = Math.sqrt(tfidf.reduce((s, v) => s + v * v, 0)) || 1;
            return tfidf.map(v => v / norm);
        });
    }

    _cosineSimilarity(vecA, vecB) {
        return vecA.reduce((sum, a, i) => sum + a * vecB[i], 0);
    }

    // ─── Step 3: Content-Based Recommendations ────────────────────────────────
    getContentRecommendations(placeName, topN = 5) {
        if (!this.loaded) return [];
        let idx = this.places.findIndex(p => p.name.toLowerCase() === placeName.toLowerCase());
        if (idx === -1) {
            // Partial name match
            idx = this.places.findIndex(p =>
                p.name.toLowerCase().includes(placeName.toLowerCase()) ||
                placeName.toLowerCase().includes(p.name.toLowerCase())
            );
        }
        if (idx === -1) {
            // Fallback: category match
            const byTag = this.places.filter(p =>
                p.category.toLowerCase().includes(placeName.toLowerCase()) ||
                p.tags.includes(placeName.toLowerCase())
            );
            return byTag.sort((a, b) => b.rating - a.rating).slice(0, topN);
        }
        return this.places
            .map((p, i) => ({ place: p, score: this._cosineSimilarity(this.tfidfMatrix[idx], this.tfidfMatrix[i]) }))
            .filter((_, i) => i !== idx)
            .sort((a, b) => b.score - a.score || b.place.rating - a.place.rating)
            .slice(0, topN)
            .map(s => s.place);
    }

    // ─── Step 4: Proximity + Route Optimisation ───────────────────────────────
    /**
     * Returns topN nearest places to (lat, lng), sorted by nearest-neighbour route.
     * @param {string|null} filterCategory  – if set, only return places in this category/tag
     */
    getNearbyRecommendations(lat, lng, topN = 6, filterCategory = null) {
        if (!this.loaded) return [];

        let candidates = this.places.filter(p => {
            // Always exclude hospitals from regular nearby
            if (p.category.toLowerCase().includes('hospital')) return false;
            // Category/theme filter
            if (filterCategory && filterCategory !== 'all') {
                const fc = filterCategory.toLowerCase();
                return p.category.toLowerCase().includes(fc) || p.tags.includes(fc);
            }
            return true;
        });

        // Compute distance from origin and take wide candidate pool
        candidates = candidates
            .map(p => ({ ...p, distanceKm: this._haversine(lat, lng, p.lat, p.lng) }))
            .sort((a, b) => a.distanceKm - b.distanceKm)
            .slice(0, topN * 4); // wider pool so NN has options

        // Nearest-neighbour route optimisation
        const route = [];
        let curLat = lat, curLng = lng;
        while (route.length < topN && candidates.length > 0) {
            let bestIdx = 0, bestDist = Infinity;
            candidates.forEach((p, i) => {
                const d = this._haversine(curLat, curLng, p.lat, p.lng);
                if (d < bestDist) { bestDist = d; bestIdx = i; }
            });
            const next = candidates.splice(bestIdx, 1)[0];
            next.distanceKm = this._haversine(lat, lng, next.lat, next.lng);
            route.push(next);
            curLat = next.lat;
            curLng = next.lng;
        }
        return route;
    }

    _haversine(lat1, lng1, lat2, lng2) {
        const R = 6371, toR = Math.PI / 180;
        const dLat = (lat2 - lat1) * toR, dLng = (lng2 - lng1) * toR;
        const a = Math.sin(dLat / 2) ** 2 +
            Math.cos(lat1 * toR) * Math.cos(lat2 * toR) * Math.sin(dLng / 2) ** 2;
        return R * 2 * Math.asin(Math.min(1, Math.sqrt(a)));
    }

    // ─── Step 5: Category Filter ──────────────────────────────────────────────
    getCategoryRecommendations(category, topN = 5) {
        if (!this.loaded) return [];
        return this.places
            .filter(p => p.category.toLowerCase().includes(category.toLowerCase()) ||
                         p.tags.includes(category.toLowerCase()))
            .sort((a, b) => b.rating - a.rating)
            .slice(0, topN);
    }

    // ─── Step 6: Time-Aware Day Itinerary ─────────────────────────────────────
    /**
     * Reads trip start/end from Trip Details form fields.
     * Divides the outing into 3 equal slots (morning / afternoon / evening).
     * Only suggests places that have open timings overlapping each slot.
     * Falls back to rating-sorted if no timing data matches.
     */
    getDayItinerary(theme = null, startLat = 13.6288, startLng = 79.4192) {
        if (!this.loaded) return { morning: [], afternoon: [], evening: [] };

        // Read form values
        const startStr = (document.getElementById('mainOutingStart')?.value) || '09:00';
        const endStr   = (document.getElementById('mainOutingEnd')?.value)   || '21:00';
        const startMin = this._toMinutes(startStr);
        const endMin   = this._toMinutes(endStr);
        const totalDur = Math.max(endMin - startMin, 120);

        // Divide into 3 roughly equal slots
        const s1 = startMin;
        const s2 = startMin + Math.round(totalDur / 3);
        const s3 = startMin + Math.round((totalDur * 2) / 3);
        const slots = [
            { key: 'morning',   start: s1,  end: s2   },
            { key: 'afternoon', start: s2,  end: s3   },
            { key: 'evening',   start: s3,  end: endMin },
        ];

        const themeVal = (theme === 'all' || !theme) ? null : theme.toLowerCase();
        const used = new Set();
        let curLat = startLat;
        let curLng = startLng;

        const pickForSlot = (slotStart, slotEnd) => {
            // Filter places open during this slot
            let pool = this.places.filter(p => {
                if (used.has(p.name)) return false;
                if (p.category.toLowerCase().includes('hospital')) return false;
                if (themeVal && !p.category.toLowerCase().includes(themeVal) && !p.tags.includes(themeVal)) return false;
                return this._isOpenDuring(p.timings, slotStart, slotEnd);
            });

            if (pool.length < 2 && themeVal) {
                pool = this.places.filter(p => {
                    if (used.has(p.name)) return false;
                    if (p.category.toLowerCase().includes('hospital')) return false;
                    return this._isOpenDuring(p.timings, slotStart, slotEnd);
                });
            }

            if (!pool.length) {
                pool = this.places.filter(p => {
                    if (used.has(p.name)) return false;
                    if (p.category.toLowerCase().includes('hospital')) return false;
                    if (themeVal && !p.category.toLowerCase().includes(themeVal) && !p.tags.includes(themeVal)) return false;
                    return true;
                });
            }

            // Sort by a mix of distance from current point and rating
            pool.sort((a, b) => {
                const distA = this._haversine(curLat, curLng, a.lat, a.lng);
                const distB = this._haversine(curLat, curLng, b.lat, b.lng);
                // Give distance high priority to ensure logical route
                return distA - distB || b.rating - a.rating;
            });

            const picks = pool.slice(0, 3);
            picks.forEach(p => {
                used.add(p.name);
                // Attach distance info for the UI
                p.distanceKm = this._haversine(curLat, curLng, p.lat, p.lng);
            });
            
            // Update current location to the last place picked in this slot
            if (picks.length > 0) {
                const last = picks[picks.length - 1];
                curLat = last.lat;
                curLng = last.lng;
            }
            
            return picks;
        };

        return {
            morning:   pickForSlot(slots[0].start, slots[0].end),
            afternoon: pickForSlot(slots[1].start, slots[1].end),
            evening:   pickForSlot(slots[2].start, slots[2].end),
        };
    }

    /** "09:30" → 570 minutes */
    _toMinutes(timeStr) {
        const parts = (timeStr || '09:00').split(':');
        return parseInt(parts[0], 10) * 60 + (parseInt(parts[1], 10) || 0);
    }

    /**
     * Check if a place's timings string covers any overlap with [slotStart, slotEnd].
     * Timings format examples: "09:00-17:00", "05:00-12:00 | 14:00-22:00", "24 Hours"
     */
    _isOpenDuring(timings, slotStart, slotEnd) {
        if (!timings) return true; // unknown → assume open
        const t = timings.toLowerCase();
        if (t.includes('24') || t.includes('always') || t.includes('open')) return true;

        const segments = t.split('|').map(s => s.trim());
        for (const seg of segments) {
            const m = seg.match(/(\d{1,2}):?(\d{2})\s*[-–]\s*(\d{1,2}):?(\d{2})/);
            if (!m) continue;
            const openMin  = parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
            const closeMin = parseInt(m[3], 10) * 60 + parseInt(m[4], 10);
            // Overlap check
            if (slotStart < closeMin && slotEnd > openMin) return true;
        }
        return false;
    }
}

// Export as global singleton
window.GeoTripRecommender = GeoTripRecommender;
window.recommender = new GeoTripRecommender();
