const TTS_ENDPOINT = "http://127.0.0.1:5150/speak";
const DEFAULT_VOICE = "bf_isabella";
const DEFAULT_SPEED = 1.0;

let currentAudio = null;
let currentObjectUrl = null;
let activeRequestController = null;
let requestSerial = 0;

function cleanUpPlayback() {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio.src = "";
    currentAudio = null;
  }

  if (currentObjectUrl) {
    URL.revokeObjectURL(currentObjectUrl);
    currentObjectUrl = null;
  }
}

function stopSpeech() {
  requestSerial += 1;

  if (activeRequestController) {
    activeRequestController.abort();
    activeRequestController = null;
  }

  cleanUpPlayback();
}

async function speak(text) {
  stopSpeech();
  const thisRequest = requestSerial;
  activeRequestController = new AbortController();

  try {
    const response = await fetch(TTS_ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        text,
        voice: DEFAULT_VOICE,
        speed: DEFAULT_SPEED,
      }),
      signal: activeRequestController.signal,
    });

    if (!response.ok) {
      const details = await response.text();
      throw new Error(`Kokoro returned ${response.status}: ${details}`);
    }

    const audioBlob = await response.blob();

    if (thisRequest !== requestSerial) {
      return;
    }

    currentObjectUrl = URL.createObjectURL(audioBlob);
    currentAudio = new Audio(currentObjectUrl);

    currentAudio.addEventListener("ended", cleanUpPlayback, { once: true });
    currentAudio.addEventListener("error", cleanUpPlayback, { once: true });

    await currentAudio.play();
  } catch (error) {
    if (error.name !== "AbortError") {
      console.error("Isabella Local Reader:", error);
    }
  } finally {
    activeRequestController = null;
  }
}

chrome.runtime.onMessage.addListener((message) => {
  if (message.target !== "offscreen") {
    return;
  }

  if (message.type === "ISABELLA_SPEAK") {
    speak(message.text);
  }

  if (message.type === "ISABELLA_STOP") {
    stopSpeech();
  }
});
