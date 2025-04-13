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

import torch
import bittensor as bt
import jiwer
from typing import List, Dict

from ocr_subnet.protocol import CaptionSynapse

def calculate_wer(reference: str, hypothesis: str) -> float:
    """
    Calculate Word Error Rate between reference and hypothesis
    
    Args:
        reference (str): Ground truth transcript
        hypothesis (str): Predicted transcript
        
    Returns:
        float: Word Error Rate (0.0 is perfect match)
    """
    try:
        return jiwer.wer(reference, hypothesis)
    except Exception as e:
        bt.logging.error(f"Error calculating WER: {e}")
        return 1.0  # Return worst score on error

def reward(
    self,
    ground_truth: str,
    response: CaptionSynapse
) -> float:
    """
    Calculate reward for a single response
    
    Args:
        ground_truth (str): The correct transcript
        response (CaptionSynapse): Response from miner
        
    Returns:
        float: Reward value between 0 and 1
    """
    if not hasattr(response, 'response') or not response.response:
        return 0.0
        
    transcript = response.response
    
    # Calculate Word Error Rate (WER)
    wer = calculate_wer(ground_truth, transcript)
    
    # Convert WER to a reward (lower WER = higher reward)
    # WER of 0 means perfect match, so reward should be 1
    # WER of 1 or higher means completely wrong, so reward should be 0
    transcription_reward = max(0, 1 - wer)
    
    # Calculate time reward (faster is better)
    alpha_prediction = 0.8  # Weight for transcription quality
    alpha_time = 0.2       # Weight for response time
    
    time_reward = max(1 - response.time_elapsed / self.config.neuron.timeout, 0)
    total_reward = (alpha_prediction * transcription_reward + alpha_time * time_reward) / (alpha_prediction + alpha_time)
    
    bt.logging.info(f"WER: {wer:.3f}, transcription_reward: {transcription_reward:.3f}, time_reward: {time_reward:.3f}, total_reward: {total_reward:.3f}")
    return total_reward

def get_rewards(
    self,
    ground_truth: str,
    responses: List[CaptionSynapse],
) -> torch.FloatTensor:
    """
    Returns a tensor of rewards for the given transcript and responses.
    
    Args:
        ground_truth (str): The correct transcript
        responses (List[CaptionSynapse]): A list of responses from miners
        
    Returns:
        torch.FloatTensor: A tensor of rewards for the given transcript and responses
    """
    # Get all the reward results by iteratively calling the reward() function
    return torch.FloatTensor(
        [reward(self, ground_truth, response) for response in responses]
    ).to(self.device)
