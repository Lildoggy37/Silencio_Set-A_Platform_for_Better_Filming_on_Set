"""Request-scoped MCP server. No network listener or public unauthenticated route."""
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx2
from fastmcp import FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.tools import Tool, ToolResult
from fastmcp.utilities.asgi_transport import StreamingASGITransport
from langchain.mcp import MCPAdapter
from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from silencio_set.accounts.errors import AuthError


class BusinessTool(Tool):
    # The bound service tool is private; never serialized into the MCP schema.
    business: object

    async def run(self, arguments):
        try:
            result = await self.business.ainvoke(arguments, config={'callbacks': []})
        except ValidationError:
            result = {'error': 'INVALID_TOOL_ARGUMENTS', 'message': '工具参数无效。'}
        except AuthError as error:
            result = {'error': error.code, 'message': error.public_message, 'http_status': error.status}
        return ToolResult(structured_content=result)


@asynccontextmanager
async def mcp_tools(bound_tools):
    server = FastMCP('Silencio Set · Diane', mask_error_details=True)
    for item in bound_tools:
        server.add_tool(BusinessTool(name=item.name, description=item.description,
            parameters=item.args_schema.model_json_schema(), business=item))
    app = server.http_app(path='/mcp', stateless_http=True, json_response=True)

    def http_client(**kwargs):
        return httpx2.AsyncClient(transport=StreamingASGITransport(app), **kwargs)

    transport = StreamableHttpTransport('http://127.0.0.1/mcp', httpx_client_factory=http_client)
    async with app.router.lifespan_context(app), MCPAdapter(transport) as adapter:
        discovered = {item.name: item for item in await adapter.list_tools()}
        tools = []
        for original in bound_tools:
            adapted = discovered[original.name]

            def wrap(selected):
                async def invoke(**arguments):
                    message = await selected.ainvoke({'name': selected.name, 'args': arguments,
                        'id': str(uuid4()), 'type': 'tool_call'}, config={'callbacks': []})
                    if message.status == 'error':
                        raise AuthError(503, 'MCP_TOOL_FAILED', '业务工具暂不可用。')
                    result = message.artifact['structured_content']
                    if result.get('error'):
                        raise AuthError(result.get('http_status', 400), result['error'], result['message'])
                    return result
                return invoke

            # Keep strict business argument validation before crossing the protocol.
            tools.append(StructuredTool(name=original.name, description=original.description,
                args_schema=original.args_schema, coroutine=wrap(adapted)))
        yield tools
