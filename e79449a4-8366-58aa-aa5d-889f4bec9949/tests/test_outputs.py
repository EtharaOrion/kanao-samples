import base64
import hashlib
import importlib
import itertools
import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path


WORKDIR = Path('/app/workspace')
CONFIG_REL = 'envs/prod'
PROTECTED = {
    'docs/POLICY-AS-CODE-STANDARD.md': '83b9aaaab7c0b40b6b4df240700f6d7e4b0cb52e338b0d66e271f1ee4643bb38',
    'policies/deny-public-sql.json': 'b2f306665128669f95d219fa5ed3ec99cd4e1b9a7ee99447e50db499a0bc1380',
    'policies/require-tags.json': 'c56a2210448884c884e85a653314c3b3cd56334fd9d1272b61020b8b5ec6b1da',
}
CHECKED_SETTINGS = (
    'ipsecurityrestrictionsdefaultaction',
    'mintlsversion',
    'httpsonly',
    'publicnetworkaccess',
)
PUBLISHED_ALIASES = {
    'Microsoft.Sql/servers/publicNetworkAccess',
    'Microsoft.Web/sites/httpsOnly',
    'Microsoft.Web/sites/publicNetworkAccess',
    'Microsoft.Web/sites/config/ipSecurityRestrictionsDefaultAction',
    'Microsoft.Web/sites/config/minTlsVersion',
    'Microsoft.Web/sites/config/web.minTlsVersion',
    'Microsoft.Web/sites/config/publicNetworkAccess',
    'Microsoft.Web/sites/siteConfig.ipSecurityRestrictionsDefaultAction',
    'Microsoft.Web/sites/siteConfig.minTlsVersion',
    'Microsoft.Web/sites/siteConfig.publicNetworkAccess',
    'Microsoft.Web/sites/slots/httpsOnly',
    'Microsoft.Web/sites/slots/publicNetworkAccess',
    'Microsoft.Web/sites/slots/config/ipSecurityRestrictionsDefaultAction',
    'Microsoft.Web/sites/slots/config/minTlsVersion',
    'Microsoft.Web/sites/slots/config/web.minTlsVersion',
    'Microsoft.Web/sites/slots/config/publicNetworkAccess',
    'Microsoft.Web/sites/slots/siteConfig.ipSecurityRestrictionsDefaultAction',
    'Microsoft.Web/sites/slots/siteConfig.minTlsVersion',
    'Microsoft.Web/sites/slots/siteConfig.publicNetworkAccess',
}
SETTING_SEPARATOR = re.compile(r'[/.]')

REVIEW_DIR = 'paymesh-review'
PIPELINE_DIR = 'paymesh-pipeline'
CACHE_PREFIX = 'paymesh-catalogue-'
VAR_FILE = 'prod.tfvars'
HOME_BINDING = 'prod'
ALT_BINDING = 'dev'
BINDINGS = [HOME_BINDING, ALT_BINDING]
SLUG_TOKEN = re.compile(r'[a-z0-9]+')
BINDING_SLUG = re.compile(r'^[a-z]+$')
COPY_IGNORE = ('.terraform', '.terraform.lock.hcl', 'tfplan')
TERRAFORM_DIR = '.terraform'
RUN_TIMEOUT = 900
STEP_TIMEOUT = 300

CATALOGUE_KEY = 'require_ip_restriction'
SLOT_CATALOGUE_KEY = 'require_slot_ip_restriction'
TLS_KEY = 'require_tls_version'
HTTPS_KEY = 'require_https_only'
GROUP_TAGS_KEY = 'require_resource_group_tags'
SLUG_KEYS = (CATALOGUE_KEY, SLOT_CATALOGUE_KEY, TLS_KEY, HTTPS_KEY)

BROAD_MODE = 'All'
NARROW_MODE = 'Indexed'
BROAD_TYPES = {
    'microsoft.web/sites/config',
    'microsoft.web/sites/slots/config',
    'microsoft.resources/subscriptions',
    'microsoft.resources/subscriptions/resourcegroups',
}
TYPE_FIELD = 'type'
TYPE_OPERATORS = ('equals', 'in')

SUBSCRIPTION = '/subscriptions/3f6c2a9e-8d41-4b7a-9e1f-5c0d2b7a4e18'
LOCATION = 'eastus2'
CONFIG_TYPE = 'Microsoft.Web/sites/config'
SLOT_CONFIG_TYPE = 'Microsoft.Web/sites/slots/config'
GROUP_TYPE = 'Microsoft.Resources/subscriptions/resourceGroups'
STORAGE_TYPE = 'Microsoft.Storage/storageAccounts'
CONFIG_NAME = 'web'
SLOT_NAME = 'blue'
IP_ALIAS = 'Microsoft.Web/sites/config/ipSecurityRestrictionsDefaultAction'
SLOT_IP_ALIAS = 'Microsoft.Web/sites/slots/config/ipSecurityRestrictionsDefaultAction'
TLS_ALIASES = ('Microsoft.Web/sites/config/web.minTlsVersion', 'Microsoft.Web/sites/config/minTlsVersion')
OPEN_ACTION = 'Allow'
REFUSAL = 'Deny'
OLD_TLS = '1.0'
OBSOLETE_TLS = '1.1'
CURRENT_TLS = '1.2'
NEWEST_TLS = '1.3'
PAYMENTS_NAMES = ['func-paymesh-pagos', 'func-paymesh-pagos-canary']
BYSTANDER_NAMES = ['func-paymesh-workers', 'app-paymesh-api']
GROUP_PREFIX = 'rg-paymesh-'
STORAGE_PREFIX = 'stpaymeshpagos'
TAG_KEYS = ('environment', 'data_classification')
TAG_FIELD = re.compile(r"^tags(?:\[\s*'?([^'\]]+)'?\s*\]|\.(.+))$", re.I)
WILDCARD = '*'
PLACEHOLDER = '${'
EXPRESSION_START = '['
TRUE_TEXT = 'true'
CONDITION_KEY = 'if'
THEN_KEY = 'then'
EFFECT_KEY = 'effect'

