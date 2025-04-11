import subprocess

def startPTP():
    subprocess.run(["sudo","systemctl", "start", "ptp4l.service"])
    subprocess.run(["sudo","systemctl", "start", "phc2sys.service"])
    
def restartPTP():
    subprocess.run(["sudo","systemctl", "restart", "ptp4l.service"])
    subprocess.run(["sudo","systemctl", "restart", "phc2sys.service"])
    
def stopPTP():
    subprocess.run(["sudo","systemctl", "stop", "ptp4l.service"])
    subprocess.run(["sudo","systemctl", "stop", "phc2sys.service"])
    
def statusPTP():
    subprocess.run(["sudo","systemctl", "status", "ptp4l.service"])
    subprocess.run(["sudo","systemctl", "status", "phc2sys.service"])
    
def testEcho():
    subprocess.run(["echo", "hello world"])

while True:
    cmd = input("\nEnter your selection: ")
    
    match cmd:
        case "1":
            startPTP()
        case "2":
            restartPTP()
        case "3":
            stopPTP()
        case "4":
            statusPTP()
        case "5":
            testEcho()