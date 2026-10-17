"""Read bounded role definitions from an XBRL archive's embedded XSD schemas.

This examines local extension schemas only, NOT the complete external DTS.
A roleType, presentationLink, or textual definition cannot alone certify
a fact as a primary statement fact or prove OFS/CFS/PIT eligibility.
"""
from __future__ import annotations

from hashlib import sha256
import re

from .clients import ProbeError

XSD = '{http://www.w3.org/2001/XMLSchema}'
LINK = '{http://www.xbrl.org/2003/linkbase}'
MAX_EMBEDDED_XSD = 8
MAX_ROLE_TYPES = 5000
MAX_ROLE_DEFINITION_SIZE = 4096

# Recognisable *hints*, not an allowlist for primary-statement roles.
_HINT_TERMS = (
    ('income_statement', ('income statement', '손익계산서', '포괄손익계산서')),
    ('notes', ('notes', '주석')),
    ('revenue', ('revenue', '매출')),
    ('separate', ('separate', '별도', '개별')),
    ('consolidated', ('consolidated', '연결')),
)


def extract_embedded_role_definitions(root, existing=None):
    """Scan roleTypes nested in xs:schema appinfo; reject conflicting role URIs.

    Returns in-memory records keyed by FULL URI, which are never exported.
    The caller only persists fixed vocabulary, truncated hashes and counts.
    """
    records = {} if existing is None else existing
    if root.tag != XSD + 'schema':
        return records
    for role in root.iter(LINK + 'roleType'):
        if len(records) >= MAX_ROLE_TYPES:
            raise ProbeError('Excessive embedded XBRL roleType definitions')
        uri = role.get('roleURI')
        if not isinstance(uri, str) or not 1 <= len(uri) <= 2048:
            continue
        definition_node = role.find(LINK + 'definition')
        definition = (definition_node.text or '') if definition_node is not None else ''
        if len(definition) > MAX_ROLE_DEFINITION_SIZE:
            definition = ''
        lowered = definition.casefold()
        hints = tuple(name for name, phrases in _HINT_TERMS
                      if any(phrase in lowered for phrase in phrases))
        used_on = tuple(sorted({
            (elem.text or '').strip().split(':')[-1]
            for elem in role.findall(LINK + 'usedOn')
            if (elem.text or '').strip().split(':')[-1] in (
                'presentationLink', 'calculationLink', 'definitionLink',
                'labelLink', 'referenceLink')
        }))
        entry = {'role_uri_sha256_prefix': sha256(uri.encode()).hexdigest()[:16],
                 'role_type_present': True,
                 'definition_sha256_prefix': (sha256(definition.encode()).hexdigest()[:16]
                                               if definition else None),
                 'definition_hints_unverified': list(hints),
                 'used_on_link_types': list(used_on)}
        if uri in records and records[uri] != entry:
            # Conflicting schemas are ambiguous; caller must fail closed.
            raise ProbeError('Conflicting embedded XBRL roleType definitions')
        records[uri] = entry
    return records


def summarize_role_definition(uri, records):
    """No raw URI/definition text or arbitrary document content in output."""
    item = records.get(uri)
    if item is None:
        return {'embedded_role_type_found': False,
                'embedded_role_type_used_on_presentation': False,
                'embedded_role_definition_sha256_prefix': None,
                'embedded_role_definition_hints_unverified': []}
    return {'embedded_role_type_found': True,
            'embedded_role_type_used_on_presentation':
                'presentationLink' in item['used_on_link_types'],
            'embedded_role_definition_sha256_prefix':
                item['definition_sha256_prefix'],
            'embedded_role_definition_hints_unverified':
                item['definition_hints_unverified']}