WORKFLOW_REL = '.github/workflows/terraform-ci.yml'
RENDER_COMMAND = re.compile(r'\bterraform\b[^\n|;&]*\bconsole\b')
REVIEW_ACTION = 'paymesh-platform/policy-review-action@v3'
HOME_INPUT = 'catalogue'
ALT_INPUT = 'catalogue-alt'
DEFAULT_RENDERS = ('/tmp/catalogue-prod.txt', '/tmp/catalogue-dev.txt')
BASH_SHELLS = ('bash',)
SH_SHELLS = ('sh',)
BASH_COMMAND = ['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c']
SH_COMMAND = ['sh', '-e', '-c']
DEFAULT_SHELL = 'bash'
DEFAULT_DIRECTORY = '.'
SANDBOX_USER = 'nobody'
RUNUSER = 'runuser'
EXPRESSION_REF = re.compile(r'\$\{\{\s*(matrix|env)\.([A-Za-z0-9_-]+)\s*\}\}')
MATRIX_CONTEXT = 'matrix'

EXPRESSION = (
    'base64encode(jsonencode({ definitions = { for k, v in '
    'local.policy_definitions : k => { mode = v.mode, rule = v.rule } }, '
    'assigned = local.assigned_policies }))'
)

DECL = re.compile(r'^\s*resource\s+"(azurerm_policy_definition|azurerm_policy_set_definition|azurerm_[a-z_]*policy_assignment)"\s+"([^"]+)"', re.M)
DEFINITION_KIND = 'azurerm_policy_definition'
ASSIGNMENT_KIND = r'azurerm_[a-z_]*policy_assignment'
POLICY_SET_KIND = 'azurerm_policy_set_definition'
COPY_SLUG = re.compile(r'[.-](prod|dev|qa|staging|stage|test|uat)\.json$')
CATALOGUE_REF = 'local.policy_definitions'
ASSIGNED_REF = 'local.assigned_policies'
MODE_REF = re.compile(r'each\.value\s*(?:\.\s*mode|\[\s*"mode"\s*\])|local\.policy_definitions\s*\[\s*each\.(?:key|value)\s*\]\s*\.\s*mode')
RULE_REF = re.compile(r'each\.value\s*(?:\.\s*rule|\[\s*"rule"\s*\])|local\.policy_definitions\s*\[\s*each\.(?:key|value)\s*\]\s*\.\s*rule')
RESOURCE_DECL = re.compile(r'^\s*resource\s+"([a-z_]+)"\s+"([^"]+)"', re.M)
MANAGED_RESOURCES = [
    ('azurerm_policy_definition', 'this'),
    ('azurerm_subscription_policy_assignment', 'this'),
    ('azurerm_resource_group', 'this'),
    ('azurerm_virtual_network', 'this'),
    ('azurerm_subnet', 'this'),
    ('azurerm_service_plan', 'this'),
    ('azurerm_linux_function_app', 'this'),
    ('azurerm_storage_account', 'this'),
    ('azurerm_mssql_server', 'this'),
    ('azurerm_mssql_database', 'this'),
]

