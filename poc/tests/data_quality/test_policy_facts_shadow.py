"""Synthetic contract fixtures only; not human gold or customer grade evaluation."""
from __future__ import annotations

import copy
import itertools
import json
import traceback
from dataclasses import replace

import pytest

from koipa.modules.m3_labeling.policy_engine import Policy, Rule
from koipa.policy_facts import FactContext, FactContractError, text_digest, value_digest
from koipa.policy_shadow import attach_shadow, evaluate_shadow, parse_shadow_policy, policy_digest

BODY = '가상 문서: 제조 온도 80도, 유지 시간 10분.'
CONTEXT = FactContext(org_id='fictional-org', document_id='fictional-doc',
                      document_sha256=text_digest(BODY), as_of='2026-09-15T03:00:00+00:00')


def policy(*rules):
    return Policy(CONTEXT.org_id, 'fictional-policy-v1', '2026-09-15',
                  ('TS', 'S1', 'S2', 'S3'), tuple(rules) or (
        Rule('HIGH', 'TS', 10, {'public_disclosed': {'op': 'eq', 'value': False},
                              'access_scope': {'op': 'eq', 'value': 'approved_only'}}, ('access_scope',)),
        Rule('LOW', 'S2', 50, {'public_disclosed': {'op': 'eq', 'value': False}}),
        Rule('PUBLIC', 'S3', 90, {'public_disclosed': {'op': 'eq', 'value': True}}),
    ), 'S3')


def packet(pol=None):
    pol = pol or policy()
    return {'schema_version': 'policy-facts-v1-draft', 'org_id': CONTEXT.org_id,
            'document_id': CONTEXT.document_id, 'document_sha256': CONTEXT.document_sha256,
            'policy_version': pol.version, 'policy_sha256': policy_digest(pol),
            'sources': [], 'facts': [], 'estimates': []}


def add_claim(pack, fact, value, *, origin='human_review', state=None):
    absent = value is None or value is False or value == []
    state = state or ('proven_absent' if absent else 'observed')
    sid = f'source-{len(pack["sources"])}'
    payload = {fact: value}
    digest = value_digest(payload)
    pack['sources'].append({
        'source_id': sid, 'kind': origin, 'org_id': CONTEXT.org_id,
        'document_id': CONTEXT.document_id, 'document_sha256': CONTEXT.document_sha256,
        'payload': payload, 'payload_sha256': digest, 'captured_at': '2026-09-15T02:00:00Z',
        'source_ref': 'fictional-receipt-not-a-human-signature',
    })
    pack['facts'].append({'fact': fact, 'state': state, 'value': value, 'origin': origin,
                         'evidence': [{'source_id': sid, 'source_sha256': digest,
                                       'locator': {'kind': 'json_pointer', 'pointer': '/' + fact},
                                       'value_sha256': value_digest(value)}]})
    return pack


def text_claim(pack, fact='has_concrete_parameters', value=True):
    digest = text_digest(BODY)
    pack['sources'].append({
        'source_id': 'body', 'kind': 'document_text', 'org_id': CONTEXT.org_id,
        'document_id': CONTEXT.document_id, 'document_sha256': CONTEXT.document_sha256,
        'payload': BODY, 'payload_sha256': digest, 'captured_at': '2026-09-15T02:00:00Z',
        'source_ref': 'fictional-extracted-text-v1',
    })
    pack['facts'].append({'fact': fact, 'state': 'observed', 'value': value, 'origin': 'document_text',
                         'evidence': [{'source_id': 'body', 'source_sha256': digest,
                                       'locator': {'kind': 'text_span', 'start': 0, 'end': len(BODY)},
                                       'value_sha256': digest}]})
    return pack


def run(pack, pol=None):
    return evaluate_shadow(pol or policy(), pack, context=CONTEXT)


