import os
import io
import json
import re
import csv
import socket
import secrets
import requests
import random
import uuid
from threading import Lock
from urllib.parse import quote, urlparse
from datetime import datetime, timedelta, timezone, date
from sqlalchemy import inspect, text
from sqlalchemy.exc import ProgrammingError
from flask import Flask, request, jsonify, send_from_directory, send_file, redirect, session
from flask_cors import CORS
from flask_mail import Mail, Message
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from google import genai
from dotenv import load_dotenv
try:
    from twilio.rest import Client as TwilioClient
except ImportError:
    TwilioClient = None

load_dotenv()

app = Flask(__name__, static_url_path='', static_folder='.')
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.secret_key = os.getenv("SECRET_KEY", "dev_secret_key")
CORS(app)

# Database Configuration (PostgreSQL with SQLAlchemy)
database_url = f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Mail Configuration
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS') == 'True'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_USERNAME')

db = SQLAlchemy(app)
mail = Mail(app)

# User Model
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    qr_token = db.Column(db.String(32), unique=True, nullable=True, index=True)
    is_verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __init__(self, username, email, password_hash, phone=None, is_verified=False, qr_token=None):
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.phone = phone
        self.is_verified = is_verified
        self.qr_token = qr_token


class Temple(db.Model):
    __tablename__ = 'temples'
    id = db.Column(db.Integer, primary_key=True)
    csv_name = db.Column(db.String(200), unique=True, nullable=True, index=True)
    temple_name = db.Column(db.String(120), nullable=False)
    full_name = db.Column(db.String(200), nullable=False)
    location = db.Column(db.String(200), nullable=True)
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)


class TempleVisit(db.Model):
    __tablename__ = 'temple_visits'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    temple_id = db.Column(db.Integer, db.ForeignKey('temples.id'), nullable=False, index=True)
    visit_date = db.Column(db.Date, nullable=False, index=True)
    visit_count = db.Column(db.Integer, default=1, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now())
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
    )

    __table_args__ = (
        db.UniqueConstraint('user_id', 'temple_id', 'visit_date', name='uq_user_temple_day'),
    )

    user = db.relationship('User', backref=db.backref('temple_visits', lazy=True))
    temple = db.relationship('Temple', backref=db.backref('visits', lazy=True))


TIRUPATI_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tirupati_main_data.csv')
TEMPLE_VISIT_COOLDOWN_MINUTES = max(1, int(os.getenv('TEMPLE_VISIT_COOLDOWN_MINUTES', '10')))


QR_TOKEN_PATTERN = re.compile(r'^(GTP-USER-[A-Z0-9]{8}|GTX-[A-Z0-9]{5})$', re.IGNORECASE)


def generate_qr_token():
    """Secure personal QR token, e.g. GTP-USER-8F3K9X2P"""
    while True:
        token = f"GTP-USER-{secrets.token_hex(4).upper()}"
        if not User.query.filter_by(qr_token=token).first():
            return token


def is_valid_qr_token(token):
    if not token:
        return False
    return bool(QR_TOKEN_PATTERN.match(str(token).strip().upper()))


def ensure_qr_token(user):
    """Assign token once per user; never regenerate if already set."""
    if not user:
        return None
    existing = (user.qr_token or '').strip().upper()
    if existing and is_valid_qr_token(existing):
        if user.qr_token != existing:
            user.qr_token = existing
            db.session.commit()
        return existing
    token = generate_qr_token()
    user.qr_token = token
    db.session.commit()
    return token


def resolve_user(identifier):
    """Resolve visitor by secure QR token only (not numeric user id)."""
    if not identifier:
        return None
    ident = str(identifier).strip().upper()
    if ident.isdigit():
        return None
    if not is_valid_qr_token(ident):
        return None
    return User.query.filter_by(qr_token=ident).first()


def normalize_phone(raw):
    """Normalize Indian mobile number to E.164 format (+91XXXXXXXXXX)."""
    if not raw:
        return None
    digits = re.sub(r'\D', '', raw)
    if digits.startswith('91') and len(digits) == 12:
        digits = digits[2:]
    if len(digits) != 10 or digits[0] not in '6789':
        return None
    return '+91' + digits


def _send_twilio_verify(phone_e164):
    """Send OTP via Twilio Verify; returns (ok, error_message)."""
    if not twilio_client or not TWILIO_VERIFY_SID:
        print(f'[WARN] Twilio not configured. Skipping phone OTP for {phone_e164}.')
        return False, 'SMS service not configured. Contact admin.'
    try:
        twilio_client.verify.v2.services(TWILIO_VERIFY_SID) \
            .verifications.create(to=phone_e164, channel='sms')
        print(f'[DEBUG] Phone OTP sent via Twilio to {phone_e164}')
        return True, None
    except Exception as e:
        print(f'[ERROR] Twilio send failed: {e}')
        return False, str(e)


def _check_twilio_verify(phone_e164, code):
    """Check OTP via Twilio Verify; returns (approved, error_message)."""
    if not twilio_client or not TWILIO_VERIFY_SID:
        return False, 'SMS service not configured.'
    try:
        check = twilio_client.verify.v2.services(TWILIO_VERIFY_SID) \
            .verification_checks.create(to=phone_e164, code=code)
        if check.status == 'approved':
            return True, None
        return False, 'Invalid OTP code'
    except Exception as e:
        print(f'[ERROR] Twilio verify failed: {e}')
        return False, 'Verification failed. Try again.'