DOC_ABSENT = 'absent_from_catalogue'
DOC_UNSUPPORTED = 'rule_uses_a_condition_review_cannot_evaluate'
SEE_COVERAGE = 'payments_apps_covered'
SEE_CLOSED = 'matches_a_closed_restriction'
SEE_BYSTANDERS = 'bystanders_matched'
SEE_OTHER_BINDING = 'matches_the_other_binding'
SEE_WILDCARDS = 'wildcards_per_pattern'
SEE_UNPUBLISHED = 'unpublished_alias'
TLS_MISSES = 'misses_an_outdated_site_configuration'
TLS_REFUSES_COMPLIANT = 'matches_a_compliant_site_configuration'
GROUP_MISSES = 'misses_an_untagged_resource_group'
GROUP_REFUSES_TAGGED = 'matches_a_tagged_resource_group'
GROUP_REACHES_OTHERS = 'reaches_beyond_resource_groups'
OWN_NO_CATALOGUE = 'no_catalogue'
OWN_UNASSIGNED = 'definition_without_assignment'
OWN_MODE = 'evaluation_mode'
OWN_COUNT = 'assignment_count'
DECL_BESIDE = 'declared_beside_the_catalogue'
WIRE_DEFINITION = 'definition_resource_does_not_read_the_catalogue'
WIRE_ASSIGNMENT = 'assignment_resource_does_not_read_the_assignment_list'
DOC_STATIC = 'document_does_not_follow_environment'
DOC_PLACEHOLDER = 'placeholder_left_in_document'
DOC_UNPARSED = 'document_does_not_parse'
DOC_EFFECT = 'document_only_records'
PIPE_MISSING = 'pipeline_file_missing'
PIPE_UNPARSED = 'pipeline_does_not_parse'
PIPE_NO_RENDER = 'pipeline_renders_nothing'
PIPE_TREE = 'pipeline_tree_does_not_initialise'
PIPE_STEP_FAILED = 'pipeline_render_step_failed'
PIPE_HOME = 'pipeline_catalogue_is_not_the_home_render'
PIPE_SECOND = 'pipeline_second_render_is_not_a_second_binding'
SCOPE_CODES = (SEE_COVERAGE, SEE_OTHER_BINDING, SEE_BYSTANDERS)
SETTING_CODES = (SEE_COVERAGE, SEE_CLOSED)
TLS_SCOPE_CODES = (TLS_MISSES, SEE_OTHER_BINDING)
TLS_VERSION_CODES = (TLS_MISSES, TLS_REFUSES_COMPLIANT)
WRAPPER_ACTION = 'hashicorp/setup-terraform@'
WRAPPER_INPUT = 'terraform_wrapper'
WRAPPER_OFF = 'false'
WRAPPER_DIR = 'paymesh-terraform-wrapper'
WRAPPER_BIN = 'terraform-bin'
WRAPPER_SHIM = '#!/bin/sh\nexec "$TERRAFORM_CLI_PATH/terraform-bin" "$@" < /dev/null\n'


def _run(args, cwd, feed=None):
    proc = subprocess.run(args, cwd=str(cwd), input=feed, capture_output=True,
                          text=True, timeout=RUN_TIMEOUT)
    return proc.returncode, proc.stdout or ''


def _prepared_copy(name):
    root = Path(tempfile.gettempdir()) / name
    config = root / CONFIG_REL
    if (config / TERRAFORM_DIR).is_dir():
        return config
    shutil.rmtree(str(root), ignore_errors=True)
    shutil.copytree(str(WORKDIR), str(root), symlinks=True,
                    ignore=shutil.ignore_patterns(*COPY_IGNORE))
    if not config.is_dir():
        return None
    code, _ = _run(['terraform', 'init', '-backend=false', '-input=false',
                    '-no-color'], config)
    if code != 0:
        return None
    return config


def _decode_console(text):
    token = ''
    for line in (text or '').splitlines():
        line = line.strip()
        if len(line) > 2 and line.startswith('"') and line.endswith('"'):
            token = line[1:-1]
    if not token:
        return {}
    try:
        data = json.loads(base64.b64decode(token).decode())
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _catalogue(binding):
    cache = Path(tempfile.gettempdir()) / (CACHE_PREFIX + binding + '.json')
    if cache.is_file():
        try:
            return json.loads(cache.read_text())
        except ValueError:
            pass
    config = _prepared_copy(REVIEW_DIR)
    if config is None:
        return {}
    args = ['terraform', 'console', '-no-color']
    if (config / VAR_FILE).is_file():
        args.append('-var-file=' + VAR_FILE)
    args.append('-var')
    args.append('environment=' + binding)
    code, out = _run(args, config, EXPRESSION + '\n')
    if code != 0:
        return {}
    data = _decode_console(out)
    if data:
        cache.write_text(json.dumps(data))
    return data


def _documents(binding):
    found = {}
    for key, entry in (_catalogue(binding).get('definitions') or {}).items():
        if not isinstance(entry, dict):
            continue
        try:
            found[key] = json.loads(entry.get('rule') or '')
        except ValueError:
            found[key] = None
    return found


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            for item in _walk(value):
                yield item
    elif isinstance(node, list):
        for value in node:
            for item in _walk(value):
                yield item


def _lowered(node):
    return {str(k).lower(): v for k, v in node.items()}


def _other_binding(binding):
    return ALT_BINDING if binding == HOME_BINDING else HOME_BINDING


def _like(value, pattern):
    regex = '^' + '.*'.join(re.escape(part) for part in pattern.split(WILDCARD)) + '$'
    return re.match(regex, value, re.I | re.S) is not None


def _field_value(resource, field):
    match = TAG_FIELD.match(field.strip())
    if match:
        key = (match.group(1) or match.group(2)).lower()
        tags = {str(k).lower(): v for k, v in resource['tags'].items()}
        return key in tags, tags.get(key)
    name = field.strip().lower()
    if name in resource['core']:
        return True, resource['core'][name]
    if name in resource['aliases']:
        return True, resource['aliases'][name]
    return False, None


def _text(value):
    return str(value).lower()


