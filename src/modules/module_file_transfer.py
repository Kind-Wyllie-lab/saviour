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
import socket

class ModuleFileTransfer:
    def __init__(self, controller_ip: str, logger: logging.Logger):
        self.controller_ip = controller_ip
        self.session = None
        self.logger = logger
        self.timeout = ClientTimeout(total=10)  # 10 second timeout for entire operation
        self.chunk_size = 8 * 1024 * 1024  # 8MB chunks for local network
        
        # Log the controller IP we're using
        self.logger.info(f"Initialized file transfer with controller IP: {self.controller_ip}")
        
        # Verify IP format
        try:
            socket.inet_aton(self.controller_ip)
            self.logger.info("Controller IP format is valid")
        except socket.error:
            self.logger.error(f"Invalid controller IP format: {self.controller_ip}")

    @asynccontextmanager
    async def _get_session(self):
        """Context manager to ensure we have a session"""
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=self.timeout)
        try:
            yield self.session
        except Exception as e:
            self.logger.error(f"Session error: {str(e)}")
            if self.session:
                await self.session.close()
                self.session = None
            raise

    def _test_port(self, host, port):
        """Test if a port is open using socket"""
        try:
            self.logger.info(f"Testing connection to {host}:{port}")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                self.logger.info(f"Port {port} is open on {host}")
            else:
                self.logger.error(f"Port {port} is not open on {host} (error code: {result})")
            return result == 0
        except Exception as e:
            self.logger.error(f"Socket test failed for {host}:{port}: {str(e)}")
            return False

    async def _test_connection(self, session):
        """Test connection to controller"""
        # First test if port is open
        if not self._test_port(self.controller_ip, 8080):
            self.logger.error(f"Port 8080 is not open on {self.controller_ip}")
            return False

        try:
            url = f'http://{self.controller_ip}:8080/upload'
            self.logger.info(f"Testing HTTP connection to {url}")
            async with session.get(url, timeout=5) as response:
                response_text = await response.text()
                if response.status == 405:  # Method Not Allowed is expected for GET
                    self.logger.info("Connection test successful (got expected 405 response)")
                    return True
                self.logger.error(f"Unexpected response testing connection: {response.status} - {response_text}")
                return False
        except aiohttp.ClientError as e:
            self.logger.error(f"HTTP client error during connection test: {str(e)}")
            return False
        except asyncio.TimeoutError:
            self.logger.error("Connection test timed out after 5 seconds")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error during connection test: {str(e)}")
            return False

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
            async with session.post(url, data=data, timeout=5) as response:
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
        except asyncio.TimeoutError:
            self.logger.error(f"Upload of chunk {chunk_num}/{total_chunks} timed out after 5 seconds")
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
                        # Test connection first
                        if not await self._test_connection(session):
                            self.logger.error("Failed to connect to controller")
                            return False

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
                        # Test connection first
                        if not await self._test_connection(session):
                            self.logger.error("Failed to connect to controller")
                            return False

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



        