import sys
import os
import time
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from module import Module

# Main entry point
def main():
    """Main entry point for the controller application"""
    module = Module(module_type="generic",
                    config={})
    print("Habitat Module initialized")

    # Start the main loop
    module.start()

    # Keep running until interrupted
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        module.stop()

# Run the main function if the script is executed directly
if __name__ == "__main__":
    main()
