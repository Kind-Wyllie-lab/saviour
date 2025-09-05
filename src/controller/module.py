"""
Proposal for a ModuleState class which holds module state within the program?

It runs
network.get_discovered_modules()
For a top level overview of what modules have been _discovered_, and their IP addresses etc.
Then
health.get_module_state() or get_module_health() or get_module_status()
This should tell us whether a module is "online" or "offline"
- Online/offline state is based on whether it is sending heartbeats.
- Maybe it should also check the health of the communication socket? Health class could send something and see if it gets an ACK.
This should also report module status - NOT READY, READY, RECORDING, FAULT etc.
Controller can then run get_modules() here for a neatly packaged dict of modules which can be passed to frontend.
"""