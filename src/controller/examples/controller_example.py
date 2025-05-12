import sys
import os
import time
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from controller import HabitatController

# Main entry point
def main():
    """Main entry point for the controller application"""
    controller = HabitatController()

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
