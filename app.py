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

@app.route('/')
def home():
    return send_from_directory('.', 'login.html')

@app.route('/dashboard')
def dashboard():
    return send_from_directory('.', 'main_page.html')

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
            print(f"[DEBUG] Login failed: User {email} not found in database.")
            return jsonify({"status": "error", "message": "Email not found"}), 401

        if check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            print(f"[DEBUG] Login successful for {email}")
            return jsonify({
                "status": "success", 
                "user": {"name": user.username, "email": user.email}
            })
        else:
            print(f"[DEBUG] Login failed: Invalid password for {email}.")
            return jsonify({"status": "error", "message": "Invalid password"}), 401
    except Exception as e:
        print(f"Login Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"status": "success", "message": "Logged out successfully"})

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
        
        # Clean response text in case of markdown blocks
        clean_text = response.text.strip()
        if clean_text.startswith('```'):
            clean_text = clean_text.split('\n', 1)[1].rsplit('\n', 1)[0]
            if clean_text.startswith('json'):
                clean_text = clean_text[4:].strip()
                
        return jsonify({"status": "success", "recommendation": json.loads(clean_text)})
    except Exception as e:
        print(f"Recommend Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
