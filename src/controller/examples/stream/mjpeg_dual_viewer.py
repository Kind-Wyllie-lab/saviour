from flask import Flask, render_template_string

app = Flask(__name__)

# Set these to your actual remote MJPEG stream URLs
STREAM_URL_1 = "http://192.168.0.31:8080/video_feed"
STREAM_URL_2 = "http://192.168.0.35:8080/video_feed"
STREAM_URL_3 = "http://192.168.0.13:8080/video_feed"

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Dual MJPEG Stream Viewer</title>
    <style>
        body { font-family: Arial, sans-serif; text-align: center; }
        .stream-container {
            display: flex;
            justify-content: center;
            align-items: flex-start;
            gap: 40px;
            margin-top: 40px;
        }
        .stream-box {
            border: 1px solid #ccc;
            padding: 10px;
            background: #fafafa;
        }
        .stream-title {
            margin-bottom: 10px;
            font-weight: bold;
        }
        img {
            max-width: 100%;
            height: auto;
            border: 1px solid #888;
        }
    </style>
</head>
<body>
    <h1>Dual MJPEG Stream Viewer</h1>
    <div class="stream-container">
        <div class="stream-box">
            <div class="stream-title">Camera 1</div>
            <img src="{{ stream1 }}" width="640" height="480" />
        </div>
        <div class="stream-box">
            <div class="stream-title">Camera 2</div>
            <img src="{{ stream2 }}" width="640" height="480" />
        </div>
        <div class="stream-box">
            <div class="stream-title">Camera 3</div>
            <img src="{{ stream3 }}" width="640" height="480" />
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    
    return render_template_string(HTML_PAGE, stream1=STREAM_URL_1, stream2=STREAM_URL_2, stream3=STREAM_URL_3)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)