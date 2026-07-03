"""Run GeoTrip ADK agent from Flask (sync wrapper + tool fallback)."""
import asyncio
import json
import os
import re
import sys
import uuid
from typing import Any, Optional

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv

load_dotenv(os.path.join(BACKEND_DIR, '.env'))

_agent_lock = asyncio.Lock()
_sessions: dict[str, str] = {}  # client session_id -> adk session id

_runner = None
_app = None
_current_model: Optional[str] = None


def _ensure_api_key_env():
    key = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
    if key and not os.getenv('GOOGLE_API_KEY'):
        os.environ['GOOGLE_API_KEY'] = key
    if key and not os.getenv('GEMINI_API_KEY'):
        os.environ['GEMINI_API_KEY'] = key
    return key


def _get_runner(model: Optional[str] = None):
    global _runner, _app, _current_model
    from gemini_config import get_gemini_models

    use_model = model or os.getenv('GEMINI_MODEL') or get_gemini_models()[0]
    if _runner is not None and _current_model == use_model:
        return _runner

    _runner = None
    _app = None
    _ensure_api_key_env()
    os.environ['GEMINI_MODEL'] = use_model

    from google.adk.apps import App
    from google.adk.runners import InMemoryRunner
    from agents.geotrip_agent.agent import build_root_agent

    agent = build_root_agent(use_model)
    _app = App(name='geotrip_app', root_agent=agent)
    _runner = InMemoryRunner(app=_app)
    _current_model = use_model
    return _runner


def _extract_actions_from_text(text: str) -> list[dict]:
    actions = []
    lower = (text or '').lower()
    if any(w in lower for w in ('itinerary', 'plan', 'day plan', 'trip')):
        actions.append({'type': 'open_map'})
    if 'package' in lower:
        actions.append({'type': 'show_package', 'url': '/packages.html'})
    if 'qr' in lower or 'check-in' in lower or 'check in' in lower:
        actions.append({'type': 'show_qr'})
    if 'hospital' in lower or 'emergency' in lower:
        actions.append({'type': 'show_emergency_map'})
    return actions


def _default_coords(lat: Optional[float], lng: Optional[float]) -> tuple[float, float]:
    use_lat = lat if lat is not None else 13.6288
    use_lng = lng if lng is not None else 79.4192
    return use_lat, use_lng


def _detect_intent(message: str) -> str:
    msg = (message or '').strip().lower()
    if 'weather' in msg:
        return 'weather'
    if any(k in msg for k in ('hospital', 'emergency', 'nearest', 'ambulance', 'medical')):
        return 'hospitals'
    if 'qr' in msg or 'check-in' in msg or 'check in' in msg:
        return 'qr'
    if any(k in msg for k in ('package', 'packages', 'tier', 'basic', 'standard', 'premium', 'elite', 'budget plan', 'trip cost')):
        return 'packages'
    if any(k in msg for k in ('plan', 'itinerary', 'trip', 'day')):
        return 'itinerary'
    if any(k in msg for k in ('timing', 'timings', 'open', 'hours', 'when does', 'what time')):
        return 'lookup'
    if any(k in msg for k in ('food', 'restaurant', 'eat', 'meal', 'dining', 'breakfast', 'lunch')):
        return 'places_food'
    if any(k in msg for k in ('temple', 'darshan', 'tirumala', 'tirupati', 'shrine', 'pilgrim')):
        return 'lookup'
    if any(k in msg for k in ('place', 'nearby', 'recommend', 'best', 'visit', 'sightseeing', 'adventure', 'wildlife')):
        return 'places'
    return 'general'


