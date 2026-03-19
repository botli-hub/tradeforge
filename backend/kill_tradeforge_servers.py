import os
import signal
import subprocess

TARGET = '/Users/alibot/.openclaw/workspace/forge/projects/tradeforge/backend'
SELF = os.getpid()

result = subprocess.run(['ps', '-ax', '-o', 'pid=,command='], capture_output=True, text=True, check=True)
killed = []

for line in result.stdout.splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        pid_str, command = line.split(None, 1)
    except ValueError:
        continue
    pid = int(pid_str)
    if pid == SELF:
        continue
    if TARGET not in command:
        continue
    if any(key in command for key in ['run.py', 'run_test_8001.py', 'app.main:app']):
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append({'pid': pid, 'command': command})
        except ProcessLookupError:
            pass

print(killed)
