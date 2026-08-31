// Runs on the audio rendering thread, its own separate global scope with no
// access to the page's other modules (build-plan item 21b) - so this stays
// self-contained rather than importing anything from lib/audio.ts. It only
// batches raw mic samples; PCM16 conversion happens on the main thread,
// where the WebSocket send happens, keeping this thread free of anything
// but audio math (CLAUDE.md section 9: no blocking I/O in the audio path).
class PCMCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    // 320 samples = 20ms at 16kHz - a reasonable batch size for network
    // send frequency without adding perceptible extra latency.
    this._batchSize = 320;
    this._buffer = new Float32Array(this._batchSize);
    this._bufferedCount = 0;
  }

  process(inputs) {
    const input = inputs[0];

    if (!input || input.length === 0) {
      return true;
    }

    const channel = input[0];

    for (let i = 0; i < channel.length; i++) {
      this._buffer[this._bufferedCount] = channel[i];
      this._bufferedCount++;

      if (this._bufferedCount === this._batchSize) {
        this.port.postMessage(this._buffer.slice());
        this._bufferedCount = 0;
      }
    }

    return true;
  }
}

registerProcessor("pcm-capture-processor", PCMCaptureProcessor);
