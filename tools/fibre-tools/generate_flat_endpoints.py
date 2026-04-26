#!/usr/bin/env python3
"""
Generate flat_endpoints.json and flat_endpoints.hpp from odrive-interface.yaml.

Produces:
  - flat_endpoints.json:  { "endpoints": { "path": {"id": N, "type": T, "access": A}, ... } }
  - flat_endpoints.hpp:   C++ header with EndpointInfo table + find_endpoint_by_id()

Usage:
  python generate_flat_endpoints.py --definitions odrive-interface.yaml --output flat_endpoints.json

The C++ header is written as an extra output alongside the JSON file.
"""

import yaml
import json
import argparse
import sys
from collections import OrderedDict

# ---------------------------------------------------------------------------
# YAML loader (copied from interface_generator.py)
# ---------------------------------------------------------------------------

class SafeLineLoader(yaml.SafeLoader):
    pass

def construct_mapping(loader, node):
    loader.flatten_mapping(node)
    return OrderedDict(loader.construct_pairs(node))

SafeLineLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping
)

# ---------------------------------------------------------------------------
# Built-in value types (same as interface_generator.py)
# ---------------------------------------------------------------------------

BUILTIN_TYPES = {
    'bool': {'c_name': 'bool', 'py_type': 'bool'},
    'float32': {'c_name': 'float', 'py_type': 'float'},
    'uint8': {'c_name': 'uint8_t', 'py_type': 'int'},
    'uint16': {'c_name': 'uint16_t', 'py_type': 'int'},
    'uint32': {'c_name': 'uint32_t', 'py_type': 'int'},
    'uint64': {'c_name': 'uint64_t', 'py_type': 'int'},
    'int8': {'c_name': 'int8_t', 'py_type': 'int'},
    'int16': {'c_name': 'int16_t', 'py_type': 'int'},
    'int32': {'c_name': 'int32_t', 'py_type': 'int'},
    'int64': {'c_name': 'int64_t', 'py_type': 'int'},
    'endpoint_ref': {'c_name': 'endpoint_ref_t', 'py_type': 'object'},
}

# Canonical type name used in the flat JSON (matches flat_endpoints_6.11.json)
CANONICAL_TYPE_MAP = {
    'bool': 'bool',
    'float32': 'float',
    'uint8': 'uint8',
    'uint16': 'uint16',
    'uint32': 'uint32',
    'uint64': 'uint64',
    'int8': 'int8',
    'int16': 'int16',
    'int32': 'int32',
    'int64': 'int64',
}

# C++ type code for the endpoint table (matches the enum in flat_endpoints.hpp)
TYPE_CODE_MAP = {
    'bool': 0, 'uint8': 1, 'int8': 2,
    'uint16': 3, 'int16': 4,
    'uint32': 5, 'int32': 6, 'float': 7,
    'uint64': 8, 'int64': 9,
}

BYTE_SIZE_MAP = {
    'bool': 1, 'uint8': 1, 'int8': 1,
    'uint16': 2, 'int16': 2,
    'uint32': 4, 'int32': 4, 'float': 4,
    'uint64': 8, 'int64': 8,
}

# ---------------------------------------------------------------------------
# Simple YAML parser – walks the tree and extracts leaf attributes
# ---------------------------------------------------------------------------

def parse_yaml(path):
    """Load and return the YAML dict from path."""
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.load(f, Loader=SafeLineLoader)