def _gather_tool_context(message: str, lat: Optional[float], lng: Optional[float]) -> dict:
    """Prefetch tool results so the agent can answer the exact user question."""
    from agents.geotrip_agent import tools as T

    intent = _detect_intent(message)
    ctx: dict[str, Any] = {'intent': intent}
    use_lat, use_lng = _default_coords(lat, lng)
    msg = (message or '').strip()

    if intent == 'weather':
        city = 'Tirupati'
        m = re.search(r'weather in ([a-zA-Z\s]+)', msg, re.I)
        if m:
            city = m.group(1).strip()
        ctx['weather'] = T.get_weather(city)

    elif intent == 'hospitals':
        ctx['hospitals'] = T.find_nearest_hospitals(use_lat, use_lng, 5)

    elif intent == 'qr':
        ctx['qr'] = T.get_user_qr_info()

    elif intent == 'packages':
        budget = 0
        bm = re.search(r'(\d{3,6})', msg.replace(',', ''))
        if bm:
            budget = int(bm.group(1))
        tier = ''
        for tid in ('elite', 'superpremium', 'premium', 'standard', 'basic'):
            if tid in msg.lower():
                tier = tid
                break
        ctx['packages'] = T.get_trip_packages(tier, budget)
        ctx['catalog'] = T.get_app_data_catalog()

    elif intent == 'itinerary':
        days = 1
        dm = re.search(r'(\d+)\s*day', msg, re.I)
        if dm:
            days = int(dm.group(1))
        budget = 'medium'
        if re.search(r'\b(low|cheap|budget)\b', msg, re.I):
            budget = 'low'
        elif re.search(r'\b(high|premium|luxury)\b', msg, re.I):
            budget = 'high'
        interests = 'temples'
        if 'food' in msg.lower():
            interests = 'temples, food'
        elif 'sight' in msg.lower():
            interests = 'temples, sightseeing'
        ctx['itinerary'] = T.generate_itinerary(days, budget, interests)
        ctx['places'] = T.search_places('temple', 5)

    elif intent == 'places_food':
        ctx['places'] = T.search_places('food', 8)
        ctx['lookup'] = T.lookup_place(msg, 5)

    elif intent in ('lookup', 'places'):
        ctx['lookup'] = T.lookup_place(msg, 6)
        if 'temple' in msg.lower():
            ctx['temples'] = T.list_temples(msg, 8)
        if not ctx['lookup'].get('places'):
            cat = 'temple'
            for key in ('food', 'sightseeing', 'hospital', 'adventure', 'wildlife', 'temple'):
                if key in msg.lower():
                    cat = key
                    break
            ctx['places'] = T.search_places(cat, 6)

    else:
        ctx['lookup'] = T.lookup_place(msg, 4)
        ctx['catalog'] = T.get_app_data_catalog()

    # Always attach a compact catalog so local/cloud agents know available data
    if 'catalog' not in ctx:
        ctx['catalog'] = T.get_app_data_catalog()
    if intent == 'lookup' and 'temples' not in ctx and any(k in msg.lower() for k in ('temple', 'tirumala', 'darshan')):
        ctx['temples'] = T.list_temples(msg, 6)

    ctx['user_location'] = {'lat': use_lat, 'lng': use_lng}
    return ctx


def _build_agent_message(message: str, lat: Optional[float], lng: Optional[float], user_id: Optional[str]) -> str:
    ctx = _gather_tool_context(message, lat, lng)
    use_lat, use_lng = _default_coords(lat, lng)
    tool_json = json.dumps(ctx, ensure_ascii=False, default=str)
    uid = user_id or 'geotrip_user'
    return (
        f'USER QUESTION: {message.strip()}\n'
        f'USER ID: {uid}\n'
        f'USER LOCATION: latitude={use_lat}, longitude={use_lng}\n'
        f'TOOL DATA (use this — do not invent names or timings):\n{tool_json}\n\n'
        'Answer the USER QUESTION directly using TOOL DATA. '
        'If hospitals were requested, list nearest hospitals with distances. '
        'If timings were requested, quote timings from lookup results. '
        'If packages or budget were asked, use packages data from TOOL DATA. '
        'If temples were asked, use temples/list_temples data with timings. '
        'Do not ask the user for GPS coordinates.'
    )