def test_no_policy_is_exact_noop_without_inspecting_invalid_inputs():
    before = {'label': 'S1', 'status': 'needs_review', 'score': 0.8, 'reasons': ['old']}
    after = attach_shadow(before, packet={'malformed': object()}, context=None)
    assert after == before and after is not before
    after['reasons'].append('copy')
    assert before['reasons'] == ['old']


def test_known_bound_claims_return_candidate_never_approval():
    result = run(add_claim(add_claim(packet(), 'public_disclosed', False), 'access_scope', 'approved_only'))
    assert (result['status'], result['grade']) == ('candidate', 'TS')
    assert result['evidence_binding_verified'] is True
    for field in ('automation_allowed', 'policy_approval_verified', 'evidence_authenticity_verified',
                  'semantic_truth_verified', 'customer_accuracy_measured'):
        assert result[field] is False


def test_missing_high_rule_does_not_publish_lower_grade():
    result = run(add_claim(packet(), 'public_disclosed', False))
    assert result['status'] == 'needs_evidence' and result['grade'] is None
    assert result['matched_candidate']['grade'] == 'S2'
    assert result['missing_evidence'] == ['access_scope']


def test_unknown_input_is_not_default_grade():
    result = run(packet())
    assert result['grade'] is None and result['status'] == 'needs_evidence'
    assert result['facts']['access_scope']['state'] == 'unknown'


@pytest.mark.parametrize('key,value', [('label', 'TS'), ('score', 0.9), ('evaluation_factors', {}),
                                    ('rule_factors', {}), ('source_type', 'internal')])
def test_unrecognized_packet_fields_do_not_become_evidence(key, value):
    pack = packet()
    pack[key] = value
    with pytest.raises(FactContractError, match='invalid_fact_contract'):
        run(pack)


@pytest.mark.parametrize('origin', ['model_estimated', 'rule_estimated', 'label_derived'])
def test_estimated_origin_cannot_be_asserted_fact(origin):
    pack = add_claim(packet(), 'public_disclosed', False)
    pack['facts'][0]['origin'] = origin
    with pytest.raises(FactContractError):
        run(pack)


def test_estimates_remain_separate_and_unused():
    pack = packet()
    pack['estimates'] = [{'fact': 'public_disclosed', 'value': False, 'origin': 'model_estimated',
                         'producer_ref': 'fictional-model'}]
    result = run(pack)
    assert result['estimate_count'] == 1 and result['estimates_used_for_policy'] is False
    assert result['grade'] is None
    assert result['facts']['public_disclosed']['state'] == 'unknown'


@pytest.mark.parametrize('fact,value', [('access_scope', 'approved_only'), ('public_disclosed', True)])
def test_document_text_cannot_assert_actual_access_or_public_status(fact, value):
    with pytest.raises(FactContractError):
        run(text_claim(packet(), fact, value))


def test_text_span_binding_does_not_claim_semantic_truth():
    pol = policy(Rule('PARAM', 'S2', 10, {'has_concrete_parameters': {'op': 'eq', 'value': True}}))
    result = run(text_claim(packet(pol)), pol)
    assert result['grade'] == 'S2'
    assert result['semantic_truth_verified'] is False


@pytest.mark.parametrize('field', ['org_id', 'document_id', 'document_sha256', 'policy_version', 'policy_sha256'])
def test_packet_scope_and_policy_must_match_independent_context(field):
    pack = packet()
    pack[field] = 'a' * 64 if 'sha256' in field else 'other'
    with pytest.raises(FactContractError):
        run(pack)


@pytest.mark.parametrize('field', ['org_id', 'document_id', 'document_sha256'])
def test_source_cannot_be_replayed_from_another_scope(field):
    pack = add_claim(packet(), 'public_disclosed', False)
    pack['sources'][0][field] = 'a' * 64 if 'sha256' in field else 'other'
    with pytest.raises(FactContractError, match='source_scope_mismatch'):
        run(pack)