def _record_visit_for_user(user, temple_id):
    temple = db.session.get(Temple, int(temple_id))
    if not temple:
        return None, {'message': 'Temple not found.'}, 404

    today = date.today()
    now = _visit_now()
    cooldown_sec = _cooldown_seconds()
    cooldown_min = cooldown_sec // 60

    visit = TempleVisit.query.filter_by(
        user_id=user.id,
        temple_id=temple.id,
        visit_date=today,
    ).first()

    if visit:
        # Same temple today already checked in — enforce 10 min gap (only updated_at)
        if (visit.visit_count or 0) >= 1 and visit.updated_at:
            last_touch = _visit_dt_from_db(visit.updated_at)
            if last_touch:
                elapsed_sec = (now - last_touch).total_seconds()
                # Negative = clock skew / TZ mismatch → do not block with huge wait
                if 0 <= elapsed_sec < cooldown_sec:
                    wait_sec = int(cooldown_sec - elapsed_sec)
                    wait_sec = max(1, min(wait_sec, cooldown_sec))
                    return None, {
                        'message': _cooldown_message(wait_sec),
                        'cooldown': True,
                        'retryAfterSeconds': wait_sec,
                        'cooldownMinutes': cooldown_min,
                    }, 429
        visit.visit_count = (visit.visit_count or 0) + 1
        visit.updated_at = now
        count = visit.visit_count
        is_first = False
    else:
        visit = TempleVisit(
            user_id=user.id,
            temple_id=temple.id,
            visit_date=today,
            visit_count=1,
            created_at=now,
            updated_at=now,
        )
        db.session.add(visit)
        count = 1
        is_first = True

    db.session.commit()
    return {
        'status': 'ok',
        'isFirstVisitToday': is_first,
        'visitCount': count,
        'message': visit_message(count),
        'cooldownMinutes': cooldown_min,
        'temple': {
            'id': temple.id,
            'templeName': temple.temple_name,
            'fullName': temple.full_name,
            'location': temple.location or '',
        },
        'user': {'id': user.id, 'name': user.username},
        'visitDate': today.isoformat(),
    }, None, 200


def user_public_dict(user, client_origin=None):
    token = ensure_qr_token(user)
    base = get_share_base_url(client_origin)
    return {
        'id': user.id,
        'name': user.username,
        'email': user.email,
        'phone': user.phone or '',
        'qrToken': token,
        'qrUrl': f'{base}/user/{token}',
    }


def visit_message(count):
    if count <= 1:
        return 'Welcome! This is your first visit today.'
    suffix = 'th'
    if count % 10 == 1 and count % 100 != 11:
        suffix = 'st'
    elif count % 10 == 2 and count % 100 != 12:
        suffix = 'nd'
    elif count % 10 == 3 and count % 100 != 13:
        suffix = 'rd'
    return f'This is your {count}{suffix} visit today.'


def _visit_now():
    """Local naive time — matches PostgreSQL timestamp without time zone."""
    return datetime.now()


def _visit_dt_from_db(dt):
    if not dt:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _cooldown_seconds():
    return max(60, int(os.getenv('TEMPLE_VISIT_COOLDOWN_MINUTES', '10')) * 60)


def _cooldown_message(wait_seconds):
    wait_seconds = max(1, int(wait_seconds))
    minutes, seconds = divmod(wait_seconds, 60)
    if minutes and seconds:
        return (
            f'Please wait {minutes} min {seconds} sec before checking in again at this temple.'
        )
    if minutes:
        return f'Please wait {minutes} minute(s) before checking in again at this temple.'
    return f'Please wait {seconds} second(s) before checking in again at this temple.'


def _ensure_user_columns():
    try:
        cols = {c['name'] for c in inspect(db.engine).get_columns('users')}
    except Exception:
        return
    stmts = []
    if 'phone' not in cols:
        stmts.append('ALTER TABLE users ADD COLUMN phone VARCHAR(20)')
    if 'qr_token' not in cols:
        stmts.append('ALTER TABLE users ADD COLUMN qr_token VARCHAR(32)')
    for sql in stmts:
        try:
            db.session.execute(text(sql))
            db.session.commit()
        except ProgrammingError:
            db.session.rollback()


