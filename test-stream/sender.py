import asyncio
import json
import logging
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaPlayer
from aiortc.sdp import candidate_from_sdp
from aiortc.mediastreams import MediaStreamError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webrtc-sender")

class RepeatedVideoTrack(VideoStreamTrack):
    def __init__(self, filename, repeats=30):
        super().__init__()
        self.filename = filename
        self.repeats = repeats
        self.current_loop = 0
        self.frame_count = 0
        self.player = MediaPlayer(self.filename)
        self.pts_offset = 0
        self.last_pts = 0

    async def recv(self):
        try:
            frame = await self.player.video.recv()
            self.last_pts = frame.pts
            frame.pts += self.pts_offset
            self.frame_count += 1
            print(f"Sent video frame {self.frame_count} (Loop {self.current_loop + 1}/{self.repeats})")
            return frame
        except MediaStreamError:
            self.current_loop += 1
            if self.current_loop >= self.repeats:
                logger.info(f"Finished repeating video {self.repeats} times.")
                raise
            logger.info(f"Repeating video... (Loop {self.current_loop + 1}/{self.repeats})")
            
            # Increase the offset by the last seen PTS so the new loop's timestamps continue increasing
            self.pts_offset += self.last_pts
            
            self.player = MediaPlayer(self.filename)
            frame = await self.player.video.recv()
            self.last_pts = frame.pts
            frame.pts += self.pts_offset
            self.frame_count += 1
            print(f"Sent video frame {self.frame_count} (Loop {self.current_loop + 1}/{self.repeats})")
            return frame

async def run(pc, video_track, signaling_url):
    # Add video track
    if video_track:
        pc.addTrack(video_track)
        logger.info("Added video track")

    # Create data channel for metadata
    channel = pc.createDataChannel("metadata")
    logger.info("Created data channel 'metadata'")

    @channel.on("open")
    def on_open():
        logger.info("Data channel is open")
        
        async def send_sensor_data():
            sensor_data = {
                "lat": 40.7128, 
                "lon": -74.0060, 
                "alt": 10.0, 
                "heading": 0.0, 
                "pitch": 0.0, 
                "roll": 0.0
            }
            while channel.readyState == "open":
                try:
                    channel.send(json.dumps(sensor_data))
                    await asyncio.sleep(0.1)  # Roughly 30 times a second
                except Exception as e:
                    logger.error(f"Error sending sensor data: {e}")
                    break
                    
        asyncio.ensure_future(send_sensor_data())

    # Connect to signaling server
    async with websockets.connect(signaling_url) as websocket:
        logger.info("Connected to signaling server")

        # Create offer
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        # Send offer
        await websocket.send(json.dumps({
            "type": pc.localDescription.type,
            "sdp": pc.localDescription.sdp
        }))
        logger.info("Sent offer")

        # Listen for messages
        async for message in websocket:
            data = json.loads(message)
            
            if data["type"] == "answer":
                logger.info("Received answer")
                answer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
                await pc.setRemoteDescription(answer)
                
            elif data["type"] == "candidate":
                logger.info("Received ICE candidate")
                candidate = candidate_from_sdp(data["candidate"]["candidate"])
                candidate.sdpMid = data["candidate"]["sdpMid"]
                candidate.sdpMLineIndex = data["candidate"]["sdpMLineIndex"]
                await pc.addIceCandidate(candidate)

async def main():
    pc = RTCPeerConnection()
    
    # Load video file with custom repeating track
    video_track = RepeatedVideoTrack('shortMovie.MOV', repeats=30)
    
    signaling_url = "ws://localhost:3004/ws/signaling"
    
    try:
        await run(pc, video_track, signaling_url)
        # Keep the connection alive
        await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        await pc.close()

if __name__ == "__main__":
    asyncio.run(main())
