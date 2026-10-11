"""Lossless wire projection for OpenAI's closed-object union subset.

Domain schemas, prompts and parsers remain authoritative and unchanged.
Only the supported closed-object + enum-restriction conjunction is expanded.
"""
from copy import deepcopy
from .agent_providers import ProviderError


def strict_schema(schema):
    def reject():
        raise ProviderError('unsupported_schema_projection', 'OpenAI schema projection unavailable')

    def visit(node):
        if not isinstance(node, dict):
            reject()
        node = deepcopy(node)
        if node.get('type') == 'object' and 'anyOf' in node:
            # A AND (B OR C) == (A AND B) OR (A AND C). Never drop A.
            if set(node) != {'type', 'properties', 'required', 'additionalProperties', 'anyOf'}:
                reject()
            properties = node['properties']
            if not isinstance(properties, dict) or node['additionalProperties'] is not False or set(node['required']) != set(properties):
                reject()
            branches = node.pop('anyOf')
            if not isinstance(branches, list) or not branches:
                reject()
            expanded = []
            for branch in branches:
                if not isinstance(branch, dict) or set(branch) != {'properties'} or not isinstance(branch['properties'], dict):
                    reject()
                full = deepcopy(node)
                for name, restriction in branch['properties'].items():
                    if name not in properties or not isinstance(restriction, dict) or set(restriction) != {'enum'}:
                        reject()
                    base = properties[name]
                    narrowed = restriction['enum']
                    # Bounded enum intersection; no invented type or permissive fallback.
                    if base.get('type') != 'string' or not isinstance(base.get('enum'), list) or not isinstance(narrowed, list) or not narrowed or any(not isinstance(v, str) or v not in base['enum'] for v in narrowed):
                        reject()
                    full['properties'][name]['enum'] = deepcopy(narrowed)
                expanded.append(visit(full))
            return {'anyOf': expanded}
        if 'properties' in node:
            node['properties'] = {k: visit(v) for k, v in node['properties'].items()}
        if 'items' in node:
            node['items'] = visit(node['items'])
        if 'anyOf' in node:
            node['anyOf'] = [visit(branch) for branch in node['anyOf']]
        return node

    return visit(schema)
