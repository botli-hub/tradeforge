import os
import signal

for pid in [98497, 98258]:
    try:
        os.kill(pid, signal.SIGKILL)
        print(f'killed {pid}')
    except ProcessLookupError:
        print(f'missing {pid}')
