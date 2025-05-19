import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from controller import Controller

# Example of changing configuration at runtime
def main():
    """Example of using the config manager to change settings at runtime"""
    # Initialize controller with default config
    controller = Controller()
    
    # Print current configuration
    print("Current configuration:")
    print(f"  Max buffer size: {controller.get_config('controller.max_buffer_size')}")
    print(f"  Manual control: {controller.get_config('controller.manual_control')}")
    print(f"  Show received data: {controller.get_config('controller.print_received_data')}")
    
    # Change some settings temporarily (in-memory only)
    print("\nChanging configuration in memory...")
    controller.set_config("controller.max_buffer_size", 5000)
    controller.set_config("controller.print_received_data", True)
    
    # Print updated configuration
    print("Updated configuration:")
    print(f"  Max buffer size: {controller.get_config('controller.max_buffer_size')}")
    print(f"  Manual control: {controller.get_config('controller.manual_control')}")
    print(f"  Show received data: {controller.get_config('controller.print_received_data')}")
    
    # Save changes to config file
    print("\nSaving configuration to file...")
    controller.set_config("controller.max_buffer_size", 3000, persist=True)
    print(f"Max buffer size set to {controller.get_config('controller.max_buffer_size')} and saved to config file")
    
    # Get the entire configuration
    print("\nEntire configuration:")
    all_config = controller.config_manager.get_all()
    print(all_config)
    
    # Clean up
    controller.stop()

# Run the main function if the script is executed directly
if __name__ == "__main__":
    main()