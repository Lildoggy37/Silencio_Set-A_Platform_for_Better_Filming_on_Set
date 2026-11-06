from silencio_set.core.config import Settings
from silencio_set.diane.configuration import model_options


def test_preview_imports_only_qwen_settings_and_masks_key(tmp_path):
    path = tmp_path / '.env'
    path.write_text('QWEN_API_KEY=example-private-key\nQWEN_BASE_URL=https://model.example/v1\nQWEN_MODEL=qwen-example\nDATABASE_URL=should-not-be-imported\nDIANE_ENABLED=true\n', encoding='utf-8')
    options = model_options(path)
    assert set(options) == {'llm_api_key', 'llm_base_url', 'llm_model'}
    assert 'example-private-key' not in repr(options)
    config = Settings(_env_file=None, **options)
    assert config.database_url is None and config.diane_enabled is False
    assert config.llm_api_key.get_secret_value() == 'example-private-key'


def test_environment_overrides_file_without_expanding_key(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    path.write_text('QWEN_API_KEY=example-${UNCHANGED}\nQWEN_MODEL=from-file\n', encoding='utf-8')
    monkeypatch.setenv('QWEN_MODEL', 'from-environment')
    options = model_options(path)
    assert options['llm_api_key'].get_secret_value() == 'example-${UNCHANGED}'
    assert options['llm_model'] == 'from-environment'


def test_official_dashscope_key_name_is_supported(tmp_path):
    path = tmp_path / '.env'
    path.write_text('DASHSCOPE_API_KEY=example-official-key\nLLM_MODEL=deepseek-v4.1-flash\n', encoding='utf-8')
    options = model_options(path)
    assert options['llm_api_key'].get_secret_value() == 'example-official-key'
    assert 'example-official-key' not in repr(options)
