import aiohttp
import asyncio

sender_ip = "192.168.0.12"
receiver_ip = "192.168.0.14"

async def send_file():
    async with aiohttp.ClientSession() as session:
        # Create form data
        data = aiohttp.FormData()
        data.add_field('file',
                       open('rec/test.mp4', 'rb'),
                       filename='test.mp4',
                       content_type='video/mp4')
        async with session.post('http://192.168.0.14:8080/upload', data=data) as response:
            print(await response.text())

asyncio.run(send_file())