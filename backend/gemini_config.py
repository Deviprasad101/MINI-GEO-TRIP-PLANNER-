"""Shared Gemini model list and quota-error helpers."""
import os

DEFAULT_GEMINI_MODELS = (
    'gemini-2.0-flash',
    'gemini-1.5-flash',
    'gemini-2.5-flash',
)


def get_gemini_models() -> list[str]:
    """Return ordered model names to try (primary first, then fallbacks)."""
    primary = (os.getenv('GEMINI_MODEL') or '').strip()
    raw = (os.getenv('GEMINI_MODELS') or '').strip()
    models: list[str] = []

    if primary:
        models.append(primary)
    if raw:
        for part in raw.split(','):
            name = part.strip()
            if name and name not in models:
                models.append(name)
    for name in DEFAULT_GEMINI_MODELS:
        if name not in models:
            models.append(name)
    return models or list(DEFAULT_GEMINI_MODELS)


def is_quota_error(exc: BaseException) -> bool:
    err = str(exc).lower()
    return '429' in err or 'resource_exhausted' in err or 'quota' in err


def is_retryable_model_error(exc: BaseException) -> bool:
    """True when another Gemini model may succeed (quota or unknown model)."""
    if is_quota_error(exc):
        return True
    err = str(exc).lower()
    if '404' in err and ('not_found' in err or 'not found' in err):
        return True
    if 'model' in err and ('not found' in err or 'not supported' in err):
        return True
    return False
