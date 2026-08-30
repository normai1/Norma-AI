from fastapi.testclient import TestClient

from app.main import app


def test_media_echo_streams_audio_bidirectionally() -> None:
    """
    Sends raw audio bytes into the /media/echo WebSocket and asserts the
    exact same bytes come back out, having actually passed through a real
    Pipecat Pipeline/PipelineWorker (app/media_session.py) - not a
    hand-rolled bypass of the framework this feature evaluated and chose.
    """

    # Large enough to clear the output transport's internal chunking buffer
    # (audio_out_10ms_chunks defaults to 4 - 1280 bytes at 16kHz/16-bit
    # mono/10ms-per-chunk); a too-small payload never flushes and the read
    # below would hang forever. The output comes back as several
    # fixed-size WebSocket messages, not one - receiving in a loop until
    # the full length is back is what a real streaming client does too.
    sent = bytes(range(256)) * 20

    received = b""
    with TestClient(app) as client, client.websocket_connect("/media/echo") as websocket:
        websocket.send_bytes(sent)
        while len(received) < len(sent):
            received += websocket.receive_bytes()

    assert received == sent