@pytest.mark.parametrize('change', ['source_payload', 'source_hash', 'ref_hash', 'pointer', 'location_hash', 'source_id'])
def test_tampered_evidence_is_rejected(change):
    pack = add_claim(packet(), 'public_disclosed', False)
    source, ref = pack['sources'][0], pack['facts'][0]['evidence'][0]
    if change == 'source_payload':
        source['payload']['public_disclosed'] = True
    elif change == 'source_hash':
        source['payload_sha256'] = 'a' * 64
    elif change == 'ref_hash':
        ref['source_sha256'] = 'a' * 64
    elif change == 'pointer':
        ref['locator']['pointer'] = '/does-not-exist'
    elif change == 'location_hash':
        ref['value_sha256'] = 'a' * 64
    else:
        ref['source_id'] = 'not-found'
    with pytest.raises(FactContractError):
        run(pack)


@pytest.mark.parametrize('start,end', [(0, 9999), (4, 2), (-1, 5)])
def test_text_offsets_are_checked(start, end):
    pack = text_claim(packet())
    pack['facts'][0]['evidence'][0]['locator'].update(start=start, end=end)
    with pytest.raises(FactContractError):
        run(pack)


@pytest.mark.parametrize('field,value', [
    ('captured_at', '2026-09-16T00:00:00Z'), ('captured_at', '2026-09-15T00:00:00'),
    ('valid_until', '2026-09-15T03:00:00Z'), ('valid_until', '2026-09-14T00:00:00Z'),
])
def test_future_expired_and_timezone_less_sources_are_rejected(field, value):
    pack = add_claim(packet(), 'public_disclosed', False)
    pack['sources'][0][field] = value
    with pytest.raises(FactContractError):
        run(pack)


def test_absence_is_not_missing_input_even_for_missing_operator():
    pol = policy(Rule('NO-MARK', 'S2', 10, {'security_marking': {'op': 'missing'}}, ('security_marking',)))
    unknown = run(packet(pol), pol)
    absent = run(add_claim(packet(pol), 'security_marking', None, origin='system_metadata'), pol)
    assert unknown['grade'] is None and unknown['status'] == 'needs_evidence'
    assert absent['grade'] == 'S2' and absent['facts']['security_marking']['state'] == 'proven_absent'


@pytest.mark.parametrize('field,value', [('value', False), ('origin', 'human_review')])
def test_unknown_cannot_carry_value_or_claimed_origin(field, value):
    pack = packet()
    pack['facts'] = [{'fact': 'public_disclosed', 'state': 'unknown', field: value}]
    with pytest.raises(FactContractError):
        run(pack)


@pytest.mark.parametrize('value', [0, 1, 'false', None])
def test_boolean_evidence_is_not_coerced(value):
    with pytest.raises(FactContractError):
        run(add_claim(packet(), 'public_disclosed', value))


def test_pointer_value_false_does_not_equal_zero():
    pack = add_claim(packet(), 'public_disclosed', False)
    source, ref = pack['sources'][0], pack['facts'][0]['evidence'][0]
    source['payload']['public_disclosed'] = 0
    source['payload_sha256'] = ref['source_sha256'] = value_digest(source['payload'])
    ref['value_sha256'] = value_digest(0)
    with pytest.raises(FactContractError, match='evidence_value_mismatch'):
        run(pack)


def test_conflicting_claims_do_not_choose_latest_or_first():
    pack = add_claim(add_claim(packet(), 'public_disclosed', False), 'public_disclosed', True)
    for order in (pack['facts'], list(reversed(pack['facts']))):
        candidate = copy.deepcopy(pack)
        candidate['facts'] = order
        result = run(candidate)
        assert result['grade'] is None and result['status'] == 'needs_evidence_conflict_review'
        assert result['conflicting_facts'] == ['public_disclosed']


def test_same_claim_from_two_sources_is_not_conflict():
    result = run(add_claim(add_claim(packet(), 'public_disclosed', True), 'public_disclosed', True))
    assert result['grade'] == 'S3' and result['conflicting_facts'] == []
    assert len(result['facts']['public_disclosed']['source_ids']) == 2


