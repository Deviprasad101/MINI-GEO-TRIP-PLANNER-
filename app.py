import os
import json
import requests
import random
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, send_from_directory, session
from flask_cors import CORS
from flask_mail import Mail, Message
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from google import genai
from dotenv import load_dotenv

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
    password_hash = db.Column(db.String(255), nullable=False)
    is_verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __init__(self, username, email, password_hash, is_verified=False):
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.is_verified = is_verified

# Create tables
with app.app_context():
    db.create_all()

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

# Authentication Routes
@app.route('/api/auth/register', methods=['POST'])
def register():
    try:
        data = request.json
        username = data.get('name')
        email = data.get('email', '').strip().lower()
        password = data.get('password')

        if User.query.filter_by(email=email).first():
            return jsonify({"status": "error", "message": "Email already registered"}), 400

        # Generate OTP
        otp = str(random.randint(100000, 999999))
        print(f"[DEBUG] Generated OTP for {email}: {otp}")
        
        # Store in session
        session['registration_data'] = {
            'username': username,
            'email': email,
            'password': password,
            'otp': otp,
            'otp_expiry': (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        }

        # Send Email
        msg = Message('GeoTrip Planner - Verify Your Email', recipients=[email])
        msg.body = f"Hello {username},\n\nYour OTP for GeoTrip Planner registration is: {otp}\n\nThis code expires in 5 minutes."
        mail.send(msg)

        return jsonify({"status": "success", "message": "OTP sent to email"})
    except Exception as e:
        print(f"Registration Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/auth/verify_otp', methods=['POST'])
def verify_otp():
    try:
        data = request.json
        user_otp = data.get('otp')
        reg_data = session.get('registration_data')

        if not reg_data:
            return jsonify({"status": "error", "message": "Session expired. Please register again."}), 400

        # Check expiry
        expiry = datetime.fromisoformat(reg_data['otp_expiry'])
        if datetime.now(timezone.utc) > expiry:
            session.pop('registration_data', None)
            return jsonify({"status": "error", "message": "OTP expired"}), 400

        if user_otp == reg_data['otp']:
            # Create user
            new_user = User(
                username=reg_data['username'],
                email=reg_data['email'].lower(),
                password_hash=generate_password_hash(reg_data['password']),
                is_verified=True
            )
            db.session.add(new_user)
            db.session.commit()
            
            session.pop('registration_data', None)
            print(f"[DEBUG] User {reg_data['email']} successfully verified and created.")
            return jsonify({"status": "success", "message": "Email verified and account created!"})
        else:
            return jsonify({"status": "error", "message": "Invalid OTP code"}), 400

    except Exception as e:
        print(f"Verification Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/auth/resend_otp', methods=['POST'])
def resend_otp():
    try:
        reg_data = session.get('registration_data')
        if not reg_data:
            return jsonify({"status": "error", "message": "Please register first"}), 400

        otp = str(random.randint(100000, 999999))
        reg_data['otp'] = otp
        reg_data['otp_expiry'] = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        session['registration_data'] = reg_data

        msg = Message('GeoTrip Planner - Resend OTP', recipients=[reg_data['email']])
        msg.body = f"Your new OTP is: {otp}"
        mail.send(msg)

        return jsonify({"status": "success", "message": "New OTP sent"})
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
            return jsonify({
                "status": "success", 
                "user": {"name": user.username, "email": user.email}
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
    app.run(debug=True, port=5000)
