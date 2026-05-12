import os
import json
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from google import genai
from dotenv import load_dotenv

load_dotenv()

# Set static_folder='.' to let Flask serve HTML, JS, CSS, and CSV files from the current directory
app = Flask(__name__, static_url_path='', static_folder='.')
CORS(app)

# Configure the new google-genai client
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key) if api_key else None

@app.route('/')
def home():
    # Serve main_page.html when someone visits http://127.0.0.1:5000/
    return send_from_directory('.', 'main_page.html')

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
