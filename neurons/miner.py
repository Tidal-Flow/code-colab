# The MIT License (MIT)
# Copyright © 2023 Yuma Rao

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import time
import typing
import bittensor as bt
import whisper
import base64
import io
import torch
import psycopg2
from pydub import AudioSegment

# Bittensor Caption Subnet
import ocr_subnet as caption_subnet

# Import base miner class which takes care of most of the boilerplate
from ocr_subnet.base.miner import BaseMinerNeuron

class Miner(BaseMinerNeuron):
    """
    Caption Subnet Miner implementation that transcribes audio using Whisper.
    """

    def __init__(self, config=None):
        super(Miner, self).__init__(config=config)
        
        # Initialize Whisper model
        bt.logging.info("Loading Whisper model...")
        self.model = whisper.load_model("base")
        bt.logging.info(f"Whisper model loaded: {self.model.device}")
        
        # Initialize database connection for job tracking (optional)
        self.setup_database()
        
    def setup_database(self):
        """Set up a local database to track processed jobs"""
        try:
            self.conn = psycopg2.connect(
                dbname=self.config.database.name,
                user=self.config.database.user,
                password=self.config.database.password,
                host=self.config.database.host,
                port=self.config.database.port
            )
            bt.logging.info("Database connection established")
            
            # Create jobs table if it doesn't exist
            cursor = self.conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS processed_jobs (
                    job_id TEXT PRIMARY KEY,
                    transcript TEXT NOT NULL,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.conn.commit()
            cursor.close()
        except Exception as e:
            bt.logging.error(f"Database connection failed: {e}")
            self.conn = None

    async def forward(
        self, synapse: caption_subnet.protocol.CaptionSynapse
    ) -> caption_subnet.protocol.CaptionSynapse:
        """
        Processes the incoming audio synapse and attaches the transcription to the synapse.

        Args:
            synapse (caption_subnet.protocol.CaptionSynapse): The synapse object containing the audio data.

        Returns:
            caption_subnet.protocol.CaptionSynapse: The synapse object with the 'response' field set to the transcription.
        """
        bt.logging.info(f"Received transcription job: {synapse.job_id}")
        
        try:
            # Check if we've already processed this job
            if self.conn:
                cursor = self.conn.cursor()
                cursor.execute("SELECT transcript FROM processed_jobs WHERE job_id = %s", (synapse.job_id,))
                result = cursor.fetchone()
                cursor.close()
                
                if result:
                    bt.logging.info(f"Job {synapse.job_id} already processed, returning cached result")
                    synapse.response = result[0]
                    return synapse
            
            # Decode base64 audio
            audio_bytes = base64.b64decode(synapse.base64_audio)
            
            # Convert to format Whisper can process
            audio = AudioSegment.from_file(io.BytesIO(audio_bytes))
            audio.export("temp_audio.mp3", format="mp3")
            
            # Transcribe with Whisper
            bt.logging.info("Transcribing audio...")
            result = self.model.transcribe("temp_audio.mp3")
            transcript = result["text"].strip()
            
            # Store result in database
            if self.conn:
                cursor = self.conn.cursor()
                cursor.execute(
                    "INSERT INTO processed_jobs (job_id, transcript) VALUES (%s, %s) ON CONFLICT (job_id) DO UPDATE SET transcript = %s",
                    (synapse.job_id, transcript, transcript)
                )
                self.conn.commit()
                cursor.close()
            
            # Attach response to synapse
            synapse.response = transcript
            bt.logging.info(f"Transcription complete: {transcript[:50]}...")
            
            return synapse
            
        except Exception as e:
            bt.logging.error(f"Error in transcription: {e}")
            synapse.response = "ERROR: Transcription failed"
            return synapse

    async def blacklist(
        self, synapse: caption_subnet.protocol.CaptionSynapse
    ) -> typing.Tuple[bool, str]:
        """
        Determines whether an incoming request should be blacklisted and thus ignored.
        
        Args:
            synapse (caption_subnet.protocol.CaptionSynapse): A synapse object constructed from the headers of the incoming request.
            
        Returns:
            Tuple[bool, str]: A tuple containing a boolean indicating whether the synapse's hotkey is blacklisted,
                            and a string providing the reason for the decision.
        """
        if synapse.dendrite.hotkey not in self.metagraph.hotkeys:
            # Ignore requests from unrecognized entities.
            bt.logging.trace(
                f"Blacklisting unrecognized hotkey {synapse.dendrite.hotkey}"
            )
            return True, "Unrecognized hotkey"

        bt.logging.trace(
            f"Not Blacklisting recognized hotkey {synapse.dendrite.hotkey}"
        )
        return False, "Hotkey recognized!"

    async def priority(self, synapse: caption_subnet.protocol.CaptionSynapse) -> float:
        """
        Determines the priority of the request based on the caller's stake.
        
        Args:
            synapse (caption_subnet.protocol.CaptionSynapse): The synapse object that contains metadata about the incoming request.
            
        Returns:
            float: A priority score derived from the stake of the calling entity.
        """
        caller_uid = self.metagraph.hotkeys.index(
            synapse.dendrite.hotkey
        )  # Get the caller index.
        priority = float(
            self.metagraph.S[caller_uid]
        )  # Return the stake as the priority.
        bt.logging.trace(
            f"Prioritizing {synapse.dendrite.hotkey} with value: {priority}"
        )
        return priority

# This is the main function, which runs the miner.
if __name__ == "__main__":
    with Miner() as miner:
        while True:
            bt.logging.info("Miner running...", time.time())
            time.sleep(5)
