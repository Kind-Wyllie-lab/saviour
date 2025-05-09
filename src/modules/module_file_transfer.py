import aiohttp
import os
import logging


class ModuleFileTransfer:
    def __init__(self, controller_ip: str, logger: logging.Logger):
        self.controller_ip = controller_ip
        self.session = aiohttp.ClientSession()
        self.logger = logger # Use the modules logger

    async def _ensure_session(self):
        """Private method to ensure we have a session, create if needed"""
        if self.session is None:
            self.session = aiohttp.ClientSession()

    async def send_file(self, filepath: str):
        """Send a file to the controller"""
        try:
            self.logger.debug(f"Sending file to controller: {filepath}")
            
            # Ensure we have a session
            await self._ensure_session()
            self.logger.debug(f"Session: {self.session}")
            
            # Format the file for transfer
            data = aiohttp.FormData()
            data.add_field('file',
                open(filepath, 'rb'),
                filename=os.path.basename(filepath),
                content_type='video/mp4')
            self.logger.debug(f"Data: {data}")
            
            # Send the file to controller
            async with self.session.post(
                f'http://{self.controller_ip}:8080/upload', 
                data=data
                ) as response:
                print(f"Response: {response}")
                if response.status == 200:
                    self.logger.debug(f"File uploaded successfully")
                    return True
                else:
                    self.logger.error(f"File upload failed with status: {response.status}")
                    return False


        except Exception as e:
            self.logger.error(f"Error sending file: {e}")
            return False
    
    async def close(self):
        """Close the session if it exists"""
        if self.session:
            await self.session.close()
            self.session = None



        