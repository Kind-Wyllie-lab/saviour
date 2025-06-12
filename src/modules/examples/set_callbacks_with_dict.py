#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Example program demonstrating dictionary-based callback patterns.

This shows different ways to:
1. Define callbacks
2. Register callbacks using dictionaries
3. Inherit and extend callbacks
4. Use different types of callbacks (methods, lambdas, functions)
"""

import logging
from typing import Dict, Callable, Any
import time

# First, let's create a simple class that will use callbacks
class CallbackHandler:
    def __init__(self):
        # Initialize an empty dictionary to store our callbacks
        self.callbacks: Dict[str, Callable[..., Any]] = {}
        
    def set_callbacks(self, callbacks: Dict[str, Callable[..., Any]]):
        """Register multiple callbacks at once using a dictionary
        
        Args:
            callbacks: Dictionary mapping callback names to their functions
        """
        # Update our callbacks dictionary with the new callbacks
        self.callbacks.update(callbacks)
        
    def execute_callback(self, name: str, *args, **kwargs) -> Any:
        """Execute a callback by name
        
        Args:
            name: Name of the callback to execute
            *args: Positional arguments to pass to the callback
            **kwargs: Keyword arguments to pass to the callback
            
        Returns:
            The result of the callback execution
        """
        if name not in self.callbacks:
            raise KeyError(f"Callback '{name}' not found")
        return self.callbacks[name](*args, **kwargs)

# Now let's create a class that will provide callbacks
class Sensor:
    def __init__(self, name: str):
        self.name = name
        self.value = 0
        
    def read_value(self) -> float:
        """Method that will be used as a callback"""
        self.value += 1
        return self.value
        
    def get_status(self) -> dict:
        """Another method that will be used as a callback"""
        return {
            "name": self.name,
            "value": self.value,
            "timestamp": time.time()
        }

def main():
    # Create a handler
    handler = CallbackHandler()
    
    # Create a sensor
    sensor = Sensor("temperature")
    
    # Example 1: Register callbacks using methods
    handler.set_callbacks({
        "read": sensor.read_value,  # Method reference
        "status": sensor.get_status  # Another method reference
    })
    
    # Example 2: Register callbacks using lambda functions
    handler.set_callbacks({
        "timestamp": lambda: time.time(),  # Lambda with no arguments
        "add": lambda x, y: x + y  # Lambda with arguments
    })
    
    # Example 3: Register callbacks using regular functions
    def multiply(x: float, y: float) -> float:
        return x * y
        
    handler.set_callbacks({
        "multiply": multiply
    })
    
    # Example 4: Extend existing callbacks
    # First, get current callbacks
    current_callbacks = handler.callbacks.copy()
    # Add new callbacks while preserving existing ones
    handler.set_callbacks({
        **current_callbacks,  # Spread existing callbacks
        "new_callback": lambda: "I'm new!"
    })
    
    # Now let's try using our callbacks
    print("Reading sensor:", handler.execute_callback("read"))  # Should print 1
    print("Reading sensor again:", handler.execute_callback("read"))  # Should print 2
    print("Sensor status:", handler.execute_callback("status"))
    print("Current timestamp:", handler.execute_callback("timestamp"))
    print("Adding numbers:", handler.execute_callback("add", 5, 3))  # Should print 8
    print("Multiplying numbers:", handler.execute_callback("multiply", 4, 2))  # Should print 8
    print("New callback:", handler.execute_callback("new_callback"))

if __name__ == "__main__":
    main()