def _resolve_type(type_elem, value_types):
    """
    Resolve a type specification to a canonical type name string.

    Handles:
      - string type names (e.g. 'bool', 'float32')
      - 'readonly <type>' prefix
      - dict with 'type' key (e.g. {'type': 'bool'})
      - enum/flags types (dict with 'values' or 'flags' key)
      - fibre.Property<type, mode> references
    """
    if type_elem is None:
        return None

    if isinstance(type_elem, str):
        type_name = type_elem
    elif isinstance(type_elem, dict):
        if 'type' in type_elem:
            return _resolve_type(type_elem['type'], value_types)
        if 'values' in type_elem or 'flags' in type_elem:
            # Enum/flags – resolve to underlying integer type
            max_val = 0
            for v in (type_elem.get('values') or type_elem.get('flags')).values():
                if isinstance(v, dict):
                    vval = v.get('value', 0)
                else:
                    vval = 0
                if vval > max_val:
                    max_val = vval
            if max_val <= 0xff:
                return 'uint8'
            elif max_val <= 0xffff:
                return 'uint16'
            elif max_val <= 0xffffffff:
                return 'uint32'
            else:
                return 'uint64'
        return None
    else:
        return None

    # Strip 'readonly ' prefix
    if type_name.startswith('readonly '):
        type_name = type_name[len('readonly '):]

    # Handle fibre.Property<type, mode>
    if type_name.startswith('fibre.Property<'):
        inner = type_name[len('fibre.Property<'):-1]  # strip <>
        parts = [p.strip() for p in inner.split(',')]
        type_name = parts[0]

    # Resolve against builtins and user-defined value types
    if type_name in BUILTIN_TYPES:
        return CANONICAL_TYPE_MAP.get(type_name, type_name)
    if type_name in value_types:
        vt = value_types[type_name]
        if vt.get('is_enum', False):
            max_val = max(v.get('value', 0) for v in vt.get('values', {}).values())
            if max_val <= 0xff:
                return 'uint8'
            elif max_val <= 0xffff:
                return 'uint16'
            elif max_val <= 0xffffffff:
                return 'uint32'
            else:
                return 'uint64'
        # User-defined value type (not an enum) – treat as its c_name
        return CANONICAL_TYPE_MAP.get(type_name, type_name)

    return None


def _is_leaf_attribute(prop):
    """
    Check if a property represents a leaf attribute (not a nested object).

    Leaf attributes have a concrete type (bool, float, uint32, etc.) or an enum.
    Non-leaf attributes have nested 'attributes' or 'functions'.
    """
    if isinstance(prop, str):
        return prop not in ('',)  # non-empty string = type reference
    if isinstance(prop, dict):
        # If it has 'attributes' or 'functions' keys, it's a nested object
        if 'attributes' in prop or 'functions' in prop:
            return False
        # If it has a 'type' key, check if the type is a leaf type
        if 'type' in prop:
            return True
        # If it has 'values' or 'flags', it's an enum = leaf
        if 'values' in prop or 'flags' in prop:
            return True
    return False


def walk_attributes(interface_data, prefix, value_types, endpoints, path_stack=None):
    """
    Recursively walk an interface's attributes and collect leaf endpoints.

    Args:
        interface_data: dict of attribute_name -> attribute_spec
        prefix: current path prefix (e.g. 'axis0.config')
        value_types: dict of user-defined value types
        endpoints: list to append (path, type, access, id) tuples to
        path_stack: for cycle detection
    """
    if path_stack is None:
        path_stack = set()

    for attr_name, attr_spec in interface_data.items():
        if attr_name.startswith('__'):
            continue

        full_path = f"{prefix}.{attr_name}" if prefix else attr_name

        # Cycle detection
        if full_path in path_stack:
            continue
        path_stack.add(full_path)

        if attr_spec is None:
            # No spec – could be a reference to a nested interface
            # Treat as non-leaf
            path_stack.discard(full_path)
            continue

        # Check if this is a leaf attribute
        if not _is_leaf_attribute(attr_spec):
            # It's a nested object – recurse into its attributes
            if isinstance(attr_spec, dict):
                nested_attrs = attr_spec.get('attributes', {})
                if nested_attrs:
                    walk_attributes(nested_attrs, full_path, value_types, endpoints, path_stack)
            path_stack.discard(full_path)
            continue

        # Resolve the type
        type_name = _resolve_type(attr_spec, value_types)
        if type_name is None:
            path_stack.discard(full_path)
            continue

        # Determine access mode
        access = 'rw'
        if isinstance(attr_spec, str) and attr_spec.startswith('readonly '):
            access = 'r'
        elif isinstance(attr_spec, dict):
            type_part = attr_spec.get('type', '')
            if isinstance(type_part, str) and type_part.startswith('readonly '):
                access = 'r'
            # Check for c_setter – presence means writable
            if 'c_setter' in attr_spec:
                access = 'rw'

        endpoints.append({
            'path': full_path,
            'type': type_name,
            'access': access,
        })

        path_stack.discard(full_path)