def _ensure_temple_columns():
    try:
        cols = {c['name'] for c in inspect(db.engine).get_columns('temples')}
    except Exception:
        return
    stmts = []
    if 'csv_name' not in cols:
        stmts.append('ALTER TABLE temples ADD COLUMN csv_name VARCHAR(200)')
    if 'lat' not in cols:
        stmts.append('ALTER TABLE temples ADD COLUMN lat DOUBLE PRECISION')
    if 'lng' not in cols:
        stmts.append('ALTER TABLE temples ADD COLUMN lng DOUBLE PRECISION')
    for sql in stmts:
        try:
            db.session.execute(text(sql))
            db.session.commit()
        except ProgrammingError:
            db.session.rollback()
    try:
        db.session.execute(text(
            'CREATE UNIQUE INDEX IF NOT EXISTS ix_temples_csv_name ON temples (csv_name) '
            'WHERE csv_name IS NOT NULL'
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()


def _sync_temples_from_csv():
    """Load all Temple-category rows from tirupati_main_data.csv into PostgreSQL."""
    if not os.path.isfile(TIRUPATI_CSV_PATH):
        print('[WARN] tirupati_main_data.csv not found; temple list not synced.')
        return

    seen_names = set()
    with open(TIRUPATI_CSV_PATH, encoding='utf-8', newline='') as fh:
        for row in csv.DictReader(fh):
            if (row.get('category') or '').strip().lower() != 'temple':
                continue
            name = (row.get('name') or '').strip()
            if not name:
                continue
            seen_names.add(name)
            lat = lng = None
            try:
                if row.get('lat'):
                    lat = float(row['lat'])
                if row.get('lng'):
                    lng = float(row['lng'])
            except (TypeError, ValueError):
                pass
            location = 'Tirupati region'
            if lat is not None and lng is not None:
                location = f'Tirupati ({lat:.4f}, {lng:.4f})'

            existing = Temple.query.filter_by(csv_name=name).first()
            if existing:
                existing.temple_name = name[:120]
                existing.full_name = name[:200]
                existing.location = location
                existing.lat = lat
                existing.lng = lng
            else:
                db.session.add(Temple(
                    csv_name=name,
                    temple_name=name[:120],
                    full_name=name[:200],
                    location=location,
                    lat=lat,
                    lng=lng,
                ))

    db.session.commit()
    print(f'[INFO] Synced {len(seen_names)} temples from tirupati_main_data.csv')


def _backfill_qr_tokens():
    for user in User.query.filter((User.qr_token == None) | (User.qr_token == '')).all():  # noqa: E711
        ensure_qr_token(user)
    for user in User.query.all():
        if user.qr_token and not is_valid_qr_token(user.qr_token):
            user.qr_token = None
            db.session.commit()
            ensure_qr_token(user)


def init_db():
    db.create_all()
    _ensure_user_columns()
    _ensure_temple_columns()
    _sync_temples_from_csv()
    _backfill_qr_tokens()


with app.app_context():
    init_db()

# Disable caching for development
@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers['Cache-Control'] = 'public, max-age=0'
    return response

# Configure Gemini
api_key = os.getenv("GEMINI_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
client = genai.Client(api_key=api_key) if api_key else None

# Configure Ollama (local LLM — runs alongside Gemini, not a replacement)
OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))

# Twilio Verify (phone OTP during registration)
TWILIO_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_VERIFY_SID = os.getenv('TWILIO_VERIFY_SERVICE_SID')
twilio_client = (
    TwilioClient(TWILIO_SID, TWILIO_TOKEN)
    if (TwilioClient and TWILIO_SID and TWILIO_TOKEN) else None
)
if not twilio_client:
    print('[WARN] Twilio not configured – phone OTP verification will be unavailable.')


def ask_ollama(prompt: str) -> str:
    url = f"{OLLAMA_BASE}/api/generate"
    payload = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
    r = requests.post(url, json=payload, timeout=OLLAMA_TIMEOUT)
    r.raise_for_status()
    return r.json()["response"]


def chat_ollama(messages: list) -> str:
    url = f"{OLLAMA_BASE}/api/chat"
    payload = {"model": OLLAMA_MODEL, "messages": messages, "stream": False}
    r = requests.post(url, json=payload, timeout=OLLAMA_TIMEOUT)
    r.raise_for_status()
    return r.json()["message"]["content"]


def clean_json_from_llm(text: str) -> str:
    clean_text = text.strip()
    if clean_text.startswith("```"):
        clean_text = clean_text.split("\n", 1)[1].rsplit("\n", 1)[0]
        if clean_text.startswith("json"):
            clean_text = clean_text[4:].strip()
    return clean_text

@app.route('/')
def home():
    return send_from_directory('.', 'login.html')

@app.route('/dashboard')
def dashboard():
    return send_from_directory('.', 'main_page.html')

@app.route('/.well-known/appspecific/com.chrome.devtools.json')
def chrome_devtools():
    return jsonify({})

@app.route('/favicon.ico')
def favicon():
    return '', 204

# In-memory store for package trip QR shares (scan opens /trip/<id>)
_package_shares = {}
_package_shares_lock = Lock()

_LOCAL_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})


