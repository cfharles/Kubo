// Batch microphone frames to the UI thread. Never store, play or send the input here.
class KuboInput extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(2048);
    this.offset = 0;
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) {
      return true;
    }
    for (const sample of channel) {
      this.buf[this.offset++] = sample;
      if (this.offset === this.buf.length) {
        this.port.postMessage(this.buf, [this.buf.buffer]);
        this.buf = new Float32Array(2048);
        this.offset = 0;
      }
    }
    return true;
  }
}
registerProcessor("kubo-input", KuboInput);
