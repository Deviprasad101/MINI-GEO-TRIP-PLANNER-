let isLiveLocation = false;
let currentCoords = null;

async function useLiveLocation() {
    const cityInput = document.getElementById("cityInput");
    const liveLocBtn = document.getElementById("liveLocBtn");

    if (isLiveLocation) {
        // Toggle off: re-enable manual entry
        isLiveLocation = false;
        currentCoords = null;
        cityInput.value = "";
        cityInput.disabled = false;
        cityInput.placeholder = "Enter city (e.g. Tirupati)";
        cityInput.classList.remove("bg-slate-100", "cursor-not-allowed", "opacity-70");
        liveLocBtn.classList.remove("bg-blue-600", "text-white");
        liveLocBtn.classList.add("bg-blue-100", "text-blue-700");
        return;
    }

    if (!navigator.geolocation) {
        alert("Geolocation is not supported by your browser");
        return;
    }

    liveLocBtn.innerHTML = `<span class="material-symbols-outlined animate-spin">sync</span>`;
    
    navigator.geolocation.getCurrentPosition(
        async (position) => {
            isLiveLocation = true;
            currentCoords = {
                lat: position.coords.latitude,
                lon: position.coords.longitude
            };

            cityInput.value = "📍 Using Current Location";
            cityInput.disabled = true;
            // Add visual feedback for "Disabled" state
            cityInput.classList.add("bg-slate-100", "cursor-not-allowed", "opacity-70");
            
            liveLocBtn.innerHTML = `<span class="material-symbols-outlined">my_location</span>`;
            liveLocBtn.classList.remove("bg-blue-100", "text-blue-700");
            liveLocBtn.classList.add("bg-blue-600", "text-white");

            // Automatically fetch weather for live location
            getWeather();
        },
        (error) => {
            console.error("Geolocation error:", error);
            alert("Unable to retrieve your location. Please check permissions.");
            liveLocBtn.innerHTML = `<span class="material-symbols-outlined">my_location</span>`;
        }
    );
}

async function getWeather() {
    const cityInput = document.getElementById("cityInput");
    const weatherResult = document.getElementById("weatherResult");
    
    let query;
    if (isLiveLocation && currentCoords) {
        query = `${currentCoords.lat},${currentCoords.lon}`;
    } else {
        query = cityInput.value.trim() || "Tirupati";
    }

    // Show loading state
    weatherResult.innerHTML = `
        <div class="flex items-center justify-center py-8">
            <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-amber-600"></div>
        </div>
    `;

    try {
        const response = await fetch(`/weather?city=${encodeURIComponent(query)}`);
        const data = await response.json();

        if (data.error) {
            weatherResult.innerHTML = `
                <div class="p-4 bg-red-50 border border-red-200 rounded-xl text-red-700 text-center">
                    <span class="material-symbols-outlined block mb-1">error</span>
                    ${data.error}
                </div>
            `;
            return;
        }

        weatherResult.innerHTML = `
            <div class="weather-card bg-white/80 backdrop-blur-sm p-6 rounded-2xl border border-amber-100 shadow-sm transition-all hover:shadow-md">
                <div class="flex items-center justify-between mb-4">
                    <div>
                        <h3 class="text-2xl font-bold text-slate-800">${data.city}, ${data.country}</h3>
                        <p class="text-slate-500 font-medium">${data.condition}</p>
                    </div>
                    <div class="text-right">
                        <img src="${data.icon}" alt="Weather Icon" class="w-16 h-16 -my-2">
                    </div>
                </div>
                
                <div class="grid grid-cols-2 gap-3 sm:grid-cols-3">
                    <div class="bg-amber-50/50 p-3 rounded-xl border border-amber-100/50">
                        <span class="text-[10px] font-bold text-amber-700 uppercase tracking-wider block mb-1">Temp</span>
                        <div class="text-xl font-black text-slate-800">${Math.round(data.temperature)}°C</div>
                    </div>
                    <div class="bg-blue-50/50 p-3 rounded-xl border border-blue-100/50">
                        <span class="text-[10px] font-bold text-blue-700 uppercase tracking-wider block mb-1">Humidity</span>
                        <div class="text-xl font-black text-slate-800">${data.humidity}%</div>
                    </div>
                    <div class="bg-emerald-50/50 p-3 rounded-xl border border-emerald-100/50 col-span-2 sm:col-span-1">
                        <span class="text-[10px] font-bold text-emerald-700 uppercase tracking-wider block mb-1">Wind</span>
                        <div class="text-xl font-black text-slate-800">${data.wind} <span class="text-xs font-normal">kph</span></div>
                    </div>
                </div>
            </div>
        `;
    } catch (error) {
        console.error("Weather fetch error:", error);
        weatherResult.innerHTML = `
            <div class="p-4 bg-red-50 border border-red-200 rounded-xl text-red-700 text-center">
                <span class="material-symbols-outlined block mb-1">wifi_off</span>
                Connection error. Please try again.
            </div>
        `;
    }
}
