#!/usr/bin/env python3
"""
Generate Postman collection from OpenAPI specification.

Usage:
    python scripts/generate_postman.py [output_file]

This script:
1. Starts the FastAPI app temporarily
2. Fetches the OpenAPI spec
3. Converts it to Postman collection format
4. Saves to docs/postman_collection.json
"""

import json
import sys
from pathlib import Path


def convert_openapi_to_postman(openapi_spec: dict) -> dict:
    """Convert OpenAPI 3.x spec to Postman Collection v2.1 format."""

    collection = {
        "info": {
            "name": openapi_spec.get("info", {}).get("title", "API Collection"),
            "description": openapi_spec.get("info", {}).get("description", ""),
            "version": openapi_spec.get("info", {}).get("version", "1.0.0"),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": [],
        "variable": [
            {"key": "baseUrl", "value": "http://localhost:8000", "type": "string"},
            {"key": "apiKey", "value": "", "type": "string"},
        ],
    }

    # Group endpoints by tag
    tag_groups: dict[str, list] = {}

    paths = openapi_spec.get("paths", {})
    for path, methods in paths.items():
        for method, details in methods.items():
            if method in ("get", "post", "put", "patch", "delete"):
                tags = details.get("tags", ["Other"])
                tag = tags[0] if tags else "Other"

                if tag not in tag_groups:
                    tag_groups[tag] = []

                # Build request
                request = {
                    "name": details.get("summary", details.get("operationId", path)),
                    "request": {
                        "method": method.upper(),
                        "header": [
                            {"key": "Content-Type", "value": "application/json"},
                            {"key": "X-API-Key", "value": "{{apiKey}}"},
                        ],
                        "url": {
                            "raw": "{{baseUrl}}" + path,
                            "host": ["{{baseUrl}}"],
                            "path": [p for p in path.split("/") if p],
                        },
                    },
                    "response": [],
                }

                # Add description
                if details.get("description"):
                    request["request"]["description"] = details["description"]

                # Add request body for POST/PUT/PATCH
                if method in ("post", "put", "patch"):
                    request_body = details.get("requestBody", {})
                    json_content = request_body.get("content", {}).get(
                        "application/json", {}
                    )
                    schema = json_content.get("schema", {})

                    # Generate example body from schema
                    example_body = generate_example_from_schema(schema, openapi_spec)
                    if example_body:
                        request["request"]["body"] = {
                            "mode": "raw",
                            "raw": json.dumps(example_body, indent=2),
                            "options": {"raw": {"language": "json"}},
                        }

                # Add path parameters
                path_params = []
                for param in details.get("parameters", []):
                    if param.get("in") == "path":
                        path_params.append(
                            {
                                "key": param["name"],
                                "value": f"<{param['name']}>",
                                "description": param.get("description", ""),
                            }
                        )
                if path_params:
                    request["request"]["url"]["variable"] = path_params

                # Add query parameters
                query_params = []
                for param in details.get("parameters", []):
                    if param.get("in") == "query":
                        query_params.append(
                            {
                                "key": param["name"],
                                "value": "",
                                "description": param.get("description", ""),
                                "disabled": not param.get("required", False),
                            }
                        )
                if query_params:
                    request["request"]["url"]["query"] = query_params

                tag_groups[tag].append(request)

    # Create folder structure
    for tag, requests in tag_groups.items():
        folder = {"name": tag, "item": requests}
        collection["item"].append(folder)

    return collection


def generate_example_from_schema(
    schema: dict, openapi_spec: dict, depth: int = 0
) -> dict | list | str | int | float | bool | None:
    """Generate example value from JSON schema."""
    if depth > 5:
        return None

    # Handle $ref
    if "$ref" in schema:
        ref_path = schema["$ref"].split("/")
        ref_schema = openapi_spec
        for part in ref_path[1:]:  # Skip leading #
            ref_schema = ref_schema.get(part, {})
        return generate_example_from_schema(ref_schema, openapi_spec, depth + 1)

    schema_type = schema.get("type", "object")

    if "example" in schema:
        return schema["example"]

    if "default" in schema:
        return schema["default"]

    if schema_type == "object":
        result = {}
        properties = schema.get("properties", {})
        for prop_name, prop_schema in properties.items():
            result[prop_name] = generate_example_from_schema(
                prop_schema, openapi_spec, depth + 1
            )
        return result

    if schema_type == "array":
        items = schema.get("items", {})
        item_example = generate_example_from_schema(items, openapi_spec, depth + 1)
        return [item_example] if item_example else []

    if schema_type == "string":
        if schema.get("format") == "uuid":
            return "00000000-0000-0000-0000-000000000000"
        if schema.get("format") == "date":
            return "2024-01-01"
        if schema.get("format") == "date-time":
            return "2024-01-01T00:00:00Z"
        if schema.get("format") == "email":
            return "example@example.com"
        if "enum" in schema:
            return schema["enum"][0]
        return "string"

    if schema_type == "integer":
        return schema.get("minimum", 0)

    if schema_type == "number":
        return schema.get("minimum", 0.0)

    if schema_type == "boolean":
        return True

    return None


def main() -> int:
    output_file = sys.argv[1] if len(sys.argv) > 1 else "docs/postman_collection.json"

    # Try to load OpenAPI spec from file or generate it
    openapi_path = Path("openapi.json")

    if openapi_path.exists():
        with open(openapi_path) as f:
            openapi_spec = json.load(f)
    else:
        # Generate from FastAPI app
        try:
            from src.api.main import app

            openapi_spec = app.openapi()
        except Exception as e:
            print(f"Error loading OpenAPI spec: {e}")
            print("Make sure the API can be imported or provide openapi.json file")
            return 1

    # Convert to Postman format
    postman_collection = convert_openapi_to_postman(openapi_spec)

    # Save collection
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(postman_collection, f, indent=2)

    print(f"Postman collection saved to: {output_path}")
    print(f"Endpoints: {sum(len(folder['item']) for folder in postman_collection['item'])}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
