"""
Client-side MCP executor for Streamable HTTP transport.
Used for providers without native API MCP support (Gemini),
and for testing local MCP servers.

MCP Streamable HTTP flow:
  1. POST /mcp with initialize → get session ID header
  2. POST /mcp (with session ID) → initialized notification
  3. POST /mcp (with session ID) → tools/list, tools/call, etc.
"""

import json
import asyncio
import httpx
from typing import Any


class MCPClient:
    """
    Async MCP client for Streamable HTTP transport.
    Handles session initialization automatically.
    """

    def __init__(self, url: str, token: str = None):
        self.url = url.rstrip("/")
        self.session_id: str | None = None
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def _req_headers(self) -> dict:
        h = dict(self.headers)
        if self.session_id:
            h["mcp-session-id"] = self.session_id
        return h

    async def _post(self, body: dict) -> dict | None:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(self.url, json=body, headers=self._req_headers())
            # Capture session ID from initialization response
            if "mcp-session-id" in r.headers:
                self.session_id = r.headers["mcp-session-id"]

            if r.status_code == 202:
                return None  # Notification accepted (no body)

            r.raise_for_status()

            ct = r.headers.get("content-type", "")
            if "text/event-stream" in ct:
                # Parse SSE stream — extract first data: line with valid JSON
                for line in r.text.splitlines():
                    line = line.strip()
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data and data != "[DONE]":
                            try:
                                return json.loads(data)
                            except json.JSONDecodeError:
                                pass
                return None

            if not r.text.strip():
                return None
            return r.json()

    async def initialize(self):
        """Perform MCP handshake. Called automatically by list_tools/call_tool."""
        if self.session_id:
            return
        resp = await self._post({
            "jsonrpc": "2.0", "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "llm-explorer", "version": "1.0"},
            },
        })
        # Send initialized notification (no response expected)
        await self._post({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })

    async def list_tools(self) -> list[dict]:
        """Return list of tool definitions from the MCP server."""
        await self.initialize()
        resp = await self._post({
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/list", "params": {},
        })
        if resp is None:
            return []
        return resp.get("result", {}).get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> str:
        """Call a named tool and return its result as a string."""
        await self.initialize()
        resp = await self._post({
            "jsonrpc": "2.0", "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        if resp is None:
            return ""
        content = resp.get("result", {}).get("content", [])
        if isinstance(content, list):
            return "\n".join(c.get("text", str(c)) for c in content)
        return str(content)


def mcp_tools_to_gemini_declarations(tools: list[dict]) -> list:
    """Convert MCP tool list to Gemini FunctionDeclaration objects."""
    from google.genai import types

    decls = []
    for t in tools:
        schema = t.get("inputSchema", {})
        props = {}
        for pname, pdef in schema.get("properties", {}).items():
            ptype = pdef.get("type", "string").upper()
            type_map = {"STRING": "STRING", "NUMBER": "NUMBER", "INTEGER": "INTEGER",
                        "BOOLEAN": "BOOLEAN", "ARRAY": "ARRAY", "OBJECT": "OBJECT"}
            props[pname] = types.Schema(
                type=type_map.get(ptype, "STRING"),
                description=pdef.get("description", ""),
            )
        decls.append(types.FunctionDeclaration(
            name=t["name"],
            description=t.get("description", ""),
            parameters=types.Schema(
                type="OBJECT",
                properties=props,
                required=schema.get("required", []),
            ) if props else None,
        ))
    return decls


async def test_mcp_server(url: str) -> dict:
    """Quick connectivity + tool-list test. Returns status dict."""
    try:
        client = MCPClient(url)
        tools = await client.list_tools()
        return {
            "ok": True,
            "tool_count": len(tools),
            "tools": [t["name"] for t in tools],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
