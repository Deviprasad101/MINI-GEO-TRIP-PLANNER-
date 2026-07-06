"""Local Ollama/Gemma agent — same tools as Gemini ADK, runs offline."""
import os
import sys
import uuid
from typing import Optional
from urllib.parse import urlparse

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv

load_dotenv(os.path.join(BACKEND_DIR, '.env'))

_ollama_sessions: dict[str, list[dict]] = {}
_cached_ollama_models: list[str] | None = None

# Tried in order when configured model is missing
OLLAMA_MODEL_FALLBACKS = (
    'gemma3:4b',
    'gemma3:1b',
    'gemma3',
    'gemma2:2b',
    'gemma2',
    'llama3.2',
    'llama3.2:3b',
    'mistral',
)


def _ollama_host() -> str:
    base = (os.getenv('OLLAMA_BASE_URL') or 'http://localhost:11434').strip().rstrip('/')
    parsed = urlparse(base if '://' in base else f'http://{base}')
    if parsed.scheme and parsed.netloc:
        return f'{parsed.scheme}://{parsed.netloc}'
    return 'http://localhost:11434'


def _agent_model() -> str:
    return (os.getenv('OLLAMA_AGENT_MODEL') or os.getenv('OLLAMA_MODEL') or 'gemma3:4b').strip()


def _list_ollama_models(refresh: bool = False) -> list[str]:
    """Return model names installed in local Ollama."""
    global _cached_ollama_models
    if _cached_ollama_models is not None and not refresh:
        return _cached_ollama_models
    import requests

    host = _ollama_host()
    try:
        r = requests.get(f'{host}/api/tags', timeout=10)
        r.raise_for_status()
        names = []
        for item in (r.json() or {}).get('models') or []:
            name = (item.get('name') or '').strip()
            if name:
                names.append(name)
        if names:
            _cached_ollama_models = names
        else:
            _cached_ollama_models = None
        return names
    except Exception:
        _cached_ollama_models = None
        return []


def _resolve_ollama_model(preferred: str) -> str:
    """Pick an installed model — preferred first, then env list, then any local model."""
    installed = _list_ollama_models()
    if not installed:
        return preferred

    def _find(name: str) -> Optional[str]:
        if name in installed:
            return name
        base = name.split(':')[0]
        for inst in installed:
            if inst == name or inst.startswith(name + ':') or inst.split(':')[0] == base:
                return inst
        return None

    found = _find(preferred)
    if found:
        return found

    raw = (os.getenv('OLLAMA_MODELS') or '').strip()
    candidates = [preferred]
    if raw:
        candidates.extend(p.strip() for p in raw.split(',') if p.strip())
    for name in OLLAMA_MODEL_FALLBACKS:
        if name not in candidates:
            candidates.append(name)

    for name in candidates:
        found = _find(name)
        if found:
            return found

    return installed[0]


def _chat_ollama(messages: list[dict], model: str) -> str:
    """Call Ollama via python package, fallback to HTTP."""
    host = _ollama_host()
    timeout = int(os.getenv('OLLAMA_TIMEOUT', '120'))

    try:
        from ollama import Client

        client = Client(host=host)
        response = client.chat(model=model, messages=messages)
        return (response.get('message') or {}).get('content') or ''
    except ImportError:
        pass

    import requests

    url = f'{host}/api/chat'
    payload = {'model': model, 'messages': messages, 'stream': False}
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()['message']['content']


def run_ollama_agent(
    message: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
) -> dict:
    """Sync entry point — tool-augmented local agent (Gemma3 via Ollama)."""
    from agent_runner import (
        _build_agent_message,
        _ensure_route_action,
        _extract_actions_from_text,
        _format_tool_answer,
        _gather_tool_context,
        _is_weak_reply,
        _tool_fallback,
    )

    message = (message or '').strip()
    if not message:
        return {'status': 'error', 'message': 'Message is required'}

    model = _resolve_ollama_model(_agent_model())
    if not _list_ollama_models():
        fb = _tool_fallback(message, lat, lng)
        fb['warning'] = (
            'No Ollama models installed. Open a terminal and run: '
            'ollama pull gemma3:4b  (then restart the app or try again).'
        )
        fb['provider'] = 'tool_fallback'
        return fb

    uid = user_id or 'geotrip_user'
    sid = session_id or str(uuid.uuid4())
    tool_ctx = _gather_tool_context(message, lat, lng)
    agent_message = _build_agent_message(message, lat, lng, uid)

    system_prompt = (
        'You are GeoTrip Assistant for Tirupati and Tirumala, India. '
        'The user message includes USER QUESTION, USER LOCATION, and TOOL DATA from real app sources: '
        'tirupati_main_data.csv (temples, food, sightseeing, hospitals), '
        'geotrip_packages.json (Basic/Standard/Premium/Super Premium/Elite tiers), '
        'weather API, and QR/booking features. '
        'Answer the USER QUESTION directly using TOOL DATA only. '
        'For packages: quote tier name, budget range, duration, and place limits from packages data. '
        'For temples: use temples/lookup data with timings — never invent names. '
        'For hospitals: use provided lat/lng. '
        'Suggest /packages.html for package booking and /dashboard for map planning when relevant.'
    )

    history = _ollama_sessions.get(sid, [])
    messages = [{'role': 'system', 'content': system_prompt}]
    messages.extend(history[-6:])
    messages.append({'role': 'user', 'content': agent_message})

    try:
        reply = _chat_ollama(messages, model).strip()
        actions = []

        if _is_weak_reply(reply, message):
            reply, actions = _format_tool_answer(message, tool_ctx)
        else:
            actions = _extract_actions_from_text(reply)

        if not reply:
            reply, actions = _format_tool_answer(message, tool_ctx)

        actions = _ensure_route_action(message, actions, tool_ctx)

        history.append({'role': 'user', 'content': message})
        history.append({'role': 'assistant', 'content': reply})
        _ollama_sessions[sid] = history[-12:]

        result = {
            'status': 'success',
            'reply': reply,
            'actions': actions,
            'session_id': sid,
            'provider': 'ollama',
            'model': model,
        }
        if model != _agent_model():
            result['warning'] = f'Using installed model: {model}'
        return result
    except Exception as exc:
        _cached_ollama_models = None
        fb = _tool_fallback(message, lat, lng)
        installed = _list_ollama_models()
        if installed:
            hint = f'Installed models: {", ".join(installed[:5])}. Set OLLAMA_AGENT_MODEL in backend/.env.'
        else:
            hint = 'Run: ollama pull gemma3:4b'
        fb['warning'] = f'Ollama error — using tool data. {hint} ({str(exc)[:60]})'
        fb['provider'] = 'tool_fallback'
        return fb
