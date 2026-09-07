import re
import base64
import fnmatch
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


WORKDIR = Path('/app/workspace')
CONFIG_REL = 'envs/prod'
TARGET = 'envs/prod/policies.tf'
TARGETS = [
    'resource "azurerm_policy_definition" "this" {',
    'resource "azurerm_subscription_policy_assignment" "this" {',
]
GRADER_FILES = ['test_outputs.py', 'test_weights.json', 'rubric.json']
PROTECTED = {
    'docs/POLICY-AS-CODE-STANDARD.md': '91edad80fd9cc843a5f74eab8612aa1329e971676a6ca4763fd3491655052956',
    'policies/deny-public-sql.json': 'b2f306665128669f95d219fa5ed3ec99cd4e1b9a7ee99447e50db499a0bc1380',
    'policies/require-tags.json': 'c56a2210448884c884e85a653314c3b3cd56334fd9d1272b61020b8b5ec6b1da',
}

REVIEW_DIR = 'paymesh-review'
CACHE_PREFIX = 'paymesh-catalogue-'
VAR_FILE = 'prod.tfvars'
HOME_BINDING = 'prod'
ALT_BINDING = 'dev'
CATALOGUE_KEY = 'require_ip_restriction'
SLOT_CATALOGUE_KEY = 'require_slot_ip_restriction'
WILDCARD = '*'
CANDIDATE_MARKER = 'canary'
BROAD_MODE = 'All'
NARROW_MODE = 'Indexed'
TYPE_FIELD = 'type'
CHILD_TYPE = 'Microsoft.Web/sites/config'
SLOT_CHILD_TYPE = 'Microsoft.Web/sites/slots/config'
SETTING_ALIAS = 'Microsoft.Web/sites/config/web.ipSecurityRestrictionsDefaultAction'
SLOT_SETTING_ALIAS = 'Microsoft.Web/sites/slots/config/web.ipSecurityRestrictionsDefaultAction'
REFUSAL = 'Deny'
PLACEHOLDER = '${'
ID_STEM = '/subscriptions/x/resourceGroups/rg-paymesh-%s/providers/Microsoft.Web/sites/%s-%s/config/web'
PAYMENTS_NAMES = ['func-paymesh-pagos', 'func-paymesh-pagos-canary']
BYSTANDER_NAMES = ['func-paymesh-workers', 'app-paymesh-api']
BINDINGS = ['prod', 'dev']
WORKFLOW_REL = '.github/workflows/terraform-ci.yml'
RENDER_MARKER = 'terraform console'
MIN_RENDERS = 2

EXPRESSION = (
    'base64encode(jsonencode({ definitions = { for k, v in '
    'local.policy_definitions : k => { mode = v.mode, rule = v.rule } }, '
    'assigned = local.assigned_policies }))'
)

CI_ONE_BINDING = 'pipeline_renders_one_binding'
SEE_WILDCARDS = 'wildcards_per_pattern'
SEE_COVERAGE = 'payments_apps_covered'
SEE_BYSTANDERS = 'bystanders_matched'
SEE_OBJECT = 'object_and_alias'
OWN_UNASSIGNED = 'definition_without_assignment'
OWN_MODE = 'evaluation_mode'
OWN_COUNT = 'assignment_count'
DOC_STATIC = 'document_does_not_follow_environment'
DOC_PLACEHOLDER = 'placeholder_left_in_document'
DOC_UNPARSED = 'document_does_not_parse'
DOC_EFFECT = 'document_only_records'


def _run(args, cwd, feed=None):
    proc = subprocess.run(args, cwd=str(cwd), input=feed, capture_output=True,
                          text=True, timeout=900)
    return proc.returncode, proc.stdout or ''


def _config_dir():
    root = Path(tempfile.gettempdir()) / REVIEW_DIR
    config = root / CONFIG_REL
    if (config / '.terraform').is_dir():
        return config
    shutil.rmtree(str(root), ignore_errors=True)
    shutil.copytree(str(WORKDIR), str(root),
                    ignore=shutil.ignore_patterns('.terraform',
                                                  '.terraform.lock.hcl',
                                                  'tfplan'))
    if not config.is_dir():
        return None
    code, _ = _run(['terraform', 'init', '-backend=false', '-input=false',
                    '-no-color'], config)
    if code != 0:
        return None
    return config