def _format_tool_answer(message: str, ctx: dict) -> tuple[str, list[dict]]:
    """Build a direct answer from prefetched tool data."""
    intent = ctx.get('intent', 'general')
    parts: list[str] = []
    actions: list[dict] = []

    if intent == 'weather' and ctx.get('weather', {}).get('status') == 'ok':
        w = ctx['weather']
        parts.append(
            f"Weather in {w['city']}: {w.get('temperature_c')}°C, "
            f"humidity {w.get('humidity_pct')}%, wind {w.get('wind_kmh')} km/h."
        )

    if intent == 'hospitals' and ctx.get('hospitals', {}).get('hospitals'):
        lines = [
            f"{i + 1}. {h['name']} — {h['distance_km']} km away"
            for i, h in enumerate(ctx['hospitals']['hospitals'])
        ]
        parts.append('Nearest hospitals from your location:\n' + '\n'.join(lines))
        actions.append({'type': 'show_emergency_map'})

    if intent == 'qr' and ctx.get('qr'):
        parts.append(ctx['qr'].get('message', ''))
        actions.append({'type': 'show_qr'})

    if intent == 'packages' and ctx.get('packages', {}).get('packages'):
        pkg_lines = []
        for p in ctx['packages']['packages']:
            pkg_lines.append(
                f"• {p.get('title', p.get('id'))}: {p.get('display_range', '')} — "
                f"{p.get('duration_days', '')} day(s), up to {p.get('place_limit', '?')} places, "
                f"group {p.get('people', '')}"
            )
            for h in (p.get('highlights') or [])[:2]:
                pkg_lines.append(f"  - {h}")
        sug = ctx['packages'].get('suggested_tier_id')
        intro = 'Trip packages on GeoTrip:'
        if sug:
            intro = f'Best package tier for your budget: {sug}. All tiers:'
        parts.append(intro + '\n' + '\n'.join(pkg_lines))
        parts.append('Book and customize at /packages.html')
        actions.append({'type': 'show_package', 'url': '/packages.html'})

    if ctx.get('temples', {}).get('temples'):
        lines = []
        for t in ctx['temples']['temples']:
            line = f"• {t['name']}"
            if t.get('timings'):
                line += f" — {t['timings']}"
            lines.append(line)
        parts.append('Temples from GeoTrip database:\n' + '\n'.join(lines))
        actions.append({'type': 'open_map'})

    if ctx.get('lookup', {}).get('places'):
        lines = []
        for p in ctx['lookup']['places']:
            line = f"• {p['name']} ({p.get('category', '')})"
            if p.get('timings'):
                line += f" — Timings: {p['timings']}"
            if p.get('description'):
                line += f". {p['description'][:120]}"
            lines.append(line)
        label = 'Here is what I found' if intent == 'lookup' else 'Matching places'
        parts.append(label + ':\n' + '\n'.join(lines))
        actions.append({'type': 'open_map'})

    if ctx.get('places', {}).get('places') and intent not in ('lookup',):
        cat = ctx['places'].get('category', 'place')
        lines = [
            f"• {p['name']}" + (f" — {p['timings']}" if p.get('timings') else '')
            for p in ctx['places']['places']
        ]
        parts.append(f"Recommended {cat} options in Tirupati:\n" + '\n'.join(lines))
        actions.append({'type': 'open_map'})

    if intent == 'itinerary':
        it = ctx.get('itinerary') or {}
        if it.get('status') == 'ok' and it.get('itinerary'):
            summary = []
            for day in it['itinerary']:
                acts = day.get('activities') or []
                summary.append(f"Day {day.get('day', '?')}: " + '; '.join(acts[:4]))
            parts.append('Suggested itinerary:\n' + '\n'.join(summary))
            actions.append({'type': 'open_map'})
        elif ctx.get('places', {}).get('places'):
            names = ', '.join(p['name'] for p in ctx['places']['places'][:5])
            parts.append(f'Key temples to include: {names}')

    if not parts:
        parts.append(
            'I can help with trip plans, weather, temple timings, food places, QR check-in, and nearest hospitals. '
            'Try asking something specific, e.g. "Timings of Tirumala temple" or "Nearest hospitals".'
        )

    return '\n\n'.join(parts), actions