def _leaf(resource, node):
    keys = _lowered(node)
    if isinstance(keys.get('field'), str):
        present, value = _field_value(resource, keys['field'])
    elif isinstance(keys.get('value'), str) and not keys['value'].startswith(EXPRESSION_START):
        present, value = True, keys['value']
    else:
        return None
    operators = [k for k in keys if k in _OPERATORS]
    if len(operators) != 1:
        return None
    return _OPERATORS[operators[0]](present, value, keys[operators[0]])


def _op_equals(present, value, argument):
    return present and value is not None and _text(value) == _text(argument)


def _op_in(present, value, argument):
    if not isinstance(argument, list):
        return None
    return present and value is not None and _text(value) in [_text(a) for a in argument]


def _op_like(present, value, argument):
    return present and value is not None and _like(_text(value), _text(argument))


def _op_contains(present, value, argument):
    return present and value is not None and _text(argument) in _text(value)


def _ordered(value, argument):
    try:
        return float(value), float(argument)
    except (TypeError, ValueError):
        return _text(value), _text(argument)


def _op_less(present, value, argument):
    if not present or value is None:
        return False
    left, right = _ordered(value, argument)
    return type(left) is type(right) and left < right


def _op_greater_or_equals(present, value, argument):
    if not present or value is None:
        return False
    left, right = _ordered(value, argument)
    return type(left) is type(right) and left >= right


def _op_greater(present, value, argument):
    if not present or value is None:
        return False
    left, right = _ordered(value, argument)
    return type(left) is type(right) and left > right


def _op_less_or_equals(present, value, argument):
    if not present or value is None:
        return False
    left, right = _ordered(value, argument)
    return type(left) is type(right) and left <= right


def _op_exists(present, value, argument):
    return present == (_text(argument) == TRUE_TEXT)


def _negated(operator):
    def check(present, value, argument):
        verdict = operator(present, value, argument)
        return None if verdict is None else not verdict
    return check


_OPERATORS = {
    'equals': _op_equals,
    'notequals': _negated(_op_equals),
    'in': _op_in,
    'notin': _negated(_op_in),
    'like': _op_like,
    'notlike': _negated(_op_like),
    'contains': _op_contains,
    'notcontains': _negated(_op_contains),
    'exists': _op_exists,
    'less': _op_less,
    'lessorequals': _op_less_or_equals,
    'greater': _op_greater,
    'greaterorequals': _op_greater_or_equals,
}


def _evaluate(resource, node):
    if not isinstance(node, dict):
        return None
    keys = _lowered(node)
    for combinator, reducer in (('allof', all), ('anyof', any)):
        if combinator in keys:
            parts = keys[combinator]
            if not isinstance(parts, list) or len(keys) != 1:
                return None
            verdicts = [_evaluate(resource, part) for part in parts]
            if None in verdicts:
                return None
            return reducer(verdicts)
    if 'not' in keys:
        if len(keys) != 1:
            return None
        verdict = _evaluate(resource, keys['not'])
        return None if verdict is None else not verdict
    return _leaf(resource, node)


def _condition(rule):
    return _lowered(rule).get(CONDITION_KEY) if isinstance(rule, dict) else None


def _resource(kind, resource_id, name, full_name, aliases=None, tags=None):
    return {
        'core': {'type': kind, 'id': resource_id, 'name': name,
                 'fullname': full_name, 'location': LOCATION},
        'aliases': {k.lower(): v for k, v in (aliases or {}).items()},
        'tags': tags or {},
    }


def _site_id(app, binding):
    return (SUBSCRIPTION + '/resourceGroups/' + GROUP_PREFIX + binding
            + '/providers/Microsoft.Web/sites/' + app + '-' + binding)


def _site_config(app, binding, aliases):
    site = app + '-' + binding
    return _resource(CONFIG_TYPE, _site_id(app, binding) + '/config/' + CONFIG_NAME,
                     CONFIG_NAME, site + '/' + CONFIG_NAME, aliases)


def _slot_config(app, binding, aliases):
    site = app + '-' + binding
    return _resource(SLOT_CONFIG_TYPE,
                     _site_id(app, binding) + '/slots/' + SLOT_NAME + '/config/' + CONFIG_NAME,
                     CONFIG_NAME, site + '/' + SLOT_NAME + '/' + CONFIG_NAME, aliases)


def _tls(version):
    return {alias: version for alias in TLS_ALIASES}


def _group(binding, tags):
    name = GROUP_PREFIX + binding
    return _resource(GROUP_TYPE, SUBSCRIPTION + '/resourceGroups/' + name, name, name, tags=tags)


def _storage(binding):
    name = STORAGE_PREFIX + binding
    return _resource(STORAGE_TYPE, SUBSCRIPTION + '/resourceGroups/' + GROUP_PREFIX + binding
                     + '/providers/Microsoft.Storage/storageAccounts/' + name, name, name)


def _scope_gaps(prefix, rule, expectations):
    gaps = []
    condition = _condition(rule)
    for resource, wanted, code in expectations:
        verdict = _evaluate(resource, condition)
        if verdict is None:
            gaps.append(prefix + DOC_UNSUPPORTED)
        elif verdict != wanted:
            gaps.append(prefix + code)
    return gaps