def _get_lan_ip():
    """Best-effort LAN IPv4 so phone QR scans work (not localhost)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.4)
        sock.connect(('8.8.8.8', 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return None


def get_share_base_url(client_origin=None):
    """
    Base URL encoded in QR codes. Phones cannot open 127.0.0.1 on your PC.
    Priority: PUBLIC_BASE_URL env > client origin (if not local) > LAN IP > request host.
    """
    explicit = (os.getenv('PUBLIC_BASE_URL') or '').strip().rstrip('/')
    if explicit:
        return explicit

    try:
        port = str(urlparse(request.host_url).port or int(os.getenv('PORT', '5000')))
    except (TypeError, ValueError):
        port = os.getenv('PORT', '5000')

    if client_origin:
        try:
            parsed = urlparse(client_origin)
            host = (parsed.hostname or '').lower()
            if host and host not in _LOCAL_HOSTS:
                scheme = parsed.scheme or 'http'
                netloc = parsed.netloc or host
                return f'{scheme}://{netloc}'.rstrip('/')
        except Exception:
            pass

    req_host = (request.host or '').split(':')[0].lower()
    if req_host in _LOCAL_HOSTS:
        lan = _get_lan_ip()
        if lan:
            return f'http://{lan}:{port}'

    return request.host_url.rstrip('/')


def _trip_share_url(share_id, payload=None, client_origin=None):
    if payload and payload.get('shareUrl'):
        return payload['shareUrl']
    return f"{get_share_base_url(client_origin)}/trip/{share_id}"


@app.route('/api/package-share', methods=['POST'])
def create_package_share():
    data = request.get_json(silent=True) or {}
    stops = data.get('stops')
    if not stops or not isinstance(stops, list):
        return jsonify({'status': 'error', 'message': 'Trip must include at least one stop.'}), 400
    share_id = uuid.uuid4().hex[:12]
    client_origin = data.get('clientOrigin')
    trip_url = _trip_share_url(share_id, client_origin=client_origin)
    data['sharedAt'] = datetime.now(timezone.utc).isoformat()
    data['shareUrl'] = trip_url
    with _package_shares_lock:
        _package_shares[share_id] = data
    return jsonify({
        'status': 'ok',
        'shareId': share_id,
        'url': trip_url,
        'scanHint': 'Phone must be on the same Wi‑Fi as this computer.',
    })


@app.route('/api/package-share/<share_id>', methods=['GET', 'PATCH'])
def package_share_detail(share_id):
    if request.method == 'PATCH':
        patch = request.get_json(silent=True) or {}
        with _package_shares_lock:
            payload = _package_shares.get(share_id)
            if not payload:
                return jsonify({'status': 'error', 'message': 'Share not found.'}), 404
            if 'routePath' in patch and isinstance(patch['routePath'], list):
                payload['routePath'] = patch['routePath']
            if 'trip' in patch and isinstance(patch['trip'], dict):
                payload.setdefault('trip', {}).update(patch['trip'])
        return jsonify({'status': 'ok'})

    with _package_shares_lock:
        payload = _package_shares.get(share_id)
    if not payload:
        return jsonify({'status': 'error', 'message': 'Share link expired or not found.'}), 404
    return jsonify({'status': 'ok', 'trip': payload})


@app.route('/trip/<share_id>/<path:subpath>')
def trip_share_nested_assets(share_id, subpath):
    """Serve images, scripts, CSV, etc. when the share page is opened at /trip/<id> (relative URLs)."""
    return send_from_directory('.', subpath)


@app.route('/trip/<share_id>')
def trip_share_page(share_id):
    return send_from_directory('.', 'packages.html')


@app.route('/api/package-share/<share_id>/qr.png')
def package_share_qr_png(share_id):
    with _package_shares_lock:
        payload = _package_shares.get(share_id)
    if not payload:
        return jsonify({'status': 'error', 'message': 'Share not found.'}), 404
    trip_url = _trip_share_url(share_id, payload=payload)
    try:
        import qrcode
        buf = io.BytesIO()
        qrcode.make(trip_url).save(buf, format='PNG')
        buf.seek(0)
        return send_file(buf, mimetype='image/png', max_age=300)
    except Exception:
        return redirect(
            'https://api.qrserver.com/v1/create-qr-code/?size=200x200&margin=8&data='
            + quote(trip_url, safe=''),
            code=302,
        )


# Authentication Routes
@app.route('/api/auth/register', methods=['POST'])
def register():
    try:
        data = request.json
        username = data.get('name')
        email = data.get('email', '').strip().lower()
        password = data.get('password')
        raw_phone = (data.get('phone') or '').strip()

        if not raw_phone:
            return jsonify({"status": "error", "message": "Phone number is required"}), 400

        phone = normalize_phone(raw_phone)
        if not phone:
            return jsonify({"status": "error", "message": "Enter a valid 10-digit Indian mobile number"}), 400

        if User.query.filter_by(email=email).first():
            return jsonify({"status": "error", "message": "Email already registered"}), 400

        existing_phone = User.query.filter_by(phone=phone).first()
        if existing_phone:
            return jsonify({"status": "error", "message": "Phone number already registered"}), 400

        otp = str(random.randint(100000, 999999))
        print(f"[DEBUG] Generated email OTP for {email}: {otp}")

        session['registration_data'] = {
            'username': username,
            'email': email,
            'password': password,
            'phone': phone,
            'step': 'email',
            'email_otp': otp,
            'email_otp_expiry': (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            'email_verified': False,
        }

        msg = Message('GeoTrip Planner - Verify Your Email', recipients=[email])
        msg.body = f"Hello {username},\n\nYour OTP for GeoTrip Planner registration is: {otp}\n\nThis code expires in 5 minutes."
        mail.send(msg)

        return jsonify({"status": "success", "step": "email", "message": "OTP sent to email"})
    except Exception as e:
        print(f"Registration Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/auth/verify_otp', methods=['POST'])
def verify_otp():
    """Step 1: verify email OTP, then trigger phone OTP via Twilio."""
    try:
        data = request.json
        user_otp = data.get('otp')
        reg_data = session.get('registration_data')

        if not reg_data:
            return jsonify({"status": "error", "message": "Session expired. Please register again."}), 400

        if reg_data.get('step') != 'email':
            return jsonify({"status": "error", "message": "Email already verified."}), 400

        expiry = datetime.fromisoformat(reg_data['email_otp_expiry'])
        if datetime.now(timezone.utc) > expiry:
            session.pop('registration_data', None)
            return jsonify({"status": "error", "message": "OTP expired. Please register again."}), 400

        if user_otp != reg_data['email_otp']:
            return jsonify({"status": "error", "message": "Invalid OTP code"}), 400

        reg_data['email_verified'] = True
        reg_data['step'] = 'phone'
        session['registration_data'] = reg_data

        phone = reg_data['phone']
        ok, err = _send_twilio_verify(phone)
        if not ok:
            reg_data['step'] = 'email'
            reg_data['email_verified'] = False
            session['registration_data'] = reg_data
            return jsonify({"status": "error", "message": f"Email verified but could not send SMS: {err}"}), 500

        masked = phone[:4] + '••••••' + phone[-2:]
        return jsonify({
            "status": "success",
            "step": "phone",
            "message": f"Email verified! OTP sent to {masked}",
            "maskedPhone": masked,
        })

    except Exception as e:
        print(f"Verification Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/auth/verify_phone_otp', methods=['POST'])
def verify_phone_otp():
    """Step 2: verify phone OTP via Twilio Verify, then create the user."""
    try:
        data = request.json
        user_otp = data.get('otp')
        reg_data = session.get('registration_data')

        if not reg_data or not reg_data.get('email_verified'):
            return jsonify({"status": "error", "message": "Session expired. Please register again."}), 400

        if reg_data.get('step') != 'phone':
            return jsonify({"status": "error", "message": "Complete email verification first."}), 400

        phone = reg_data['phone']
        approved, err = _check_twilio_verify(phone, user_otp)
        if not approved:
            return jsonify({"status": "error", "message": err or "Invalid OTP code"}), 400

        new_user = User(
            username=reg_data['username'],
            email=reg_data['email'].lower(),
            password_hash=generate_password_hash(reg_data['password']),
            phone=phone,
            is_verified=True,
            qr_token=generate_qr_token(),
        )
        db.session.add(new_user)
        db.session.commit()
        ensure_qr_token(new_user)

        session.pop('registration_data', None)
        print(f"[DEBUG] User {reg_data['email']} verified (email + phone) and created.")
        return jsonify({
            "status": "success",
            "message": "Phone verified and account created!",
            "user": user_public_dict(new_user),
        })

    except Exception as e:
        print(f"Phone Verification Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/auth/resend_otp', methods=['POST'])
def resend_otp():
    """Resend OTP for whichever step the user is on (email or phone)."""
    try:
        reg_data = session.get('registration_data')
        if not reg_data:
            return jsonify({"status": "error", "message": "Please register first"}), 400

        step = reg_data.get('step', 'email')

        if step == 'email':
            otp = str(random.randint(100000, 999999))
            reg_data['email_otp'] = otp
            reg_data['email_otp_expiry'] = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
            session['registration_data'] = reg_data

            msg = Message('GeoTrip Planner - Resend OTP', recipients=[reg_data['email']])
            msg.body = f"Your new OTP is: {otp}"
            mail.send(msg)
            return jsonify({"status": "success", "step": "email", "message": "New email OTP sent"})

        if step == 'phone':
            phone = reg_data['phone']
            ok, err = _send_twilio_verify(phone)
            if not ok:
                return jsonify({"status": "error", "message": f"Could not resend SMS: {err}"}), 500
            masked = phone[:4] + '••••••' + phone[-2:]
            return jsonify({"status": "success", "step": "phone", "message": f"New OTP sent to {masked}"})

        return jsonify({"status": "error", "message": "Invalid registration step"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    try:
        data = request.json
        email = data.get('email', '').strip().lower()
        password = data.get('password')

        user = User.query.filter_by(email=email).first()
        if not user:
            print(f"[DEBUG] Login failed: Email '{email}' not found in database.")
            return jsonify({"status": "error", "message": "Email not found"}), 401

        if check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            session.permanent = True  # Ensure session persists
            print(f"[DEBUG] Login successful for: {email}")
            client_origin = data.get('clientOrigin')
            return jsonify({
                "status": "success",
                "user": user_public_dict(user, client_origin=client_origin),
            })
        else:
            print(f"[DEBUG] Login failed: Invalid password for '{email}'.")
            return jsonify({"status": "error", "message": "Incorrect password"}), 401
    except Exception as e:
        print(f"Login Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"status": "success", "message": "Logged out successfully"})


@app.route('/api/auth/me', methods=['GET'])
def auth_me():
    uid = session.get('user_id')
    if not uid:
        return jsonify({"status": "error", "message": "Not logged in"}), 401
    user = db.session.get(User, uid)
    if not user:
        session.clear()
        return jsonify({"status": "error", "message": "User not found"}), 404
    client_origin = request.args.get('clientOrigin')
    return jsonify({"status": "success", "user": user_public_dict(user, client_origin=client_origin)})


# --- Temple QR visit flow ---
@app.route('/user/<identifier>/<path:subpath>')
def user_scan_nested_assets(identifier, subpath):
    """Serve images/scripts when staff opens /user/<token> (relative URLs on main_page)."""
    if not is_valid_qr_token(identifier):
        return (
            '<!DOCTYPE html><html><body style="font-family:sans-serif;padding:2rem;text-align:center">'
            '<h1>Invalid QR link</h1><p>This personal temple QR link is not valid.</p></body></html>',
            400,
            {'Content-Type': 'text/html; charset=utf-8'},
        )
    return send_from_directory('.', subpath)


@app.route('/user/<identifier>')
def user_scan_page(identifier):
    if not is_valid_qr_token(identifier):
        return (
            '<!DOCTYPE html><html><body style="font-family:sans-serif;padding:2rem;text-align:center">'
            '<h1>Invalid QR link</h1><p>This personal temple QR link is not valid.</p></body></html>',
            400,
            {'Content-Type': 'text/html; charset=utf-8'},
        )
    return send_from_directory('.', 'main_page.html')


@app.route('/api/qr/validate/<token>', methods=['GET'])
def validate_qr_token(token):
    if not is_valid_qr_token(token):
        return jsonify({'status': 'error', 'valid': False, 'message': 'Invalid QR token format.'}), 400
    user = resolve_user(token)
    if not user:
        return jsonify({'status': 'error', 'valid': False, 'message': 'QR token not recognized.'}), 404
    return jsonify({
        'status': 'ok',
        'valid': True,
        'user': {'name': user.username, 'phone': user.phone or ''},
    })


@app.route('/api/qr/generate', methods=['POST'])
def generate_user_qr():
    """Ensure logged-in user has a unique QR token (created once)."""
    uid = session.get('user_id')
    if not uid:
        return jsonify({'status': 'error', 'message': 'Not logged in'}), 401
    user = db.session.get(User, uid)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    client_origin = (request.get_json(silent=True) or {}).get('clientOrigin')
    token = ensure_qr_token(user)
    payload = user_public_dict(user, client_origin=client_origin)
    return jsonify({
        'status': 'ok',
        'created': True,
        'qrToken': token,
        'qrUrl': payload['qrUrl'],
        'user': payload,
    })


@app.route('/api/scan-user/<identifier>', methods=['GET'])
def scan_user_info(identifier):
    if not is_valid_qr_token(identifier):
        return jsonify({'status': 'error', 'message': 'Invalid QR token.'}), 400
    user = resolve_user(identifier)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found.'}), 404
    return jsonify({
        'status': 'ok',
        'user': {
            'name': user.username,
            'phone': user.phone or '',
        },
    })


@app.route('/api/temples', methods=['GET'])
def list_temples():
    q = (request.args.get('q') or '').strip()
    query = Temple.query
    if q:
        like = f'%{q}%'
        query = query.filter(
            db.or_(
                Temple.temple_name.ilike(like),
                Temple.full_name.ilike(like),
                Temple.csv_name.ilike(like),
                Temple.location.ilike(like),
            )
        )
    temples = query.order_by(Temple.temple_name.asc()).all()
    return jsonify({
        'status': 'ok',
        'count': len(temples),
        'temples': [
            {
                'id': t.id,
                'templeName': t.temple_name,
                'fullName': t.full_name,
                'location': t.location or '',
                'lat': t.lat,
                'lng': t.lng,
            }
            for t in temples
        ],
    })


@app.route('/api/temple-visits', methods=['POST'])
def record_temple_visit():
    data = request.get_json(silent=True) or {}
    temple_id = data.get('templeId')
    if not temple_id:
        return jsonify({'status': 'error', 'message': 'Temple is required.'}), 400

    user = None
    user_token = (data.get('userToken') or '').strip()
    if user_token:
        if not is_valid_qr_token(user_token):
            return jsonify({'status': 'error', 'message': 'Invalid QR token.'}), 400
        user = resolve_user(user_token)
    elif session.get('user_id'):
        user = db.session.get(User, session['user_id'])

    if not user:
        return jsonify({'status': 'error', 'message': 'Valid user token or login required.'}), 401

    result, err_payload, code = _record_visit_for_user(user, temple_id)
    if err_payload:
        body = {'status': 'error'}
        if isinstance(err_payload, dict):
            body.update(err_payload)
        else:
            body['message'] = err_payload
        return jsonify(body), code
    return jsonify(result)


@app.route('/api/temple-visits/history', methods=['GET'])
def temple_visit_history():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'status': 'error', 'message': 'Not logged in'}), 401
    limit = min(int(request.args.get('limit', 30)), 100)
    rows = (
        TempleVisit.query.filter_by(user_id=uid)
        .order_by(TempleVisit.visit_date.desc(), TempleVisit.updated_at.desc())
        .limit(limit)
        .all()
    )
    return jsonify({
        'status': 'ok',
        'history': [
            {
                'id': v.id,
                'templeId': v.temple_id,
                'templeName': v.temple.temple_name if v.temple else '',
                'fullName': v.temple.full_name if v.temple else '',
                'visitDate': v.visit_date.isoformat(),
                'visitCount': v.visit_count,
                'createdAt': v.created_at.isoformat() if v.created_at else None,
            }
            for v in rows
        ],
    })


@app.route('/api/temple-visits/visited-temples', methods=['GET'])
def visited_temples_list():
    """Distinct temples this user has checked into (aggregated)."""
    uid = session.get('user_id')
    if not uid:
        return jsonify({'status': 'error', 'message': 'Not logged in'}), 401

    rows = (
        TempleVisit.query.filter_by(user_id=uid)
        .order_by(TempleVisit.visit_date.desc())
        .all()
    )
    by_temple = {}
    for v in rows:
        tid = v.temple_id
        if tid not in by_temple:
            by_temple[tid] = {
                'templeId': tid,
                'templeName': v.temple.temple_name if v.temple else '',
                'fullName': v.temple.full_name if v.temple else '',
                'location': (v.temple.location or '') if v.temple else '',
                'totalVisits': 0,
                'visitDays': 0,
                'lastVisitDate': v.visit_date.isoformat(),
            }
        entry = by_temple[tid]
        entry['totalVisits'] += v.visit_count or 0
        entry['visitDays'] += 1
        if v.visit_date.isoformat() > entry['lastVisitDate']:
            entry['lastVisitDate'] = v.visit_date.isoformat()

    temples = sorted(by_temple.values(), key=lambda x: x['lastVisitDate'], reverse=True)
    return jsonify({
        'status': 'ok',
        'count': len(temples),
        'visitedTemples': temples,
    })


@app.route('/api/temple-visits/analytics', methods=['GET'])
def temple_visit_analytics():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'status': 'error', 'message': 'Not logged in'}), 401
    today = date.today()
    today_rows = TempleVisit.query.filter_by(user_id=uid, visit_date=today).all()
    all_rows = TempleVisit.query.filter_by(user_id=uid).all()

    visits_today_total = sum((r.visit_count or 0) for r in today_rows)
    temples_today = len(today_rows)
    lifetime_visits = sum((r.visit_count or 0) for r in all_rows)

    by_temple_today = [
        {
            'templeId': r.temple_id,
            'templeName': r.temple.temple_name if r.temple else '',
            'visitCount': r.visit_count,
        }
        for r in today_rows
    ]

    return jsonify({
        'status': 'ok',
        'date': today.isoformat(),
        'visitsTodayTotal': visits_today_total,
        'templesVisitedToday': temples_today,
        'lifetimeVisitCount': lifetime_visits,
        'todayByTemple': by_temple_today,
    })


@app.route('/api/user/me/qr.png')
def my_user_qr_png():
    uid = session.get('user_id')
    if not uid:
        return jsonify({'status': 'error', 'message': 'Not logged in'}), 401
    user = db.session.get(User, uid)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    return _user_qr_png_response(user)


@app.route('/api/user/<identifier>/qr.png')
def user_qr_png(identifier):
    ident = str(identifier).strip().upper()
    user = None
    if is_valid_qr_token(ident):
        user = resolve_user(ident)
    elif session.get('user_id') and str(session['user_id']) == str(identifier):
        user = db.session.get(User, session['user_id'])
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    return _user_qr_png_response(user)


def _user_qr_png_response(user):
    client_origin = request.args.get('clientOrigin')
    scan_url = user_public_dict(user, client_origin=client_origin)['qrUrl']
    try:
        import qrcode
        buf = io.BytesIO()
        qrcode.make(scan_url).save(buf, format='PNG')
        buf.seek(0)
        return send_file(buf, mimetype='image/png', max_age=120)
    except Exception:
        return redirect(
            'https://api.qrserver.com/v1/create-qr-code/?size=220x220&margin=8&data='
            + quote(scan_url, safe=''),
            code=302,
        )

@app.route('/api/auth/forgot_password', methods=['POST'])
def forgot_password():
    try:
        data = request.json
        email = data.get('email', '').strip().lower()
        user = User.query.filter_by(email=email).first()
        if not user:
            return jsonify({"status": "error", "message": "Email address not registered"}), 404

        otp = str(random.randint(100000, 999999))
        session['reset_data'] = {
            'email': email,
            'otp': otp,
            'expiry': (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        }

        print(f"[DEBUG] Password reset OTP for {email}: {otp}")

        msg = Message('GeoTrip Planner - Password Reset Verification', recipients=[email])
        msg.body = f"Hello {user.username},\n\nYou requested a password reset for your GeoTrip Planner account.\nYour reset OTP is: {otp}\n\nThis code expires in 5 minutes. If you did not request this reset, please ignore this email."
        mail.send(msg)

        return jsonify({"status": "success", "message": "Reset OTP sent to your email"})
    except Exception as e:
        print(f"Forgot Password Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/auth/reset_password', methods=['POST'])
def reset_password():
    try:
        data = request.json
        email = data.get('email', '').strip().lower()
        user_otp = data.get('otp')
        new_password = data.get('new_password')

        reset_data = session.get('reset_data')
        if not reset_data or reset_data['email'] != email:
            return jsonify({"status": "error", "message": "Reset session expired or invalid. Please request OTP again."}), 400

        expiry = datetime.fromisoformat(reset_data['expiry'])
        if datetime.now(timezone.utc) > expiry:
            session.pop('reset_data', None)
            return jsonify({"status": "error", "message": "OTP expired"}), 400

        if user_otp == reset_data['otp']:
            user = User.query.filter_by(email=email).first()
            if not user:
                return jsonify({"status": "error", "message": "User not found"}), 404

            user.password_hash = generate_password_hash(new_password)
            db.session.commit()
            session.pop('reset_data', None)
            print(f"[DEBUG] Password successfully reset for user: {email}")
            return jsonify({"status": "success", "message": "Password updated successfully! You can now login."})
        else:
            return jsonify({"status": "error", "message": "Invalid OTP code"}), 400
    except Exception as e:
        print(f"Reset Password Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/weather')
def get_weather():
    city = request.args.get('city', 'Tirupati')
    url = f"http://api.weatherapi.com/v1/current.json?key={WEATHER_API_KEY}&q={city}"
    try:
        response = requests.get(url)
        data = response.json()
        if response.status_code != 200:
            return jsonify({"error": "Failed to fetch weather"}), response.status_code
        return jsonify({
            "city": data["location"]["name"],
            "temperature": data["current"]["temp_c"],
            "condition": data["current"]["condition"]["text"],
            "icon": data["current"]["condition"]["icon"],
            "humidity": data["current"]["humidity"],
            "wind": data["current"]["wind_kph"]
        })
    except: return jsonify({"error": "Weather error"}), 500

@app.route('/api/recommend', methods=['POST'])
def recommend():
    try:
        data = request.json
        destination = data.get('destination', 'Tirupati')
        budget = data.get('budget', 'medium')
        interests = data.get('interests', 'temples, nature')
        
        prompt = f"Create a 3-day itinerary for {destination}. Budget: {budget}. Interests: {interests}. Return the itinerary ONLY as a JSON array of daily objects, each containing 'day', 'activities' (array of strings), and 'tips'."
        
        if not client:
            return jsonify({"status": "error", "message": "Gemini API key not configured"}), 500
            
        response = client.models.generate_content(model='gemini-1.5-flash', contents=prompt)
        clean_text = clean_json_from_llm(response.text)
        return jsonify({"status": "success", "recommendation": json.loads(clean_text), "provider": "gemini"})
    except Exception as e:
        print(f"Recommend Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/recommend-ollama', methods=['POST'])
def recommend_ollama():
    try:
        data = request.json or {}
        destination = data.get('destination', 'Tirupati')
        budget = data.get('budget', 'medium')
        interests = data.get('interests', 'temples, nature')

        prompt = (
            f"Create a 3-day itinerary for {destination}. Budget: {budget}. Interests: {interests}. "
            "Return the itinerary ONLY as a JSON array of daily objects, each containing "
            "'day', 'activities' (array of strings), and 'tips'. No markdown, no extra text."
        )

        raw = ask_ollama(prompt)
        clean_text = clean_json_from_llm(raw)
        return jsonify({
            "status": "success",
            "recommendation": json.loads(clean_text),
            "provider": "ollama",
            "model": OLLAMA_MODEL,
        })
    except requests.exceptions.ConnectionError:
        return jsonify({
            "status": "error",
            "message": "Cannot reach Ollama. Start Ollama (ollama serve) and ensure the model is pulled.",
        }), 503
    except requests.exceptions.HTTPError as e:
        print(f"Ollama HTTP Error: {e}")
        return jsonify({"status": "error", "message": f"Ollama error: {e}"}), 502
    except json.JSONDecodeError as e:
        print(f"Ollama JSON parse Error: {e}")
        return jsonify({"status": "error", "message": "Ollama returned invalid JSON. Try again."}), 500
    except Exception as e:
        print(f"Recommend Ollama Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/chat', methods=['POST'])
def api_chat():
    try:
        data = request.json or {}
        message = (data.get('message') or '').strip()
        if not message:
            return jsonify({"status": "error", "message": "Message is required"}), 400

        system_prompt = (
            "You are GeoTrip Assistant for Tirupati and Tirumala trip planning in India. "
            "Help users with temples, darshan timing, local food, transport, budgets, and itinerary tips. "
            "Keep answers concise, practical, and friendly. If unsure, suggest using the app's planner or booking pages."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ]
        reply = chat_ollama(messages)
        return jsonify({
            "status": "success",
            "reply": reply,
            "provider": "ollama",
            "model": OLLAMA_MODEL,
        })
    except requests.exceptions.ConnectionError:
        return jsonify({
            "status": "error",
            "message": "Cannot reach Ollama. Start Ollama and verify OLLAMA_BASE_URL in .env.",
        }), 503
    except requests.exceptions.HTTPError as e:
        print(f"Ollama Chat HTTP Error: {e}")
        return jsonify({"status": "error", "message": f"Ollama error: {e}"}), 502
    except Exception as e:
        print(f"Chat Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == '__main__':
    # 0.0.0.0 so browser works without Cursor port-forward; use http://127.0.0.1:5000
    app.run(debug=True, host='0.0.0.0', port=5000)
