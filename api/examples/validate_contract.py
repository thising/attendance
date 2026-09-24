#!/usr/bin/env python3
"""Offline checks for this contract's schema subset; no Django, DB or network."""

from __future__ import annotations

import copy
import json
import re
import sys
import uuid
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
METHODS = {'get', 'post', 'put', 'patch', 'delete', 'head', 'options'}
WEIGHT_KEYS = {'absent', 'late', 'leave', 'low', 'mid', 'high', 'dlow', 'dmid', 'dhigh'}
VALUES = {
    'class': {'normal', 'late', 'absent', 'leave'},
    'activity': {'normal', 'low', 'mid', 'high'},
    'discipline': {'normal', 'dlow', 'dmid', 'dhigh'},
}
ERROR_STATUSES = {
    400: {'validation_error', 'invalid_record_date', 'invalid_term', 'invalid_cursor'},
    401: {'unauthenticated', 'credential_expired', 'credential_revoked'},
    403: {'forbidden', 'class_scope_denied', 'scope_denied', 'term_read_only', 'august_read_only'},
    404: {'not_found'},
    409: {'idempotency_conflict', 'revision_conflict', 'roster_conflict', 'context_expired',
          'pagination_stale', 'historical_snapshot_unavailable'},
    503: {'database_busy', 'temporarily_unavailable'},
}