def _catalogue(binding):
    cache = Path(tempfile.gettempdir()) / (CACHE_PREFIX + binding + '.json')
    if cache.is_file():
        try:
            return json.loads(cache.read_text())
        except ValueError:
            pass
    config = _config_dir()
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
    token = ''
    for line in out.splitlines():
        line = line.strip()
        if len(line) > 2 and line.startswith('"') and line.endswith('"'):
            token = line[1:-1]
    if not token:
        return {}
    try:
        data = json.loads(base64.b64decode(token).decode())
    except (ValueError, UnicodeDecodeError):
        return {}
    if isinstance(data, dict):
        cache.write_text(json.dumps(data))
        return data
    return {}


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


def _ids(names, binding):
    return [ID_STEM % (binding, name, binding) for name in names]


def _slot_ids(names, binding):
    return [
        '/subscriptions/x/resourceGroups/rg-paymesh-%s/providers/Microsoft.Web/sites/%s-%s/slots/blue/config/web'
        % (binding, name, binding)
        for name in names
    ]


def _patterns(rule):
    return [n['like'] for n in _walk(rule) if isinstance(n.get('like'), str)]


def _targets_child(rule):
    subjects = {n.get('equals') for n in _walk(rule or {}) if n.get('field') == TYPE_FIELD}
    return [s for s in subjects if isinstance(s, str) and s.count('/') > 1]


def _gaps_the_guardrail_sees_the_payments_apps_and_nothing_else():
    gaps = []
    for binding in BINDINGS:
        rule = _documents(binding).get(CATALOGUE_KEY)
        assert rule, 'the catalogue renders no payments document at ' + binding
        patterns = _patterns(rule)
        payments = _ids(PAYMENTS_NAMES, binding)
        bystanders = _ids(BYSTANDER_NAMES, binding)
        if [p for p in patterns if p.count(WILDCARD) > 1]:
            gaps.append(binding + ':' + SEE_WILDCARDS)
        seen = {i for p in patterns
                for i in payments + bystanders if fnmatch.fnmatchcase(i, p)}
        if [i for i in payments if i not in seen]:
            gaps.append(binding + ':' + SEE_COVERAGE)
        if [i for i in bystanders if i in seen]:
            gaps.append(binding + ':' + SEE_BYSTANDERS)
        aliases = {n.get('field') for n in _walk(rule) if isinstance(n.get('field'), str)}
        if CHILD_TYPE not in _targets_child(rule) or SETTING_ALIAS not in aliases:
            gaps.append(binding + ':' + SEE_OBJECT)
    return gaps


def _gaps_the_slot_guardrail_sees_both_blue_slots_and_no_bystander():
    gaps = []
    for binding in BINDINGS:
        rule = _documents(binding).get(SLOT_CATALOGUE_KEY)
        assert rule, 'the catalogue renders no payments-slot document at ' + binding
        patterns = _patterns(rule)
        payments = _slot_ids(PAYMENTS_NAMES, binding)
        bystanders = _slot_ids(BYSTANDER_NAMES, binding)
        if [p for p in patterns if p.count(WILDCARD) > 1]:
            gaps.append(binding + ':' + SEE_WILDCARDS)
        seen = {i for p in patterns
                for i in payments + bystanders if fnmatch.fnmatchcase(i, p)}
        if [i for i in payments if i not in seen]:
            gaps.append(binding + ':' + SEE_COVERAGE)
        if [i for i in bystanders if i in seen]:
            gaps.append(binding + ':' + SEE_BYSTANDERS)
        aliases = {n.get('field') for n in _walk(rule) if isinstance(n.get('field'), str)}
        types = {n.get('equals') for n in _walk(rule) if n.get('field') == TYPE_FIELD}
        if SLOT_CHILD_TYPE not in types or SLOT_SETTING_ALIAS not in aliases:
            gaps.append(binding + ':' + SEE_OBJECT)
    return gaps


