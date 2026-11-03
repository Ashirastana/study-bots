"""Озвучка испанского.
1) На Mac — встроенные голоса macOS (say), без интернета.
2) На сервере (Linux) — нейроголоса Microsoft через edge-tts (бесплатно, без ключа).
Если ни то ни другое недоступно — None, бот честно скажет, что озвучка недоступна."""
import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile

log = logging.getLogger("tts")
EDGE_VOICE = os.getenv("EDGE_VOICE", "es-ES-ElviraNeural")
_voice = {"name": None, "err": ""}


# ---------------- macOS ----------------

def _try_voice(name: str) -> bool:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "t.aiff")
            r = subprocess.run(["say", "-v", name, "-o", out, "hola"], capture_output=True, timeout=30)
            return r.returncode == 0 and os.path.getsize(out) > 1000
    except Exception as e:
        _voice["err"] = repr(e)
        return False


def mac_voice():
    if _voice["name"]:
        return _voice["name"]
    if not shutil.which("say"):
        return None
    for cand in ["Paulina", "Mónica", "Monica", "Jorge", "Juan", "Marisol"]:
        if _try_voice(cand):
            _voice["name"] = cand
            return cand
    return None


def edge_available() -> bool:
    try:
        import edge_tts  # noqa: F401
        return True
    except Exception:
        return False


def spanish_voice():
    """Имя голоса, которым будем озвучивать (или None)."""
    return mac_voice() or (f"edge:{EDGE_VOICE}" if edge_available() else None)


def _mac_render(text: str, rate: int) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        aiff, m4a = os.path.join(tmp, "s.aiff"), os.path.join(tmp, "s.m4a")
        subprocess.run(["say", "-v", mac_voice(), "-r", str(rate), "-o", aiff, text], check=True, timeout=120)
        subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", aiff, m4a], check=True, timeout=120)
        with open(m4a, "rb") as f:
            return f.read()


async def _edge_render(text: str, rate: int) -> bytes:
    import edge_tts
    pct = max(-50, min(50, int((rate - 175) / 175 * 100)))  # 175 слов/мин ≈ обычная скорость
    comm = edge_tts.Communicate(text, EDGE_VOICE, rate=f"{pct:+d}%")
    buf = bytearray()
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    return bytes(buf)


async def speak(text: str, rate: int = 150):
    """(аудио, имя_файла) или (None, None)."""
    try:
        if await asyncio.to_thread(mac_voice):
            return await asyncio.to_thread(_mac_render, _mac_text(text), rate), "audio.m4a"
        if edge_available():
            data = await _edge_render(_edge_text(text), rate)
            return (data, "audio.mp3") if data else (None, None)
    except Exception as e:
        log.warning("TTS error: %s", e)
    return None, None


PAUSE = "⁣"  # маркер паузы внутри скрипта


def _mac_text(t: str) -> str:
    return t.replace(PAUSE + PAUSE, " [[slnc 2500]] ").replace(PAUSE, " [[slnc 1500]] ")


def _edge_text(t: str) -> str:
    return t.replace(PAUSE + PAUSE, " ... ... ").replace(PAUSE, " ... ")


def words_script(words: list[str]) -> str:
    """Каждое слово дважды, с паузами, чтобы успеть повторить."""
    parts = []
    for w in words:
        w = w.replace(" / ", ", ").replace("…", "").replace("  ", " ").strip()
        parts.append(f"{w}.{PAUSE}{w}.{PAUSE}{PAUSE}")
    return " ".join(parts)


def duration(audio: bytes, fname: str = "audio.m4a"):
    """Длительность в секундах (если можем узнать), чтобы Telegram показывал время."""
    try:
        if shutil.which("afinfo"):
            with tempfile.NamedTemporaryFile(suffix=os.path.splitext(fname)[1]) as f:
                f.write(audio); f.flush()
                out = subprocess.run(["afinfo", f.name], capture_output=True, text=True, timeout=10).stdout
            m = re.search(r"estimated duration:\s*([\d.]+)", out)
            return max(1, round(float(m.group(1)))) if m else None
        if fname.endswith(".mp3"):
            return max(1, round(len(audio) * 8 / 48000))  # edge-tts: 48 кбит/с
    except Exception:
        return None
    return None
