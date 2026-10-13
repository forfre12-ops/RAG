"""Offline policy mechanics only, not customer grade answers or approvals."""
from __future__ import annotations

import itertools
import json

import pytest

import check_org_policy_rules as checker
from koipa.modules.m3_labeling.policy_engine import Policy, Rule, evaluate


def policy(*rules):
    return Policy('fixture', 'test-v1', '2026-09-15', ('TS', 'S1', 'S2', 'S3'),
                  tuple(rules), 'S3')


@pytest.mark.parametrize('conditions', list(itertools.permutations((
    ('public_disclosed', {'op': 'eq', 'value': False}),
    ('access_scope', {'op': 'eq', 'value': 'designated'}),
))))
def test_false_conjunction_does_not_become_blocked_by_required_evidence(conditions):
    result = evaluate(policy(
        Rule('high', 'TS', 10, dict(conditions), ('access_scope',)),
        Rule('public', 'S3', 90, {'public_disclosed': {'op': 'eq', 'value': True}}),
    ), {'public_disclosed': True})
    assert result.grade == 'S3'
    assert result.blocked_rules == ()
    assert result.missing_evidence == ()
    assert result.needs_review is False


def test_missing_evidence_of_possible_rule_still_blocks():
    result = evaluate(policy(
        Rule('high', 'TS', 10, {'public_disclosed': {'op': 'eq', 'value': False}}, ('access_scope',)),
        Rule('low', 'S2', 50, {'public_disclosed': {'op': 'eq', 'value': False}}),
    ), {'public_disclosed': False})
    assert result.needs_review and result.blocked_rules == ('high',)


def test_second_witness_prevents_false_unreachable_report():
    result = checker.check(policy(
        Rule('department', 'TS', 10, {'access_scope': {'op': 'eq', 'value': 'department'}}),
        Rule('limited', 'S2', 20, {'access_scope': {'op': 'in', 'value': ['department', 'designated']}}),
    ))
    assert result['unreachable'] == []
    row = next(r for r in result['rules'] if r['rule'] == 'limited')
    assert row['reachable'] is True
    assert row['facts']['access_scope'] == 'designated'


def test_mutually_exclusive_same_priority_is_not_proven_conflict():
    result = checker.check(policy(
        Rule('private', 'TS', 10, {'public_disclosed': {'op': 'eq', 'value': False}}),
        Rule('public', 'S3', 10, {'public_disclosed': {'op': 'eq', 'value': True}}),
    ))
    assert result['priority_conflicts'] == []
    assert result['potential_priority_conflicts']


def test_actual_overlap_has_replayable_witness():
    pol = policy(
        Rule('high', 'TS', 10, {'access_scope': {'op': 'in', 'value': ['department', 'designated']}}),
        Rule('low', 'S2', 10, {'access_scope': {'op': 'eq', 'value': 'designated'}}),
    )
    result = checker.check(pol)
    conflict = result['priority_conflicts'][0]
    assert conflict['facts']['access_scope'] == 'designated'
    assert evaluate(pol, conflict['facts']).needs_review is True
    assert result['status'] == 'FINDINGS'


def test_probe_limit_is_incomplete_never_clear():
    result = checker.check(policy(
        Rule('private', 'TS', 10, {'public_disclosed': {'op': 'eq', 'value': False}}),
        Rule('public', 'S3', 90, {'public_disclosed': {'op': 'eq', 'value': True}}),
    ), probe_limit=1)
    assert result['incomplete_checks']
    assert result['probe_coverage']['limit_reached'] is True
    assert result['policy_approval_granted'] is False
    assert result['exhaustive_proof'] is False


def test_not_in_probe_avoids_every_forbidden_value():
    forbidden = ['approved_only', 'all_employees']
    result = checker.check(policy(Rule('other', 'S2', 10,
        {'access_scope': {'op': 'not_in', 'value': forbidden}})))
    row = result['rules'][0]
    assert row['reachable'] is True
    assert row['facts']['access_scope'] not in forbidden


def _write_policy(tmp_path, rules):
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps({
        'org_id': 'fixture', 'policy_version': 'test-v1', 'effective_date': '2026-09-15',
        'grade_order': ['TS', 'S1', 'S2', 'S3'], 'default_grade': 'S3', 'rules': rules,
    }), encoding='utf-8')
    return path


