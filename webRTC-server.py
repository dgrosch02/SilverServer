import json
import logging
import asyncio
import math
import time
from fastapi import FastAPI, WebSocket
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
from aiortc.contrib.media import MediaRelay
from ultralytics import YOLO
import cv2
import uvicorn

app = FastAPI()
relay = MediaRelay()
# model = YOLO("yolov8x-oiv7.pt")
model = YOLO("yolov8s-oiv7.pt")
# model = YOLO("yolov8n-oiv7.pt")

# Store connected React Mapbox clients
map_clients = set()

# Configure logging to see detections in the console and save to a file
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("tracking_data.log"),
        logging.StreamHandler()
    ]
)

def estimate_distance(label, box_w, fov_x_deg=60.0):
    """
    Estimates distance to an object based on its bounding box width and typical real-world size.
    """
    # Typical real-world widths in meters for your targets
    REAL_WIDTHS = {
        "Drone": 0.5,   # Half a meter wide
        "Bird": 0.3,    # 30 cm
        "Eagle": 0.6,   # 60 cm (body/wingspan average)
        "Tiger": 2.0    # 2 meters long
    }
    
    # Get estimated real width, default to 0.5m if unknown
    real_width = REAL_WIDTHS.get(label, 0.5)
    
    # Distance formula using pinhole camera model:
    # Distance = Real_Width / (2 * normalized_box_width * tan(FOV_X / 2))
    fov_x_rad = math.radians(fov_x_deg)
    
    # Prevent division by zero if box_w is somehow 0
    if box_w <= 0:
        return 50.0 
        
    distance = real_width / (2 * box_w * math.tan(fov_x_rad / 2))
    return distance

def calculate_target_gps(phone_lat, phone_lon, phone_alt, phone_heading, phone_pitch, phone_roll, box_x, box_y, distance_meters=50.0):
    """
    Calculates the real-world GPS coordinates of an object on screen, accounting for device roll.
    """
    # 1. Phone Camera Field of View (FOV) - Approximate for most modern smartphones
    FOV_X_DEG = 60.0  # Horizontal FOV
    FOV_Y_DEG = 45.0  # Vertical FOV

    # 2. Calculate the object's angle offset from the center of the screen
    screen_offset_x_deg = (box_x - 0.5) * FOV_X_DEG
    screen_offset_y_deg = (0.5 - box_y) * FOV_Y_DEG

    # 3. Apply roll rotation to get true horizontal and vertical offsets
    # Assuming positive roll is clockwise rotation of the device
    roll_rad = math.radians(phone_roll)
    true_offset_x_deg = screen_offset_x_deg * math.cos(roll_rad) + screen_offset_y_deg * math.sin(roll_rad)
    true_offset_y_deg = -screen_offset_x_deg * math.sin(roll_rad) + screen_offset_y_deg * math.cos(roll_rad)

    # 4. Calculate the true bearing (azimuth) to the object
    target_heading = (phone_heading + true_offset_x_deg) % 360.0
    
    # 5. Calculate the true pitch (elevation) to the object
    target_pitch = phone_pitch + true_offset_y_deg

    # 5. Calculate horizontal and vertical distance based on pitch
    pitch_rad = math.radians(target_pitch)
    horizontal_dist = distance_meters * math.cos(pitch_rad)
    vertical_dist = distance_meters * math.sin(pitch_rad)

    # 6. Project the new GPS coordinate using the Haversine formula
    R = 6378137.0 # Earth's radius in meters
    lat_rad = math.radians(phone_lat)
    lon_rad = math.radians(phone_lon)
    bearing_rad = math.radians(target_heading)

    target_lat_rad = math.asin(
        math.sin(lat_rad) * math.cos(horizontal_dist / R) +
        math.cos(lat_rad) * math.sin(horizontal_dist / R) * math.cos(bearing_rad)
    )

    target_lon_rad = lon_rad + math.atan2(
        math.sin(bearing_rad) * math.sin(horizontal_dist / R) * math.cos(lat_rad),
        math.cos(horizontal_dist / R) - math.sin(lat_rad) * math.sin(target_lat_rad)
    )

    target_lat = math.degrees(target_lat_rad)
    target_lon = math.degrees(target_lon_rad)
    # Add a safety floor so things don't go underground
    target_alt = max(0, phone_alt + vertical_dist)

    print(f"Target Location: {target_lat}, {target_lon}, {target_alt}")
    return {
        "lat": target_lat,
        "lon": target_lon,
        "alt": target_alt,
        "heading": target_heading,
        "pitch": target_pitch
    }

class ObjectTrackingTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, track):
        super().__init__()
        self.track = track
        # Provide some mock fallback data so video testing works even without a phone connected!
        self.sensor_data = {
            "lat": 40.7128, 
            "lon": -74.0060, 
            "alt": 10.0, 
            "heading": 0.0, 
            "pitch": 0.0, 
            "roll": 0.0
        }
        # Store state for Alpha-Beta filter: { "Label": {"lat": x, "lon": y, "alt": z, "v_lat": 0, "v_lon": 0, "v_alt": 0, "last_time": t} }
        self.tracking_state = {}
        
        # Filter tuning parameters
        # Alpha: How much we trust the new measurement (0.0 to 1.0)
        # Beta: How much we update our velocity estimate (0.0 to 1.0)
        self.ALPHA = 0.6  # Higher = more responsive, lower = smoother
        self.BETA = 0.3   # Higher = adapts to speed changes faster

    async def recv(self):
        # 1. Get the frame from the network
        frame = await self.track.recv()

        # logging.info(f"Frame received: {frame}")
        
        # 2. Convert to CV2 for YOLO (Open Images V7)
        img = frame.to_ndarray(format="bgr24")

        # 3. Inference - We use YOLO's built-in tracker (persist=True) to assign UUIDs to objects
        # This uses BoT-SORT/ByteTrack under the hood to track multiple objects of the same class
        results = model.track(img, imgsz=640, persist=True, verbose=False)

        current_time = time.time()
        
        # Clean up stale tracks (objects we haven't seen in 3 seconds)
        stale_keys = [k for k, v in self.tracking_state.items() if current_time - v["last_time"] > 3.0]
        for k in stale_keys:
            del self.tracking_state[k]

        for r in results:
            # Extract tracking IDs if available (sometimes None on the very first frame)
            if r.boxes.id is not None:
                track_ids = r.boxes.id.int().cpu().tolist()
            else:
                track_ids = [None] * len(r.boxes)
                
            for box, track_id in zip(r.boxes, track_ids):
                label = model.names[int(box.cls[0])]
                conf = float(box.conf[0])
                
                # TEMPORARY DEBUG: Print everything YOLO sees
                logging.info(f"YOLO saw: {label} (Confidence: {conf:.2f})")
                
                # Filter for your specific targets
                if True:
                # if label in ["Bird", "Drone", "Tiger", "Eagle"]:
                    # Create a unique ID for this specific object (e.g. "Bird_1", "Bird_2")
                    unique_id = f"{label}_{track_id}" if track_id is not None else f"{label}_untracked"
                    
                    conf = float(box.conf[0])
                    coords = box.xywhn[0].tolist() # [x, y, w, h]
                    
                    # Ensure we actually have sensor data from the phone before calculating
                    if self.sensor_data and "lat" in self.sensor_data:
                        # Extract phone metadata
                        p_lat = self.sensor_data.get("lat", 0.0)
                        p_lon = self.sensor_data.get("lon", 0.0)
                        p_alt = self.sensor_data.get("alt", 0.0)
                        p_heading = self.sensor_data.get("heading", 0.0)
                        p_pitch = self.sensor_data.get("pitch", 0.0)
                        p_roll = self.sensor_data.get("roll", 0.0)
                        
                        # Estimate distance based on object type and bounding box width
                        # coords[2] is the normalized width (w) of the bounding box
                        estimated_distance = estimate_distance(label, coords[2])
                        
                        # Calculate real-world coordinates
                        target_location = calculate_target_gps(
                            p_lat, p_lon, p_alt, p_heading, p_pitch, p_roll,
                            box_x=coords[0], box_y=coords[1], 
                            distance_meters=estimated_distance
                        )

                        # --- SMOOTHING (Alpha-Beta Filter for Moving Objects) ---
                        if unique_id not in self.tracking_state:
                            # First time seeing this object, initialize state
                            self.tracking_state[unique_id] = {
                                "lat": target_location["lat"],
                                "lon": target_location["lon"],
                                "alt": target_location["alt"],
                                "v_lat": 0.0,
                                "v_lon": 0.0,
                                "v_alt": 0.0,
                                "last_time": current_time
                            }
                            smoothed_location = target_location
                        else:
                            state = self.tracking_state[unique_id]
                            dt = current_time - state["last_time"]
                            
                            # Prevent divide by zero if frames arrive instantly
                            if dt <= 0:
                                dt = 0.033 # Assume ~30fps
                                
                            # 1. Predict where the object should be based on its last known velocity
                            pred_lat = state["lat"] + (state["v_lat"] * dt)
                            pred_lon = state["lon"] + (state["v_lon"] * dt)
                            pred_alt = state["alt"] + (state["v_alt"] * dt)
                            
                            # 2. Calculate the error between prediction and actual measurement
                            err_lat = target_location["lat"] - pred_lat
                            err_lon = target_location["lon"] - pred_lon
                            err_alt = target_location["alt"] - pred_alt
                            
                            # 3. Update the estimate (Alpha)
                            state["lat"] = pred_lat + (self.ALPHA * err_lat)
                            state["lon"] = pred_lon + (self.ALPHA * err_lon)
                            state["alt"] = pred_alt + (self.ALPHA * err_alt)
                            
                            # 4. Update the velocity (Beta)
                            state["v_lat"] = state["v_lat"] + (self.BETA * err_lat / dt)
                            state["v_lon"] = state["v_lon"] + (self.BETA * err_lon / dt)
                            state["v_alt"] = state["v_alt"] + (self.BETA * err_alt / dt)
                            
                            state["last_time"] = current_time
                            
                            smoothed_location = {
                                "lat": state["lat"],
                                "lon": state["lon"],
                                "alt": state["alt"]
                            }

                        logging.info(f"--- {unique_id.upper()} DETECTED ---")
                        logging.info(f"Phone Location: {p_lat}, {p_lon}")
                        logging.info(f"Estimated Distance: {estimated_distance:.1f}m")
                        logging.info(f"Raw Target: {target_location['lat']:.6f}, {target_location['lon']:.6f}")
                        logging.info(f"Smoothed Target: {smoothed_location['lat']:.6f}, {smoothed_location['lon']:.6f}")
                        logging.info(f"Velocity (deg/s): lat={self.tracking_state[unique_id]['v_lat']:.6f}, lon={self.tracking_state[unique_id]['v_lon']:.6f}")
                        logging.info(f"-----------------")

                        # --- BROADCAST TO REACT MAPBOX CLIENTS ---
                        location_data = {
                            "id": unique_id,
                            "label": label,
                            "lat": smoothed_location["lat"],
                            "lon": smoothed_location["lon"],
                            "alt": smoothed_location["alt"]
                        }
                        
                        for client in list(map_clients):
                            try:
                                logging.info("Sending location data to map clients")
                                # Fire and forget so we don't block the video processing
                                asyncio.create_task(client.send_json(location_data))
                            except Exception:
                                pass

        return frame

