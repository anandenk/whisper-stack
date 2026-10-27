"""Local fix for ElevenLabs 2.74.1's async microphone thread handoff."""

import asyncio

from elevenlabs.conversational_ai.default_audio_interface import AsyncDefaultAudioInterface


class MicrophoneAudioInterface(AsyncDefaultAudioInterface):
    async def start(self, input_callback):
        # PyAudio invokes _in_callback on its own thread, which has no asyncio
        # loop. Capture the conversation loop before opening the audio streams.
        self.input_loop = asyncio.get_running_loop()
        await super().start(input_callback)

    def _in_callback(self, in_data, frame_count, time_info, status):
        if self.input_callback and not self.input_loop.is_closed():
            asyncio.run_coroutine_threadsafe(self.input_callback(in_data), self.input_loop)
        return (None, self.pyaudio.paContinue)
