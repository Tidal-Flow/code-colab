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
import uuid
import json
import random
import hashlib
import tempfile
import asyncio
import subprocess
import numpy as np
import bittensor as bt
import torch
import psutil
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple, Optional, Union

# Import the code collaboration subnet module
import code_colab as code_subnet
from code_colab.utils.storage import JSONStorage
from code_colab.validator.reward import GitPerformanceMetrics

# import base validator class which takes care of most of the boilerplate
from code_colab.base.validator import BaseValidatorNeuron


class Validator(BaseValidatorNeuron):
    """
    Code Collaboration Subnet validator neuron class.
    
    This validator acts as a Git client, issuing code challenges to miners,
    cloning repositories, validating code submissions, and scoring miners
    based on code quality, test success, and other metrics.
    """

    def __init__(self, config=None):
        super(Validator, self).__init__(config=config)

        bt.logging.info("load_state()")
        self.load_state()

        # Create repository directory for storing cloned repositories
        self.repo_dir = os.path.expanduser(self.config.validator.repo_directory)
        os.makedirs(self.repo_dir, exist_ok=True)
        bt.logging.info(f"Repository directory initialized at: {self.repo_dir}")
            
        # Set up JSON storage instead of database
        self.setup_storage()
        
        # Initialize metrics collector
        self.metrics = GitPerformanceMetrics(
            metrics_dir=os.path.join(self.repo_dir, "metrics")
        )
        
        # Verify Git is installed and working
        try:
            result = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True)
            bt.logging.info(f"Git version: {result.stdout.strip()}")
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            bt.logging.error(f"Git installation error: {e}. Please ensure Git is installed.")
            raise RuntimeError("Git must be installed to run the Code Collaboration Validator")
            
        # Initialize challenge repository list
        self.challenge_repos = self.config.validator.challenge_repos.split(',')
        bt.logging.info(f"Challenge repositories: {self.challenge_repos}")
        
        # Ensure we have at least one repository for challenges
        if not self.challenge_repos or len(self.challenge_repos) == 0:
            bt.logging.warning("No challenge repositories specified - validator may not function correctly")
        
    def setup_storage(self):
        """Set up JSON storage for tracking challenges and submissions"""
        try:
            # Create storage directory
            storage_dir = os.path.join(self.repo_dir, "storage")
            self.storage = JSONStorage(storage_dir)
            bt.logging.info(f"JSON storage initialized at: {storage_dir}")
        except Exception as e:
            bt.logging.error(f"Storage initialization failed: {e}")
            self.storage = None

    async def forward(self):
        """
        The forward function is called by the validator every time step.
        
        It consists of 3 main operations:
        1. Test miners for Git operations performance
        2. Generate code challenges for miners
        3. Score miners based on their Git performance and challenge responses
        """
        try:
            # Decide which operation to perform based on step count
            operation = self.step % 3
            
            if operation == 0:
                # Test miners' Git operations
                await self._test_git_operations()
            elif operation == 1:
                # Generate and issue code challenges to miners
                await self._issue_code_challenges()
            else:
                # Update scores based on performance metrics
                await self._update_miner_scores()
                
        except Exception as e:
            bt.logging.error(f"Error in forward: {e}")
    
    async def _test_git_operations(self):
        """Test miners' Git server performance with clone/fetch/push operations"""
        bt.logging.info("Testing miners' Git operations")
        
        # Select random miners to test
        miner_uids = code_subnet.utils.uids.get_random_uids(
            self, 
            k=min(self.config.neuron.sample_size, self.metagraph.n.item())
        )
        
        if not miner_uids or len(miner_uids) == 0:
            bt.logging.warning("No miners available to test")
            return
            
        # Select a testing operation
        operation_type = random.choice(["clone", "fetch", "push"])
        bt.logging.info(f"Testing {operation_type} operation on {len(miner_uids)} miners")
        
        if operation_type == "clone":
            await self._test_clone_operation(miner_uids)
        elif operation_type == "fetch":
            await self._test_fetch_operation(miner_uids)
        elif operation_type == "push":
            await self._test_push_operation(miner_uids)
    
    async def _test_clone_operation(self, miner_uids: List[int]):
        """Test miners' Git clone performance"""
        # Generate test repository parameters
        repo_name = f"test-repo-{uuid.uuid4().hex[:8]}"
        
        # Prepare clone synapse
        synapse = code_subnet.protocol.GitCloneSynapse(
            repo_url=repo_name,
            validator_hotkey=self.wallet.hotkey.ss58_address,
            ref=None
        )
        
        # Query miners
        bt.logging.info(f"Sending clone request for {repo_name} to {len(miner_uids)} miners")
        start_time = time.time()
        
        responses = await self.dendrite.query(
            axons=[self.metagraph.axons[uid] for uid in miner_uids],
            synapse=synapse,
            deserialize=True,
        )
        
        # Process responses and score miners
        scores = []
        for i, response in enumerate(responses):
            if not response or not isinstance(response, dict) or "status" not in response:
                bt.logging.warning(f"Invalid response from miner {miner_uids[i]}")
                scores.append(0.0)
                continue
                
            if response["status"] != "success":
                bt.logging.info(f"Miner {miner_uids[i]} failed clone operation: {response.get('error', 'unknown error')}")
                scores.append(0.0)
                continue
                
            # Check if metrics are included
            metrics = getattr(synapse, "metrics", None)
            if metrics:
                # Score based on metrics
                operation_score = self.metrics.score_operation(metrics)
                scores.append(operation_score)
                bt.logging.info(f"Miner {miner_uids[i]} clone operation score: {operation_score:.4f}")
            else:
                # Score based on response time if no metrics
                elapsed = time.time() - start_time
                time_score = max(0.0, 1.0 - (elapsed / 30.0))  # 30 second timeout for ideal score
                scores.append(time_score)
                bt.logging.info(f"Miner {miner_uids[i]} clone time score: {time_score:.4f}")
        
        # Update scores in metagraph
        for i, score in enumerate(scores):
            if i < len(miner_uids):
                uid = miner_uids[i]
                self.scores[uid] = 0.9 * self.scores[uid] + 0.1 * score
    
    async def _test_fetch_operation(self, miner_uids: List[int]):
        """Test miners' Git fetch performance"""
        # First ensure a repository exists to fetch from
        repo_name = f"test-repo-{uuid.uuid4().hex[:8]}"
        
        # Create repository on miners first
        await self._test_clone_operation(miner_uids)
        
        # Prepare fetch synapse
        synapse = code_subnet.protocol.GitFetchSynapse(
            repo_url=repo_name,
            validator_hotkey=self.wallet.hotkey.ss58_address,
            ref=None
        )
        
        # Query miners
        bt.logging.info(f"Sending fetch request for {repo_name} to {len(miner_uids)} miners")
        start_time = time.time()
        
        responses = await self.dendrite.query(
            axons=[self.metagraph.axons[uid] for uid in miner_uids],
            synapse=synapse,
            deserialize=True,
        )
        
        # Process responses and score miners
        scores = []
        for i, response in enumerate(responses):
            if not response or not isinstance(response, dict) or "status" not in response:
                bt.logging.warning(f"Invalid response from miner {miner_uids[i]}")
                scores.append(0.0)
                continue
                
            if response["status"] != "success":
                bt.logging.info(f"Miner {miner_uids[i]} failed fetch operation: {response.get('error', 'unknown error')}")
                scores.append(0.0)
                continue
                
            # Check if metrics are included
            metrics = getattr(synapse, "metrics", None)
            if metrics:
                # Score based on metrics
                operation_score = self.metrics.score_operation(metrics)
                scores.append(operation_score)
                bt.logging.info(f"Miner {miner_uids[i]} fetch operation score: {operation_score:.4f}")
            else:
                # Score based on response time if no metrics
                elapsed = time.time() - start_time
                time_score = max(0.0, 1.0 - (elapsed / 10.0))  # 10 second timeout for ideal score (fetch should be faster than clone)
                scores.append(time_score)
                bt.logging.info(f"Miner {miner_uids[i]} fetch time score: {time_score:.4f}")
        
        # Update scores in metagraph
        for i, score in enumerate(scores):
            if i < len(miner_uids):
                uid = miner_uids[i]
                self.scores[uid] = 0.9 * self.scores[uid] + 0.1 * score
    
    async def _test_push_operation(self, miner_uids: List[int]):
        """Test miners' Git push performance"""
        # First ensure a repository exists to push to
        repo_name = f"test-repo-{uuid.uuid4().hex[:8]}"
        
        # Create repository on miners first
        await self._test_clone_operation(miner_uids)
        
        # Prepare push data - a small test file
        commit_data = {
            "message": f"Test commit {uuid.uuid4().hex[:8]}",
            "files": {
                "README.md": f"# Test Repository\n\nThis is a test repository created on {datetime.now().isoformat()}.\n",
                "test.txt": f"This is a test file generated for push testing.\nRandom content: {uuid.uuid4().hex}\n"
            }
        }
        
        # Prepare push synapse
        synapse = code_subnet.protocol.GitPushSynapse(
            repo_url=repo_name,
            validator_hotkey=self.wallet.hotkey.ss58_address,
            branch="main",
            commit_data=commit_data
        )
        
        # Query miners
        bt.logging.info(f"Sending push request for {repo_name} to {len(miner_uids)} miners")
        start_time = time.time()
        
        responses = await self.dendrite.query(
            axons=[self.metagraph.axons[uid] for uid in miner_uids],
            synapse=synapse,
            deserialize=True,
        )
        
        # Process responses and score miners
        scores = []
        for i, response in enumerate(responses):
            if not response or not isinstance(response, dict) or "status" not in response:
                bt.logging.warning(f"Invalid response from miner {miner_uids[i]}")
                scores.append(0.0)
                continue
                
            if response["status"] != "success":
                bt.logging.info(f"Miner {miner_uids[i]} failed push operation: {response.get('error', 'unknown error')}")
                scores.append(0.0)
                continue
                
            # Verify commit hash is returned
            if "commit_hash" not in response:
                bt.logging.warning(f"Miner {miner_uids[i]} did not return commit hash")
                scores.append(0.5)  # Partial credit - operation worked but verification incomplete
                continue
                
            # Check if metrics are included
            metrics = getattr(synapse, "metrics", None)
            if metrics:
                # Score based on metrics
                operation_score = self.metrics.score_operation(metrics)
                scores.append(operation_score)
                bt.logging.info(f"Miner {miner_uids[i]} push operation score: {operation_score:.4f}")
            else:
                # Score based on response time if no metrics
                elapsed = time.time() - start_time
                time_score = max(0.0, 1.0 - (elapsed / 15.0))  # 15 second timeout for ideal score
                scores.append(time_score)
                bt.logging.info(f"Miner {miner_uids[i]} push time score: {time_score:.4f}")
        
        # Update scores in metagraph
        for i, score in enumerate(scores):
            if i < len(miner_uids):
                uid = miner_uids[i]
                self.scores[uid] = 0.9 * self.scores[uid] + 0.1 * score
    
    async def _issue_code_challenges(self):
        """Generate and send code challenges to miners"""
        bt.logging.info("Issuing code challenges to miners")
        
        # Select a subset of miners to challenge
        miner_uids = code_subnet.utils.uids.get_random_uids(
            self, 
            k=min(self.config.neuron.sample_size, self.metagraph.n.item())
        )
        
        if not miner_uids or len(miner_uids) == 0:
            bt.logging.warning("No miners available to challenge")
            return
            
        # Select a repository to use for the challenge
        repo_url = random.choice(self.challenge_repos)
        
        # Create a unique challenge ID
        challenge_id = str(uuid.uuid4())
        
        # Choose a base branch for the challenge
        base_branch = "main"  # Default to main
        
        # Generate a challenge based on the repository
        challenge_types = ["feature", "bugfix", "refactor", "test", "documentation"]
        challenge_type = random.choice(challenge_types)
        
        challenge_description = self._generate_challenge_description(repo_url, challenge_type)
        
        # Create synapse with challenge details
        synapse = code_subnet.protocol.GitChallengeSynapse(
            repo_url=repo_url,
            validator_hotkey=self.wallet.hotkey.ss58_address,
            challenge_id=challenge_id,
            challenge_type=challenge_type,
            description=challenge_description,
            base_branch=base_branch
        )
        
        # Send challenge to miners
        bt.logging.info(f"Sending challenge {challenge_id} to {len(miner_uids)} miners")
        responses = await self.dendrite.query(
            axons=[self.metagraph.axons[uid] for uid in miner_uids],
            synapse=synapse,
            deserialize=True,
        )
        
        # Store challenge in JSON storage
        if self.storage:
            self.storage.store_object(
                object_type="challenge",
                object_id=challenge_id,
                data={
                    "id": challenge_id,
                    "repo_url": repo_url,
                    "challenge_type": challenge_type,
                    "description": challenge_description,
                    "base_branch": base_branch,
                    "status": "active",
                    "created_at": datetime.now().isoformat(),
                    "miners": miner_uids
                }
            )
        
        # Log responses
        for i, response in enumerate(responses):
            if not response or not isinstance(response, dict):
                bt.logging.warning(f"Invalid response from miner {miner_uids[i]}")
                continue
                
            if response.get("status") == "accepted":
                bt.logging.info(f"Miner {miner_uids[i]} accepted challenge with solution branch: {response.get('solution_branch')}")
                
                # Record acceptance in JSON storage
                if self.storage:
                    self.storage.store_object(
                        object_type="submission",
                        object_id=f"{challenge_id}_{miner_uids[i]}",
                        data={
                            "challenge_id": challenge_id,
                            "miner_uid": miner_uids[i].item(),
                            "miner_hotkey": self.metagraph.hotkeys[miner_uids[i]],
                            "status": "accepted",
                            "solution_branch": response.get("solution_branch"),
                            "submitted_at": datetime.now().isoformat()
                        }
                    )
            else:
                bt.logging.warning(f"Miner {miner_uids[i]} rejected or failed challenge: {response.get('error', 'unknown error')}")
    
    async def _update_miner_scores(self):
        """Update miner scores based on Git performance metrics"""
        bt.logging.info("Updating miner scores based on Git performance")
        
        # Get all miners to collect metrics from
        miner_uids = list(range(self.metagraph.n.item()))
        
        # Select a subset for performance check
        test_uids = random.sample(
            miner_uids, 
            min(self.config.neuron.sample_size, len(miner_uids))
        )
        
        # Collect performance metrics
        scores = {}
        for uid in test_uids:
            axon = self.metagraph.axons[uid]
            hotkey = self.metagraph.hotkeys[uid]
            
            # Create metrics collection synapse
            synapse = code_subnet.protocol.PerformanceMetricsSynapse(
                validator_hotkey=self.wallet.hotkey.ss58_address,
                metric_type="git_operations",
                timeframe="24h"  # Last 24 hours
            )
            
            # Query miner for metrics
            bt.logging.info(f"Requesting performance metrics from miner {uid}")
            response = await self.dendrite.query(
                axons=[axon],
                synapse=synapse,
                deserialize=True,
            )
            
            # Process response
            if not response or not isinstance(response, dict) or response.get("status") != "success":
                bt.logging.warning(f"Invalid or error response from miner {uid}")
                scores[uid] = 0.2  # Minimum score for not providing metrics
                continue
                
            # Extract scores
            operation_scores = response.get("scores", {})
            overall_score = operation_scores.get("overall", 0.0)
            
            # Calculate combined score using weights from config
            if "scores" in response and all(key in operation_scores for key in ["clone", "fetch", "push"]):
                # Apply the weights from config
                weights = self.config.validator.scoring_weights
                
                weighted_score = (
                    weights.availability * (1.0 if response.get("operation_count", 0) > 0 else 0.0) +
                    weights.latency * operation_scores.get("overall", 0.0) +
                    weights.throughput * operation_scores.get("overall", 0.0) +
                    weights.integrity * 1.0  # Assume integrity is good if operations succeeded
                )
                
                # Normalize score
                total_weight = sum(weights.values()) - weights.security  # Security not measured here
                if total_weight > 0:
                    weighted_score = weighted_score / total_weight
                    
                scores[uid] = max(0.2, weighted_score)  # Minimum score of 0.2 for active miners
            else:
                # Use overall score if detailed scores not available
                scores[uid] = max(0.2, overall_score)
                
            bt.logging.info(f"Miner {uid} performance score: {scores[uid]:.4f}")
        
        # Update weights in metagraph
        for uid, score in scores.items():
            # Update with exponential moving average
            alpha = 0.2  # Weight of the new score
            self.scores[uid] = (1 - alpha) * self.scores[uid] + alpha * score
        
        # Set weights on the network
        bt.logging.info(f"Setting weights based on performance scores")
        self.set_weights()
    
    def _generate_challenge_description(self, repo_url: str, challenge_type: str) -> str:
        """
        Generate a description for a code challenge
        
        This is simplified - in a real implementation, you might analyze the repository
        to generate meaningful challenges based on actual code.
        """
        # Extract repository name from URL
        repo_name = repo_url.split("/")[-1]
        
        # Base description template
        description = f"# Code Challenge: {challenge_type.capitalize()} for {repo_name}\n\n"
        
        # General challenge instructions
        description += f"You are tasked with implementing a {challenge_type} for the {repo_name} repository.\n\n"
        
        # Random challenge features based on type
        if challenge_type == "feature":
            features = [
                "Add a caching layer to improve performance",
                "Implement pagination for API endpoints",
                "Add dark mode support to the UI",
                "Create a new visualization component",
                "Implement user authentication and authorization"
            ]
            description += f"Feature request: {random.choice(features)}\n"
        
        elif challenge_type == "bugfix":
            bugs = [
                "Fix memory leak in the data processing module",
                "Resolve race condition in concurrent operations",
                "Fix incorrect error handling in API endpoints",
                "Resolve UI rendering issues in mobile view",
                "Fix data corruption issue in the persistence layer"
            ]
            description += f"Bug description: {random.choice(bugs)}\n"
        
        elif challenge_type == "refactor":
            refactors = [
                "Improve code organization and modularity",
                "Reduce code duplication across components",
                "Improve type safety and error handling",
                "Optimize performance bottlenecks",
                "Improve readability and maintainability"
            ]
            description += f"Refactoring goal: {random.choice(refactors)}\n"
        
        elif challenge_type == "test":
            tests = [
                "Increase test coverage for core modules",
                "Add integration tests for API endpoints",
                "Implement performance benchmarks",
                "Add UI component tests",
                "Create end-to-end test suite"
            ]
            description += f"Testing objective: {random.choice(tests)}\n"
        
        elif challenge_type == "documentation":
            docs = [
                "Create comprehensive API documentation",
                "Write developer setup guide",
                "Document architecture and design decisions",
                "Create user guides with examples",
                "Document configuration options and best practices"
            ]
            description += f"Documentation task: {random.choice(docs)}\n"
        
        # Add more specific details based on challenge type
        if challenge_type == "feature":
            description += "\n\nRequirements:\n- The feature should be well-tested\n- Include error handling\n- Update documentation"
        elif challenge_type == "bugfix":
            description += "\n\nSteps to reproduce:\n1. Open the file\n2. Try to perform the action\n3. Observe the error\n\nFix the issue and add tests to prevent regression."
        elif challenge_type == "refactor":
            description += "\n\nGoals:\n- Improve code readability\n- Reduce complexity\n- Maintain existing functionality\n- Add tests if missing"
        elif challenge_type == "test":
            description += "\n\nTest requirements:\n- Cover edge cases\n- Aim for at least 80% code coverage\n- Include both unit and integration tests where appropriate"
        elif challenge_type == "documentation":
            description += "\n\nDocumentation should include:\n- API reference\n- Usage examples\n- Configuration options\n- Common troubleshooting steps"
            
        return description

# This is the main function, which runs the validator.
if __name__ == "__main__":
    with Validator() as validator:
        while True:
            bt.logging.info("Validator running...", time.time())
            time.sleep(5)
