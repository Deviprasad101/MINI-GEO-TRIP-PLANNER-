"""ADK tool functions for GeoTrip Planner — return dict/JSON only."""
import csv
import json
import math
import os
import re
from typing import Optional

import requests

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
CSV_PATH = os.path.join(DATA_DIR, 'tirupati_main_data.csv')
PACKAGES_JSON = os.path.join(DATA_DIR, 'geotrip_packages.json')

_HOSPITAL_FALLBACK = {
    'SVIMS Hospital': {'lat': 13.642478, 'lng': 79.405348},
    'Ruia Government Hospital': {'lat': 13.644965, 'lng': 79.405757},
    'Apollo Hospital Tirupati': {'lat': 13.623068, 'lng': 79.429942},
    'Sri Chakra Hospital': {'lat': 13.6360533, 'lng': 79.4210585},
    'Aster Narayanadri Hospital': {'lat': 13.62846, 'lng': 79.463813},
    'Helios Hospital': {'lat': 13.638177, 'lng': 79.423773},
    'Venkataramana Hospital': {'lat': 13.6353807, 'lng': 79.4199327},
    'Suraksha Hospital': {'lat': 13.635603, 'lng': 79.4204007},
    'Mother Hospital': {'lat': 13.6382376, 'lng': 79.4184239},
    'Life Line Hospital': {'lat': 13.6367942, 'lng': 79.4214374},
    'Balaji Hospital': {'lat': 13.6366949, 'lng': 79.4274731},
}


def _haversine_km(lat1, lng1, lat2, lng2):
    r = 6371
    to_r = math.pi / 180
    d_lat = (lat2 - lat1) * to_r
    d_lng = (lng2 - lng1) * to_r
    a = math.sin(d_lat / 2) ** 2 + math.cos(lat1 * to_r) * math.cos(lat2 * to_r) * math.sin(d_lng / 2) ** 2
    return 2 * r * math.asin(min(1, math.sqrt(a)))


def _load_places():
    places = []
    if not os.path.isfile(CSV_PATH):
        return places
    with open(CSV_PATH, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) < 6:
                continue
            name = (row[0] or '').strip()
            category = (row[1] or '').strip()
            if not name:
                continue
            try:
                lat = float(row[2])
                lng = float(row[3])
            except (ValueError, TypeError):
                continue
            places.append({
                'name': name,
                'category': category,
                'lat': lat,
                'lng': lng,
                'description': (row[4] or '').strip() if len(row) > 4 else '',
                'timings': (row[5] or '').strip() if len(row) > 5 else '',
            })
    return places