@app.websocket("/ws/signaling")
async def signaling(websocket: WebSocket):
    await websocket.accept()
    pc = RTCPeerConnection()
    video_track = None

    @pc.on("datachannel")
    def on_datachannel(channel):
        logging.info(f"Data channel established: {channel.label}")
        
        @channel.on("message")
        def on_message(message):
            # This is where React Native sends: {"lat": 39.9, "pitch": -0.1, ...}
            try:
                data = json.loads(message)
                # logging.info(f"Received sensor data via datachannel: {data}")
                if video_track:
                    video_track.sensor_data = data
            except Exception as e:
                logging.error(f"Metadata error: {e}")

    @pc.on("track")
    def on_track(track):
        # logging.info(f"Track received: {track.kind} (ID: {track.id})")
        
        nonlocal video_track
        if track.kind == "video":
            logging.info("Wrapping video track with YOLO ObjectTrackingTrack...")
            # Wrap the incoming track with our YOLO logic
            video_track = ObjectTrackingTrack(relay.subscribe(track))
            pc.addTrack(video_track)

    # Simple Signaling Handshake
    while True:
        message = await websocket.receive_text()
        data = json.loads(message)

        if data["type"] == "offer":
            await pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type=data["type"]))
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            await websocket.send_text(json.dumps({
                "type": pc.localDescription.type,
                "sdp": pc.localDescription.sdp
            }))
        elif data["type"] == "candidate":
            # Handle ICE candidates for network traversal
            candidate = data["candidate"]
            if candidate:
                await pc.addIceCandidate(candidate)

@app.websocket("/ws/locations")
async def map_locations(websocket: WebSocket):
    """
    WebSocket endpoint for React Mapbox clients to receive live coordinates.
    """
    await websocket.accept()
    logging.info("New map client connected")
    map_clients.add(websocket)
    try:
        # Instead of waiting for text, we just sleep in a loop.
        # This prevents the connection from closing, and allows the 
        # broadcast loop in ObjectTrackingTrack to send data to this socket.
        while True:
            await asyncio.sleep(1)
    except Exception as e:
        logging.info(f"Map client disconnected: {e}")
    finally:
        # Ensure client is removed when they disconnect
        if websocket in map_clients:
            map_clients.remove(websocket)


# Add this at the bottom of your file
if __name__ == "__main__":
    uvicorn.run("webRTC-server:app", host="0.0.0.0", port=3004, reload=True)