def _is_weak_reply(reply: str, message: str) -> bool:
    if not (reply or '').strip():
        return True
    lower = reply.lower()
    weak_phrases = (
        'cannot provide',
        'do not have access',
        "don't have access",
        'need your current location',
        'share your latitude',
        'share your longitude',
        'could you please share',
        'i need your',
        'processed your request',
        'check the map or recommendations',
    )
    if any(p in lower for p in weak_phrases):
        return True
    if len(reply) < 40 and _detect_intent(message) != 'general':
        return True
    return False


def _tool_fallback(message: str, lat: Optional[float], lng: Optional[float]) -> dict:
    """Direct tool routing when Gemini/ADK is unavailable."""
    ctx = _gather_tool_context(message, lat, lng)
    reply, actions = _format_tool_answer(message, ctx)
    return {
        'status': 'success',
        'reply': reply,
        'actions': actions,
        'provider': 'tool_fallback',
    }


async def _run_adk_async(
    message: str,
    user_id: str,
    session_id: Optional[str],
    model: str,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
) -> dict:
    runner = _get_runner(model)
    uid = user_id or 'geotrip_user'
    sid = session_id or str(uuid.uuid4())
    agent_message = _build_agent_message(message, lat, lng, uid)
    tool_ctx = _gather_tool_context(message, lat, lng)

    async with _agent_lock:
        try:
            session = await runner.session_service.create_session(
                app_name='geotrip_app',
                user_id=uid,
                session_id=sid,
            )
        except Exception:
            sid = str(uuid.uuid4())
            session = await runner.session_service.create_session(
                app_name='geotrip_app',
                user_id=uid,
                session_id=sid,
            )

    events = await runner.run_debug(agent_message, user_id=uid, session_id=session.id)
    reply_parts = []
    actions = []
    for event in events:
        if hasattr(event, 'is_final_response') and event.is_final_response():
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if getattr(part, 'text', None):
                        reply_parts.append(part.text)
        # collect tool results if exposed on events
        if getattr(event, 'actions', None):
            for act in (event.actions or []):
                if isinstance(act, dict) and act.get('type'):
                    actions.append(act)

    reply = '\n'.join(reply_parts).strip()
    if _is_weak_reply(reply, message):
        tool_reply, tool_actions = _format_tool_answer(message, tool_ctx)
        reply = tool_reply
        if tool_actions:
            actions = tool_actions

    if not reply:
        reply, actions = _format_tool_answer(message, tool_ctx)

    if not actions:
        actions = _extract_actions_from_text(reply)

    return {
        'status': 'success',
        'reply': reply,
        'actions': actions,
        'session_id': sid,
        'provider': 'google_adk',
        'model': model,
    }


def _is_quota_error(exc: BaseException) -> bool:
    from gemini_config import is_retryable_model_error
    return is_retryable_model_error(exc)


def run_geotrip_agent(
    message: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
) -> dict:
    """Sync entry point for Flask."""
    message = (message or '').strip()
    if not message:
        return {'status': 'error', 'message': 'Message is required'}

    if not _ensure_api_key_env():
        return _tool_fallback(message, lat, lng)

    from gemini_config import get_gemini_models

    models = get_gemini_models()
    last_exc = None
    for model in models:
        try:
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    _run_adk_async(
                        message,
                        user_id or 'geotrip_user',
                        session_id,
                        model,
                        lat=lat,
                        lng=lng,
                    )
                )
                if model != models[0]:
                    result['warning'] = f'Used fallback model: {model}'
                return result
            finally:
                loop.close()
        except Exception as exc:
            last_exc = exc
            if _is_quota_error(exc):
                continue
            fb = _tool_fallback(message, lat, lng)
            fb['warning'] = f'Agent unavailable ({str(exc)[:120]}); using tool fallback.'
            return fb

    fb = _tool_fallback(message, lat, lng)
    fb['warning'] = (
        'Gemini quota limit reached on all models '
        f'({", ".join(models)}); using direct tool recommendations.'
    )
    if last_exc:
        fb['detail'] = str(last_exc)[:200]
    return fb
