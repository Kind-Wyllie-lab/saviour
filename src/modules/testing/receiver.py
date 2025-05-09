from aiohttp import web
import asyncio

async def handle_upload(request):
    reader = await request.multipart()
    field = await reader.next()
    filename = field.filename
    
    with open(filename, 'wb') as f:
        while True:
            chunk = await field.read_chunk()
            if not chunk:
                break
            f.write(chunk)
    
    return web.Response(text=f'Uploaded as {filename}')

app = web.Application()
app.router.add_post('/upload', handle_upload)

web.run_app(app, host='0.0.0.0', port=8080)