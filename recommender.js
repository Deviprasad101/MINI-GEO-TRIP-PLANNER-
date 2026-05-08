/**
 * GeoTrip Planner — Recommendation Engine (Pure JS, No Backend)
 * Implements:
 *   1. Content-Based Filtering via TF-IDF Cosine Similarity on tags
 *   2. Proximity-Based Recommendations using Haversine Distance
 *   3. Category-Based Filtering
 */

class GeoTripRecommender {
    constructor() {
        this.places = [];
        this.tfidfMatrix = [];
        this.loaded = false;
    }

    // ─── Step 1: Load & Parse CSV ─────────────────────────────────────────────
    async loadCSV(csvUrl) {
        try {
            const res = await fetch(csvUrl);
            const text = await res.text();
            const lines = text.trim().split(/\r?\n/);
            const headers = lines[0].split(',').map(h => h.trim().toLowerCase());

            this.places = [];
            for (let i = 1; i < lines.length; i++) {
                const cols = this._splitCSVLine(lines[i]);
                if (!cols[0] || !cols[0].trim()) continue; // skip hospital rows
                const place = {
                    name:        (cols[0] || '').trim(),
                    category:    (cols[1] || '').trim(),
                    lat:         parseFloat(cols[2]) || 0,
                    lng:         parseFloat(cols[3]) || 0,
                    description: (cols[4] || '').trim(),
                    timings:     (cols[5] || '').trim(),
                    tags:        (cols[6] || '').trim(),
                    rating:      parseFloat(cols[7]) || 4.0,
                };
                if (place.name && !isNaN(place.lat) && !isNaN(place.lng)) {
                    this.places.push(place);
                }
            }

            // Build TF-IDF matrix for content similarity
            this._buildTFIDF();
            this.loaded = true;
            console.log(`[Recommender] Loaded ${this.places.length} places.`);
        } catch (e) {
            console.error('[Recommender] CSV load failed:', e);
        }
    }

    _splitCSVLine(line) {
        const result = [];
        let inQuotes = false, current = '';
        for (let ch of line) {
            if (ch === '"') { inQuotes = !inQuotes; continue; }
            if (ch === ',' && !inQuotes) { result.push(current); current = ''; continue; }
            current += ch;
        }
        result.push(current);
        return result;
    }

    // ─── Step 2: TF-IDF Implementation ────────────────────────────────────────
    _tokenize(text) {
        return (text || '').toLowerCase().replace(/[^a-z0-9\s]/g, '').split(/\s+/).filter(Boolean);
    }

    _buildTFIDF() {
        const docs = this.places.map(p => {
            // Combine tags + category + description keywords for richer vectors
            const descWords = this._tokenize(p.description).slice(0, 10).join(' ');
            return `${p.tags} ${p.category} ${descWords}`;
        });

        // Build vocabulary
        const vocabSet = new Set();
        docs.forEach(doc => this._tokenize(doc).forEach(t => vocabSet.add(t)));
        const vocab = Array.from(vocabSet);

        // Compute TF for each document
        const tfMatrix = docs.map(doc => {
            const tokens = this._tokenize(doc);
            const tf = {};
            tokens.forEach(t => { tf[t] = (tf[t] || 0) + 1; });
            const total = tokens.length || 1;
            vocab.forEach(v => { tf[v] = (tf[v] || 0) / total; });
            return vocab.map(v => tf[v] || 0);
        });

        // Compute IDF
        const N = docs.length;
        const idf = vocab.map((v, vi) => {
            const df = tfMatrix.filter(row => row[vi] > 0).length;
            return Math.log((N + 1) / (df + 1)) + 1;
        });

        // Compute TF-IDF matrix (normalized)
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
        const idx = this.places.findIndex(p => p.name.toLowerCase() === placeName.toLowerCase());
        if (idx === -1) {
            // Fallback: match by category
            const cat = this.places.find(p => p.category.toLowerCase().includes(placeName.toLowerCase()));
            if (cat) return this.getCategoryRecommendations(cat.category, topN);
            return [];
        }

        const scores = this.places.map((p, i) => ({
            place: p,
            score: this._cosineSimilarity(this.tfidfMatrix[idx], this.tfidfMatrix[i])
        }));

        return scores
            .filter((_, i) => i !== idx)
            .sort((a, b) => b.score - a.score || b.place.rating - a.place.rating)
            .slice(0, topN)
            .map(s => s.place);
    }

    // ─── Step 4: Proximity-Based Recommendations ──────────────────────────────
    getNearbyRecommendations(lat, lng, topN = 5, excludeName = null) {
        if (!this.loaded) return [];
        return this.places
            .filter(p => p.name !== excludeName)
            .map(p => ({ ...p, distanceKm: this._haversine(lat, lng, p.lat, p.lng) }))
            .sort((a, b) => a.distanceKm - b.distanceKm)
            .slice(0, topN);
    }

    _haversine(lat1, lng1, lat2, lng2) {
        const R = 6371;
        const toR = Math.PI / 180;
        const dLat = (lat2 - lat1) * toR;
        const dLng = (lng2 - lng1) * toR;
        const a = Math.sin(dLat / 2) ** 2 +
            Math.cos(lat1 * toR) * Math.cos(lat2 * toR) * Math.sin(dLng / 2) ** 2;
        return R * 2 * Math.asin(Math.min(1, Math.sqrt(a)));
    }

    // ─── Step 5: Category Filter ───────────────────────────────────────────────
    getCategoryRecommendations(category, topN = 5) {
        if (!this.loaded) return [];
        return this.places
            .filter(p => p.category.toLowerCase().includes(category.toLowerCase()))
            .sort((a, b) => b.rating - a.rating)
            .slice(0, topN);
    }

    // ─── Step 6: Smart Itinerary (Time-of-Day) ────────────────────────────────
    getDayItinerary() {
        if (!this.loaded) return { morning: [], afternoon: [], evening: [] };
        const byTag = tag => this.places.filter(p => p.tags.toLowerCase().includes(tag));
        return {
            morning:   [...byTag('morning'), ...this.getCategoryRecommendations('Temple', 2)].slice(0, 3),
            afternoon: [...byTag('afternoon'), ...this.getCategoryRecommendations('Food Place', 2)].slice(0, 3),
            evening:   [...byTag('evening'), ...this.getCategoryRecommendations('Sightseeing', 2)].slice(0, 3),
        };
    }
}

// Export as global singleton
window.GeoTripRecommender = GeoTripRecommender;
window.recommender = new GeoTripRecommender();
