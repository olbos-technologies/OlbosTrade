"""Keep authorization codes out of Uvicorn's request-target access log."""
from starlette.datastructures import QueryParams


class BrokerOAuthQueryRedaction:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == "/api/brokers/oauth/alpaca/callback":
            scope["broker_oauth_query"] = QueryParams(scope.get("query_string", b""))
            scope["query_string"] = b""
        await self.app(scope, receive, send)
