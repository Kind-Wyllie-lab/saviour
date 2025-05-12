"""
MOSDACS System - Module File Transfer Class

This class is used to send files to the controller using asyncio HTTP requests.

Author: Andrew SG
Created: 09/05/2025
License: GPLv3
"""

import aiohttp
import os
import logging
from contextlib import asynccontextmanager
import asyncio
from aiohttp import ClientTimeout

class ModuleFileTransfer:
    def __init__(self, controller_ip: str, logger: logging.Logger):
        self.controller_ip = controller_ip
        self.session = None
        self.logger = logger
        self.timeout = ClientTimeout(total=10)  # 10 second timeout for entire operation
        self.chunk_size = 8 * 1024 * 1024  # 8MB chunks for local network

    @asynccontextmanager
    async def _get_session(self):
        """Context manager to ensure we have a session"""
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=self.timeout)
        try:
            yield self.session
        except Exception as e:
            self.logger.error(f"Session error: {e}")
            if self.session:
                await self.session.close()
                self.session = None
            raise

    async def _upload_chunk(self, session, url, chunk, filename, chunk_num, total_chunks, remote_path=None):
        """Upload a single chunk of the file"""
        data = aiohttp.FormData()
        data.add_field('file', chunk, filename=filename, content_type='video/mp4')
        data.add_field('chunk_num', str(chunk_num))
        data.add_field('total_chunks', str(total_chunks))
        if remote_path:
            data.add_field('remote_path', remote_path)

        try:
            self.logger.info(f"Starting upload of chunk {chunk_num}/{total_chunks} to {url}")
            async with session.post(url, data=data) as response:
                response_text = await response.text()
                if response.status == 200:
                    self.logger.info(f"Chunk {chunk_num}/{total_chunks} uploaded successfully: {response_text}")
                    return True
                else:
                    self.logger.error(f"Chunk {chunk_num}/{total_chunks} upload failed with status {response.status}: {response_text}")
                    return False
        except aiohttp.ClientError as e:
            self.logger.error(f"Network error uploading chunk {chunk_num}/{total_chunks}: {str(e)}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error uploading chunk {chunk_num}/{total_chunks}: {str(e)}")
            return False

    async def send_file(self, filepath: str, remote_path: str = None):
        """Send a file to the controller"""
        if not os.path.exists(filepath):
            self.logger.error(f"File not found: {filepath}")
            return False

        try:
            file_size = os.path.getsize(filepath)
            self.logger.info(f"Sending file to controller: {filepath} ({file_size/1024/1024:.1f}MB)")
            
            # For small files, send in one chunk
            if file_size < self.chunk_size:
                with open(filepath, 'rb') as f:
                    async with self._get_session() as session:
                        self.logger.info(f"Uploading small file ({file_size/1024/1024:.1f}MB) in single chunk")
                        success = await self._upload_chunk(
                            session,
                            f'http://{self.controller_ip}:8080/upload',
                            f.read(),
                            os.path.basename(filepath),
                            1,
                            1,
                            remote_path
                        )
                        if not success:
                            self.logger.error("Failed to upload file in single chunk")
                            return False
            else:
                # Calculate number of chunks
                total_chunks = (file_size + self.chunk_size - 1) // self.chunk_size
                
                # Open the file and send in chunks
                with open(filepath, 'rb') as f:
                    async with self._get_session() as session:
                        for chunk_num in range(total_chunks):
                            # Read chunk
                            chunk = f.read(self.chunk_size)
                            if not chunk:
                                break
                                
                            # Upload chunk
                            success = await self._upload_chunk(
                                session,
                                f'http://{self.controller_ip}:8080/upload',
                                chunk,
                                os.path.basename(filepath),
                                chunk_num + 1,
                                total_chunks,
                                remote_path
                            )
                            
                            if not success:
                                self.logger.error(f"Failed to upload chunk {chunk_num + 1}/{total_chunks}")
                                return False
            
            self.logger.info(f"File upload completed successfully")
            return True

        except Exception as e:
            self.logger.error(f"Error sending file: {str(e)}")
            return False
    
    async def close(self):
        """Close the session if it exists"""
        if self.session:
            await self.session.close()
            self.session = None



        