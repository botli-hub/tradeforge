import subprocess

result = subprocess.run(['ps', '-ax', '-o', 'pid=,command='], capture_output=True, text=True, check=True)
for line in result.stdout.splitlines():
    if 'python' in line.lower() or 'uvicorn' in line.lower():
        print(line)