def _guardrail_gaps(key, build, alias, codes):
    gaps = []
    for binding in BINDINGS:
        prefix = binding + ':' + key + ':'
        rule = _documents(binding).get(key)
        if not isinstance(rule, dict):
            gaps.append(prefix + DOC_ABSENT)
            continue
        other = _other_binding(binding)
        expectations = []
        for app in PAYMENTS_NAMES:
            expectations.append((build(app, binding, {alias: OPEN_ACTION}), True, SEE_COVERAGE))
            expectations.append((build(app, binding, {alias: REFUSAL}), False, SEE_CLOSED))
            expectations.append((build(app, other, {alias: OPEN_ACTION}), False, SEE_OTHER_BINDING))
        for app in BYSTANDER_NAMES:
            expectations.append((build(app, binding, {alias: OPEN_ACTION}), False, SEE_BYSTANDERS))
        expectations = [e for e in expectations if e[2] in codes]
        gaps.extend(_scope_gaps(prefix, rule, expectations))
    return gaps


def _gaps_the_guardrail_sees_the_payments_apps_and_nothing_else():
    return _guardrail_gaps(CATALOGUE_KEY, _site_config, IP_ALIAS, SCOPE_CODES)


def _gaps_the_guardrail_reads_the_payments_setting():
    return _guardrail_gaps(CATALOGUE_KEY, _site_config, IP_ALIAS, SETTING_CODES)


def _gaps_the_slot_guardrail_sees_both_blue_slots_and_no_bystander():
    return _guardrail_gaps(SLOT_CATALOGUE_KEY, _slot_config, SLOT_IP_ALIAS, SCOPE_CODES)


def _gaps_the_slot_guardrail_reads_the_slot_setting():
    return _guardrail_gaps(SLOT_CATALOGUE_KEY, _slot_config, SLOT_IP_ALIAS, SETTING_CODES)


def _gaps_the_tls_guardrail_sees_every_site_configuration():
    return _tls_gaps(TLS_SCOPE_CODES)


def _gaps_the_tls_guardrail_refuses_only_obsolete_versions():
    return _tls_gaps(TLS_VERSION_CODES)


def _tls_gaps(codes):
    gaps = []
    for binding in BINDINGS:
        prefix = binding + ':' + TLS_KEY + ':'
        rule = _documents(binding).get(TLS_KEY)
        if not isinstance(rule, dict):
            gaps.append(prefix + DOC_ABSENT)
            continue
        other = _other_binding(binding)
        expectations = []
        for app in PAYMENTS_NAMES + BYSTANDER_NAMES:
            expectations.append((_site_config(app, binding, _tls(OLD_TLS)), True, TLS_MISSES))
            expectations.append((_site_config(app, binding, _tls(OBSOLETE_TLS)), True, TLS_MISSES))
            expectations.append((_site_config(app, binding, _tls(CURRENT_TLS)), False, TLS_REFUSES_COMPLIANT))
            expectations.append((_site_config(app, binding, _tls(NEWEST_TLS)), False, TLS_REFUSES_COMPLIANT))
            expectations.append((_site_config(app, other, _tls(OLD_TLS)), False, SEE_OTHER_BINDING))
        expectations = [e for e in expectations if e[2] in codes]
        gaps.extend(_scope_gaps(prefix, rule, expectations))
    return gaps


def _gaps_the_group_tag_guardrail_sees_untagged_resource_groups():
    gaps = []
    for binding in BINDINGS:
        prefix = binding + ':' + GROUP_TAGS_KEY + ':'
        rule = _documents(binding).get(GROUP_TAGS_KEY)
        if not isinstance(rule, dict):
            gaps.append(prefix + DOC_ABSENT)
            continue
        full = {tag: binding for tag in TAG_KEYS}
        partial = {TAG_KEYS[0]: binding}
        expectations = [
            (_group(binding, {}), True, GROUP_MISSES),
            (_group(binding, partial), True, GROUP_MISSES),
            (_group(binding, full), False, GROUP_REFUSES_TAGGED),
            (_storage(binding), False, GROUP_REACHES_OTHERS),
        ]
        gaps.extend(_scope_gaps(prefix, rule, expectations))
    return gaps


def _patterns(rule):
    found = []
    for node in _walk(rule):
        keys = _lowered(node)
        for operator in ('like', 'notlike'):
            if isinstance(keys.get(operator), str):
                found.append(keys[operator])
    return found


def _gaps_every_pattern_inside_constraint(keys=None, skip=()):
    gaps = []
    for binding in BINDINGS:
        for key, rule in _documents(binding).items():
            if key in skip or (keys is not None and key not in keys):
                continue
            if rule and [p for p in _patterns(rule) if p.count(WILDCARD) > 1]:
                gaps.append(binding + ':' + key + ':' + SEE_WILDCARDS)
    return gaps


def _gaps_every_alias_is_published(keys=None, skip=()):
    published = {alias.lower() for alias in PUBLISHED_ALIASES}
    gaps = []
    for binding in BINDINGS:
        for key, rule in _documents(binding).items():
            if key in skip or (keys is not None and key not in keys):
                continue
            for node in _walk(rule or {}):
                field = _lowered(node).get('field')
                if not isinstance(field, str):
                    continue
                name = field.strip().lower()
                setting = SETTING_SEPARATOR.split(name)[-1]
                if setting in CHECKED_SETTINGS and name not in published:
                    gaps.append(binding + ':' + key + ':' + SEE_UNPUBLISHED)
    return gaps


