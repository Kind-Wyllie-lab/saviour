from ultralytics import YOLO

# Load a YOLOv8n model
model = YOLO('yolo11n.pt')

path = "RatTracker.v2i.yolov8"

# Train on the dataset
model.train(data=f'{path}/data.yaml', epochs=100, imgsz=640, batch=16)