def test_duplicate_source_id_is_rejected():
    pack = add_claim(packet(), 'public_disclosed', False)
    pack['sources'].append(copy.deepcopy(pack['sources'][0]))
    with pytest.raises(FactContractError, match='duplicate_source_id'):
        run(pack)


def test_rule_conflict_suppresses_main_grade():
    pol = policy(Rule('a', 'S2', 10, {'public_disclosed': {'value': False}}),
                 Rule('b', 'TS', 10, {'public_disclosed': {'value': False}}))
    result = run(add_claim(packet(pol), 'public_disclosed', False), pol)
    assert result['grade'] is None and result['status'] == 'needs_policy_review'
    assert result['conflicting_rules'] == ['a', 'b']


def test_lower_grade_but_higher_priority_blocked_rule_also_blocks_decision():
    pol = policy(Rule('exception', 'S3', 5, {'access_scope': {'value': 'all_employees'}}),
                 Rule('other', 'TS', 10, {'public_disclosed': {'value': False}}))
    result = run(add_claim(packet(pol), 'public_disclosed', False), pol)
    assert result['grade'] is None and result['decision_blocked_rules'] == ['exception']


def test_known_false_rule_is_not_blocked_by_other_missing_evidence():
    result = run(add_claim(packet(), 'public_disclosed', True))
    assert result['grade'] == 'S3' and result['blocked_rules'] == []


def test_no_matching_rule_does_not_apply_default_grade():
    pol = policy(Rule('private', 'S1', 10, {'public_disclosed': {'value': False}}))
    result = run(add_claim(packet(pol), 'public_disclosed', True), pol)
    assert result['status'] == 'no_matching_rule' and result['grade'] is None


def test_composition_does_not_mutate_existing_routing_or_inputs():
    existing = {'label': 'S1', 'status': 'classified', 'score': 0.7, 'needs_review': False}
    pack = add_claim(packet(), 'public_disclosed', False)
    before = copy.deepcopy(pack)
    result = attach_shadow(existing, policy=policy(), packet=pack, context=CONTEXT)
    assert {k: result[k] for k in existing} == existing
    assert result['policy_proposal']['grade'] is None
    assert pack == before and 'policy_proposal' not in existing


def test_invalid_shadow_is_isolated_from_legacy_result():
    result = attach_shadow({'label': 'S1'}, policy=policy(), packet={}, context=CONTEXT)
    assert result['label'] == 'S1' and result['policy_proposal']['status'] == 'invalid_input'
    assert 'BODY' not in str(result)


def test_existing_proposal_is_not_overwritten():
    with pytest.raises(FactContractError):
        attach_shadow({'policy_proposal': {'existing': True}}, policy=policy())


@pytest.mark.parametrize('change', ['org', 'future', 'duplicate_grades', 'invalid_priority', 'unknown_evidence'])
def test_policy_scope_and_shape_checked_before_proposal(change):
    pol = policy()
    if change == 'org':
        pol = replace(pol, org_id='other')
    elif change == 'future':
        pol = replace(pol, effective_date='2099-01-01')
    elif change == 'duplicate_grades':
        pol = replace(pol, grade_order=('TS', 'S1', 'S2', 'S3', 'S3'))
    elif change == 'invalid_priority':
        pol.rules[0].priority = True
    else:
        pol.rules[0].requires_evidence = ('evaluation_factors',)
    with pytest.raises(FactContractError):
        run(packet(pol), pol)


