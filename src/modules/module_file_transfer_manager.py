#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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
from aiohttp import ClientTimeout

class ModuleFileTransfer:
    def __init__(self, controller_ip: str, logger: logging.Logger):
        self.controller_ip = controller_ip
        self.logger = logger
        self.timeout = ClientTimeout(total=30)  # 30 second timeout
        self.logger.info(f"Initialized file transfer with controller IP: {self.controller_ip}")

    async def send_file(self, filepath: str, remote_path: str = None):
        """Send a file to the controller"""
        if not os.path.exists(filepath):
            self.logger.error(f"File not found: {filepath}")
            return False

        try:
            file_size = os.path.getsize(filepath)
            self.logger.info(f"Sending file to controller: {filepath} ({file_size/1024/1024:.1f}MB)")
            
            # Read the file
            with open(filepath, 'rb') as f:
                data = f.read()
            
            # Send the file
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                form = aiohttp.FormData()
                form.add_field('file', data, filename=os.path.basename(filepath))
                
                async with session.post(f'http://{self.controller_ip}:8080/upload', data=form) as response:
                    if response.status == 200:
                        self.logger.info("File uploaded successfully")
                        return True
                    else:
                        text = await response.text()
                        self.logger.error(f"Upload failed with status {response.status}: {text}")
                        return False
                
                # Should we close the session here?
                        
        except Exception as e:
            self.logger.error(f"Error sending file: {str(e)}")
            return False


        