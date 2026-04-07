const WebSocket = require('ws');
// const detection = require('./detection');

// Starting coordinates (New York City)
const startCoordinate = [-73.970895, 40.723279];

// 1/4 mile in degrees
// At latitude ~40.7: 1 degree longitude ≈ 52.3 miles, 1 degree latitude ≈ 69 miles
// 1/4 mile ≈ 0.00478 degrees longitude, 0.003623 degrees latitude
const quarterMileInDegrees = {
  longitude: 0.25 / (69 * Math.cos(startCoordinate[1] * Math.PI / 180)),
  latitude: 0.25 / 69,
};

// Total steps: 5 miles / 0.25 miles = 20 steps
const totalSteps = 20;

const test_mode = false;

// Create WebSocket server on port 3002
const wss = new WebSocket.Server({ port: 3002 });

console.log('WebSocket server started on ws://localhost:3002');

// Track current position and step
let currentCoordinate = [...startCoordinate];
let currentStep = 0;

// Function to move the marker
function moveMarker() {
  // If we've completed 5 miles (20 steps), reset to start
  if (currentStep >= totalSteps) {
    currentCoordinate = [...startCoordinate];
    currentStep = 0;
  } else {
    // Move 1/4 mile east (increase longitude)
    currentCoordinate[0] += quarterMileInDegrees.longitude;
    currentStep++;
  }
}

// Send location updates continuously every second to all connected clients
const updateInterval = setInterval(() => {
  if(test_mode) {

    moveMarker();
    console.log('Sending location update');
    
    // Send update to all connected clients
    const message = JSON.stringify({
      type: 'test_update',
      lat: currentCoordinate[1],
      lon: currentCoordinate[0],
      step: currentStep,
      totalSteps: totalSteps,
      timestamp: new Date().toISOString(),
    });

    wss.clients.forEach((client) => {
      if (client.readyState === WebSocket.OPEN) {
        console.log(`Sent location update: lat=${currentCoordinate[1]}, lon=${currentCoordinate[0]} (Step: ${currentStep}/${totalSteps})`);
        client.send(message);
      }
    });
  }
  
}, 1000);

// Handle new connections
wss.on('connection', (ws) => {
  console.log('New client connected');
  
  // Send current position immediately when client connects
  // const initialMessage = JSON.stringify({
  //   type: 'location_update',
  //   lat: currentCoordinate[1],
  //   lon: currentCoordinate[0],
  //   step: currentStep,
  //   totalSteps: totalSteps,
  //   timestamp: new Date().toISOString(),
  // });
  // ws.send(initialMessage);
  // console.log(`Sent initial position to new client: lat=${currentCoordinate[1]}, lon=${currentCoordinate[0]}`);
  
  // Handle incoming messages (optional - for any client-to-server communication)
  ws.on('message', async (data) => {
    try {
      const message = JSON.parse(data.toString());
      console.log('Received message from client:', message);
      if (message.type === 'client_user_location') {
        const broadcastMessage = JSON.stringify({
          type: 'user_location',
          lat: message.lat,
          lon: message.lon,
          timestamp: new Date().toISOString(),
        }); 
        wss.clients.forEach((client) => {
          if (client.readyState === WebSocket.OPEN) {
            client.send(broadcastMessage);
          }
        });
        console.log(`Broadcasted user location: lat=${message.lat}, lon=${message.lon}`);
      }
      // Server now continuously sends updates, so no response needed
    } catch (error) {
      console.error('Error processing message:', error);
    }
  });
  
  // Handle client disconnection
  ws.on('close', () => {
    console.log('Client disconnected');
  });
  
  // Handle errors
  ws.on('error', (error) => {
    console.error('WebSocket error:', error);
  });
});

// Handle server shutdown
process.on('SIGINT', () => {
  console.log('\nShutting down WebSocket server...');
  clearInterval(updateInterval);
  wss.close(() => {
    console.log('WebSocket server closed');
    process.exit(0);
  });
});