def _rule_types(rule):
    types = set()
    for node in _walk(rule or {}):
        keys = _lowered(node)
        if _text(keys.get('field', '')) != TYPE_FIELD:
            continue
        for operator in TYPE_OPERATORS:
            value = keys.get(operator)
            if isinstance(value, str):
                types.add(value.lower())
            elif isinstance(value, list):
                types.update(_text(item) for item in value)
    return types


def _allowed_modes(rule):
    types = _rule_types(rule)
    if types & BROAD_TYPES:
        return {BROAD_MODE}
    if not types:
        return {NARROW_MODE}
    return {BROAD_MODE, NARROW_MODE}


def _gaps_every_definition_the_environment_owns_is_enforced():
    gaps = []
    for binding in BINDINGS:
        catalogue = _catalogue(binding)
        definitions = catalogue.get('definitions') or {}
        assigned = catalogue.get('assigned')
        if not definitions or not isinstance(assigned, list):
            gaps.append(binding + ':' + OWN_NO_CATALOGUE)
            continue
        documents = _documents(binding)
        if [key for key in definitions if key not in assigned]:
            gaps.append(binding + ':' + OWN_UNASSIGNED)
        if len(set(assigned)) != len(definitions):
            gaps.append(binding + ':' + OWN_COUNT)
        for key, entry in definitions.items():
            if entry.get('mode') not in _allowed_modes(documents.get(key)):
                gaps.append(binding + ':' + OWN_MODE + ':' + key)
    return gaps


def _gaps_nothing_declared_beside():
    found = set()
    for tf in WORKDIR.rglob('*.tf'):
        if TERRAFORM_DIR in tf.parts:
            continue
        for m in DECL.finditer(tf.read_text(errors='replace')):
            found.add((m.group(1), m.group(2)))
    definitions = [t for t, _ in found if t == DEFINITION_KIND]
    assignments = [t for t, _ in found if re.fullmatch(ASSIGNMENT_KIND, t)]
    sets = [t for t, _ in found if t == POLICY_SET_KIND]
    gaps = []
    if len(definitions) != 1 or len(assignments) != 1 or sets:
        gaps.append(DECL_BESIDE)
    return gaps


def _blocks(text, kind):
    found = []
    for match in re.finditer(r'resource\s+"' + kind + r'"\s+"[^"]+"\s*\{', text):
        depth = 0
        for index in range(match.end() - 1, len(text)):
            if text[index] == '{':
                depth += 1
            elif text[index] == '}':
                depth -= 1
                if depth == 0:
                    found.append(text[match.end():index])
                    break
    return found


def _attribute(block, name):
    match = re.search(r'^\s*' + name + r'\s*=\s*(.*?)(?=\n\s*[A-Za-z_]+\s*(?:=|\{)|\Z)',
                      block, re.M | re.S)
    return match.group(1) if match else ''


def _gaps_the_resources_read_the_catalogue():
    text = '\n'.join(tf.read_text(errors='replace')
                     for tf in sorted((WORKDIR / CONFIG_REL).glob('*.tf')))
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)   
    text = re.sub(r'(#|//).*', '', text)                
    gaps = []
    definitions = _blocks(text, DEFINITION_KIND)

    if not definitions or not all(
            CATALOGUE_REF in _attribute(block, 'for_each')
            and MODE_REF.search(_attribute(block, 'mode'))
            and RULE_REF.search(_attribute(block, 'policy_rule'))
            for block in definitions):
        gaps.append(WIRE_DEFINITION)
    assignments = _blocks(text, ASSIGNMENT_KIND)
    if not assignments or not all(ASSIGNED_REF in _attribute(block, 'for_each')
                                  for block in assignments):
        gaps.append(WIRE_ASSIGNMENT)
    return gaps


def _gaps_every_document_follows_the_environment_it_is_rendered_for():
    home = _catalogue(HOME_BINDING).get('definitions') or {}
    away = _catalogue(ALT_BINDING).get('definitions') or {}
    if not home or not away:
        return [OWN_NO_CATALOGUE]
    gaps = []
    for key in SLUG_KEYS:
        if home.get(key, {}).get('rule') == away.get(key, {}).get('rule'):
            gaps.append(key + ':' + DOC_STATIC)
    for key, entry in home.items():
        text = entry.get('rule') or ''
        if PLACEHOLDER in text:
            gaps.append(key + ':' + DOC_PLACEHOLDER)
        try:
            document = json.loads(text)
        except ValueError:
            gaps.append(key + ':' + DOC_UNPARSED)
            continue
        effect = _lowered(_lowered(document).get(THEN_KEY) or {}).get(EFFECT_KEY)
        if _text(effect) != _text(REFUSAL):
            gaps.append(key + ':' + DOC_EFFECT)
    return gaps


def _expand(text, combination, env):
    def value(match):
        source = combination if match.group(1) == MATRIX_CONTEXT else env
        return str(source.get(match.group(2), match.group(0)))
    return EXPRESSION_REF.sub(value, text)