def test_cli_fails_on_findings_and_writes_diagnostic(tmp_path, capsys):
    path = _write_policy(tmp_path, [
        {'id': 'high', 'grade': 'TS', 'priority': 10, 'when': {'public_disclosed': {'value': False}}},
        {'id': 'low', 'grade': 'S2', 'priority': 10, 'when': {'public_disclosed': {'value': False}}},
    ])
    output = tmp_path / 'report.json'
    assert checker.main([str(path), '--json', str(output)]) == 3
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['status'] == 'FINDINGS'
    assert report['policy_approval_granted'] is False
    assert '구멍 없음' not in capsys.readouterr().out


def test_cli_fails_on_incomplete_coverage(tmp_path):
    path = _write_policy(tmp_path, [
        {'id': 'private', 'grade': 'TS', 'when': {'public_disclosed': {'value': False}}},
    ])
    assert checker.main([str(path), '--probe-limit', '1']) == 3


def test_cli_does_not_overwrite_existing_report(tmp_path):
    path = _write_policy(tmp_path, [
        {'id': 'private', 'grade': 'TS', 'when': {'public_disclosed': {'value': False}}},
    ])
    output = tmp_path / 'report.json'
    output.write_text('preserve', encoding='utf-8')
    assert checker.main([str(path), '--json', str(output)]) == 2
    assert output.read_text(encoding='utf-8') == 'preserve'


def test_clean_report_is_sampled_not_an_approval():
    result = checker.check(policy(Rule('private', 'TS', 10,
        {'public_disclosed': {'op': 'eq', 'value': False}})))
    assert result['status'] == 'SAMPLED_NO_FINDINGS'
    assert result['exhaustive_proof'] is False
    assert result['policy_approval_granted'] is False


def test_probe_not_selected_is_not_unreachability_proof():
    result = checker.check(policy(
        Rule('wide', 'TS', 10, {'public_disclosed': {'op': 'eq', 'value': False}}),
        Rule('narrow', 'S2', 20, {'public_disclosed': {'op': 'eq', 'value': False},
                               'access_scope': {'op': 'eq', 'value': 'department'}}),
    ))
    assert result['unreachable'][0]['proof'] is False
    assert result['unreachable'][0]['status'] == 'NOT_SELECTED_IN_PROBES'


@pytest.mark.parametrize('op', ['gte', 'lte'])
def test_numeric_boundary_probes_do_not_inject_invalid_string_values(op):
    result = checker.check(policy(Rule('numeric', 'S2', 10,
        {'has_concrete_parameters': {'op': op, 'value': 1}})))
    assert result['rules'][0]['reachable'] is True
    assert result['inert_conditions'] == []


def test_clean_cli_is_diagnostic_success_only(tmp_path):
    path = _write_policy(tmp_path, [
        {'id': 'private', 'grade': 'TS', 'when': {'public_disclosed': {'value': False}}},
    ])
    assert checker.main([str(path)]) == 0


def test_cli_invalid_policy_fails_without_writing_report(tmp_path):
    path = _write_policy(tmp_path, [])
    output = tmp_path / 'report.json'
    assert checker.main([str(path), '--json', str(output)]) == 2
    assert not output.exists()


def test_cli_policy_change_during_check_fails(tmp_path, monkeypatch):
    path = _write_policy(tmp_path, [
        {'id': 'private', 'grade': 'TS', 'when': {'public_disclosed': {'value': False}}},
    ])
    original = checker.check

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        path.write_text(path.read_text(encoding='utf-8') + '\n', encoding='utf-8')
        return result

    monkeypatch.setattr(checker, 'check', mutate)
    output = tmp_path / 'report.json'
    assert checker.main([str(path), '--json', str(output)]) == 2
    assert not output.exists()


def test_rule_order_does_not_change_findings_or_selected_witnesses():
    rules = (
        Rule('department', 'TS', 10, {'access_scope': {'op': 'eq', 'value': 'department'}}),
        Rule('limited', 'S2', 20, {'access_scope': {'op': 'in', 'value': ['department', 'designated']}}),
    )
    first, second = (checker.check(policy(*order)) for order in (rules, tuple(reversed(rules))))
    for key in ('status', 'priority_conflicts', 'unreachable', 'probe_coverage'):
        assert first[key] == second[key]
    assert {r['rule']: r['facts'] for r in first['rules']} == {r['rule']: r['facts'] for r in second['rules']}
