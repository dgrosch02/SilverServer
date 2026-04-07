# WebSocket Server for SilverNorth

This WebSocket server sends location updates to the SilverNorth React Native app.

## Installation

```bash
cd websocket-server
npm install
```

## Running the Server

From the project root:
```bash
npm run websocket:server
```

Or from the websocket-server directory:
```bash
npm start
```

## How It Works

- The server listens on port 3002
- It simulates a marker moving 1/4 mile east every second
- After 20 steps (5 miles), it resets to the starting position
- Sends JSON messages in format: `{"lat": 40.723279, "lon": -73.970895}`
- Updates are sent every 1 second to all connected clients

## Starting Coordinates

- Longitude: -73.970895
- Latitude: 40.723279
- Location: New York City

## Video Processing & Animal Detection

The server supports processing video frames for animal detection:

### Features
- Receives video frames from connected clients via WebSocket
- Processes frames using OpenCV for animal detection
- Returns detection results with location coordinates
- Each frame triggers location movement (1/4 mile increment)

### Message Protocol

**Client to Server (Video Frame):**
```json
{
  "type": "video_frame",
  "data": "base64_encoded_image",
  "frameNumber": 1
}
```

**Server to Client (Detection Result):**
```json
{
  "type": "detection_result",
  "frameNumber": 1,
  "lat": 40.723279,
  "lon": -73.970895,
  "detections": [
    {
      "type": "dog",
      "confidence": 0.85,
      "bbox": { "x": 100, "y": 150, "width": 200, "height": 250 },
      "timestamp": "2024-01-01T12:00:00Z"
    }
  ],
  "timestamp": "2024-01-01T12:00:00Z"
}
```

### OpenCV Setup

To enable animal detection:
1. Install OpenCV on your system
2. Run `npm install` in the websocket-server directory
3. The server will automatically initialize the detection model on startup

Note: Currently uses a placeholder detection model. To enable actual detection, you'll need to:
- Download a pre-trained YOLO model for animal detection
- Place model files in the `models/` directory
- Update `detection.js` to load and use the model

