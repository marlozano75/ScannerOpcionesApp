"""Lanza la CLI de Claude (`claude -p`, sin interfaz) con un prompt y unas herramientas permitidas, y devuelve su respuesta.

La aplicación solo lee: las herramientas permitidas son siempre de lectura del conector de Tortuga. La CLI se ejecuta en un hilo
(`subprocess.run`) para no depender del tipo de bucle de eventos de Windows."""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import dataclass

TOOL_PREFIX = "mcp__claude_ai_Tortuga_Trades__"


class ClaudeRunError(RuntimeError):
    """La CLI de Claude no se pudo ejecutar o terminó mal."""


def tool_names(*names: str) -> str:
    return ",".join(TOOL_PREFIX + n for n in names)


def extract_json(text: str) -> str:
    """El JSON de la respuesta: del primer «{» al último «}» (el modelo a veces añade una frase delante o detrás)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ClaudeRunError("la respuesta no contiene un JSON: " + text.strip()[:200])
    return text[start:end + 1]


@dataclass(frozen=True)
class ToolCall:
    """Una llamada de Claude a una herramienta del conector, con lo que la herramienta devolvió TAL CUAL (sin que el modelo lo copie)."""
    name: str           # sin el prefijo del conector («get_market_radar»)
    input: dict
    output: str


def parse_tool_calls(stream: str) -> list[ToolCall]:
    """Las llamadas a herramientas (y su resultado bruto) de la salida `stream-json` de la CLI. Ignora lo que no sea una herramienta del conector."""
    uses: dict[str, tuple[str, dict]] = {}
    calls: list[ToolCall] = []
    for line in stream.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        content = (msg.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and str(block.get("name", "")).startswith(TOOL_PREFIX):
                uses[block["id"]] = (block["name"][len(TOOL_PREFIX):], block.get("input") or {})
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in uses:
                body = block.get("content")
                if isinstance(body, list):
                    body = "".join(str(b.get("text", "")) for b in body if isinstance(b, dict))
                name, args = uses[block["tool_use_id"]]
                calls.append(ToolCall(name, args, str(body or "")))
    return calls


def _run(command: str, prompt: str, tools: str, model: str, max_turns: int, timeout: int, stream: bool = False) -> str:
    exe = shutil.which(command)
    if exe is None:
        raise ClaudeRunError(f"no se encuentra la CLI de Claude («{command}») en el PATH: instálala e inicia sesión")
    try:
        done = subprocess.run(
            [exe, "-p", "--model", model, "--allowedTools", tools, "--max-turns", str(max_turns),
             *(["--output-format", "stream-json", "--verbose"] if stream else ["--output-format", "text"])],
            input=prompt.encode("utf-8"), capture_output=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        raise ClaudeRunError(f"claude no terminó en {timeout} s") from None
    except OSError as exc:
        raise ClaudeRunError(f"no se pudo ejecutar claude: {exc}") from exc
    out = done.stdout.decode("utf-8", errors="replace")
    if done.returncode != 0:
        raise ClaudeRunError(f"claude terminó con el código {done.returncode}: {(done.stderr.decode('utf-8', errors='replace') or out).strip()[:300]}")
    if not stream and out.strip() == "FAILED":
        raise ClaudeRunError("Claude no pudo leer el conector de Tortuga (devolvió FAILED)")
    return out


async def run_claude(command: str, prompt: str, tools: str, model: str, max_turns: int, timeout: int) -> str:
    return await asyncio.to_thread(_run, command, prompt, tools, model, max_turns, timeout)


async def run_claude_calls(command: str, prompt: str, tools: str, model: str, max_turns: int, timeout: int) -> list[ToolCall]:
    """Como `run_claude`, pero devuelve las llamadas a herramientas con su resultado bruto: la respuesta de texto del modelo no se usa
    (así no hay que pedirle que copie decenas de filas, donde se salta algunas)."""
    return parse_tool_calls(await asyncio.to_thread(_run, command, prompt, tools, model, max_turns, timeout, True))
