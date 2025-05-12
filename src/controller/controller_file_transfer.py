from aiohttp import web
import logging
import os

class ControllerFileTransfer:
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.app = web.Application()
        self.app.router.add_post('/upload', self.handle_upload)
        self.upload_dir = "uploads"
        os.makedirs(self.upload_dir, exist_ok=True)
        self.logger.info(f"File transfer server initialized with upload directory: {os.path.abspath(self.upload_dir)}")

    async def handle_upload(self, request):
        """Handle receiving a file from the module"""
        try:
            reader = await request.multipart()
            field = await reader.next()
            
            if not field:
                return web.Response(status=400, text='No file in request')
            
            # Get the file data
            data = await field.read()
            filename = field.filename
            
            # Save the file
            filepath = os.path.join(self.upload_dir, filename)
            with open(filepath, 'wb') as f:
                f.write(data)
            
            self.logger.info(f'File saved: {filepath}')
            return web.Response(text='File uploaded successfully')
            
        except Exception as e:
            self.logger.error(f'Error handling upload: {e}')
            return web.Response(status=500, text=str(e))

    async def start(self):
        """Start the file transfer server"""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()
        self.logger.info("File transfer server started on 0.0.0.0:8080")

    async def stop(self):
        """Stop the file transfer server"""
        runner = web.AppRunner(self.app)
        await runner.cleanup()
        self.logger.info("File transfer server stopped")
