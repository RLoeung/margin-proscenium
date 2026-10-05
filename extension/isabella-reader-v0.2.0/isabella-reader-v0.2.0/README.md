# Isabella Local Reader v0.2.0

This version removes page injection and response-channel messaging entirely.

The extension now:
1. receives selected text directly from Vivaldi's context-menu event;
2. creates a hidden offscreen extension document;
3. lets that document call the local Kokoro server;
4. plays the returned WAV from the hidden document.

This avoids HTTPS mixed-content restrictions and the service-worker message
channel closing while a large audio response is in flight.

## Update

1. Extract this archive over the existing extension folder.
2. Open `vivaldi://extensions`.
3. Click Reload on Isabella Local Reader.
4. Refresh the webpage.
5. Select text and choose **Read selection with Isabella**.

The Kokoro server must still be running at `http://127.0.0.1:5150`.
