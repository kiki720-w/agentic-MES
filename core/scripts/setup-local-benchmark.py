"""Download pinned public test assets; never install a system service or change the active model."""

import hashlib
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2] / ".local-models"
ASSETS = [
    ("llama-b10936-bin-win-vulkan-x64.zip",
     "https://github.com/ggml-org/llama.cpp/releases/download/b10936/llama-b10936-bin-win-vulkan-x64.zip",
     "156093831ceded51c3929ad8c1bf5daca86b60761d43dd98b3752df9c6336b25"),
    ("Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
     ("https://huggingface.co/bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF/resolve/"
     "ae44f08e1392f39c0e474af10c3ff8355c8b6688/Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf"),
     "2fde00ce69dd4899c70d020845e2638353015bba0fdf161b3eb965f2bca4464e"),
]


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def download(asset: tuple[str, str, str]) -> Path:
    name, url, expected = asset
    target = ROOT / name
    if target.exists() and digest(target) == expected:
        print(f"Verified existing asset: {name}", flush=True)
        return target
    temporary = target.with_suffix(target.suffix + ".part")
    count = 0
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as response:
        response.raise_for_status()
        with temporary.open("wb") as handle:
            for chunk in response.iter_bytes(4 * 1024 * 1024):
                handle.write(chunk)
                count += len(chunk)
                if count % (256 * 1024 * 1024) == 0:
                    print(f"{name}: {count // (1024 * 1024)} MiB", flush=True)
    if digest(temporary) != expected:
        raise ValueError("download checksum mismatch")
    temporary.replace(target)
    print(f"SHA256 verified: {name}", flush=True)
    return target


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=2) as executor:
        files = list(executor.map(download, ASSETS))
    runtime = ROOT / "llama-b10936-vulkan"
    runtime.mkdir(exist_ok=True)
    with zipfile.ZipFile(files[0]) as archive:
        for member in archive.infolist():
            if not (runtime / member.filename).resolve().is_relative_to(runtime.resolve()):
                raise ValueError("unsafe archive path")
        archive.extractall(runtime)
    print(f"Local benchmark assets ready: {ROOT}")


if __name__ == "__main__":
    main()
