"""
Local test MCP server — Streamable HTTP transport on port 3001.
Provides simple tools for testing the MCP connector without any external services.

Start:    python mcp_server.py
URL:      http://localhost:3001/mcp

For Anthropic/OpenAI native MCP (requires public HTTPS):
  ngrok http 3001  →  use the https://xxx.ngrok.io/mcp URL

Free public alternative (no auth): https://api.usefulai.fun/mcp
"""

import json
import math
import datetime
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("llm-explorer-test")


@mcp.tool()
def get_datetime(timezone: str = "UTC") -> str:
    """Get the current date and time in UTC."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return json.dumps({
        "utc": now.isoformat(),
        "timestamp": int(now.timestamp()),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "day_of_week": now.strftime("%A"),
    })


@mcp.tool()
def echo(message: str) -> str:
    """Echo a message back. Use to verify MCP connectivity."""
    return f"MCP echo: {message}"


@mcp.tool()
def calculate(expression: str) -> str:
    """
    Evaluate a safe math expression.
    Supports: +, -, *, /, **, sqrt, sin, cos, tan, log, abs, pi, e
    Example: "sqrt(16) + 2**8"
    """
    safe = {
        "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "log": math.log, "log10": math.log10, "abs": abs, "round": round,
        "pi": math.pi, "e": math.e, "pow": pow, "min": min, "max": max,
        "floor": math.floor, "ceil": math.ceil,
    }
    try:
        result = eval(expression, {"__builtins__": {}}, safe)
        return json.dumps({"expression": expression, "result": result})
    except Exception as ex:
        return json.dumps({"error": str(ex)})


@mcp.tool()
def word_count(text: str) -> str:
    """Count words, characters, and lines in text."""
    return json.dumps({
        "words": len(text.split()),
        "characters": len(text),
        "lines": len(text.splitlines()),
    })


@mcp.tool()
def list_available_tools() -> str:
    """List all tools available on this MCP server."""
    return json.dumps({
        "server": "llm-explorer-test",
        "tools": ["get_datetime", "echo", "calculate", "word_count", "list_available_tools"],
        "transport": "Streamable HTTP",
        "local_url": "http://localhost:3001/mcp",
    })


if __name__ == "__main__":
    import uvicorn
    print("Starting LLM Explorer test MCP server...")
    print("  Local URL:  http://localhost:3001/mcp")
    print("  For public: ngrok http 3001")
    print()
    # Get the ASGI app and run it with uvicorn on port 3001
    app = mcp.streamable_http_app()
    uvicorn.run(app, host="127.0.0.1", port=3001)
