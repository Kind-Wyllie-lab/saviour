#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Service Manager

The service manager is responsible for discovering, registering and unregistering services (modules) with the controller.

"""

from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo # for mDNS module discovery
import os
import socket
import uuid

class ControllerServiceManager():
    def __init__(self):
        # Get the ip address of the controller
        if os.name == 'nt': # Windows
            self.ip = socket.gethostbyname(socket.gethostname())
        else: # Linux/Unix
            self.ip = os.popen('hostname -I').read().split()[0]

        # Initialize zeroconf
        self.zeroconf = Zeroconf()
        self.service_info = ServiceInfo(
            "_controller._tcp.local.", # the service type - tcp protocol, local domain
            "controller._controller._tcp.local.", # a unique name for the service to advertise itself
            addresses=[socket.inet_aton(self.ip)], # the ip address of the controller
            port=5000, # the port number of the controller
            properties={'type': 'controller'} # the properties of the service
        )
        self.zeroconf.register_service(self.service_info) # register the service with the above info
        # self.browser = ServiceBrowser(self.zeroconf, "_module._tcp.local.", self) # Browse for habitat_module services"

    def cleanup(self):
        """Cleanup zeroconf resources"""
        if hasattr(self, 'zeroconf'):
            try:
                self.zeroconf.unregister_service(self.service_info)
                self.zeroconf.close()
            except:
                pass # Ignore errors during cleanup


