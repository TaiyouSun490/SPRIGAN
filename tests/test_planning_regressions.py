from dataclasses import replace

import pytest

from game_stack_planner.dependency_graph import add_dependency_evidence, plan_dependency_review
from game_stack_planner.benchmark_planner import benchmark
from game_stack_planner.graph_reasoning import dependency_issues
from game_stack_planner.models import Candidate, GameRequirement, ProjectSnapshot
from game_stack_planner.planning_context import _contains, _normalized
from game_stack_planner.planning_graph import CandidateIndex, build_graph
from game_stack_planner.repository import StackRepository
from game_stack_planner.service import GameStackPlanner, PlanningPolicy, _candidate_score, _make_plan, _tokens


def test_free_budget_retains_evidence_about_unowned_dependencies():
    repo = StackRepository(':memory:')
    try:
        main = Candidate(id='asset_store:1', source='asset_store', external_id='1',
                         title='Inventory', url='', categories=('inventory',), ownership='owned',
                         metadata={'asset_store_details': {'dependency_ids': ['2']}})
        dependency = Candidate(id='asset_store:2', source='asset_store', external_id='2',
                               title='Desktop dependency', url='',
                               metadata={'asset_store_details': {'platforms': ['pc']}})
        repo.upsert_candidates((main, dependency))
        result = GameStackPlanner(repo).recommend(prompt='インベントリ', platform='quest',
                                                   budget='free', remote=False)
        item = next(i for i in result['recommendations']['inventory'] if i['candidate']['id'] == main.id)
        assert item['planning_dependency_issues'][0]['kind'] == 'dependency_incompatible'
        assert all(i['candidate']['id'] != dependency.id for rows in result['recommendations'].values() for i in rows)
    finally:
        repo.close()


def test_known_registry_dependency_still_follows_resolved_unity_lock():
    main = Candidate(id='asset_store:1', source='asset_store', external_id='1', title='Inventory', url='',
                     categories=('inventory',), metadata={'local_validation': {'package_inspection': {
                         'package_manifests': [{'dependencies': {'com.example.child': '1.0.0'}}]}}})
    child = Candidate(id='openupm:com.example.child', source='openupm', external_id='com.example.child',
                      title='Child', url='')
    project = ProjectSnapshot(path='test', name='test', unity_version='6000.0.1f1', render_pipeline='urp',
                              input_backend='both', packages=(), package_lock={
                                  'com.example.child': {'version': '1.0.0', 'dependencies': {'com.example.shared': '2.0.0'}},
                                  'com.example.shared': {'version': '1.0.0', 'dependencies': {}},
                              })
    req = GameRequirement('inventory', 'inventory', 'high', 'inventory', '')
    graph = build_graph((req,), {'inventory': [_candidate_score(req, main, project=project, platform='pc')]},
                        [main, child], project, 'pc')
    review = plan_dependency_review(graph, {main.id})
    mismatch = next(e for e in review['dependencies'] if e['to'] == 'upm:com.example.shared')
    assert mismatch['version_status'] == 'below_minimum'
    assert mismatch['path'] == [main.id, child.id, 'upm:com.example.shared']


def test_japanese_only_scene_asset_is_retrieved_and_edits_are_not_stale():
    repo = StackRepository(':memory:')
    try:
        forest = Candidate(id='local:forest', source='local', external_id='forest', title='森林の素材', url='')
        repo.upsert_candidates((forest,))
        planner = GameStackPlanner(repo)
        first = planner.recommend(prompt='森を探索するゲーム', remote=False)
        assert first['recommendations']['visual_assets'][0]['candidate']['id'] == forest.id
        # A request-local index must not retain stale scenes or user style tags.
        repo.upsert_candidates((replace(forest, title='都市の素材'),))
        second = planner.recommend(prompt='森を探索するゲーム', remote=False)
        assert not any(i['candidate']['id'] == forest.id for i in second['recommendations']['visual_assets'])
    finally:
        repo.close()


@pytest.mark.parametrize('field,value', [('beam_width', True), ('beam_width', 2.5),
                                        ('options_per_requirement', False), ('options_per_requirement', 3.5)])
def test_search_width_must_be_an_integer(field, value):
    with pytest.raises(ValueError):
        PlanningPolicy(**{field: value})


@pytest.mark.parametrize('value', [True, '20', None, float('inf'), float('nan'), -1])
def test_policy_costs_reject_non_numeric_or_non_finite_values(value):
    with pytest.raises(ValueError):
        PlanningPolicy(dependency_version_cost=value)


def test_zero_threshold_does_not_revive_rejected_candidates():
    repo = StackRepository(':memory:')
    try:
        candidate = Candidate(id='asset_store:1', source='asset_store', external_id='1', title='Inventory',
                              url='', ownership='owned', categories=('inventory',),
                              metadata={'asset_store_details': {'platforms': ['pc']}})
        repo.upsert_candidates((candidate,))
        result = GameStackPlanner(repo, planning_policy=PlanningPolicy(minimum_relevance=0)).recommend(
            prompt='インベントリ', platform='quest', remote=False)
        assert not result['recommendations']['inventory']
        assert all('inventory' in p['missing'] for p in result['plans'])
        req = GameRequirement('inventory', 'inventory', 'high', 'inventory', '')
        rejected = _candidate_score(req, candidate, project=None, platform='quest')
        assert not _make_plan('installed_first', (req,), {'inventory': [rejected]},
                              policy=PlanningPolicy(minimum_relevance=0))['selected']
    finally:
        repo.close()


