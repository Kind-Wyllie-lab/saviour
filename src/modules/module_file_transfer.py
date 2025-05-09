import aiohttp
import os
import logging


class ModuleFileTransfer:
    def __init__(self, controller_ip: str, logger: logging.Logger):
        self.controller_ip = controller_ip
        self.session = aiohttp.ClientSession()
        self.logger = logger # Use the modules logger

    async def send_file(self, filepath: str):
        """Send a file to the controller"""
        try:
            # Format the file for transfer
            data = aiohttp.FormData()
            data.add_field('file',
                open('filepath', 'rb'),
                filename=os.path.basename(filepath),
                content_type='video/mp4')
            
            # Send the file to controller
            async with self.session.post(
                f'http://{self.controller_ip}:8080/upload', 
                data=data
                ) as response:
                print(await response.text())
        
            # TODO: Check if the file was uploaded successfully

            # TODO: Log the file transfer

            # TODO: If successful, delete the file

        except Exception as e:
            self.logger.error(f"Error sending file: {e}")
            return False
    




        