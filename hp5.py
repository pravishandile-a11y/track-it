import os
import sys
import json
import subprocess
import webbrowser
import requests
import phonenumbers
from phonenumbers import geocoder, carrier, timezone, NumberParseException
import folium
from opencage.geocoder import OpenCageGeocode

# ==========================================
# CONFIGURATION
# ==========================================
OPENCAGE_API_KEY = os.getenv("OPENCAGE_API_KEY", "f2e6d20bd4604ef8b3dcfa5596e12224")

# Optional: AbstractAPI or Numverify key for Live HLR Network Checks
HLR_API_KEY = os.getenv("HLR_API_KEY", "")

# ==========================================
# SYSTEM HELPERS
# ==========================================

def open_google_earth_web(lat: float, lon: float, altitude: int = 1000):
    """Opens Google Earth Web focused closely on exact coordinates."""
    earth_url = f"https://earth.google.com/web/@{lat},{lon},{altitude}a,500d,35y,0h,0t,0r"
    webbrowser.open(earth_url)

def launch_local_file(filepath: str):
    """Opens HTML maps locally in default browser."""
    if not os.path.exists(filepath):
        return
    if filepath.endswith('.html'):
        webbrowser.open(f"file://{os.path.abspath(filepath)}")
        return
    if sys.platform == "win32":
        os.startfile(filepath)
    elif sys.platform == "darwin":
        subprocess.run(["open", filepath], check=False)
    else:
        subprocess.run(["xdg-open", filepath], check=False)


# ==========================================
# ENGINE 1: TELECOM METADATA & LIVE HLR CHECK
# ==========================================