def test_cached_scoring_is_equivalent_and_retrieval_metadata_does_not_leak():
    candidate = Candidate(id='asset_store:1', source='asset_store', external_id='1', title='Forest Inventory',
                          url='', ownership='owned', categories=('inventory',),
                          description='A realistic forest.', metadata={'user_art_styles': ['水彩']})
    req = GameRequirement('inventory', 'inventory', 'high', 'inventory', '')
    index = CandidateIndex([candidate], _tokens)
    cache = {}
    rag = {'retrieval_mode': 'hybrid_dense', 'score': .85, 'rank': 2}
    for match in (rag, None):
        ordinary = _candidate_score(req, candidate, project=None, platform='pc', rag_match=match)
        cached = _candidate_score(req, candidate, project=None, platform='pc', rag_match=match,
                                  features=index.features[candidate.id], payload_cache=cache,
                                  query_tokens=_tokens(req.query))
        assert cached == ordinary
    assert 'retrieval' not in cached['candidate']
    assert 'compatibility' not in cache[candidate.id]
    assert candidate.metadata == {'user_art_styles': ['水彩']}


@pytest.mark.parametrize('text,term,expected', [
    ('photorealistic', 'realistic', False), ('forest2', 'forest', False),
    ('Forest models', 'forest', True), ('LOW-POLY forest', 'low-poly', True),
    ('森林の素材', '森林', True), ('ＦＯＲＥＳＴ', 'forest', True),
    ('woodland', 'urban', False), ('a forest scene', 'forest', True),
])
def test_fast_vocabulary_matching_keeps_word_boundaries(text, term, expected):
    assert _contains(_normalized(text), term) is expected


def test_dependency_fanout_is_bounded_and_reported_as_incomplete():
    root = Candidate(id='asset_store:1', source='asset_store', external_id='1', title='Root', url='',
                     metadata={'asset_store_details': {'dependency_ids': ['2', '3', '4', '5']}})
    nodes = {root.id: {'id': root.id, 'kind': 'candidate'}}
    edges = []
    add_dependency_evidence(nodes, edges, {root.id: root}, None, 'pc', max_edges=2)
    assert len([e for e in edges if e['relation'] == 'requires']) == 2
    assert plan_dependency_review({'nodes': list(nodes.values()), 'edges': edges}, {root.id})['truncated']


def test_dependency_depth_bound_and_cycles_remain_inspectable():
    candidates = {f'asset_store:{i}': Candidate(id=f'asset_store:{i}', source='asset_store', external_id=str(i),
                 title=str(i), url='', metadata={'asset_store_details': {'dependency_ids': [str(i % 4 + 1)]}})
                  for i in range(1, 5)}
    for depth, expected_count, truncated in ((2, 2, True), (8, 4, False)):
        nodes = {'asset_store:1': {'id': 'asset_store:1', 'kind': 'candidate'}}
        edges = []
        add_dependency_evidence(nodes, edges, candidates, None, 'pc', max_depth=depth, max_nodes=4)
        review = plan_dependency_review({'nodes': list(nodes.values()), 'edges': edges}, {'asset_store:1'})
        assert len(review['dependencies']) == expected_count
        assert review['truncated'] is truncated
        if not truncated:
            assert any(e['cycle'] for e in review['dependencies'])


def test_duplicate_issue_keeps_shortest_evidence_path():
    graph = {'edges': [
        {'from': 'root', 'to': 'shared', 'relation': 'requires', 'version_status': 'below_minimum'},
        {'from': 'root', 'to': 'bridge', 'relation': 'requires'},
        {'from': 'bridge', 'to': 'shared', 'relation': 'requires', 'version_status': 'below_minimum'},
    ]}
    issues = dependency_issues(graph, {'root'}, version_cost=20, incompatible_cost=45)
    assert len(issues['root']) == 1
    assert issues['root'][('shared', 'below_minimum')]['path'] == ['root', 'shared']


@pytest.mark.parametrize('length,truncated', [(8, False), (9, True)])
def test_review_and_costs_use_the_same_eight_edge_boundary(length, truncated):
    # Multiple graph seeds can expose a chain deeper than any one seed's limit.
    graph = {'nodes': [], 'edges': [{'from': str(i), 'to': str(i + 1), 'relation': 'requires',
                                   'version_status': 'below_minimum'} for i in range(length)]}
    review = plan_dependency_review(graph, {'0'})
    issues = dependency_issues(graph, {'0'}, version_cost=20, incompatible_cost=45)
    assert len(review['dependencies']) == len(issues['0']) == 8
    assert review['truncated'] is truncated
    assert all(len(edge['path']) <= 9 for edge in review['dependencies'])


def test_dependency_metadata_cutoff_is_not_presented_as_complete():
    candidate = Candidate(id='asset_store:1', source='asset_store', external_id='1', title='Root', url='',
                          metadata={'asset_store_details': {'dependency_ids': [str(i) for i in range(2, 103)]}})
    nodes, edges = {candidate.id: {'id': candidate.id, 'kind': 'candidate'}}, []
    add_dependency_evidence(nodes, edges, {candidate.id: candidate}, None, 'pc')
    assert len([e for e in edges if e['relation'] == 'requires']) == 100
    assert plan_dependency_review({'nodes': list(nodes.values()), 'edges': edges}, {candidate.id})['truncated']


def test_benchmark_keeps_source_catalog_and_history_unchanged(tmp_path):
    path = tmp_path / 'catalog.sqlite3'
    repo = StackRepository(path)
    try:
        repo.upsert_candidates((Candidate(id='local:1', source='local', external_id='1', title='Inventory', url=''),))
        before = list(repo._connection.iterdump())
        result = benchmark(path, prompt='インベントリ', runs=2)
        assert list(repo._connection.iterdump()) == before
        assert result['catalog_size'] == 1
        assert result['stable_results']
        assert len(result['seconds']) == 2
        assert result['median_seconds'] >= 0
    finally:
        repo.close()
