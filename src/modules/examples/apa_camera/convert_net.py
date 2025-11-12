from ultralytics import YOLO

# Load a YOLO11n PyTorch model
model = YOLO("yolo11n.pt")

# Export the model
model.export(format="imx", int8=False)  # exports with PTQ quantization by default

# Load the exported model
# imx_model = YOLO("yolo11n_imx_model")

# Run inference
# results = imx_model("https://ultralytics.com/images/bus.jpg")