def _load_packages():
    if not os.path.isfile(PACKAGES_JSON):
        return []
    try:
        with open(PACKAGES_JSON, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _category_counts():
    counts = {}
    for p in _load_places():
        cat = (p.get('category') or 'Other').strip()
        key = cat.split()[0].lower() if cat else 'other'
        counts[key] = counts.get(key, 0) + 1
    return counts


def get_trip_packages(tier_id: str = '', budget_inr: int = 0) -> dict:
    """Return GeoTrip package tiers (Basic, Standard, Premium, Super Premium, Elite).

    Args:
        tier_id: Optional filter — basic, standard, premium, superpremium, elite.
        budget_inr: Optional budget in INR to suggest the best matching tier.

    Returns:
        dict with package list and booking URL.
    """
    packages = _load_packages()
    tid = (tier_id or '').strip().lower()
    if tid:
        packages = [p for p in packages if p.get('id', '').lower() == tid]

    budget = int(budget_inr or 0)
    suggested = None
    if budget > 0 and not tid:
        for p in _load_packages():
            raw = (p.get('budget_range_inr') or '').replace('+', '')
            parts = raw.split('-')
            try:
                lo = int(parts[0])
                hi = int(parts[1]) if len(parts) > 1 else lo + 99999
            except (ValueError, IndexError):
                continue
            if lo <= budget <= hi:
                suggested = p.get('id')
                break
        if not suggested:
            if budget < 5000:
                suggested = 'basic'
            elif budget < 10000:
                suggested = 'standard'
            elif budget < 15000:
                suggested = 'premium'
            elif budget < 25000:
                suggested = 'superpremium'
            else:
                suggested = 'elite'

    return {
        'status': 'ok',
        'count': len(packages),
        'packages': packages,
        'suggested_tier_id': suggested,
        'booking_url': '/packages.html',
        'action': 'show_package',
    }


def list_temples(query: str = '', limit: int = 10) -> dict:
    """List temples from Tirupati CSV with timings and descriptions.

    Args:
        query: Optional name/location filter.
        limit: Max temples to return (1-15).

    Returns:
        dict with temple places from app data.
    """
    limit = max(1, min(int(limit or 10), 15))
    temples = [p for p in _load_places() if 'temple' in (p.get('category') or '').lower()]
    q = (query or '').strip().lower()
    if q:
        temples = [
            p for p in temples
            if q in (p.get('name') or '').lower()
            or q in (p.get('description') or '').lower()
        ]
    return {
        'status': 'ok',
        'count': len(temples[:limit]),
        'temples': temples[:limit],
        'total_in_database': len(temples),
    }


def get_app_data_catalog() -> dict:
    """Summary of all GeoTrip data the agent can use (places, packages, features)."""
    counts = _category_counts()
    packages = _load_packages()
    temple_count = sum(1 for p in _load_places() if 'temple' in (p.get('category') or '').lower())
    return {
        'status': 'ok',
        'place_categories': counts,
        'temple_count': temple_count,
        'package_tiers': [p.get('id') for p in packages],
        'packages_summary': [
            {
                'id': p.get('id'),
                'title': p.get('title'),
                'budget': p.get('display_range'),
                'places': p.get('place_limit'),
                'duration': p.get('duration_days'),
            }
            for p in packages
        ],
        'features': {
            'trip_packages': '/packages.html',
            'map_planner': '/dashboard',
            'booking': '/booking.html',
            'qr_checkin': '/dashboard (floating QR after login)',
            'emergency_hospitals': 'Emergency FAB on map pages',
        },
    }


def search_places(category: str = 'temple', limit: int = 5) -> dict:
    """Search Tirupati places from the app CSV by category keyword.

    Args:
        category: Place type filter, e.g. temple, food, sightseeing, hospital, adventure.
        limit: Maximum number of places to return (1-10).

    Returns:
        dict with status and list of matching places.
    """
    limit = max(1, min(int(limit or 5), 10))
    cat = (category or '').strip().lower()
    places = _load_places()
    if cat and cat not in ('all', 'any'):
        places = [p for p in places if cat in (p.get('category') or '').lower()]
    places = places[:limit]
    return {
        'status': 'ok',
        'category': category,
        'count': len(places),
        'places': places,
    }


def lookup_place(query: str, limit: int = 5) -> dict:
    """Find places by name, category, description, or timings keywords.

    Use for questions about a specific temple, timings, opening hours, or place details.

    Args:
        query: Search text, e.g. Tirumala temple timing, Kapila Teertham, food near me.
        limit: Maximum matches to return (1-10).

    Returns:
        dict with matching places including name, category, timings, and description.
    """
    query = (query or '').strip()
    if not query:
        return {'status': 'error', 'message': 'Query is required'}

    limit = max(1, min(int(limit or 5), 10))
    q_lower = query.lower()
    terms = [t for t in re.split(r'\W+', q_lower) if len(t) > 2]
    # Drop generic words so "timing of Tirumala temple" matches Tirumala
    stop = {'the', 'what', 'when', 'where', 'timing', 'timings', 'time', 'hours', 'open', 'about', 'near', 'best'}
    terms = [t for t in terms if t not in stop]

    places = _load_places()
    scored = []
    for p in places:
        hay = ' '.join([
            p.get('name', ''),
            p.get('category', ''),
            p.get('description', ''),
            p.get('timings', ''),
        ]).lower()
        score = sum(2 if t in (p.get('name') or '').lower() else 1 for t in terms if t in hay)
        if q_lower in hay:
            score += 3
        if score > 0:
            scored.append((score, p))

    scored.sort(key=lambda x: (-x[0], x[1].get('name', '')))
    results = [p for _, p in scored[:limit]]
    if not results and terms:
        # Broader match: any single term in name/category
        for p in places:
            hay = f"{p.get('name', '')} {p.get('category', '')}".lower()
            if any(t in hay for t in terms):
                results.append(p)
        results = results[:limit]

    return {
        'status': 'ok',
        'query': query,
        'count': len(results),
        'places': results,
    }


def get_weather(city: str = 'Tirupati') -> dict:
    """Get current weather for a city (defaults to Tirupati).

    Args:
        city: City name to look up.

    Returns:
        dict with temperature, condition, humidity, and wind.
    """
    city = (city or 'Tirupati').strip() or 'Tirupati'
    try:
        geo = requests.get(
            'https://geocoding-api.open-meteo.com/v1/search',
            params={'name': city, 'count': 1, 'language': 'en', 'format': 'json'},
            timeout=15,
        )
        geo.raise_for_status()
        results = (geo.json() or {}).get('results') or []
        if not results:
            return {'status': 'error', 'message': f'City "{city}" not found'}
        place = results[0]
        lat, lon = place['latitude'], place['longitude']
        name = place.get('name') or city

        wx = requests.get(
            'https://api.open-meteo.com/v1/forecast',
            params={
                'latitude': lat,
                'longitude': lon,
                'current': 'temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code',
                'wind_speed_unit': 'kmh',
            },
            timeout=15,
        )
        wx.raise_for_status()
        current = (wx.json() or {}).get('current') or {}
        return {
            'status': 'ok',
            'city': name,
            'temperature_c': current.get('temperature_2m'),
            'humidity_pct': current.get('relative_humidity_2m'),
            'wind_kmh': current.get('wind_speed_10m'),
            'weather_code': current.get('weather_code'),
        }
    except Exception as exc:
        return {'status': 'error', 'message': str(exc)}


def generate_itinerary(days: int = 1, budget: str = 'medium', interests: str = 'temples') -> dict:
    """Generate an AI day-wise itinerary for Tirupati using Gemini.

    Args:
        days: Number of days (1-5).
        budget: low, medium, or high.
        interests: Comma-separated interests, e.g. temples, food, nature.

    Returns:
        dict with itinerary array or error message.
    """
    days = max(1, min(int(days or 1), 5))
    budget = (budget or 'medium').strip()
    interests = (interests or 'temples').strip()
    api_key = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
    if not api_key:
        return {'status': 'error', 'message': 'Gemini API key not configured in .env'}

    prompt = (
        f'Create a {days}-day itinerary for Tirupati, India. Budget: {budget}. '
        f'Interests: {interests}. Return ONLY a JSON array of daily objects with '
        "'day' (number), 'activities' (array of strings), and 'tips' (string). "
        'No markdown.'
    )
    try:
        from google import genai
        from gemini_config import get_gemini_models, is_retryable_model_error

        client = genai.Client(api_key=api_key)
        last_err = None
        for model in get_gemini_models():
            try:
                response = client.models.generate_content(model=model, contents=prompt)
                text = (response.text or '').strip()
                if text.startswith('```'):
                    text = text.split('\n', 1)[1].rsplit('\n', 1)[0]
                    if text.startswith('json'):
                        text = text[4:].strip()
                itinerary = json.loads(text)
                return {
                    'status': 'ok',
                    'days': days,
                    'budget': budget,
                    'interests': interests,
                    'itinerary': itinerary,
                    'action': 'open_map',
                    'model': model,
                }
            except json.JSONDecodeError:
                return {'status': 'error', 'message': 'Gemini returned invalid JSON for itinerary'}
            except Exception as exc:
                last_err = exc
                if is_retryable_model_error(exc):
                    continue
                return {'status': 'error', 'message': str(exc)}
        return {'status': 'error', 'message': str(last_err) if last_err else 'All Gemini models failed'}
    except Exception as exc:
        return {'status': 'error', 'message': str(exc)}


def find_nearest_hospitals(lat: float, lng: float, count: int = 3) -> dict:
    """Find nearest hospitals to GPS coordinates using app hospital data.

    Args:
        lat: Latitude of user location.
        lng: Longitude of user location.
        count: Number of hospitals to return (default 3).

    Returns:
        dict with ranked nearest hospitals and distances in km.
    """
    count = max(1, min(int(count or 3), 5))
    hospitals = []
    for p in _load_places():
        if 'hospital' not in (p.get('category') or '').lower():
            continue
        hospitals.append(p)
    if not hospitals:
        for name, coords in _HOSPITAL_FALLBACK.items():
            hospitals.append({'name': name, 'lat': coords['lat'], 'lng': coords['lng'], 'category': 'Hospital'})

    ranked = []
    for h in hospitals:
        dist = _haversine_km(float(lat), float(lng), h['lat'], h['lng'])
        ranked.append({**h, 'distance_km': round(dist, 2)})
    ranked.sort(key=lambda x: x['distance_km'])
    ranked = ranked[:count]
    return {
        'status': 'ok',
        'count': len(ranked),
        'hospitals': ranked,
        'action': 'show_emergency_map',
    }


def _category_key_from_csv(category: str) -> str:
    c = (category or '').lower()
    if 'food' in c or 'restaurant' in c:
        return 'food'
    if 'temple' in c:
        return 'temple'
    if 'sight' in c:
        return 'sightseeing'
    if 'adventure' in c:
        return 'adventure'
    if 'wildlife' in c:
        return 'wildlife'
    if 'hospital' in c:
        return 'hospital'
    return 'other'


def _parse_trip_categories(message: str) -> list[str]:
    msg = (message or '').lower()
    found: list[str] = []
    patterns = (
        ('temple', 'temple'),
        ('darshan', 'temple'),
        ('tirumala', 'temple'),
        ('food', 'food'),
        ('restaurant', 'food'),
        ('meal', 'food'),
        ('dining', 'food'),
        ('sightseeing', 'sightseeing'),
        ('sight', 'sightseeing'),
        ('adventure', 'adventure'),
        ('wildlife', 'wildlife'),
    )
    for key, cat in patterns:
        if key in msg and cat not in found:
            found.append(cat)
    return found or ['temple']


def _parse_trip_budget(message: str) -> tuple[str, int]:
    msg = (message or '').lower()
    bm = re.search(r'(\d{3,6})', msg.replace(',', ''))
    if bm:
        budget_inr = int(bm.group(1))
    elif re.search(r'\b(low|cheap|basic)\b', msg):
        return 'low', 3500
    elif re.search(r'\b(high|premium|luxury|elite)\b', msg):
        return 'high', 12000
    elif re.search(r'\b(medium|standard|mid)\b', msg):
        return 'medium', 7500
    else:
        budget_inr = 7500

    if budget_inr <= 5000:
        return 'low', budget_inr
    if budget_inr <= 10000:
        return 'medium', budget_inr
    return 'high', budget_inr


def _greedy_order_stops(start_lat: float, start_lng: float, places: list) -> list:
    remaining = list(places)
    ordered = []
    cur_lat, cur_lng = start_lat, start_lng
    order = 1
    while remaining:
        remaining.sort(key=lambda p: _haversine_km(cur_lat, cur_lng, p['lat'], p['lng']))
        nxt = remaining.pop(0)
        ordered.append({
            'order': order,
            'name': nxt.get('name', ''),
            'lat': nxt['lat'],
            'lng': nxt['lng'],
            'category': nxt.get('category', ''),
            'categoryKey': _category_key_from_csv(nxt.get('category', '')),
            'timings': nxt.get('timings', ''),
            'description': (nxt.get('description') or '')[:200],
            'booking_link': nxt.get('booking_link', ''),
        })
        order += 1
        cur_lat, cur_lng = nxt['lat'], nxt['lng']
    return ordered


def plan_automated_trip(message: str, lat: float, lng: float) -> dict:
    """Build an ordered day trip from CSV places, package tier, and user categories.

    Parses days, budget, and category mix from natural language, picks a package tier,
    selects nearest places per category, and returns greedy-nearest stop order for map routing.

    Args:
        message: User request, e.g. "Plan 1-day temple + food trip, medium budget".
        lat: Start latitude (user GPS or Tirupati default).
        lng: Start longitude.

    Returns:
        dict with package tier, categories, ordered stops, and show_route action hint.
    """
    msg = (message or '').strip()
    days = 1
    dm = re.search(r'(\d+)\s*day', msg, re.I)
    if dm:
        days = max(1, min(int(dm.group(1)), 5))

    categories = _parse_trip_categories(msg)
    budget_label, budget_inr = _parse_trip_budget(msg)

    pkg_result = get_trip_packages('', budget_inr)
    suggested = pkg_result.get('suggested_tier_id') or 'standard'
    packages = pkg_result.get('packages') or _load_packages()
    tier_pkg = next((p for p in packages if p.get('id') == suggested), None)
    if not tier_pkg and packages:
        tier_pkg = packages[0]
        suggested = tier_pkg.get('id', 'standard')

    place_limit = int((tier_pkg or {}).get('place_limit') or 6)
    max_stops = min(place_limit, days * 6)
    per_cat = max(1, max_stops // len(categories))

    candidates = []
    seen_names: set[str] = set()
    all_places = _load_places()
    for cat in categories:
        cat_places = [p for p in all_places if cat in (p.get('category') or '').lower()]
        cat_places.sort(key=lambda p: _haversine_km(lat, lng, p['lat'], p['lng']))
        picked = 0
        for place in cat_places:
            name = place.get('name') or ''
            if name in seen_names:
                continue
            candidates.append(place)
            seen_names.add(name)
            picked += 1
            if picked >= per_cat:
                break

    stops = _greedy_order_stops(lat, lng, candidates)

    return {
        'status': 'ok',
        'days': days,
        'budget': budget_label,
        'budget_inr': budget_inr,
        'categories': categories,
        'package_tier': suggested,
        'package_title': (tier_pkg or {}).get('title', suggested),
        'package_display_range': (tier_pkg or {}).get('display_range', ''),
        'place_limit': place_limit,
        'stop_count': len(stops),
        'start': {'lat': lat, 'lng': lng},
        'stops': stops,
        'action': 'show_route',
    }


def get_user_qr_info(user_id: Optional[str] = None) -> dict:
    """Explain QR check-in and what the app provides (token requires logged-in session).

    Args:
        user_id: Optional user identifier; QR token is issued after login.

    Returns:
        dict with QR usage instructions and dashboard link.
    """
    return {
        'status': 'ok',
        'message': (
            'Each registered user gets a unique QR code for temple check-in. '
            'Open the dashboard, show your map/plan, then use the floating QR code. '
            'Temple staff can scan it to log your visit.'
        ),
        'action': 'show_qr',
        'dashboard_url': '/dashboard',
        'note': 'QR token is available only for logged-in users via /api/qr/generate.',
    }