def _combinations(matrix):
    axes = {k: v for k, v in (matrix or {}).items()
            if isinstance(v, list) and v and all(not isinstance(i, (dict, list)) for i in v)}
    if not axes:
        return [{}]
    names = sorted(axes)
    return [dict(zip(names, values)) for values in itertools.product(*(axes[n] for n in names))]


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _render_steps(workflow, wrapper):
    found = []
    top_run = _mapping(_mapping(workflow.get('defaults')).get('run'))
    for job in _mapping(workflow.get('jobs')).values():
        if not isinstance(job, dict):
            continue
        job_run = _mapping(_mapping(job.get('defaults')).get('run'))
        matrix = _mapping(_mapping(job.get('strategy')).get('matrix'))
        wrapped = False
        for step in job.get('steps') or []:
            if isinstance(step, dict) and str(step.get('uses', '')).strip().startswith(WRAPPER_ACTION):
                setting = _mapping(step.get('with')).get(WRAPPER_INPUT)
                wrapped = wrapper and _text(setting) != WRAPPER_OFF
            if not isinstance(step, dict) or not isinstance(step.get('run'), str):
                continue
            if not RENDER_COMMAND.search(step['run']):
                continue
            for combination in _combinations(matrix):
                env = {}
                for scope in (workflow.get('env'), job.get('env'), step.get('env')):
                    for name, raw in _mapping(scope).items():
                        env[str(name)] = _expand(str(raw), combination, env)
                directory = (step.get('working-directory') or job_run.get('working-directory')
                             or top_run.get('working-directory') or DEFAULT_DIRECTORY)
                shell = step.get('shell') or job_run.get('shell') or top_run.get('shell') or DEFAULT_SHELL
                found.append((_expand(step['run'], combination, env),
                              _expand(str(directory), combination, env), env, str(shell), wrapped))
    return found


def _review_inputs(workflow):
    for job in _mapping(workflow.get('jobs')).values():
        for step in (job.get('steps') or []) if isinstance(job, dict) else []:
            if isinstance(step, dict) and str(step.get('uses', '')).strip() == REVIEW_ACTION:
                inputs = _mapping(step.get('with'))
                home, alt = inputs.get(HOME_INPUT), inputs.get(ALT_INPUT)
                if isinstance(home, str) and isinstance(alt, str) and home and alt:
                    return home, alt
    return None


def _sandbox_uid():
    if os.geteuid() != 0 or shutil.which(RUNUSER) is None:
        return None
    try:
        return pwd.getpwnam(SANDBOX_USER).pw_uid
    except KeyError:
        return None


def _kill_user(uid):
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid == uid:
                os.kill(int(entry.name), signal.SIGKILL)
        except OSError:
            continue


def _wrapper_dir():
    shim = Path(tempfile.gettempdir()) / WRAPPER_DIR
    real = shutil.which('terraform')
    if real is None:
        return None
    if not (shim / WRAPPER_BIN).exists():
        shutil.rmtree(str(shim), ignore_errors=True)
        shim.mkdir(parents=True)
        (shim / WRAPPER_BIN).symlink_to(real)
        (shim / 'terraform').write_text(WRAPPER_SHIM)
        (shim / 'terraform').chmod(0o755)
        shim.chmod(0o755)
    return shim


