import os

from google.adk.agents.llm_agent import Agent

from .tools import (
    find_nearest_hospitals,
    generate_itinerary,
    get_app_data_catalog,
    get_trip_packages,
    get_user_qr_info,
    get_weather,
    list_temples,
    lookup_place,
    search_places,
)


def build_root_agent(model: str) -> Agent:
    return Agent(
        model=model,
        name='geotrip_agent',
        description='GeoTrip Planner assistant for Tirupati and Tirumala trip planning.',
        instruction=(
            'You are GeoTrip Assistant for pilgrims and tourists visiting Tirupati, India. '
            'The user message may include USER QUESTION, USER LOCATION (lat/lng), and TOOL DATA. '
            'Always answer the USER QUESTION directly — do not ask the user for location if lat/lng is provided. '
            'Use tools when TOOL DATA is missing or incomplete. '
            'For temple timings, opening hours, or a specific place: call lookup_place or list_temples. '
            'For food/temple lists: call search_places. '
            'For trip packages and budget tiers: call get_trip_packages. '
            'For overview of app data: call get_app_data_catalog. '
            'For weather: call get_weather. '
            'For hospitals/emergency: call find_nearest_hospitals with the provided lat/lng. '
            'For trip plans: call generate_itinerary. '
            'For QR check-in: call get_user_qr_info. '
            'Never invent place or hospital names — only use tool/TOOL DATA results. '
            'Keep answers concise, specific, and tied to what the user asked.'
        ),
        tools=[
            lookup_place,
            list_temples,
            search_places,
            get_trip_packages,
            get_app_data_catalog,
            get_weather,
            generate_itinerary,
            find_nearest_hospitals,
            get_user_qr_info,
        ],
    )


root_agent = build_root_agent(os.getenv('GEMINI_MODEL', 'gemini-2.0-flash'))
