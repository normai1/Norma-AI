from httpx import AsyncClient

from app.main import app
from app.providers.factory import get_tts_provider_dependency
from app.providers.mock_speech import MockTTS
from app.providers.speech import SpeechProviderTimeout, SpeechProviderUnavailable, Voice
from tests.conftest import _signed_in

_VOICES = "/api/v1/voices"


def _override_tts(tts: MockTTS) -> None:
    app.dependency_overrides[get_tts_provider_dependency] = lambda: tts


async def test_list_voices_requires_authentication(client: AsyncClient) -> None:
    response = await client.get(_VOICES)

    assert response.status_code == 401


async def test_list_voices_returns_the_catalogue(client: AsyncClient) -> None:
    headers = await _signed_in(client, "voices-list@example.com")
    _override_tts(
        MockTTS(
            voices=[
                Voice(id="v1", name="Alex", language="en-US", gender="male"),
                Voice(
                    id="v2",
                    name="Priya",
                    language="hi-IN",
                    gender="female",
                    preview_url="https://example.com/priya.mp3",
                ),
            ],
        ),
    )

    response = await client.get(_VOICES, headers=headers)

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "v1",
            "name": "Alex",
            "language": "en-US",
            "gender": "male",
            "preview_url": None,
        },
        {
            "id": "v2",
            "name": "Priya",
            "language": "hi-IN",
            "gender": "female",
            "preview_url": "https://example.com/priya.mp3",
        },
    ]


async def test_list_voices_returns_empty_list_for_an_empty_catalogue(
    client: AsyncClient,
) -> None:
    headers = await _signed_in(client, "voices-empty@example.com")
    _override_tts(MockTTS())

    response = await client.get(_VOICES, headers=headers)

    assert response.status_code == 200
    assert response.json() == []


async def test_list_voices_maps_a_timeout_to_504(client: AsyncClient) -> None:
    headers = await _signed_in(client, "voices-timeout@example.com")
    _override_tts(MockTTS(failure=SpeechProviderTimeout("timed out")))

    response = await client.get(_VOICES, headers=headers)

    assert response.status_code == 504
    assert "timed out" not in response.text


async def test_list_voices_maps_a_provider_error_to_503(client: AsyncClient) -> None:
    headers = await _signed_in(client, "voices-unavailable@example.com")
    _override_tts(MockTTS(failure=SpeechProviderUnavailable("vendor exploded")))

    response = await client.get(_VOICES, headers=headers)

    assert response.status_code == 503
    assert "vendor exploded" not in response.text