def _run_step(step, root, uid):
    script, directory, env, shell, wrapped = step
    cwd = (root / directory).resolve()
    if not cwd.is_dir():
        return 1
    if shell in BASH_SHELLS:
        command = BASH_COMMAND + [script]
    elif shell in SH_SHELLS:
        command = SH_COMMAND + [script]
    else:
        return 1
    full_env = dict(os.environ)
    full_env.update(env)
    full_env['HOME'] = str(root)
    if wrapped:
        shim = _wrapper_dir()
        if shim is not None:
            full_env['TERRAFORM_CLI_PATH'] = str(shim)
            full_env['PATH'] = str(shim) + os.pathsep + full_env.get('PATH', '')
    if uid is not None:
        command = [RUNUSER, '-u', SANDBOX_USER, '--'] + command
    proc = subprocess.Popen(command, cwd=str(cwd), env=full_env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        code = proc.wait(timeout=STEP_TIMEOUT)
    except subprocess.TimeoutExpired:
        code = 1
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    if uid is not None:
        _kill_user(uid)
    return code


def _second_bindings(home, alt):
    found = set()
    home_definitions = home.get('definitions') or {}
    alt_definitions = alt.get('definitions') or {}
    for key in SLUG_KEYS:
        home_tokens = SLUG_TOKEN.findall(str((home_definitions.get(key) or {}).get('rule') or ''))
        alt_tokens = SLUG_TOKEN.findall(str((alt_definitions.get(key) or {}).get('rule') or ''))
        if len(home_tokens) != len(alt_tokens):
            continue
        for home_token, alt_token in zip(home_tokens, alt_tokens):
            if home_token == HOME_BINDING and alt_token != HOME_BINDING and BINDING_SLUG.match(alt_token):
                found.add(alt_token)
    return sorted(found)


def _read_render(root, path):
    target = Path(path) if Path(path).is_absolute() else root / path
    try:
        return _decode_console(target.read_text(errors='replace'))
    except OSError:
        return {}


def _gaps_the_pipeline_renders_both_bindings(wrapper):
    pipeline = WORKDIR / WORKFLOW_REL
    if not pipeline.is_file():
        return [PIPE_MISSING]
    yaml = importlib.import_module('yaml')
    try:
        workflow = yaml.safe_load(pipeline.read_text(errors='replace'))
    except yaml.YAMLError:
        return [PIPE_UNPARSED]
    if not isinstance(workflow, dict):
        return [PIPE_UNPARSED]
    review = _review_inputs(workflow) or DEFAULT_RENDERS
    steps = _render_steps(workflow, wrapper)
    if not steps:
        return [PIPE_NO_RENDER]
    config = _prepared_copy(PIPELINE_DIR)
    if config is None:
        return [PIPE_TREE]
    root = config.parents[1]
    for path in review:
        target = Path(path) if Path(path).is_absolute() else root / path
        if target.is_file() or target.is_symlink():
            target.unlink()
    uid = _sandbox_uid()
    if uid is not None:
        subprocess.run(['chown', '-R', SANDBOX_USER, str(root)], capture_output=True)
    gaps = []
    for step in steps:
        if _run_step(step, root, uid) != 0:
            gaps.append(PIPE_STEP_FAILED)
    home = _read_render(root, review[0])
    alt = _read_render(root, review[1])
    if not home or home != _catalogue(HOME_BINDING):
        gaps.append(PIPE_HOME)
    if not alt or alt == home or not any(alt == _catalogue(b) for b in _second_bindings(home, alt)):
        gaps.append(PIPE_SECOND)
    return gaps


def test_guardrails_see_their_targets_under_review():
    gaps = (_gaps_the_guardrail_sees_the_payments_apps_and_nothing_else()
            + _gaps_the_slot_guardrail_sees_both_blue_slots_and_no_bystander()
            + _gaps_the_tls_guardrail_sees_every_site_configuration()
            + _gaps_the_group_tag_guardrail_sees_untagged_resource_groups()
            + _gaps_every_pattern_inside_constraint())
    assert not gaps, 'the guardrail scope rule is not met, at: ' + ', '.join(sorted(set(gaps)))


def test_catalogue_is_enforced_and_rendered_under_review():
    gaps = (_gaps_every_definition_the_environment_owns_is_enforced()
            + _gaps_nothing_declared_beside()
            + _gaps_the_resources_read_the_catalogue()
            + _gaps_every_document_follows_the_environment_it_is_rendered_for()
            + _gaps_every_alias_is_published(skip=(CATALOGUE_KEY, SLOT_CATALOGUE_KEY))
            + _gaps_the_pipeline_renders_both_bindings(wrapper=False))
    assert not gaps, 'the enforced catalogue rule is not met, at: ' + ', '.join(sorted(set(gaps)))


def test_payments_guardrail_reads_the_published_setting_under_review():
    gaps = (_gaps_the_guardrail_reads_the_payments_setting()
            + _gaps_every_alias_is_published(keys=(CATALOGUE_KEY,)))
    assert not gaps, 'the payments setting rule is not met, at: ' + ', '.join(sorted(set(gaps)))


def test_slot_guardrail_reads_the_published_setting_under_review():
    gaps = (_gaps_the_slot_guardrail_reads_the_slot_setting()
            + _gaps_every_alias_is_published(keys=(SLOT_CATALOGUE_KEY,)))
    assert not gaps, 'the slot setting rule is not met, at: ' + ', '.join(sorted(set(gaps)))


def test_tls_guardrail_refuses_only_obsolete_versions_under_review():
    gaps = _gaps_the_tls_guardrail_refuses_only_obsolete_versions()
    assert not gaps, 'the TLS version rule is not met, at: ' + ', '.join(sorted(set(gaps)))


def test_pipeline_renders_both_bindings_under_setup_terraform():
    gaps = _gaps_the_pipeline_renders_both_bindings(wrapper=True)
    assert not gaps, 'the CI render rule is not met, at: ' + ', '.join(sorted(set(gaps)))


def test_negative_policy_inputs_were_altered():
    damaged = []
    assert PROTECTED, 'no input document is recorded for this review'
    for rel, digest in PROTECTED.items():
        path = WORKDIR / rel
        if not path.is_file():
            damaged.append(rel)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            damaged.append(rel)
    assert damaged, 'every pinned input is byte-identical to the seed'


def test_negative_per_environment_copy_was_added():
    copies = sorted(p.name for p in (WORKDIR / 'policies').rglob('*.json') if COPY_SLUG.search(p.name))
    assert copies, 'no per-environment copy sits beside the single copies'


def test_negative_managed_resources_were_removed():
    declared = set()
    for tf in WORKDIR.rglob('*.tf'):
        if TERRAFORM_DIR in tf.parts:
            continue
        declared.update(RESOURCE_DECL.findall(tf.read_text(errors='replace')))
    removed = [kind + '.' + name for kind, name in MANAGED_RESOURCES if (kind, name) not in declared]
    assert removed, 'the tree still declares every managed resource'
