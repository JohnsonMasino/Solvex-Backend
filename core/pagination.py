from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response({
            "success": True,
            "message": "Success",
            "data": {
                "count": self.page.paginator.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data,
            },
        })

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "properties": {
                "success": {"type": "boolean", "example": True},
                "message": {"type": "string", "example": "Success"},
                "data": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer", "example": 42},
                        "next": {"type": "string", "nullable": True, "format": "uri"},
                        "previous": {"type": "string", "nullable": True, "format": "uri"},
                        "results": schema,
                    },
                },
            },
        }