class InvalidContract(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidContract(message)


def resolve(spec, ref):
    require(ref.startswith('#/'), f'External reference not allowed: {ref}')
    node = spec
    for part in ref[2:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        require(isinstance(node, dict) and part in node, f'Broken reference: {ref}')
        node = node[part]
    return node


def dereference(spec, node):
    return resolve(spec, node['$ref']) if '$ref' in node else node


def accepts(spec, schema, value):
    try:
        validate(spec, schema, value)
        return True
    except InvalidContract:
        return False


def validate(spec, schema, value, location='$'):
    """Validate every assertion keyword used by this file's schemas."""
    if isinstance(schema, bool):
        require(schema, f'{location}: false schema')
        return
    if '$ref' in schema:
        validate(spec, resolve(spec, schema['$ref']), value, location)
    for child in schema.get('allOf', []):
        validate(spec, child, value, location)
    if 'oneOf' in schema:
        matches = sum(accepts(spec, child, value) for child in schema['oneOf'])
        require(matches == 1, f'{location}: oneOf expected one match, got {matches}')
    if 'not' in schema:
        require(not accepts(spec, schema['not'], value), f'{location}: forbidden shape')
    if 'const' in schema:
        require(value == schema['const'], f'{location}: wrong constant')
    if 'enum' in schema:
        require(value in schema['enum'], f'{location}: outside enum')
    if 'type' in schema:
        kinds = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
        checks = {
            'null': value is None,
            'object': isinstance(value, dict),
            'array': isinstance(value, list),
            'string': isinstance(value, str),
            'integer': isinstance(value, int) and not isinstance(value, bool),
            'number': isinstance(value, (int, float)) and not isinstance(value, bool),
            'boolean': isinstance(value, bool),
        }
        require(all(kind in checks for kind in kinds), f'{location}: unsupported type')
        require(any(checks[kind] for kind in kinds), f'{location}: expected {kinds}')
    if isinstance(value, dict):
        missing = set(schema.get('required', [])) - value.keys()
        require(not missing, f'{location}: missing {sorted(missing)}')
        properties = schema.get('properties', {})
        if schema.get('additionalProperties') is False:
            require(not (value.keys() - properties.keys()), f'{location}: unexpected fields')
        for name, child in properties.items():
            if name in value:
                validate(spec, child, value[name], f'{location}/{name}')
    if isinstance(value, list):
        require(len(value) >= schema.get('minItems', 0), f'{location}: too few items')
        require(len(value) <= schema.get('maxItems', float('inf')), f'{location}: too many items')
        if schema.get('uniqueItems'):
            encoded = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value]
            require(len(encoded) == len(set(encoded)), f'{location}: duplicate items')
        if 'items' in schema:
            for index, item in enumerate(value):
                validate(spec, schema['items'], item, f'{location}/{index}')
    if isinstance(value, str):
        require(len(value) >= schema.get('minLength', 0), f'{location}: too short')
        require(len(value) <= schema.get('maxLength', float('inf')), f'{location}: too long')
        if 'pattern' in schema:
            require(re.search(schema['pattern'], value) is not None, f'{location}: pattern mismatch')
        fmt = schema.get('format')
        try:
            if fmt == 'date':
                require(re.fullmatch(r'\d{4}-\d{2}-\d{2}', value), f'{location}: malformed date')
                date.fromisoformat(value)
            elif fmt == 'date-time':
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                require('T' in value and parsed.tzinfo is not None, f'{location}: timezone required')
            elif fmt == 'uuid':
                uuid.UUID(value)
            elif fmt is not None:
                raise InvalidContract(f'{location}: unsupported format {fmt}')
        except ValueError as exc:
            raise InvalidContract(f'{location}: invalid {fmt}') from exc
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        require(value >= schema.get('minimum', -float('inf')), f'{location}: below minimum')
        require(value <= schema.get('maximum', float('inf')), f'{location}: above maximum')


def walk(node):
    yield node
    if isinstance(node, dict):
        for child in node.values():
            yield from walk(child)
    elif isinstance(node, list):
        for child in node:
            yield from walk(child)


def check_supported_schema(schema):
    """Fail closed if a later edit adds assertions this checker cannot enforce."""
    if isinstance(schema, bool):
        return
    supported = {
        '$ref', 'type', 'properties', 'required', 'additionalProperties', 'items',
        'allOf', 'oneOf', 'not', 'const', 'enum', 'minimum', 'maximum',
        'minItems', 'maxItems', 'uniqueItems', 'minLength', 'maxLength', 'pattern',
        'format', 'description', 'title', 'default', 'readOnly', 'writeOnly',
        'deprecated', 'example', 'examples',
    }
    require(isinstance(schema, dict), 'Schema must be an object or boolean')
    unknown = {key for key in schema if key not in supported and not key.startswith('x-')}
    require(not unknown, f'Unsupported schema keywords; extend checker: {sorted(unknown)}')
    for child in schema.get('properties', {}).values():
        check_supported_schema(child)
    for key in ('allOf', 'oneOf'):
        for child in schema.get(key, []):
            check_supported_schema(child)
    for key in ('items', 'not', 'additionalProperties'):
        if key in schema:
            check_supported_schema(schema[key])


def check_structure(spec):
    require(spec['openapi'] == '3.1.0', 'Expected OpenAPI 3.1.0')
    require(spec.get('x-implementation-status') == 'not-enabled', 'Contract must remain disabled')
    require(spec['servers'] == [{
        'url': 'https://attendance.invalid/api/v1',
        'description': '保留的不可部署占位域名；不是实际服务地址',
    }], 'Server must be the explicit non-deployable placeholder')
    require(spec.get('security') == [{'DelegatedBearer': []}], 'All operations need Bearer security')
    for node in walk(spec):
        if isinstance(node, dict) and '$ref' in node:
            resolve(spec, node['$ref'])
        if isinstance(node, dict) and 'schema' in node:
            check_supported_schema(node['schema'])
    for schema in spec['components']['schemas'].values():
        check_supported_schema(schema)
    known_scopes = set(spec['components']['securitySchemes']['DelegatedBearer']['x-scopes'])
    operations = {}
    for path, path_item in spec['paths'].items():
        require(path.startswith('/classes'), f'Unexpected public or credential path: {path}')
        for method, operation in path_item.items():
            if method not in METHODS:
                continue
            op_id = operation['operationId']
            require(op_id not in operations, f'Duplicate operationId: {op_id}')
            require(operation.get('x-implementation-status') == 'not-enabled', f'{op_id}: enabled')
            require(operation.get('security', spec['security']) == spec['security'], f'{op_id}: security override')
            scopes = operation.get('x-required-scopes', [])
            require(scopes and set(scopes) <= known_scopes, f'{op_id}: absent or unknown scope')
            for status in ('401', '403', '409', '503'):
                require(status in operation['responses'], f'{op_id}: missing {status}')
            parameters = path_item.get('parameters', []) + operation.get('parameters', [])
            params = [dereference(spec, item) for item in parameters]
            expected_path_params = set(re.findall(r'{([^}]+)}', path))
            actual_path_params = {item['name'] for item in params if item['in'] == 'path' and item.get('required')}
            require(expected_path_params == actual_path_params, f'{op_id}: path parameter mismatch')
            require(method in {'get', 'post', 'put'}, f'{op_id}: high-impact/unsupported operation added')
            if method != 'get':
                require(any(item['name'] == 'Idempotency-Key' and item.get('required') for item in params), f'{op_id}: no idempotency key')
                require(operation.get('requestBody', {}).get('required'), f'{op_id}: no required body')
                require(any(scope in {'records:create', 'records:update'} for scope in scopes), f'{op_id}: no write scope')
            operations[op_id] = (path, method, path_item, operation)
    require(len(operations) == 8, 'Unexpected operation count; review activation gates before expansion')
    return operations


def match_operation(spec, method, path):
    for pattern, path_item in spec['paths'].items():
        names = re.findall(r'{([^}]+)}', pattern)
        regex = re.sub(r'\\\{[^}]+\\\}', r'([^/]+)', re.escape(pattern))
        match = re.fullmatch(regex, path)
        if match and method in path_item:
            return path_item, path_item[method], dict(zip(names, match.groups()))
    raise InvalidContract(f'No operation for {method.upper()} {path}')


def term_for(day):
    if day.month == 8:
        return None
    if day.month == 1:
        return f'{day.year - 1}-autumn'
    return f'{day.year}-' + ('spring' if day.month <= 7 else 'autumn')


def term_bounds(key):
    year_text, season = key.split('-')
    year = int(year_text)
    require(1900 <= year <= 9998, 'term year outside supported range')
    if season == 'spring':
        return date(year, 2, 1), date(year, 8, 1)
    require(season == 'autumn', 'invalid season')
    return date(year, 9, 1), date(year + 1, 2, 1)


def expected_months(key, today):
    cursor, end = term_bounds(key)
    result = []
    while cursor < end and cursor <= today:
        result.append(cursor.strftime('%Y-%m'))
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return result


def check_record_students(students, kind):
    ids = [item['id'] for item in students]
    require(len(ids) == len(set(ids)), 'duplicate student IDs, even with different values')
    require(all(item['value'] in VALUES[kind] for item in students), 'student value mismatches kind')


def check_policy_bounds(policy):
    base, minimum = Decimal(policy['base_score']), Decimal(policy['minimum_score'])
    maximum = None if policy['maximum_score'] is None else Decimal(policy['maximum_score'])
    require(Decimal('0') <= minimum <= base <= Decimal('999999.99'), 'monthly base/minimum range or order mismatch')
    if maximum is not None:
        require(base <= maximum <= Decimal('999999.99'), 'monthly maximum range or order mismatch')


def check_response_semantics(body, status, headers):
    meta = body['meta']
    today = date.fromisoformat(meta['business_date'])
    require(meta['current_term_key'] == term_for(today), 'current term disagrees with Shanghai business date')
    if status >= 400:
        error = body['error']
        require(error['code'] in ERROR_STATUSES[status], 'error code/HTTP status mismatch')
        require(error['retryable'] == (status == 503), 'retryable must only mean automatic 503 retry')
        require(meta['replayed'] is False, 'error is not a successful replay')
        if status == 503:
            require(int(headers.get('Retry-After', 0)) == error.get('retry_after_seconds'), 'Retry-After mismatch')
        if status == 401:
            require(headers.get('WWW-Authenticate', '').startswith('Bearer'), '401 needs Bearer challenge')
        return
    if 'pagination' in body:
        require(len(body['result']) <= body['pagination']['limit'], 'page exceeds requested limit')
    if isinstance(body['result'], dict) and 'record' in body['result']:
        record = body['result']
        check_record_students(record['students'], record['record']['kind'])
    if isinstance(body['result'], dict) and 'base_score' in body['result']:
        check_policy_bounds(body['result'])
    if 'score_basis' in body:
        term, policy = body['term'], body['policy']
        check_policy_bounds(policy)
        start, end = term_bounds(term['key'])
        require(term['start_date'] == start.isoformat(), 'wrong term start')
        require(term['end_date_exclusive'] == end.isoformat(), 'wrong term end')
        require(term['months'] == expected_months(term['key'], today), 'wrong elapsed/full month set')
        require(policy['term_key'] == term['key'], 'policy belongs to a different term')
        require(term['read_only'] == (end <= today), 'wrong read-only state')
        require(policy['frozen'] == term['read_only'], 'policy lock disagrees with term')
        require(body['score_basis'] == ('archived_baseline' if term['read_only'] else 'current_facts'), 'wrong score basis')
        for row in body['result']:
            require([item['month'] for item in row['months']] == term['months'], 'student has missing/extra months')
            if body['score_basis'] == 'archived_baseline':
                continue  # Frozen legacy baseline must not be rewritten by today's algorithm.
            monthly_scores = []
            for month in row['months']:
                total = Decimal(policy['base_score'])
                for key in WEIGHT_KEYS:
                    direction = 1 if key in {'low', 'mid', 'high'} else -1
                    total += direction * month['counts'][key] * Decimal(policy['weights'][key])
                score = max(Decimal(policy['minimum_score']), total)
                if policy['maximum_score'] is not None:
                    score = min(Decimal(policy['maximum_score']), score)
                require(Decimal(month['score']) == score, 'monthly arithmetic mismatch')
                monthly_scores.append(score)
            average = (sum(monthly_scores) / len(monthly_scores)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            require(Decimal(row['term_score']) == average, 'term average/rounding mismatch')


def check_example(spec, path):
    example = json.loads(path.read_text(encoding='utf-8'))
    require(example.get('synthetic') is True, f'{path.name}: must be marked synthetic')
    request, response = example['request'], example['response']
    require(request['headers'].get('Authorization') == 'Bearer SYNTHETIC-NOT-A-CREDENTIAL', 'unexpected credential in example')
    method = request['method'].lower()
    path_item, operation, path_values = match_operation(spec, method, request['path'])
    sources = {'path': path_values, 'query': request.get('query', {}), 'header': request['headers']}
    known_query = set()
    for ref in path_item.get('parameters', []) + operation.get('parameters', []):
        parameter = dereference(spec, ref)
        source, name = sources[parameter['in']], parameter['name']
        if parameter['in'] == 'query':
            known_query.add(name)
        require(not parameter.get('required') or name in source, f'{path.name}: missing {name}')
        if name in source:
            validate(spec, parameter['schema'], source[name], f'{path.name}/{parameter["in"]}/{name}')
    require(set(request.get('query', {})) <= known_query, f'{path.name}: unknown query/owner parameter')
    body_schema = operation.get('requestBody', {}).get('content', {}).get('application/json', {}).get('schema')
    if body_schema:
        require(request['headers'].get('Content-Type') == 'application/json', 'writes require JSON')
        request_ok = accepts(spec, body_schema, request.get('body'))
        try:
            if request_ok:
                check_record_students(request['body']['students'], request['body']['kind'])
        except InvalidContract:
            request_ok = False
        require(request_ok == example.get('request_schema_valid', True), f'{path.name}: unexpected request validation result')
        if not request_ok:
            require(response['status'] == 400, 'invalid request schema must use 400 example')
    else:
        require('body' not in request, 'GET examples must not carry a body')
    status = str(response['status'])
    require(status in operation['responses'], f'{path.name}: undocumented response {status}')
    definition = dereference(spec, operation['responses'][status])
    validate(spec, definition['content']['application/json']['schema'], response['body'], path.name)
    check_response_semantics(response['body'], response['status'], response.get('headers', {}))
    return operation['operationId'], response['status'], example


def check_negative_cases(spec):
    schemas = spec['components']['schemas']
    negative_count = 0
    for bad in [1, -1, '1', '1.0', '-0.01', '1.001', '1e2', 'NaN', '01.00']:
        require(not accepts(spec, schemas['DecimalAmount'], bad), f'bad decimal accepted: {bad!r}')
        negative_count += 1
    for good in ['0.00', '1.50', '12.00', '100.00']:
        validate(spec, schemas['DecimalAmount'], good)
    for good in ['0.00', '60.00', '999999.99']:
        validate(spec, schemas['PolicyAmount'], good)
    for bad in ['1000000.00', '-0.01', '1.001', 60, '1e2']:
        require(not accepts(spec, schemas['PolicyAmount'], bad), f'bad configurable amount accepted: {bad!r}')
        negative_count += 1
    for base, minimum, maximum in [('60.00', '61.00', None), ('60.00', '0.00', '59.99'),
                                   ('1000000.00', '0.00', None)]:
        try:
            check_policy_bounds({'base_score': base, 'minimum_score': minimum, 'maximum_score': maximum})
        except InvalidContract:
            negative_count += 1
        else:
            raise InvalidContract('invalid monthly policy order accepted')
    fixture = {
        'expected_current_term_key': '2026-autumn', 'term_key': '2026-autumn',
        'roster_revision': 1, 'kind': 'class', 'date': '2026-09-22',
        'name': '合成点名', 'students': [{'id': '201', 'value': 'normal'}],
    }
    for kind, values in VALUES.items():
        for value in values:
            candidate = copy.deepcopy(fixture)
            candidate['kind'], candidate['students'][0]['value'] = kind, value
            validate(spec, schemas['CreateRecordRequest'], candidate)
    mutations = []
    bad = copy.deepcopy(fixture); bad['owner_id'] = '999'; mutations.append(bad)
    bad = copy.deepcopy(fixture); bad['revision'] = 1; mutations.append(bad)
    bad = copy.deepcopy(fixture); bad['students'][0]['value'] = 'high'; mutations.append(bad)
    bad = copy.deepcopy(fixture); bad['name'] = '   '; mutations.append(bad)
    bad = copy.deepcopy(fixture); bad['date'] = '2026-02-30'; mutations.append(bad)
    bad = copy.deepcopy(fixture); bad['students'] *= 1001; mutations.append(bad)
    bad = copy.deepcopy(fixture); del bad['roster_revision']; mutations.append(bad)
    for bad in mutations:
        require(not accepts(spec, schemas['CreateRecordRequest'], bad), 'invalid record accepted')
        negative_count += 1
    require(not accepts(spec, schemas['ReplaceRecordRequest'], fixture), 'PUT without revision accepted')
    negative_count += 1
    amended = dict(fixture, revision=2)
    validate(spec, schemas['ReplaceRecordRequest'], amended)
    try:
        check_record_students([{'id': '201', 'value': 'normal'}, {'id': '201', 'value': 'late'}], 'class')
    except InvalidContract:
        negative_count += 1
    else:
        raise InvalidContract('duplicate student ID with different values accepted')
    require(expected_months('2026-spring', date(2026, 8, 1)) == [f'2026-{m:02}' for m in range(2, 8)], 'spring calendar failed')
    require(expected_months('2026-autumn', date(2027, 2, 1)) == ['2026-09', '2026-10', '2026-11', '2026-12', '2027-01'], 'autumn calendar failed')
    require(expected_months('2026-autumn', date(2026, 10, 1)) == ['2026-09', '2026-10'], 'future months included')
    require(term_for(date(2026, 8, 31)) is None and term_for(date(2027, 1, 31)) == '2026-autumn', 'boundary mapping failed')
    return negative_count


def main():
    spec = yaml.safe_load((ROOT / 'openapi.yaml').read_text(encoding='utf-8'))
    operations = check_structure(spec)
    results = [check_example(spec, path) for path in sorted((ROOT / 'examples').glob('*.json'))]
    require(results, 'No synthetic examples')
    covered = {operation for operation, status, _ in results if status < 400}
    require(covered == set(operations), f'Missing successful operation examples: {set(operations) - covered}')
    statuses = {status for _, status, _ in results}
    require({400, 401, 403, 404, 409, 503} <= statuses, 'Missing error response scenarios')
    examples = {example['name']: example for _, _, example in results}
    initial, replay = examples['create_class_record'], examples['retry_same_submission']
    require(initial['request'] == replay['request'], 'retry must keep request/key identical')
    require(initial['response']['body']['result'] == replay['response']['body']['result'], 'retry changed result')
    require(replay['response']['body']['meta']['replayed'], 'retry not marked replayed')
    conflict = examples['same_key_changed_body']
    require(initial['request']['headers']['Idempotency-Key'] == conflict['request']['headers']['Idempotency-Key'], 'conflict example changed key')
    require(initial['request']['body'] != conflict['request']['body'], 'conflict example did not change body')
    negative_count = check_negative_cases(spec)
    print(f'PASS: {len(operations)} disabled operations; {len(results)} synthetic exchanges; {negative_count} rejection checks; calendar/scoring/retry invariants.')
    print('Contract-only validation. No HTTP endpoint, credential, Django, database or network was used.')


if __name__ == '__main__':
    try:
        main()
    except (InvalidContract, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        print(f'FAIL: {exc}', file=sys.stderr)
        raise SystemExit(1)
