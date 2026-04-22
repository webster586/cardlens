from src.pokemon_scanner.config.settings import AppSettings


def test_default_settings_values() -> None:
    settings = AppSettings()
    assert settings.app_name == "Pokemon Scanner"
    assert settings.enable_mock_recognition is True
