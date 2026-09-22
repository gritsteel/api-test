"""실제 stdio 전송으로 서버를 띄워 initialize + tools/list + resources/list 를 확인한다."""
import asyncio
import os
import pathlib
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(pathlib.Path(__file__).resolve().parent.parent / "weather.py")

async def main():
    env = dict(os.environ, KMA_API_KEY="dummy-for-smoke-test")
    params = StdioServerParameters(command=sys.executable, args=[SERVER], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server:", init.server_info.name, init.server_info.version)
            print("protocol:", init.protocol_version)
            tools = (await session.list_tools()).tools
            for t in tools:
                print(f"tool: {t.name}  out={(t.output_schema or {}).get('type')}")
            res = (await session.list_resources()).resources
            for r in res:
                print("resource:", r.uri, "-", r.name)
            content = await session.read_resource(res[0].uri)
            first = content.contents[0].text.splitlines()
            print("resource lines:", len(first), "| head:", first[1] if len(first) > 1 else "")
            # 키가 가짜라 실패해야 정상. 어떤 식으로 실패하는지 본다.
            r = await session.call_tool("get_current_weather", {"location": "서울"})
            print("call is_error:", r.is_error)
            print("call text:", (r.content[0].text[:200] if r.content else ""))
            r2 = await session.call_tool("get_current_weather", {"location": "없는동네"})
            print("bad-location is_error:", r2.is_error, "|", (r2.content[0].text[:120] if r2.content else ""))
            print("SMOKE OK")

asyncio.run(main())
