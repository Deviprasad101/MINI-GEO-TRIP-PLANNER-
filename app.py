import os
import json
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from google import genai
from dotenv import load_dotenv

load_dotenv()

# Set static_folder='.' to let Flask serve HTML, JS, CSS, and CSV files from the current directory
app = Flask(__name__, static_url_path='', static_folder='.')
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
CORS(app)

# Disable caching for development to avoid 304 status codes
@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers['Cache-Control'] = 'public, max-age=0'
    return response

# Configure the new google-genai client
api_key = os.getenv("GEMINI_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
client = genai.Client(api_key=api_key) if api_key else None

@app.route('/')
def home():
    # Serve login.html as the first page
    return send_from_directory('.', 'login.html')

@app.route('/dashboard')
def dashboard():
    # Serve main_page.html after login
    return send_from_directory('.', 'main_page.html')

@app.route('/weather')
def get_weather():
    city = request.args.get('city', 'Tirupati')
    url = f"http://api.weatherapi.com/v1/current.json?key={WEATHER_API_KEY}&q={city}"
    try:
        response = requests.get(url)
        data = response.json()
        if response.status_code != 200:
            return jsonify({"error": data.get("error", {}).get("message", "Failed to fetch weather data")}), response.status_code
            
        weather_data = {
            "city": data["location"]["name"],
            "country": data["location"]["country"],
            "temperature": data["current"]["temp_c"],
            "condition": data["current"]["condition"]["text"],
            "icon": data["current"]["condition"]["icon"],
            "humidity": data["current"]["humidity"],
            "wind": data["current"]["wind_kph"]
        }
        return jsonify(weather_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/recommend', methods=['POST'])
def recommend():
    try:
        data = request.json
        destination = data.get('destination', 'Tirupati')
        budget = data.get('budget', '5000')
        interests = data.get('interests', 'temples, nature')

        prompt = f"Create a 3-day itinerary for {destination}. The user is interested in {interests}, has a budget of Rs. {budget}. Return the response as a JSON array of daily activities. Each item in the array should have 'day' (e.g., 'Day 1'), and 'activities' (a list of strings). Only output the valid JSON array, do not wrap in markdown tags."
        
        if not client:
            print("[ERROR] API key not found")
            return jsonify({"status": "error", "message": "API key not found."}), 500

        # Use the new package structure
        print(f"[DEBUG] Requesting Gemini with model: gemini-2.5-flash")
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        
        text = response.text.strip()
        if text.startswith('```json'):
            text = text[7:]
        if text.startswith('```'):
            text = text[3:]
        if text.endswith('```'):
            text = text[:-3]
            
        return jsonify({"status": "success", "recommendation": json.loads(text.strip())})
    except Exception as e:
        print(f"[ERROR] Exception in /api/recommend: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
    # Trigger auto-reload
