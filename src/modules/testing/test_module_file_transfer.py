import sys
import os
import time
import logging
import pytest
import asyncio
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_module_file_transfer_import():
    """Test that the module file transfer can be imported"""
    from module_file_transfer import ModuleFileTransfer
    assert ModuleFileTransfer is not None

@pytest.mark.asyncio
async def test_module_file_transfer_init():
    """Test that the module file transfer can be initialized"""
    from module_file_transfer import ModuleFileTransfer
    assert ModuleFileTransfer is not None
    test_file_transfer = ModuleFileTransfer("192.168.0.14", logging.getLogger("ModuleTestLogger"))
    assert test_file_transfer is not None
    assert test_file_transfer.controller_ip == "192.168.0.14"

@pytest.mark.asyncio
async def test_module_file_transfer_session():
    """Test that the module file transfer session can be initialized"""
    from module_file_transfer import ModuleFileTransfer
    assert ModuleFileTransfer is not None
    test_file_transfer = ModuleFileTransfer("192.168.0.14", logging.getLogger("ModuleTestLogger"))
    assert test_file_transfer.session is not None
    # TODO: Implement some kind of check to see if the session is valid

@pytest.mark.asyncio
async def test_module_file_transfer_send_file():
    """Test that the module file transfer can send a file"""
    # Create a simple app to receive the file
    from aiohttp import web
    app = web.Application()
    received_files = []
    received_data = {}

    async def handle_upload(request):
        print("Received upload request")
        reader = await request.multipart()
        field = await reader.next()

        # Save the received file
        timestamped_filename = f"received_{time.strftime('%Y%m%d_%H%M%S')}_{field.filename}"
        filepath = os.path.join(f"received", timestamped_filename) # Create a unique filename
        os.makedirs("received", exist_ok=True) # Create the directory if it doesn't exist

        with open(filepath, "wb") as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                f.write(chunk)

        received_files.append(field.filename)
        received_data[field.filename] = filepath
        print(f"Received and saved file: {field.filename}")
        return web.Response(text='Success')

    app.router.add_post('/upload', handle_upload)
    
    # Start the server
    runner = web.AppRunner(app)
    await runner.setup()
    # Use 0.0.0.0 to listen on all interfaces
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print(f"Server started on 0.0.0.0:8080")
    
    # Giver server a moment to start
    await asyncio.sleep(0.1)

    try:
        # Create module file transfer
        from module_file_transfer import ModuleFileTransfer
        test_file_transfer = ModuleFileTransfer("localhost", logging.getLogger("ModuleTestLogger"))
        print(f"Module file transfer created: {test_file_transfer}")
        
        # Send file
        print(f"Sending file to controller")
        success = await test_file_transfer.send_file("test.mp4")
        print(f"File transfer success: {success}")
        assert success, "File transfer failed"
        
        # Check if file was received
        assert "test.mp4" in received_files, "File was not received"
        
    finally:
        # Cleanup
        await runner.cleanup()
        await test_file_transfer.close()
