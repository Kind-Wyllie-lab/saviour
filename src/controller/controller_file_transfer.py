from aiohttp import web
import logging
import asyncio
import os
import tempfile
from collections import defaultdict
import socket

class ControllerFileTransfer:
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.app = web.Application()
        self.app.router.add_post('/upload', self.handle_upload)
        self.upload_dir = "uploads"  # Base directory for uploads
        os.makedirs(self.upload_dir, exist_ok=True)
        
        # Track ongoing uploads
        self.active_uploads = defaultdict(dict)
        
        # Log server configuration
        self.logger.info(f"File transfer server initialized with upload directory: {os.path.abspath(self.upload_dir)}")
    
    async def handle_upload(self, request):
        """Handle receiving a file from the module"""
        self.logger.info('Received file upload request')
        reader = await request.multipart()
        
        # Get the file field
        field = await reader.next()
        if not field:
            return web.Response(status=400, text='No file field in request')
            
        filename = field.filename
        chunk_data = await field.read()
        
        # Get metadata fields
        chunk_num = None
        total_chunks = None
        remote_path = None
        
        while True:
            field = await reader.next()
            if not field:
                break
                
            if field.name == 'chunk_num':
                chunk_num = int(await field.text())
            elif field.name == 'total_chunks':
                total_chunks = int(await field.text())
            elif field.name == 'remote_path':
                remote_path = await field.text()
        
        if chunk_num is None or total_chunks is None:
            return web.Response(status=400, text='Missing chunk information')
            
        # Create a unique ID for this upload
        upload_id = f"{filename}_{remote_path}"
        
        try:
            # Initialize upload tracking if this is the first chunk
            if chunk_num == 1:
                self.active_uploads[upload_id] = {
                    'chunks': {},
                    'total_chunks': total_chunks,
                    'remote_path': remote_path
                }
            
            # Store the chunk
            self.active_uploads[upload_id]['chunks'][chunk_num] = chunk_data
            
            # Check if we have all chunks
            if len(self.active_uploads[upload_id]['chunks']) == total_chunks:
                # Determine final path
                if remote_path:
                    full_path = os.path.join(self.upload_dir, remote_path)
                    # Ensure the directory exists
                    os.makedirs(os.path.dirname(full_path), exist_ok=True)
                    self.logger.info(f"Ensuring directory exists: {os.path.dirname(full_path)}")
                else:
                    full_path = os.path.join(self.upload_dir, filename)
                
                # Combine chunks in order
                with open(full_path, 'wb') as f:
                    for i in range(1, total_chunks + 1):
                        f.write(self.active_uploads[upload_id]['chunks'][i])
                
                # Clean up
                del self.active_uploads[upload_id]
                
                self.logger.info(f'File upload complete: {filename} -> {full_path}')
                return web.Response(text=f'Uploaded as {full_path}')
            else:
                self.logger.info(f'Received chunk {chunk_num}/{total_chunks} of {filename}')
                return web.Response(text=f'Chunk {chunk_num} received')
                
        except Exception as e:
            self.logger.error(f'Error handling upload: {e}')
            # Clean up on error
            if upload_id in self.active_uploads:
                del self.active_uploads[upload_id]
            return web.Response(status=500, text=str(e))

    async def start(self):
        """Start the file transfer server"""
        try:
            # Get local IP addresses
            hostname = socket.gethostname()
            local_ips = socket.gethostbyname_ex(hostname)[2]
            self.logger.info(f"Starting file transfer server on interfaces: {local_ips}")
            
            runner = web.AppRunner(self.app)
            await runner.setup()
            site = web.TCPSite(runner, '0.0.0.0', 8080)
            await site.start()
            
            # Verify the server is running
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex(('127.0.0.1', 8080))
            sock.close()
            
            if result == 0:
                self.logger.info("File transfer server started successfully on port 8080")
            else:
                self.logger.error(f"File transfer server failed to start (error code: {result})")
                
        except Exception as e:
            self.logger.error(f"Error starting file transfer server: {str(e)}")
            raise

    async def stop(self):
        """Stop the file transfer server"""
        try:
            runner = web.AppRunner(self.app)
            await runner.cleanup()
            self.logger.info("File transfer server stopped")
        except Exception as e:
            self.logger.error(f"Error stopping file transfer server: {str(e)}")
            raise
