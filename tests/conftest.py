import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))            # randprog
sys.path.insert(0, str(HERE.parent))     # пакет flowgen без установки
