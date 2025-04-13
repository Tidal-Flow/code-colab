# The MIT License (MIT)
# Copyright © 2023 Yuma Rao

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.


import bittensor as bt
from typing import Optional, List, Dict

class CaptionSynapse(bt.Synapse):
    """
    A protocol for speech-to-text captioning tasks between validators and miners.
    
    Attributes:
    - base64_audio: Base64 encoding of audio data to be transcribed by the miner.
    - audio_metadata: Optional metadata about the audio (duration, sample rate, etc.)
    - job_id: Unique identifier for the transcription job
    - response: Transcription result from the miner
    """

    # Used by the validator for timing
    time_elapsed = 0

    # Required request input, filled by sending dendrite caller
    base64_audio: str
    audio_metadata: Optional[Dict] = None
    job_id: str

    # Optional request output, filled by receiving axon
    response: Optional[str] = None

    def deserialize(self) -> str:
        """
        Deserialize the miner response.

        Returns:
        - str: The transcribed text from the audio
        """
        return self.response
