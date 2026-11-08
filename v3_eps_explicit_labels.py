"""Bind literal basic EPS labels to local numeric per-share extension items.

This is not fuzzy concept discovery. Facts still require independent issuer,
scope, annual period, CFO and KRW/shares validation in the EPS extractor.
"""
from collections import defaultdict
import re
import v3_dart_xbrl_capex_pilot as xp
import v3_dart_reported_eps as eps
import v3_dart_cashflow_enrichment as cf

XSD='{http://www.w3.org/2001/XMLSchema}'
LINK='{http://www.xbrl.org/2003/linkbase}'
XL='{http://www.w3.org/1999/xlink}'
INSTANCE='http://www.xbrl.org/2003/instance'
NUMERIC='http://www.xbrl.org/dtr/type/numeric'
# XBRL's unit registry defines the numeric DTR per-share type separately
# from the core instance monetary type. A currency amount is not an EPS.
PER_SHARE_TYPES={(NUMERIC,'perShareItemType')}


class EPSLabelError(ValueError):pass


def bind_archive(archive,entries):
    schemas={};eligible={};links=[];definitions={}
    for entry in entries:
        if entry.is_dir() or not entry.filename.lower().endswith(('.xml','.xbrl','.xsd')):continue
        data=archive.read(entry);root=xp._safe_xbrl_xml(data)
        if root.tag==XSD+'schema':
            namespace=root.get('targetNamespace')
            if type(namespace) is not str or len(namespace)>320 or not namespace.startswith(('http://','https://')):continue
            bindings=xp._unambiguous_namespace_bindings(data)
            for element in root.findall(XSD+'element'):
                name,ident=element.get('name'),element.get('id')
                if any(type(v) is not str or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,159}',v) for v in (name,ident)):continue
                tag=(namespace,name);key=(entry.filename,ident)
                definition=(xp._safe_qname(element.get('type'),bindings),
                    xp._safe_qname(element.get('substitutionGroup'),bindings),
                    element.get('{'+INSTANCE+'}periodType'),element.get('abstract','false'))
                if (key in schemas and schemas[key]!=tag) or (tag in definitions and definitions[tag]!=definition):
                    raise EPSLabelError('ambiguous_eps_local_schema')
                schemas[key]=tag;definitions[tag]=definition
                if (not xp._ifrs_full_namespace(namespace) and definition[0] in PER_SHARE_TYPES
                    and definition[1]==(INSTANCE,'item') and definition[2]=='duration'
                    and definition[3] in ('false','0')):eligible[tag]=definition[0]
        links.extend((entry.filename,link) for link in root.findall('.//'+LINK+'labelLink'))
    matched=defaultdict(set);rejected=set()
    for member,link in links:
        locators={};labels={}
        for loc in link.findall(LINK+'loc'):
            key=loc.get(XL+'label')
            if not key:continue
            value=xp._local_taxonomy_ref(member,loc.get(XL+'href'),schemas)
            if key in locators and locators[key]!=value:raise EPSLabelError('ambiguous_eps_label_locator')
            locators[key]=value
        for label in link.findall(LINK+'label'):
            if label.get('{http://www.w3.org/XML/1998/namespace}lang','').lower() not in ('ko','ko-kr'):continue
            key=label.get(XL+'label')
            if not key:continue
            text=''.join(label.itertext());canonical=eps.canonical_basic_eps_name(text)
            value=(canonical,'주당' in ''.join(text.split()))
            if key in labels and labels[key]!=value:raise EPSLabelError('ambiguous_eps_label_resource')
            labels[key]=value
        for arc in link.findall(LINK+'labelArc'):
            if arc.get(XL+'arcrole')!='http://www.xbrl.org/2003/arcrole/concept-label' or arc.get('use')=='prohibited':continue
            concept=locators.get(arc.get(XL+'from'));label=labels.get(arc.get(XL+'to'))
            if concept not in eligible or label is None:continue
            if label[0] is not None:matched[concept].add(label[0])
            elif label[1]:rejected.add(concept)
    accepted={tag:names for tag,names in matched.items() if tag not in rejected}
    digest=cf.fingerprint([{'namespace':tag[0],'concept':tag[1],
        'type':eligible[tag],'literal_labels':sorted(accepted[tag])} for tag in sorted(accepted)])
    return frozenset('{'+ns+'}'+name for ns,name in accepted),digest
