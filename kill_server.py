import subprocess
import json


# Что считаем "нашими" процессами (по командной строке)
SERVER_MARKS = ("uvicorn", "backend.main", "backend\\main")
FFMPEG_MARKS = ("ffmpeg",)


def get_procs():
    try:
        out = subprocess.check_output(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -in @('python.exe','pythonw.exe','ffmpeg.exe') } | "
                "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress",
            ],
            stderr=subprocess.DEVNULL, text=True,
        )
    except Exception as e:
        print("Не удалось получить список процессов:", e)
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    return [(p.get("ProcessId"), p.get("Name", ""), (p.get("CommandLine") or "")) for p in data]


def is_ours(name, cmd):
    low = (cmd or "").lower()
    if name.lower().startswith("python"):
        return any(m in low for m in SERVER_MARKS)
    if name.lower().startswith("ffmpeg"):
        # ffmpeg от нашего приложения качает/ремуксит — убиваем все оставшиеся
        return True
    return False


def main():
    procs = get_procs()
    if not procs:
        print("Python/ffmpeg процессы не найдены. Всё чисто.")
        return

    print("Найдены процессы:")
    targets = []
    for pid, name, cmd in procs:
        ours = is_ours(name, cmd)
        tag = "  <-- НАШ" if ours else ""
        if ours:
            targets.append((pid, name, cmd))
        print(f"  [PID {pid}] {name}: {cmd[:100]}{tag}")

    if not targets:
        print("\nНаших (серверных/python от приложения/зависший ffmpeg) процессов не найдено.")
        return

    print(f"\nУбиваю {len(targets)} наших процесса...")
    for pid, name, cmd in targets:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            print(f"  убит PID {pid} ({name})")
        except Exception as e:
            print(f"  не удалось убить PID {pid}: {e}")
    print("Готово.")


if __name__ == "__main__":
    main()
