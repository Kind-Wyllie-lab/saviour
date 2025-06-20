from flask import Flask, render_template_string

app = Flask(__name__)

# Replace this with the actual URL of your remote MJPEG stream
REMOTE_STREAM_URL = "http://192.168.0.31:8080/video_feed"

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Remote MJPEG Stream Viewer</title>
</head>
<body>
    <h1>Remote MJPEG Stream</h1>
    <img src="{{ stream_url }}" width="640" height="480" />
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_PAGE, stream_url=REMOTE_STREAM_URL)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)