def _gaps_every_definition_the_environment_owns_is_enforced():
    gaps = []
    for binding in BINDINGS:
        catalogue = _catalogue(binding)
        definitions = catalogue.get('definitions') or {}
        assigned = catalogue.get('assigned')
        assert definitions, 'the environment declares no catalogue at ' + binding
        assert isinstance(assigned, list), 'no assignment list at ' + binding
        documents = _documents(binding)
        if [key for key in definitions if key not in assigned]:
            gaps.append(binding + ':' + OWN_UNASSIGNED)
        if len(set(assigned)) != len(definitions):
            gaps.append(binding + ':' + OWN_COUNT)
        for key, entry in definitions.items():
            wanted = BROAD_MODE if _targets_child(documents.get(key)) else NARROW_MODE
            if entry.get('mode') != wanted:
                gaps.append(binding + ':' + OWN_MODE)
    return gaps


def _gaps_every_document_follows_the_environment_it_is_rendered_for():
    home = _catalogue(HOME_BINDING).get('definitions') or {}
    away = _catalogue(ALT_BINDING).get('definitions') or {}
    assert home, 'the catalogue renders nothing at the home binding'
    assert away, 'the catalogue renders nothing at the second binding'
    gaps = []
    pipeline = WORKDIR / WORKFLOW_REL
    if not pipeline.is_file() or pipeline.read_text(errors='replace').count(RENDER_MARKER) < MIN_RENDERS:
        gaps.append(CI_ONE_BINDING)
    for key in (CATALOGUE_KEY, SLOT_CATALOGUE_KEY,
                'require_tls_version', 'require_https_only'):
        if home.get(key, {}).get('rule') == away.get(key, {}).get('rule'):
            gaps.append(key + ':' + DOC_STATIC)
    for key, entry in home.items():
        text = entry.get('rule') or ''
        if PLACEHOLDER in text:
            gaps.append(DOC_PLACEHOLDER)
        try:
            document = json.loads(text)
        except ValueError:
            gaps.append(DOC_UNPARSED)
            continue
        if (document.get('then') or {}).get('effect') != REFUSAL:
            gaps.append(DOC_EFFECT)
    return gaps


def test_negative_verifier_package_was_removed():
    here = Path(__file__).resolve().parent
    present = {name: (here / name).is_file() for name in GRADER_FILES}
    assert all(present.values()), (
        'files under /tests were removed: '
        + ', '.join(n for n, ok in present.items() if not ok)
    )


def test_negative_policy_inputs_were_altered():
    damaged = []
    assert PROTECTED, 'no input document is recorded for this review'
    for rel, digest in PROTECTED.items():
        path = WORKDIR / rel
        if not path.is_file():
            damaged.append(rel)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            damaged.append(rel)
    assert not damaged, (
        'inputs that the repair does not involve were changed: '
        + ', '.join(damaged)
    )


HOME_TFVARS_ENV = re.compile(r'^\s*environment\s*=\s*"([a-z]+)"')
RENDER_VAR = re.compile(r'-var(?:=|\s+)"?environment=([a-z]+)"?')
RENDER_ENV = re.compile(r'TF_VAR_environment="?([a-z]+)"?')
RENDER_FILE = re.compile(r'-var-file(?:=|\s+)"?([a-z]+)\.tfvars"?')
DECL = re.compile(r'^\s*resource\s+"(azurerm_policy_definition|azurerm_policy_set_definition|azurerm_[a-z_]*policy_assignment)"\s+"([^"]+)"', re.M)
COPY_SLUG = re.compile(r'[.-](prod|dev|qa|staging|stage|test|uat)\.json$')
PIPE_NO_SECOND = 'pipeline_renders_no_second_binding'
PIPE_NO_RECEIPT = 'second_rendering_not_the_one_the_reviewer_reads'
DECL_BESIDE = 'declared_beside_the_catalogue'
COPY_BESIDE = 'per_environment_copy_beside_the_single_copy'
ALL_WILDCARDS = 'wildcards_per_pattern_in_'


