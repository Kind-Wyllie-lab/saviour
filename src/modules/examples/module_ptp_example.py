import sys
import os
import time
import argparse
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from module_ptp_manager import PTPManager, PTPRole, PTPError
import logging
import subprocess

def main():
    ptp = PTPManager(role=PTPRole.SLAVE,
                            logger=logging.getLogger("testlogger"))

    ptp._check_required_packages()

    ptp._validate_interface()

    ptp._check_ptp_running()

    
    print(f"{ptp.active_ptp4l_processes}, {ptp.active_phc2sys_processes}")

    list



    # ptp4l_proc = subprocess.Popen(ptp.ptp4l_args)

    # ptp.start()

    # try:
    #     while True:
    #         status = ptp.get_status()
    #         print("\nPTP Status:")
    #         print(f"  Role: {status['role']}")
    #         print(f"  Status: {status['status']}")
    #         print(f"  Last Sync: {status['last_sync']}")
    #         print(f"  Last Offset: {status['last_offset']} ns")
    #         print(f"  Synchronized: {ptp.is_synchronized()}")
    #         time.sleep(2)
    # except KeyboardInterrupt:
    #     print("\nShutting down...")
    #     ptp.stop()

# def main():
#     parser = argparse.ArgumentParser(description='PTP Manager Example')
#     parser.add_argument('--role', choices=['master', 'slave'], default='master',
#                       help='PTP role (master or slave)')
#     parser.add_argument('--interface', default='eth0',
#                       help='Network interface to use')
#     parser.add_argument('--master-address',
#                       help='Master address (required for slave mode)')
#     args = parser.parse_args()
#
#     try:
#         # Create PTP manager with appropriate role
#         if args.role == 'slave' and not args.master_address:
#             print("Error: --master-address is required for slave mode")
#             sys.exit(1)
#
#         ptp = PTPManager(
#             role=PTPRole.SLAVE if args.role == 'slave' else PTPRole.MASTER,
#             interface=args.interface,
#             master_address=args.master_address
#         )
#
#         print(f"Starting PTP in {args.role} mode on {args.interface}")
#         if args.role == 'slave':
#             print(f"Connecting to master at {args.master_address}")
#
#         ptp.start()
#
#         try:
#             while True:
#                 status = ptp.get_status()
#                 print("\nPTP Status:")
#                 print(f"  Role: {status['role']}")
#                 print(f"  Status: {status['status']}")
#                 print(f"  Last Sync: {status['last_sync']}")
#                 print(f"  Last Offset: {status['last_offset']} ns")
#                 print(f"  Synchronized: {ptp.is_synchronized()}")
#                 time.sleep(2)
#         except KeyboardInterrupt:
#             print("\nShutting down...")
#             ptp.stop()
#
#     except PTPError as e:
#         print(f"\nError: {e}")
#         if "must be run as root" in str(e):
#             print("\nPlease run this program with sudo:")
#             print(f"sudo python {os.path.basename(__file__)} {' '.join(sys.argv[1:])}")
#         sys.exit(1)
#     except Exception as e:
#         print(f"\nUnexpected error: {e}")
#         sys.exit(1)

if __name__ == "__main__":
    main()