def test_explicit_three_state_matrix_preserves_unknown_and_false():
    # 3 x 3: synthetic mechanics, not nine independently approved grade answers.
    for public, access in itertools.product((None, True, False), (None, 'approved_only', 'all_employees')):
        pack = packet()
        if public is not None:
            add_claim(pack, 'public_disclosed', public)
        if access is not None:
            add_claim(pack, 'access_scope', access, origin='system_metadata')
        result = run(pack)
        if public is True:
            assert result['grade'] == 'S3'
        elif public is None or access is None:
            assert result['grade'] is None
        elif access == 'approved_only':
            assert result['grade'] == 'TS'
        else:
            assert result['grade'] == 'S2'


def test_content_kind_order_is_not_a_fact_conflict():
    pol = policy(Rule('kinds', 'S2', 10, {'content_kinds': {'op': 'eq', 'value': ['원가', '가격']}}))
    pack = add_claim(add_claim(packet(pol), 'content_kinds', ['가격', '원가']), 'content_kinds', ['원가', '가격'])
    result = run(pack, pol)
    assert result['grade'] == 'S2' and result['conflicting_facts'] == []


def test_bound_empty_list_can_prove_absence():
    pol = policy(Rule('empty', 'S3', 10, {'content_kinds': {'op': 'missing'}}, ('content_kinds',)))
    assert run(add_claim(packet(pol), 'content_kinds', []), pol)['grade'] == 'S3'
    assert run(packet(pol), pol)['grade'] is None


def test_equivalent_timezones_have_same_policy_activation_date():
    pack = add_claim(packet(), 'public_disclosed', True)
    alternate = CONTEXT.model_copy(update={'as_of': '2026-09-14T23:00:00-04:00'})
    assert evaluate_shadow(policy(), pack, context=alternate)['grade'] == run(pack)['grade']


def test_public_snapshot_is_bound_but_not_authenticated_or_fetched():
    pack = text_claim(packet(), 'public_disclosed', True)
    source, fact = pack['sources'][0], pack['facts'][0]
    source['kind'] = fact['origin'] = 'public_source'
    source['source_ref'] = 'https://invalid.example/fictional-publication'
    result = run(pack)
    assert result['grade'] == 'S3' and result['evidence_authenticity_verified'] is False


def test_json_pointer_escaped_keys_and_array_indices():
    pack = add_claim(packet(), 'public_disclosed', False)
    source, ref = pack['sources'][0], pack['facts'][0]['evidence'][0]
    source['payload'] = {'a/b': {'~items': [False]}}
    source['payload_sha256'] = ref['source_sha256'] = value_digest(source['payload'])
    ref['locator']['pointer'] = '/a~1b/~0items/0'
    assert run(pack)['facts']['public_disclosed']['state'] == 'proven_absent'


@pytest.mark.parametrize('pointer', ['/a~2b', '/missing/00', '/missing/-1', 'no-slash'])
def test_invalid_pointers_are_not_silently_normalized(pointer):
    pack = add_claim(packet(), 'public_disclosed', False)
    pack['facts'][0]['evidence'][0]['locator']['pointer'] = pointer
    with pytest.raises(FactContractError):
        run(pack)


def test_invalid_contract_traceback_does_not_echo_payload():
    pack = packet()
    pack['facts'] = [{'fact': 'public_disclosed', 'state': 'SENSITIVE-CONTENT-SENTINEL'}]
    try:
        run(pack)
    except FactContractError as exc:
        assert 'SENSITIVE-CONTENT-SENTINEL' not in ''.join(traceback.format_exception(exc))
    else:
        pytest.fail('Expected an invalid contract')


def raw_policy(pol):
    return {'org_id': pol.org_id, 'policy_version': pol.version, 'effective_date': pol.effective_date,
            'grade_order': list(pol.grade_order), 'default_grade': pol.default_grade, 'note': pol.note,
            'rules': [{'id': r.id, 'grade': r.grade, 'priority': r.priority, 'when': r.when,
                       'requires_evidence': list(r.requires_evidence), 'note': r.note} for r in pol.rules]}


