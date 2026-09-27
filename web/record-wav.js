const sampleRates = [8000, 16000, 22050, 24000, 44100, 48000];
const fetchAudio = window.fetch.bind(window);

window.fetch = async function (input, init) {
  const url = typeof input === "string" ? input : input instanceof Request ? input.url : "";
  if (!url.endsWith("/api/transcribe") || !init?.body || !(init.body instanceof FormData)) {
    return fetchAudio(input, init);
  }

  const audio = init.body.get("audio");
  if (!(audio instanceof Blob) || audio.type === "audio/wav") return fetchAudio(input, init);

  const wav = await recordingToWav(audio);
  const body = new FormData();
  body.append("audio", wav, "recording.wav");
  return fetchAudio(input, { ...init, body });
};

async function recordingToWav(recording) {
  const context = new AudioContext();
  try {
    const decoded = await context.decodeAudioData(await recording.arrayBuffer());
    const sampleRate = sampleRates.includes(decoded.sampleRate) ? decoded.sampleRate : 16000;
    const audio = sampleRate === decoded.sampleRate ? decoded : await resample(decoded, sampleRate);
    return encodeWav(audio);
  } finally {
    await context.close();
  }
}

function resample(buffer, sampleRate) {
  const length = Math.max(1, Math.round(buffer.duration * sampleRate));
  const offline = new OfflineAudioContext(buffer.numberOfChannels, length, sampleRate);
  const source = offline.createBufferSource();
  source.buffer = buffer;
  source.connect(offline.destination);
  source.start();
  return offline.startRendering();
}

function encodeWav(buffer) {
  const channels = buffer.numberOfChannels;
  const length = buffer.length;
  const blockAlign = channels * 2;
  const dataSize = length * blockAlign;
  const header = new ArrayBuffer(44);
  const view = new DataView(header);
  const write = (offset, text) => {
    for (let index = 0; index < text.length; index += 1) view.setUint8(offset + index, text.charCodeAt(index));
  };
  write(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, buffer.sampleRate, true);
  view.setUint32(28, buffer.sampleRate * blockAlign, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, dataSize, true);

  const pcm = new Int16Array(length * channels);
  const channelData = Array.from({ length: channels }, (_, channel) => buffer.getChannelData(channel));
  for (let index = 0; index < length; index += 1) {
    for (let channel = 0; channel < channels; channel += 1) {
      const sample = Math.max(-1, Math.min(1, channelData[channel][index]));
      pcm[index * channels + channel] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
  }
  return new Blob([header, pcm], { type: "audio/wav" });
}
