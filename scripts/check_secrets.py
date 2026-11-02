"""Проверка подготовленных к публикации Git-файлов на секреты без вывода значений."""

import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [rb"sk-or-v1-[A-Za-z0-9_-]{40,}", rb"gh[pousr]_[A-Za-z0-9]{30,}",
            rb"github_pat_[A-Za-z0-9_]{40,}", rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"]


def main() -> int:
    """Проверяет содержимое Git-индекса и сообщает только имена файлов с проблемами."""
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
    paths = [name.decode("utf-8") for name in result.stdout.split(b"\0") if name]
    problems = []
    for name in paths:
        if Path(name).name in {".env", "secrets.toml"}:
            problems.append(name)
            continue
        blob = subprocess.run(["git", "show", f":{name}"], cwd=root, capture_output=True, check=True).stdout
        if any(re.search(pattern, blob) for pattern in PATTERNS):
            problems.append(name)
    if problems:
        print("Публикация заблокирована; проверьте файлы: " + ", ".join(problems))
        return 1
    print(f"Проверено файлов: {len(paths)}. Секреты по известным сигнатурам не обнаружены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