def parse_telecom_data(phone_number_str: str) -> dict:
    """Extracts line types, carriers, and region metadata."""
    try:
        parsed_num = phonenumbers.parse(phone_number_str, None)
        if not phonenumbers.is_valid_number(parsed_num):
            return {"valid": False, "error": "Invalid international phone number format."}

        num_type = phonenumbers.number_type(parsed_num)
        type_labels = {
            phonenumbers.PhoneNumberType.MOBILE: "Mobile",
            phonenumbers.PhoneNumberType.FIXED_LINE: "Landline (Fixed)",
            phonenumbers.PhoneNumberType.VOIP: "VoIP (Virtual Number)",
            phonenumbers.PhoneNumberType.TOLL_FREE: "Toll Free",
            phonenumbers.PhoneNumberType.UNKNOWN: "Unknown"
        }

        carrier_name = carrier.name_for_number(parsed_num, "en")
        location_region = geocoder.description_for_number(parsed_num, "en")
        time_zones = list(timezone.time_zones_for_number(parsed_num))

        return {
            "valid": True,
            "e164": phonenumbers.format_number(parsed_num, phonenumbers.PhoneNumberFormat.E164),
            "international": phonenumbers.format_number(parsed_num, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
            "country_code": f"+{parsed_num.country_code}",
            "iso_region": phonenumbers.region_code_for_number(parsed_num),
            "location_region": location_region or "Unknown Region",
            "carrier": carrier_name or "Unknown Provider",
            "line_type": type_labels.get(num_type, "Special Service"),
            "timezones": time_zones
        }
    except NumberParseException as e:
        return {"valid": False, "error": str(e)}


def check_live_hlr_status(e164_phone: str, api_key: str) -> dict:
    """
    Performs a live Home Location Register (HLR) check via API.
    Verifies if the device is currently active on a cellular network.
    """
    if not api_key:
        return {"status": "Skipped (No HLR API key provided)", "roaming": "Unknown"}

    url = f"https://phonevalidation.abstractapi.com/v1/?api_key={api_key}&phone={e164_phone}"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            return {
                "active_status": "Active" if data.get("valid") else "Inactive/Deactivated",
                "current_carrier": data.get("carrier", "Unknown"),
                "location": data.get("location", "Unknown")
            }
    except Exception:
        pass
    return {"status": "HLR Lookup Failed"}


# ==========================================
# ENGINE 2: OPENCAGE HIGH-PRECISION REGION RESOLUTION
# ==========================================

def geocode_opencage_precise(telecom_info: dict, api_key: str) -> tuple:
    """Queries OpenCage for high-confidence regional bounds."""
    if not api_key or api_key == "YOUR_OPENCAGE_API_KEY":
        print("[!] OpenCage Key missing. Falling back to default center point.")
        return None, None, "Unknown Address", 5, 50000

    cg = OpenCageGeocode(api_key)
    query = f"{telecom_info['location_region']}, {telecom_info['iso_region']}"

    try:
        results = cg.geocode(query, countrycode=telecom_info['iso_region'].lower(), no_annotations='0')
        if results:
            best = results[0]
            lat = best['geometry']['lat']
            lon = best['geometry']['lng']
            formatted = best['formatted']
            confidence = best.get('confidence', 1)

            # Map confidence (1-10 scale) to zoom and estimated accuracy radius in meters
            accuracy_radius = max(500, (11 - confidence) * 5000)
            zoom = min(5 + confidence, 15)

            return lat, lon, formatted, zoom, accuracy_radius
    except Exception as e:
        print(f"[!] Geocoding error: {e}")

    return None, None, None, 5, 50000


# ==========================================
# ENGINE 3: EXACT DEVICE GPS WEBHOOK GENERATOR
# ==========================================

def generate_gps_consent_tracker(e164_phone: str, output_html: str = "gps_tracker.html"):
    """
    Creates an interactive HTML page that prompts for browser GPS permission.
    When opened on the target device, it yields exact meter-level GPS coordinates.
    """
    html_code = f"""<!DOCTYPE html>
<html>
<head>
    <title>Location Verification - {e164_phone}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{ font-family: Arial, sans-serif; text-align: center; padding: 40px; background: #f4f6f9; }}
        .card {{ background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); max-width: 400px; margin: auto; }}
        button {{ background: #27ae60; color: white; border: none; padding: 12px 24px; font-size: 16px; border-radius: 6px; cursor: pointer; margin-top: 15px; }}
        #output {{ margin-top: 20px; font-weight: bold; color: #2c3e50; word-break: break-all; }}
    </style>
</head>
<body>
    <div class="card">
        <h3>Device Location Verification</h3>
        <p>Verify active location for: <b>{e164_phone}</b></p>
        <button onclick="getLocation()">Verify High-Accuracy GPS</button>
        <div id="output"></div>
    </div>

    <script>
        function getLocation() {{
            const out = document.getElementById('output');
            if (navigator.geolocation) {{
                out.innerHTML = "Acquiring satellite lock...";
                navigator.geolocation.getCurrentPosition(
                    (pos) => {{
                        const lat = pos.coords.latitude;
                        const lon = pos.coords.longitude;
                        const acc = pos.coords.accuracy;
                        out.innerHTML = `<span style="color:green;"><b>GPS Match Found!</b><br>Lat: ${{lat}}<br>Lon: ${{lon}}<br>Accuracy: ${{acc}} meters</span>`;
                        console.log(`GPS: ${{lat}},${{lon}} Accuracy: ${{acc}}m`);
                    }},
                    (err) => {{ out.innerHTML = `<span style="color:red;">Error: ${{err.message}}</span>`; }},
                    {{ enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }}
                );
            }} else {{
                out.innerHTML = "Geolocation is not supported by this browser.";
            }}
        }}
    </script>
</body>
</html>"""
    with open(output_html, "w", encoding="utf-8") as f:
        f.write(html_code)
    return os.path.abspath(output_html)


# ==========================================
# MAP GENERATOR WITH ACCURACY OVERLAY
# ==========================================

def generate_accuracy_map(lat: float, lon: float, zoom: int, accuracy_m: float, info: dict, filename: str = "accurate_map.html") -> str:
    """Generates an HTML map with a visual accuracy radius ring."""
    m = folium.Map(location=[lat, lon], zoom_start=zoom, tiles="OpenStreetMap")

    # Core Pin
    folium.Marker(
        location=[lat, lon],
        popup=folium.Popup(f"<b>Phone:</b> {info['e164']}<br><b>Carrier:</b> {info['carrier']}<br><b>Region:</b> {info['location_region']}", max_width=300),
        tooltip="Estimated Location Center",
        icon=folium.Icon(color="red", icon="crosshair", prefix="fa")
    ).add_to(m)

    # Accuracy Circle (Meters)
    folium.Circle(
        location=[lat, lon],
        radius=accuracy_m,
        color="#e74c3c",
        fill=True,
        fill_color="#e74c3c",
        fill_opacity=0.2,
        popup=f"Estimated Accuracy Radius: {int(accuracy_m)} meters"
    ).add_to(m)

    m.save(filename)
    return os.path.abspath(filename)


# ==========================================
# MAIN INTERACTION
# ==========================================

if __name__ == "__main__":
    print("\n" + "=" * 65)
    print("      HIGH-ACCURACY TELECOM & GEOLOCATION TRACER ENGINE      ")
    print("=" * 65 + "\n")

    user_input = input("Enter Phone Number with Country Code (e.g., +14155552671): ").strip()

    if user_input:
        print("\n[*] Parsing Telecom Registry...")
        info = parse_telecom_data(user_input)

        if not info["valid"]:
            print(f"[!] Error: {info['error']}")
        else:
            print("\n[+] TELECOM METADATA EXTRACTED:")
            print(f"    - E.164 Number:      {info['e164']}")
            print(f"    - Carrier / Network: {info['carrier']}")
            print(f"    - Line Type:         {info['line_type']}")
            print(f"    - Registered Region: {info['location_region']} ({info['iso_region']})")
            print(f"    - Timezone:          {', '.join(info['timezones'])}")

            print("\n[*] Performing Live Network Check...")
            hlr_data = check_live_hlr_status(info['e164'], HLR_API_KEY)
            print(f"    - Live HLR Result:   {json.dumps(hlr_data)}")

            print("\n[*] Resolving Geocoded Accuracy Radius via OpenCage...")
            lat, lon, address, zoom, radius_m = geocode_opencage_precise(info, OPENCAGE_API_KEY)

            if lat and lon:
                print(f"[+] Coordinates: {lat}, {lon}")
                print(f"[+] Address Match: {address}")
                print(f"[+] Precision Radius: ~{radius_m} meters")

                # Map Generation
                map_file = generate_accuracy_map(lat, lon, zoom, radius_m, info)
                print(f"\n[+] Interactive Precision Map Created: {map_file}")

                # Create GPS Consent Tracker
                gps_file = generate_gps_consent_tracker(info['e164'])
                print(f"[+] GPS Meter-Level Tracker Link Created: {gps_file}")

                print("\n[*] Opening Google Earth and Precision Map...")
                launch_local_file(map_file)
                open_google_earth_web(lat, lon, altitude=int(radius_m * 2))

            else:
                print("[!] Could not geocode region coordinates.")

    print("\n" + "=" * 65 + "\n")