def _home_env():
    p = WORKDIR / CONFIG_REL / VAR_FILE
    if p.is_file():
        for line in p.read_text(errors='replace').splitlines():
            m = HOME_TFVARS_ENV.match(line)
            if m:
                return m.group(1)
    return HOME_BINDING


def _gaps_pipeline_receipt():
    gaps = []
    pipeline = WORKDIR / WORKFLOW_REL
    if not pipeline.is_file():
        return [PIPE_NO_SECOND]
    text = pipeline.read_text(errors='replace')
    alt = ''
    for line in text.splitlines():
        s = line.strip()
        if s.startswith('catalogue-alt:'):
            alt = s.split(':', 1)[1].strip()
    renders = [l for l in text.splitlines()
               if RENDER_MARKER in l and not l.strip().startswith('#')]
    home = _home_env()
    second = []
    for l in renders:
        bound = ''
        for pattern in (RENDER_VAR, RENDER_FILE, RENDER_ENV):
            found = pattern.findall(l)
            if found:
                bound = found[-1]
                break
        if bound and bound != home:
            second.append(l)
    if len(renders) < MIN_RENDERS or not second:
        gaps.append(PIPE_NO_SECOND)
    elif alt and not any(alt in l for l in second):
        gaps.append(PIPE_NO_RECEIPT)
    return gaps


def _gaps_nothing_declared_beside():
    found = set()
    for tf in WORKDIR.rglob('*.tf'):
        if '.terraform' in tf.parts:
            continue
        for m in DECL.finditer(tf.read_text(errors='replace')):
            found.add((m.group(1), m.group(2)))
    definitions = [t for t, _ in found if t == 'azurerm_policy_definition']
    assignments = [t for t, _ in found if t.endswith('policy_assignment')]
    sets = [t for t, _ in found if t == 'azurerm_policy_set_definition']
    gaps = []
    if len(definitions) != 1 or len(assignments) != 1 or sets:
        gaps.append(DECL_BESIDE)
    for p in (WORKDIR / 'policies').rglob('*.json'):
        if COPY_SLUG.search(p.name):
            gaps.append(COPY_BESIDE)
    return gaps


def _gaps_every_pattern_inside_constraint():
    gaps = []
    for binding in BINDINGS:
        for key, rule in _documents(binding).items():
            if rule and [p for p in _patterns(rule) if p.count(WILDCARD) > 1]:
                gaps.append(binding + ':' + ALL_WILDCARDS + key)
    return gaps


def _review_gaps():
    return {
        'A': _gaps_the_guardrail_sees_the_payments_apps_and_nothing_else()
             + _gaps_the_slot_guardrail_sees_both_blue_slots_and_no_bystander()
             + _gaps_every_pattern_inside_constraint(),
        'B': _gaps_every_definition_the_environment_owns_is_enforced()
             + _gaps_nothing_declared_beside(),
        'C': _gaps_every_document_follows_the_environment_it_is_rendered_for()
             + _gaps_pipeline_receipt(),
    }


def test_outcome_a_guardrails_see_their_targets_under_review():
    gaps = _review_gaps()
    own = sorted(set(gaps['A']))
    assert own == [], 'outcome A is not met, at: ' + ', '.join(own)
    elsewhere = sorted(set(gaps['B'] + gaps['C']))
    assert elsewhere == [], 'outcome A is met but review fails on: ' + ', '.join(elsewhere)


def test_outcome_b_every_definition_is_enforced_under_review():
    gaps = _review_gaps()
    assert not gaps['B'], 'outcome B is not met, at: ' + ', '.join(sorted(set(gaps['B'])))
    for other in ('A', 'C'):
        assert not gaps[other], 'outcome B is met but review fails on outcome ' + other + ': ' + ', '.join(sorted(set(gaps[other])))


def test_outcome_c_documents_follow_their_binding_under_review():
    gaps = _review_gaps()
    unmet = {key: sorted(set(value)) for key, value in gaps.items() if value}
    assert 'C' not in unmet, 'outcome C is not met, at: ' + ', '.join(unmet.get('C', []))
    assert unmet == {}, 'outcome C is met but review fails on: ' + repr(unmet)
