from fastapi import FastAPI, UploadFile, File
from ultralytics import YOLO
import io
from PIL import Image
import uvicorn

app = FastAPI()

# Load the nano model (fastest for testing)
model = YOLO("yolov8x-oiv7.pt")

TARGET_LABELS = ["Bird", "Drone", "Tiger", "Eagle", "Falcon", "Hawk"]

@app.post("/process-image")
async def process_image(file: UploadFile = File(...)):
    # Read image into memory
    contents = await file.read()
    img = Image.open(io.BytesIO(contents))

    # Run YOLO inference
    results = model(img, imgsz=1280, conf=0.15)
    
    detections = {}
    for r in results:
        for box in r.boxes:
            class_id = int(box.cls[0])
            label = model.names[class_id]

            if label in ["Eagle", "Falcon", "Hawk"] and float(box.conf[0]) < 0.5:
                label = "Bird"
            # You can now look for specific OIV7 classes
            # Classes include: "Tiger", "Drone", "Bird", "Falcon", etc.
            if label in TARGET_LABELS:
                if(detections.get(label)):
                    if(detections[label]['confidence'] < float(box.conf[0])):
                        detections[label] = {
                            "confidence": round(float(box.conf[0]), 3),
                            "coords": box.xywhn[0].tolist()
                        }
                else:
                    detections[label] = {
                        "confidence": round(float(box.conf[0]), 3),
                        "coords": box.xywhn[0].tolist()
                    }

    return {"detections": detections}

# Add this at the bottom of your file
if __name__ == "__main__":
    uvicorn.run("rest-server:app", host="0.0.0.0", port=3003, reload=True)