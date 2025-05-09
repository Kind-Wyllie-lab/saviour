import aiohttp
import asyncio

sender_ip = "192.168.0.12"
receiver_ip = "192.168.0.14"

async def send_file():
    async with aiohttp.ClientSession() as session:
        with open('rec/test.mp4', 'rb') as f:
            async with session.post('http://192.168.0.14:8080/upload', data=f) as response:
                print(await response.text())

asyncio.run(send_file())