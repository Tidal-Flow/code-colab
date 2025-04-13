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

import os
import time
import hashlib
import bittensor as bt
import torch
import base64
import uuid
import psycopg2
import jiwer
from datasets import load_dataset
from pydub import AudioSegment
import io

import ocr_subnet as caption_subnet

# import base validator class which takes care of most of the boilerplate
from ocr_subnet.base.validator import BaseValidatorNeuron


class Validator(BaseValidatorNeuron):
    """
    Caption Subnet validator neuron class.
    
    This validator fetches audio data from datasets, distributes transcription jobs to miners,
    and evaluates the quality of their transcriptions.
    """

    def __init__(self, config=None):
        super(Validator, self).__init__(config=config)

        bt.logging.info("load_state()")
        self.load_state()

        # Create audio directory for storing samples
        self.audio_dir = './data/audio/'
        if not os.path.exists(self.audio_dir):
            os.makedirs(self.audio_dir)
            
        # Set up database connection
        self.setup_database()
        
        # Load dataset
        self.load_dataset()
        
    def setup_database(self):
        """Set up a database connection for job tracking"""
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
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    audio_segment BYTEA NOT NULL,
                    transcript_source TEXT NOT NULL,
                    transcript_submitted TEXT,
                    processed_flag BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.conn.commit()
            cursor.close()
        except Exception as e:
            bt.logging.error(f"Database connection failed: {e}")
            self.conn = None
            
    def load_dataset(self):
        """Load the VoxPopuli dataset for audio samples"""
        try:
            bt.logging.info("Loading VoxPopuli dataset...")
            self.dataset = load_dataset("facebook/voxpopuli", "en", split="train", streaming=True)
            bt.logging.info("Dataset loaded successfully")
        except Exception as e:
            bt.logging.error(f"Failed to load dataset: {e}")
            self.dataset = None

    async def forward(self):
        """
        The forward function is called by the validator every time step.
        
        It consists of 3 important steps:
        - Generate a challenge for the miners (fetch audio from dataset)
        - Query the miners with the challenge
        - Score the responses from the miners
        """
        # Get random UIDs to query
        miner_uids = caption_subnet.utils.uids.get_random_uids(
            self, 
            k=min(self.config.neuron.sample_size, self.metagraph.n.item())
        )
        
        if not miner_uids or len(miner_uids) == 0:
            bt.logging.warning("No miners available to query")
            return
            
        # Get an audio sample from the dataset
        audio_data = self.get_audio_sample()
        if not audio_data:
            bt.logging.error("Failed to get audio sample")
            return
            
        # Create a unique job ID
        job_id = str(uuid.uuid4())
        
        # Store the job in the database
        if self.conn:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO jobs (job_id, audio_segment, transcript_source, processed_flag) VALUES (%s, %s, %s, FALSE)",
                (job_id, audio_data['audio_bytes'], audio_data['transcript'])
            )
            self.conn.commit()
            cursor.close()
        
        # Create synapse object to send to miners
        synapse = caption_subnet.protocol.CaptionSynapse(
            base64_audio=audio_data['base64_audio'],
            audio_metadata=audio_data['metadata'],
            job_id=job_id
        )
        
        # Query the miners
        bt.logging.info(f"Querying {len(miner_uids)} miners with job {job_id}")
        responses = self.dendrite.query(
            # Send the query to selected miner axons in the network
            axons=[self.metagraph.axons[uid] for uid in miner_uids],
            # Pass the synapse to the miners
            synapse=synapse,
            # Do not deserialize the response so we have access to the raw response
            deserialize=False,
        )
        
        # Log the results for monitoring
        bt.logging.info(f"Received {len(responses)} responses")
        
        # Calculate rewards based on transcription quality
        rewards = self.calculate_rewards(
            ground_truth=audio_data['transcript'],
            responses=responses
        )
        
        bt.logging.info(f"Scored responses: {rewards}")
        
        # Update the scores based on the rewards
        self.update_scores(rewards, miner_uids)
        
        # Store the submitted transcripts in the database
        if self.conn:
            cursor = self.conn.cursor()
            for i, response in enumerate(responses):
                if hasattr(response, 'response') and response.response:
                    cursor.execute(
                        "UPDATE jobs SET transcript_submitted = %s, processed_flag = TRUE WHERE job_id = %s",
                        (response.response, job_id)
                    )
            self.conn.commit()
            cursor.close()
    
    def get_audio_sample(self):
        """
        Fetch an audio sample from the dataset
        
        Returns:
            dict: Dictionary containing audio data and transcript
        """
        if not self.dataset:
            return None
            
        try:
            # Get a random sample from the dataset
            sample = next(iter(self.dataset.take(1)))
            
            # Extract audio and transcript
            audio_array = sample['audio']['array']
            sample_rate = sample['audio']['sampling_rate']
            transcript = sample['normalized_text']
            
            # Convert to audio file format
            audio = AudioSegment(
                audio_array.tobytes(),
                frame_rate=sample_rate,
                sample_width=2,  # 16-bit
                channels=1       # Mono
            )
            
            # Save to file temporarily
            filename = hashlib.md5(str(time.time()).encode()).hexdigest()
            file_path = os.path.join(self.audio_dir, f"{filename}.mp3")
            audio.export(file_path, format="mp3")
            
            # Read the file and encode to base64
            with open(file_path, "rb") as audio_file:
                audio_bytes = audio_file.read()
                base64_audio = base64.b64encode(audio_bytes).decode('utf-8')
            
            # Create metadata
            metadata = {
                'duration': len(audio) / 1000,  # in seconds
                'sample_rate': sample_rate,
                'channels': 1,
                'format': 'mp3'
            }
            
            return {
                'base64_audio': base64_audio,
                'audio_bytes': audio_bytes,
                'transcript': transcript,
                'metadata': metadata,
                'file_path': file_path
            }
            
        except Exception as e:
            bt.logging.error(f"Error getting audio sample: {e}")
            return None
    
    def calculate_rewards(self, ground_truth, responses):
        """
        Calculate rewards based on the quality of transcriptions
        
        Args:
            ground_truth (str): The correct transcript
            responses (List[CaptionSynapse]): Responses from miners
            
        Returns:
            torch.FloatTensor: Tensor of rewards
        """
        rewards = []
        
        for response in responses:
            if not hasattr(response, 'response') or not response.response:
                rewards.append(0.0)
                continue
                
            transcript = response.response
            
            # Calculate Word Error Rate (WER)
            try:
                wer = jiwer.wer(ground_truth, transcript)
                
                # Convert WER to a reward (lower WER = higher reward)
                # WER of 0 means perfect match, so reward should be 1
                # WER of 1 or higher means completely wrong, so reward should be 0
                transcription_reward = max(0, 1 - wer)
                
                # Calculate time reward (faster is better)
                time_reward = max(0, 1 - response.time_elapsed / self.config.neuron.timeout)
                
                # Combine rewards (80% accuracy, 20% speed)
                total_reward = 0.8 * transcription_reward + 0.2 * time_reward
                
                rewards.append(float(total_reward))
                
            except Exception as e:
                bt.logging.error(f"Error calculating reward: {e}")
                rewards.append(0.0)
        
        return torch.FloatTensor(rewards).to(self.device)

# The main function parses the configuration and runs the validator.
if __name__ == "__main__":
    with Validator() as validator:
        while True:
            bt.logging.info("Validator running...", time.time())
            time.sleep(5)