def generate_flat_endpoints(yaml_path):
    """
    Parse the YAML and return a list of endpoint dicts with sequential IDs.
    """
    data = parse_yaml(yaml_path)

    interfaces = data.get('interfaces', {})
    value_types = data.get('valuetypes', {})

    # Merge builtins
    for k, v in BUILTIN_TYPES.items():
        if k not in value_types:
            value_types[k] = v

    def resolve_interface_type(type_name):
        """Resolve a type reference like 'ODrive.Axis' or 'Motor' to the interface dict."""
        if not type_name or not isinstance(type_name, str):
            return None
        # Try exact match first
        if type_name in interfaces:
            return interfaces[type_name]
        # Try short name (e.g., 'Motor')
        short = type_name.split('.')[-1] if '.' in type_name else type_name
        if short in interfaces:
            return interfaces[short]
        # Try prepending 'ODrive.' namespace
        if not type_name.startswith('ODrive.') and f'ODrive.{short}' in interfaces:
            return interfaces[f'ODrive.{short}']
        return None

    def collect_inherited_attrs(intf_data):
        """Collect attributes from an interface and all its implemented (parent) interfaces."""
        result = {}
        visited = set()
        stack = [intf_data]
        while stack:
            current = stack.pop()
            if not isinstance(current, dict):
                continue
            intf_id = id(current)
            if intf_id in visited:
                continue
            visited.add(intf_id)

            attrs = current.get('attributes', {})
            if attrs:
                result.update(attrs)

            # Process implements (inheritance)
            implements = current.get('implements', '')
            if implements:
                parent = resolve_interface_type(implements)
                if parent:
                    stack.append(parent)
        return result

    # Find the most derived root interface (one that implements another and has axis0)
    root_interface = None
    for name, intf in interfaces.items():
        if not isinstance(intf, dict):
            continue
        if 'attributes' not in intf:
            continue
        attrs = intf.get('attributes', {})
        # Prefer interfaces that have axis0/axis1 (most derived board-specific root)
        if 'axis0' in attrs or 'axis1' in attrs:
            root_interface = intf
            break

    if root_interface is None:
        # Fallback: use first interface with attributes
        for name, intf in interfaces.items():
            if isinstance(intf, dict) and 'attributes' in intf:
                root_interface = intf
                break

    if root_interface is None:
        print("Error: no root interface with attributes found in YAML", file=sys.stderr)
        sys.exit(1)

    all_endpoints = []
    walked_paths = set()  # Track full paths to avoid duplicates from inheritance

    def walk_interface(intf_data, prefix):
        """Walk one interface and its nested interfaces."""
        if not isinstance(intf_data, dict):
            return

        # Collect all attributes including inherited ones
        attrs = collect_inherited_attrs(intf_data)
        if not attrs:
            return

        walk_attributes(attrs, prefix, value_types, all_endpoints)

        # Process nested interfaces (attributes that reference other interfaces)
        for attr_name, attr_spec in attrs.items():
            full_path = f"{prefix}.{attr_name}" if prefix else attr_name
            if full_path in walked_paths:
                continue

            # Case 1: Inlined nested interface (has 'attributes' key directly)
            if isinstance(attr_spec, dict) and 'attributes' in attr_spec:
                walked_paths.add(full_path)
                walk_interface(attr_spec, full_path)
                continue

            # Case 2: Type reference to another interface (dict with 'type' key)
            if isinstance(attr_spec, dict):
                type_ref = attr_spec.get('type', '')
                target = None
                if isinstance(type_ref, str):
                    target = resolve_interface_type(type_ref)
                elif isinstance(type_ref, dict) and 'type' in type_ref:
                    target = resolve_interface_type(type_ref['type'])

                if target and isinstance(target, dict) and 'attributes' in target:
                    walked_paths.add(full_path)
                    walk_interface(target, full_path)
                continue

            # Case 3: String type reference to another interface (e.g., 'Motor', 'Controller')
            if isinstance(attr_spec, str):
                target = resolve_interface_type(attr_spec)
                if target and isinstance(target, dict) and 'attributes' in target:
                    walked_paths.add(full_path)
                    walk_interface(target, full_path)

    # Walk the root interface
    walk_interface(root_interface, '')

    # Assign sequential IDs
    for i, ep in enumerate(all_endpoints, start=1):
        ep['id'] = i

    return all_endpoints


