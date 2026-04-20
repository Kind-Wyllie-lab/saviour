import sys
import os

# Make src/ importable without package prefix so tests can use
# 'from modules.ptp import PTP' as well as 'from src.modules.ptp import PTP'.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
