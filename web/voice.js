"use strict";

// Microphone input is retained in memory for one short utterance. PCM is sent only to
// the paired session on the same Mac; no browser speech-recognition service is used.
function monoWav(chunks, sampleRate) {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const raw = new Float32Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    raw.set(chunk, offset);
    offset += chunk.length;
  }
  const ratio = sampleRate / 16000,
    n = Math.floor(total / ratio);
  const data = new DataView(new ArrayBuffer(44 + n * 2));
  function label(pos, str) {
    for (let i = 0; i < str.length; i++) {
      data.setUint8(pos + i, str.charCodeAt(i));
    }
  }
  label(0, "RIFF");
  data.setUint32(4, 36 + n * 2, true);
  label(8, "WAVE");
  label(12, "fmt ");
  data.setUint32(16, 16, true);
  data.setUint16(20, 1, true);
  data.setUint16(22, 1, true);
  data.setUint32(24, 16000, true);
  data.setUint32(28, 32000, true);
  data.setUint16(32, 2, true);
  data.setUint16(34, 16, true);
  label(36, "data");
  data.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) {
    const start = Math.floor(i * ratio),
      end = Math.min(total, Math.max(start + 1, Math.floor((i + 1) * ratio)));
    let sum = 0;
    for (let j = start; j < end; j++) {
      sum += raw[j];
    }
    const sample = Math.max(-1, Math.min(1, sum / (end - start)));
    data.setInt16(44 + i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
  }
  return new Blob([data.buffer], {
    type: "audio/wav"
  });
}
class CubeVoice {
  constructor() {
    this.micEpoch = 0;
    this.stream = null;
    this.context = null;
    this.source = null;
    this.node = null;
    this.capture = null;
    this.audio = null;
    this.url = null;
    this.utterance = null;
  }
  get enabled() {
    return this.stream?.getAudioTracks().some(track => track.readyState === "live") || false;
  }
  async enable() {
    this.disable();
    const epoch = this.micEpoch;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      throw new Error("Microphone access needs HTTPS or localhost. Open this cube on your Mac using the printed simulator URL.");
    }
    let stream = null,
      context = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        },
        video: false
      });
      if (epoch !== this.micEpoch) {
        stream.getTracks().forEach(t => t.stop());
        return false;
      }
      context = new AudioContext();
      await context.audioWorklet.addModule("/static/pcm-worklet.js");
      if (epoch !== this.micEpoch) {
        stream.getTracks().forEach(t => t.stop());
        await context.close();
        return false;
      }
      const source = context.createMediaStreamSource(stream),
        node = new AudioWorkletNode(context, "kubo-input");
      const silent = context.createGain();
      silent.gain.value = 0;
      source.connect(node);
      node.connect(silent);
      silent.connect(context.destination);
      node.port.onmessage = event => this.frame(event.data);
      this.stream = stream;
      this.context = context;
      this.source = source;
      this.node = node;
      await context.resume();
      return epoch === this.micEpoch;
    } catch (error) {
      stream?.getTracks().forEach(t => t.stop());
      if (context?.state !== "closed") {
        await context?.close().catch(() => {});
      }
      if (epoch === this.micEpoch) {
        this.disable();
      }
      if (error.name === "NotAllowedError") {
        throw new Error("Microphone permission was declined. Allow the microphone in this site's browser settings or use Adult testing below.");
      }
      if (error.name === "NotFoundError") {
        throw new Error("No microphone was found. Connect one or use Adult testing below.");
      }
      throw error;
    }
  }
  stopCapture() {
    if (this.capture) {
      clearTimeout(this.capture.timer);
    }
    this.capture = null;
  }
  disable() {
    this.micEpoch++;
    this.stopCapture();
    this.stream?.getTracks().forEach(t => t.stop());
    this.stream = null;
    this.source?.disconnect();
    this.node?.disconnect();
    if (this.node) {
      this.node.port.onmessage = null;
    }
    this.source = this.node = null;
    const ctx = this.context;
    this.context = null;
    if (ctx && ctx.state !== "closed") {
      ctx.close().catch(() => {});
    }
  }
  listen(onAudio, onSilence, seconds = 14) {
    this.stopCapture();
    if (!this.enabled) {
      return;
    }
    const c = {
      chunks: [],
      pre: [],
      speechMs: 0,
      silenceMs: 0,
      onAudio,
      onSilence
    };
    this.capture = c;
    c.timer = setTimeout(() => this.finishCapture(c), seconds * 1000);
  }
  frame(chunk) {
    const c = this.capture;
    if (!c || !this.context) {
      return;
    }
    const ms = 1000 * chunk.length / this.context.sampleRate;
    let power = 0;
    for (const s of chunk) {
      power += s * s;
    }
    const talking = Math.sqrt(power / chunk.length) > .012;
    if (!c.chunks.length && !talking) {
      c.pre.push(chunk);
      if (c.pre.length > 5) {
        c.pre.shift();
      }
      return;
    }
    if (!c.chunks.length) {
      c.chunks.push(...c.pre);
    }
    c.chunks.push(chunk);
    if (talking) {
      c.speechMs += ms;
      c.silenceMs = 0;
    } else {
      c.silenceMs += ms;
    }
    if (c.silenceMs > 800) {
      this.finishCapture(c);
    }
  }
  finishCapture(c) {
    if (c !== this.capture) {
      return;
    }
    this.stopCapture();
    if (c.speechMs < 130 || !this.context) {
      c.onSilence();
      return;
    }
    c.onAudio(monoWav(c.chunks, this.context.sampleRate));
  }
  stopSound() {
    if (this.audio) {
      this.audio.onended = this.audio.onerror = null;
      this.audio.pause();
      this.audio.removeAttribute("src");
      this.audio.load();
      this.audio = null;
    }
    if (this.url) {
      URL.revokeObjectURL(this.url);
      this.url = null;
    }
    if (this.utterance) {
      this.utterance.onend = this.utterance.onerror = null;
      this.utterance = null;
      window.speechSynthesis?.cancel();
    }
  }
  async play(blob, onEnd, onError) {
    this.stopSound();
    this.url = URL.createObjectURL(blob);
    const audio = new Audio(this.url);
    this.audio = audio;
    audio.onended = () => {
      this.stopSound();
      onEnd();
    };
    audio.onerror = () => {
      this.stopSound();
      onError();
    };
    await audio.play();
  }
  sayLocal(text, onEnd) {
    const synth = window.speechSynthesis;
    const voice = synth?.getVoices().find(v => v.localService && /^en([-_]|$)/i.test(v.lang));
    if (!voice) {
      return false;
    }
    this.stopSound();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.voice = voice;
    utterance.lang = voice.lang;
    utterance.onend = utterance.onerror = () => {
      if (this.utterance === utterance) {
        this.utterance = null;
        onEnd();
      }
    };
    this.utterance = utterance;
    synth.speak(utterance);
    return true;
  }
}