def write_json(endpoints, output_path):
    """Write flat_endpoints.json."""
    endpoints_dict = {}
    for ep in endpoints:
        endpoints_dict[ep['path']] = {
            'id': ep['id'],
            'type': ep['type'],
            'access': ep['access'],
        }

    data = {
        'endpoints': endpoints_dict,
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

    print(f"Written {len(endpoints)} endpoints to {output_path}")


def write_cpp_header(endpoints, output_path):
    """Write flat_endpoints.hpp with C++ endpoint table."""

    type_code_map = TYPE_CODE_MAP
    byte_size_map = BYTE_SIZE_MAP

    # Build the endpoint table entries
    table_entries = []
    for ep in endpoints:
        type_code = type_code_map.get(ep['type'], 0)
        byte_size = byte_size_map.get(ep['type'], 1)
        access = 0 if ep['access'] == 'rw' else 1
        table_entries.append(
            f"    {{{ep['id']}, {byte_size}, {type_code}, {access}}}"
        )

    table = ',\n'.join(table_entries)

    header = f'''// Auto-generated by generate_flat_endpoints.py
// Do not edit. Regenerate with:
//   python generate_flat_endpoints.py --definitions odrive-interface.yaml --output flat_endpoints.json

#pragma once

#include <cstddef>
#include <cstdint>

struct EndpointInfo {{
    uint16_t id;
    uint8_t byte_size;
    uint8_t type_code;   // 0=bool, 1=uint8, 2=int8, 3=uint16, 4=int16, 5=uint32, 6=int32, 7=float, 8=uint64, 9=int64
    uint8_t access;      // 0=rw, 1=ro
}};

extern const EndpointInfo endpoint_table[];
extern const size_t endpoint_table_size;

static inline const EndpointInfo* find_endpoint_by_id(uint8_t id) {{
    // Binary search over sorted endpoint table
    size_t lo = 0, hi = endpoint_table_size;
    while (lo < hi) {{
        size_t mid = (lo + hi) >> 1;
        if (endpoint_table[mid].id == id) return &endpoint_table[mid];
        if (endpoint_table[mid].id < id) lo = mid + 1;
        else hi = mid;
    }}
    return nullptr;
}}

// Type code constants (for SDO handler type dispatch)
enum EndpointTypeCode : uint8_t {{
    TYPE_BOOL = 0,
    TYPE_UINT8 = 1,
    TYPE_INT8 = 2,
    TYPE_UINT16 = 3,
    TYPE_INT16 = 4,
    TYPE_UINT32 = 5,
    TYPE_INT32 = 6,
    TYPE_FLOAT = 7,
    TYPE_UINT64 = 8,
    TYPE_INT64 = 9,
}};

// Access constants
enum EndpointAccess : uint8_t {{
    ACCESS_RW = 0,
    ACCESS_RO = 1,
}};
'''

    # Append the endpoint table
    header += f'\nconst EndpointInfo endpoint_table[] = {{\n{table},\n}};\n'
    header += f'const size_t endpoint_table_size = {len(endpoints)};\n'

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(header)

    print(f"Written C++ header to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Generate flat_endpoints.json and flat_endpoints.hpp from odrive-interface.yaml'
    )
    parser.add_argument(
        '--definitions',
        type=str,
        required=True,
        help='Path to odrive-interface.yaml'
    )
    parser.add_argument(
        '--output',
        type=str,
        required=True,
        help='Output path for flat_endpoints.json (flat_endpoints.hpp written alongside)'
    )
    args = parser.parse_args()

    endpoints = generate_flat_endpoints(args.definitions)
    write_json(endpoints, args.output)

    # Write C++ header next to the JSON file
    cpp_path = args.output.replace('.json', '.hpp')
    write_cpp_header(endpoints, cpp_path)


if __name__ == '__main__':
    main()
