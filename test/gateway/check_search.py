import asyncio

from fastmcp import Client
from mcp.client.session_group import StreamableHttpParameters

from nomad.gateway import CodeModeGateway, GatewayConfig


async def get_host_tools():
    # Pass server directly - no deployment needed
    async with Client("http://localhost:8000/mcp") as client:
        tools = await client.list_tools()
        return tools


async def test_nomad_code_mode():
    gateway_config = GatewayConfig(
        servers={"nomad": StreamableHttpParameters(url="http://localhost:8000/mcp")}
    )
    gateway = CodeModeGateway(gateway_config)
    async with Client(gateway.fastmcp) as client:
        tools = await client.list_tools()
        print(tools)
        results = await client.call_tool("search_tools", {"query": None})
        print(results)


def main():
    host_tools = asyncio.run(get_host_tools())
    print("host tools:", [tool.name for tool in host_tools])

    asyncio.run(test_nomad_code_mode())


if __name__ == "__main__":
    main()
