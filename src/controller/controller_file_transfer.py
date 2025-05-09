from aiohttp import web
import logging
import asyncio

class ControllerFileTransfer:
    def __init__(self, logger: logging.Logger):
        self.logger = logger # Use the modules logger
        self.app = web.Application()
        self.app.router.add_post('/upload', self.handle_upload)
    
    async def handle_upload(self, request):
        """Handle receiving a file from the module"""
        self.logger.info('Received file upload request')
        reader = await request.multipart()
        field = await reader.next()
        filename = field.filename
        
        with open(filename, 'wb') as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                f.write(chunk)
        
        self.logger.info(f'Received file: {filename}')
        return web.Response(text=f'Uploaded as {filename}')

    async def start(self):
        """Start the file transfer server"""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()
        self.logger.info("File transfer server started on port 8080")

    async def stop(self):
        """Stop the file transfer server"""
        runner = web.AppRunner(self.app)
        await runner.cleanup()
        self.logger.info("File transfer server stopped")
