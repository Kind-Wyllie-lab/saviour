import sys
import os
import time
import argparse
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.controller.controller import Controller

# Main entry point
def main():
    """Main entry point for the controller application"""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Habitat Controller')
    parser.add_argument('--config', type=str, help='Path to config file')
    args = parser.parse_args()
    
    # Initialize controller with optional config file
    controller = Controller(config_file_path=args.config)
    
    # Print some config values to demonstrate config manager
    print(f"Controller running with the following configuration:")
    print(f"  Max buffer size: {controller.get_config('controller.max_buffer_size')}")
    print(f"  Manual control: {controller.get_config('controller.manual_control')}")
    print(f"  Health monitor interval: {controller.get_config('health_monitor.heartbeat_interval')}s")
    print(f"  Data export interval: {controller.get_config('data_export.export_interval')}s")

    try:
        # Start the main loop
        controller.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        controller.stop()
    except Exception as e:
        print(f"\nError: {e}")
        controller.stop()

# Run the main function if the script is executed directly
if __name__ == "__main__":
    main()