@pytest.mark.parametrize('value', [True, '10', 10.0])
def test_policy_loader_does_not_coerce_priority(value):
    raw = raw_policy(policy())
    raw['rules'][0]['priority'] = value
    with pytest.raises(FactContractError):
        parse_shadow_policy(raw)


def test_policy_loader_rejects_unknown_semantic_field():
    raw = raw_policy(policy())
    raw['rules'][0]['when_any'] = {'access_scope': {'value': 'secret'}}
    with pytest.raises(FactContractError):
        parse_shadow_policy(raw)


@pytest.mark.parametrize('field,value', [('op', 'gte'), ('value', 0)])
def test_typed_boolean_conditions_do_not_use_numeric_coercion(field, value):
    pol = policy(Rule('boolean', 'S2', 10, {'public_disclosed': {'op': 'eq', 'value': False}}))
    pol.rules[0].when['public_disclosed'][field] = value
    with pytest.raises(FactContractError):
        run(packet(pol), pol)


def cli_inputs(tmp_path, pack=None):
    pol = policy()
    objects = {'policy': raw_policy(pol), 'packet': pack or packet(pol), 'context': CONTEXT.model_dump()}
    paths = {}
    for name, value in objects.items():
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        paths[name] = path
    args = [arg for name, path in paths.items() for arg in ('--' + name, str(path))]
    return paths, args


def test_cli_review_candidate_and_no_overwrite(tmp_path):
    import check_policy_shadow as cli
    paths, args = cli_inputs(tmp_path)
    output = tmp_path / 'result.json'
    assert cli.main([*args, '--out', str(output)]) == 3
    saved = output.read_bytes()
    assert cli.main([*args, '--out', str(output)]) == 2
    assert output.read_bytes() == saved
    paths['packet'].write_text(json.dumps(add_claim(packet(), 'public_disclosed', True)), encoding='utf-8')
    assert cli.main(args) == 0


@pytest.mark.parametrize('body', ['{"duplicate":1,"duplicate":2}', '{"bad":NaN}'])
def test_cli_rejects_ambiguous_json(tmp_path, body):
    import check_policy_shadow as cli
    paths, args = cli_inputs(tmp_path)
    paths['packet'].write_text(body, encoding='utf-8')
    assert cli.main(args) == 2


def test_cli_detects_input_change_during_computation(tmp_path, monkeypatch):
    import check_policy_shadow as cli
    paths, args = cli_inputs(tmp_path)
    original = cli.evaluate_shadow

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        paths['packet'].write_text(paths['packet'].read_text(encoding='utf-8') + '\n', encoding='utf-8')
        return result

    monkeypatch.setattr(cli, 'evaluate_shadow', mutate)
    output = tmp_path / 'result.json'
    assert cli.main([*args, '--out', str(output)]) == 2
    assert not output.exists()


def test_demo_is_explicitly_synthetic_and_non_training(tmp_path):
    import check_policy_shadow as cli
    output = tmp_path / 'demo.json'
    assert cli.main(['--demo', '--out', str(output)]) == 0
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['dataset_role'] == 'policy_fixture' and report['real_documents'] == 0
    results = {item['case_id']: item['result'] for item in report['results']}
    assert results['missing_management']['grade'] is None
    assert results['bound_private']['grade'] == 'TS'
    assert results['conflicting_public_claims']['grade'] is None
    assert all(not r['training_allowed'] and not r['model_evaluation_allowed'] for r in results.values())


def test_schema_output_is_structural_only(tmp_path):
    import check_policy_shadow as cli
    output = tmp_path / 'schema.json'
    assert cli.main(['--schema', '--out', str(output)]) == 0
    assert json.loads(output.read_text(encoding='utf-8'))['additionalProperties'] is False


def test_constructed_invalid_nested_model_is_revalidated():
    from koipa.policy_facts import FactAssertion
    pack = packet()
    pack['facts'].append(FactAssertion.model_construct(fact='public_disclosed', state='unvalidated', value=True))
    with pytest.raises(FactContractError):
        run(pack)
