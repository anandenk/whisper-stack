"""Synthetic microphone-thread regression checks: no hardware/provider access."""

import asyncio
import threading
import unittest
from unittest.mock import patch

from audio import MicrophoneAudioInterface
from elevenlabs.conversational_ai.default_audio_interface import AsyncDefaultAudioInterface


class MicrophoneHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_thread_delivery_preserves_pcm_and_uses_conversation_loop(self):
        audio = MicrophoneAudioInterface()
        expected_loop = asyncio.get_running_loop()
        delivered = asyncio.Event()
        received = []
        pcm = b"\x01\x00\xff\x7f" * 2000

        async def consume(data):
            received.append((data, asyncio.get_running_loop()))
            delivered.set()

        async def fake_start(instance, callback):
            instance.input_callback = callback

        with patch.object(AsyncDefaultAudioInterface, "start", fake_start):
            await audio.start(consume)

        results = []
        worker = threading.Thread(target=lambda: results.append(audio._in_callback(pcm, 4000, {}, 0)))
        worker.start()
        await asyncio.to_thread(worker.join)
        await asyncio.wait_for(delivered.wait(), timeout=1)
        self.assertEqual(received, [(pcm, expected_loop)])
        self.assertEqual(results, [(None, audio.pyaudio.paContinue)])

    async def test_original_sdk_drops_the_same_thread_input(self):
        audio = AsyncDefaultAudioInterface()
        received = []

        async def consume(data):
            received.append(data)

        audio.input_callback = consume
        worker = threading.Thread(target=lambda: audio._in_callback(b"\x00\x00", 1, {}, 0))
        worker.start()
        await asyncio.to_thread(worker.join)
        await asyncio.sleep(0)
        self.assertEqual(received, [])


if __name__ == "__main__":
    unittest.main()
