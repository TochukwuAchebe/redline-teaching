import importlib.util
import json
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('prepare_legacy', Path(__file__).parents[1] / 'prepare_legacy_policies.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_staging_records_real_source_policy_without_inventing_an_engine_version(tmp_path):
    source = tmp_path / 'source'
    directory = source / '0123456789abcdef'
    directory.mkdir(parents=True)
    for name, data in [('status.json', b'{"status":"done"}'), ('deck.pptx', b'original'),
                       ('result.json', b'{"findings":[]}'), ('decisions.json', b'{"f":"accepted"}')]:
        (directory / name).write_bytes(data)
    rules = tmp_path / 'source-rules.yaml'
    rules.write_text('CONF_MISSING: {enabled: true}\n')
    destination = tmp_path / 'prepared'
    assert module.prepare(source, destination, rules, 'snapshot:source-build')['reviews'] == 1
    target = destination / directory.name
    policy = json.loads((target / 'guideline.json').read_text())
    status = json.loads((target / 'status.json').read_text())
    assert policy['config']['CONF_MISSING']['enabled']
    assert not policy['config']['INITIALS_PUNCTUATION']['enabled']
    assert status['engine_version'] == 'legacy-unrecorded'
    assert status['migration_source'] == 'snapshot:source-build'
    assert (directory / 'status.json').read_bytes() == b'{"status":"done"}'
    for name in ('deck.pptx', 'result.json', 'decisions.json'):
        assert (target / name).read_bytes() == (directory / name).read_bytes()
    with pytest.raises(FileExistsError):
        module.prepare(source, destination, rules, 'cannot overwrite')
