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
import psycopg2
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple, Optional, Union

# Import the code collaboration subnet module
import ocr_subnet as code_subnet

# import base validator class which takes care of most of the boilerplate
from ocr_subnet.base.validator import BaseValidatorNeuron


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
            
        # Set up database connection
        self.setup_database()
        
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
            
    def setup_database(self):
        """Set up a database connection for tracking challenges and submissions"""
        try:
            self.conn = psycopg2.connect(
                dbname=self.config.database.name,
                user=self.config.database.user,
                password=self.config.database.password,
                host=self.config.database.host,
                port=self.config.database.port
            )
            bt.logging.info("Database connection established")
            
            # Create challenges table
            cursor = self.conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS challenges (
                    challenge_id TEXT PRIMARY KEY,
                    repo_url TEXT NOT NULL,
                    challenge_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    base_branch TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expiry_at TIMESTAMP
                )
            """)
            
            # Create submissions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS submissions (
                    submission_id TEXT PRIMARY KEY,
                    challenge_id TEXT NOT NULL,
                    hotkey TEXT NOT NULL,
                    solution_branch TEXT NOT NULL,
                    score FLOAT,
                    test_results JSONB,
                    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    evaluated_at TIMESTAMP,
                    FOREIGN KEY (challenge_id) REFERENCES challenges(challenge_id)
                )
            """)
            
            self.conn.commit()
            cursor.close()
        except Exception as e:
            bt.logging.error(f"Database connection failed: {e}")
            self.conn = None

    async def forward(self):
        """
        The forward function is called by the validator every time step.
        
        It consists of 3 main operations:
        1. Generate code challenges for miners
        2. Evaluate existing solutions
        3. Score miners based on their submissions
        """
        try:
            # Decide which operation to perform based on step count
            operation = self.step % 3
            
            if operation == 0:
                # Generate and issue new code challenges to miners
                await self._issue_code_challenges()
            elif operation == 1:
                # Evaluate solutions that have been submitted but not yet scored
                await self._evaluate_solutions()
            else:
                # Update scores based on recent evaluations
                await self._update_miner_scores()
                
        except Exception as e:
            bt.logging.error(f"Error in forward: {e}")
    
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
        
        # Check if we have this repo locally, if not clone it
        repo_path = self._get_repo_path(repo_url)
        if not repo_path:
            repo_path = self._clone_repository(repo_url)
            
        # Get repo info including available branches
        repo_info = self._get_repo_info(repo_path)
        
        # Choose a base branch for the challenge
        base_branch = "main"  # Default to main
        for branch in repo_info["branches"]:
            if branch.strip() == "* main" or branch.strip() == "* master":
                base_branch = branch.replace("* ", "").strip()
                break
        
        # Generate a challenge based on the repository
        challenge_types = ["feature", "bugfix", "refactor", "test", "documentation"]
        challenge_type = random.choice(challenge_types)
        
        challenge_description = self._generate_challenge_description(repo_path, challenge_type)
        
        # Store challenge in database
        if self.conn:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO challenges 
                (challenge_id, repo_url, challenge_type, description, base_branch, expiry_at) 
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    challenge_id, 
                    repo_url, 
                    challenge_type, 
                    challenge_description, 
                    base_branch,
                    datetime.now() + timedelta(hours=24)  # Expire in 24 hours
                )
            )
            self.conn.commit()
            cursor.close()
        
        # Create synapse with challenge details
        synapse = code_subnet.protocol.GitChallengeSynapse(
            repo_url=repo_url,
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
        
        # Log responses
        for i, response in enumerate(responses):
            if not response or not isinstance(response, dict):
                bt.logging.warning(f"Invalid response from miner {miner_uids[i]}")
                continue
                
            if response.get("status") == "accepted":
                bt.logging.info(f"Miner {miner_uids[i]} accepted challenge with solution branch: {response.get('solution_branch')}")
                
                # Record acceptance in database
                if self.conn:
                    cursor = self.conn.cursor()
                    cursor.execute(
                        """
                        INSERT INTO submissions
                        (submission_id, challenge_id, hotkey, solution_branch)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (
                            str(uuid.uuid4()),
                            challenge_id,
                            self.metagraph.hotkeys[miner_uids[i]],
                            response.get("solution_branch")
                        )
                    )
                    self.conn.commit()
                    cursor.close()
            else:
                bt.logging.warning(f"Miner {miner_uids[i]} did not accept challenge: {response}")
    
    async def _evaluate_solutions(self):
        """Evaluate solutions submitted by miners"""
        bt.logging.info("Evaluating miner solutions")
        
        # Check if we have any pending submissions to evaluate
        if not self.conn:
            bt.logging.warning("No database connection available")
            return
            
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT s.submission_id, s.challenge_id, s.hotkey, s.solution_branch, c.repo_url
            FROM submissions s
            JOIN challenges c ON s.challenge_id = c.challenge_id
            WHERE s.score IS NULL AND c.expiry_at > NOW()
            LIMIT %s
            """,
            (self.config.validator.batch_size,)
        )
        
        submissions = cursor.fetchall()
        cursor.close()
        
        if not submissions:
            bt.logging.info("No pending submissions to evaluate")
            return
            
        bt.logging.info(f"Found {len(submissions)} submissions to evaluate")
        
        # Process each submission
        for submission_id, challenge_id, hotkey, solution_branch, repo_url in submissions:
            bt.logging.info(f"Evaluating submission {submission_id} from {hotkey}")
            
            # Get UID for this hotkey
            try:
                uid = self.metagraph.hotkeys.index(hotkey)
            except ValueError:
                bt.logging.warning(f"Unknown hotkey {hotkey}")
                continue
                
            # Get miner's axon
            axon = self.metagraph.axons[uid]
            
            # Fetch the solution branch
            repo_path = self._get_repo_path(repo_url)
            if not repo_path:
                repo_path = self._clone_repository(repo_url)
                
            # Create validation synapse
            synapse = code_subnet.protocol.GitValidationSynapse(
                repo_url=repo_url,
                solution_branch=solution_branch,
                challenge_id=challenge_id,
                validation_type="all"  # Run all validation types (tests, lint, etc.)
            )
            
            # Query the miner to get the solution
            bt.logging.info(f"Fetching solution from miner {uid} for branch {solution_branch}")
            response = await self.dendrite.query(
                axons=[axon],
                synapse=synapse,
                deserialize=True,
                timeout=30  # Increase timeout for validation operations
            )
            
            if not response or len(response) == 0 or not isinstance(response[0], dict):
                bt.logging.warning(f"Invalid validation response from miner {uid}")
                continue
                
            validation_result = response[0]
            
            # Calculate score based on validation results
            score = self._calculate_solution_score(validation_result)
            
            # Update submission with score and test results
            cursor = self.conn.cursor()
            cursor.execute(
                """
                UPDATE submissions
                SET score = %s, test_results = %s, evaluated_at = NOW()
                WHERE submission_id = %s
                """,
                (score, json.dumps(validation_result), submission_id)
            )
            self.conn.commit()
            cursor.close()
            
            bt.logging.info(f"Scored submission {submission_id} with score {score}")
    
    async def _update_miner_scores(self):
        """Update miner scores based on their submissions"""
        bt.logging.info("Updating miner scores")
        
        if not self.conn:
            bt.logging.warning("No database connection available")
            return
            
        # Get average scores for each miner from evaluated submissions
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT hotkey, AVG(score) as avg_score
            FROM submissions
            WHERE evaluated_at IS NOT NULL AND score IS NOT NULL
            GROUP BY hotkey
            """
        )
        
        miner_scores = cursor.fetchall()
        cursor.close()
        
        if not miner_scores:
            bt.logging.info("No scored submissions found")
            return
            
        # Create a tensor of zeros for all miners
        updated_scores = torch.zeros_like(self.metagraph.S, dtype=torch.float32)
        
        # Update scores for miners with evaluations
        for hotkey, avg_score in miner_scores:
            try:
                uid = self.metagraph.hotkeys.index(hotkey)
                updated_scores[uid] = float(avg_score)
                bt.logging.info(f"Miner {uid} ({hotkey}): score = {avg_score}")
            except ValueError:
                bt.logging.warning(f"Unknown hotkey {hotkey}")
                continue
                
        # Set minimum score for active miners
        min_score = 0.1  # Minimum score for active miners
        mask = torch.zeros_like(updated_scores)
        for uid in range(len(self.metagraph.hotkeys)):
            if self.metagraph.hotkeys[uid] in [hotkey for hotkey, _ in miner_scores]:
                mask[uid] = 1
            elif self.metagraph.axons[uid].is_serving:
                mask[uid] = min_score
        
        # Apply the mask to ensure minimum scores for active miners
        updated_scores = torch.max(updated_scores, mask)
        
        # Update weights
        self.scores = updated_scores
        
        # Set weights on the network
        bt.logging.info(f"Setting weights based on updated scores")
        self.set_weights()
    
    def _generate_challenge_description(self, repo_path: str, challenge_type: str) -> str:
        """Generate a description for a code challenge"""
        # List of challenge templates by type
        templates = {
            "feature": [
                "Implement a new feature that allows users to {action} {target}",
                "Add functionality for {action} with proper error handling",
                "Create a new component that provides {target} functionality"
            ],
            "bugfix": [
                "Fix the bug where {target} fails when {action}",
                "Address the issue with {target} that occurs during {action}",
                "Resolve the error that happens when users {action}"
            ],
            "refactor": [
                "Refactor the {target} code to improve performance",
                "Restructure {target} to follow better coding practices",
                "Improve the organization of {target} by applying {action}"
            ],
            "test": [
                "Write comprehensive tests for the {target} functionality",
                "Implement unit tests for {action} to ensure it works correctly",
                "Create integration tests that verify {target} works with other components"
            ],
            "documentation": [
                "Document the {target} API with clear examples",
                "Create user documentation for the {action} feature",
                "Update the README with instructions for using {target}"
            ]
        }
        
        # Get files in repository to use as potential targets
        files_output = subprocess.run(
            ["git", "-C", repo_path, "ls-files"],
            capture_output=True, text=True, check=True
        ).stdout
        
        files = [f for f in files_output.split("\n") if f.strip()]
        
        # Generate random actions and targets
        actions = [
            "searching", "filtering", "adding", "removing", "updating", 
            "processing", "analyzing", "exporting", "importing", "configuring"
        ]
        
        generic_targets = [
            "user profiles", "data entries", "configuration settings", "API responses",
            "input validation", "error handling", "database connections", "authentication",
            "authorization", "file uploads", "notification system", "user interface",
            "dashboard widgets", "search functionality", "request processing"
        ]
        
        # Try to find a real file target if possible
        target = random.choice(generic_targets)
        if files:
            file_target = random.choice(files)
            if "." in file_target:  # Only use files, not directories
                target = file_target
        
        # Get a template for the challenge type
        template = random.choice(templates.get(challenge_type, templates["feature"]))
        
        # Fill in the template
        description = template.format(
            action=random.choice(actions),
            target=target
        )
        
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
    
    def _calculate_solution_score(self, validation_result: Dict) -> float:
        """Calculate a score from validation results"""
        score = 50.0  # Base score
        
        # Test results
        test_score = validation_result.get("test_score", 0)
        score += test_score * 20  # Tests are worth up to 20 points
        
        # Lint score
        lint_score = validation_result.get("lint_score", 0)
        score += lint_score * 15  # Linting is worth up to 15 points
        
        # Code quality score
        quality_score = validation_result.get("quality_score", 0)
        score += quality_score * 15  # Code quality is worth up to 15 points
        
        # Normalize score to 0-1 range (we'll multiply by 100 later)
        score = max(0, min(score, 100)) / 100.0
        
        return score
    
    def _get_repo_path(self, repo_url: str) -> Optional[str]:
        """Get the local path for a repository"""
        if self.conn:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                SELECT local_path FROM repositories 
                WHERE repo_url = %s
                """, 
                (repo_url,)
            )
            result = cursor.fetchone()
            cursor.close()
            
            if result:
                return result[0]
        
        # If not in database, check if we can find it by URL hash
        repo_hash = self._hash_repo_url(repo_url)
        repo_path = os.path.join(self.repo_dir, repo_hash)
        
        if os.path.exists(repo_path) and os.path.isdir(repo_path):
            # Add to database if found
            if self.conn:
                cursor = self.conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO repositories (repo_url, local_path) 
                    VALUES (%s, %s) 
                    ON CONFLICT DO NOTHING
                    """,
                    (repo_url, repo_path)
                )
                self.conn.commit()
                cursor.close()
            return repo_path
            
        return None
    
    def _hash_repo_url(self, repo_url: str) -> str:
        """Create a filesystem-safe hash of the repository URL"""
        # Simple hashing to avoid filesystem issues with URLs
        return repo_url.replace('/', '_').replace(':', '_').replace('.', '_')
    
    def _clone_repository(self, repo_url: str) -> str:
        """Clone a Git repository to the local storage"""
        bt.logging.info(f"Cloning repository: {repo_url}")
        
        repo_hash = self._hash_repo_url(repo_url)
        repo_path = os.path.join(self.repo_dir, repo_hash)
        
        # Clone the repository
        os.makedirs(os.path.dirname(repo_path), exist_ok=True)
        subprocess.run(
            ["git", "clone", repo_url, repo_path],
            check=True, capture_output=True, text=True
        )
        
        # Add to database
        if self.conn:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO repositories (repo_url, local_path) 
                VALUES (%s, %s) 
                ON CONFLICT DO NOTHING
                """,
                (repo_url, repo_path)
            )
            self.conn.commit()
            cursor.close()
            
        return repo_path
    
    def _get_repo_info(self, repo_path: str) -> Dict[str, Any]:
        """Get information about a repository"""
        # Get branches
        branch_output = subprocess.run(
            ["git", "-C", repo_path, "branch", "-a"],
            check=True, capture_output=True, text=True
        ).stdout
        
        branches = [b.strip() for b in branch_output.split('\n') if b.strip()]
        
        # Get latest commit
        commit_output = subprocess.run(
            ["git", "-C", repo_path, "log", "-1", "--pretty=format:%H|%an|%at|%s"],
            check=True, capture_output=True, text=True
        ).stdout
        
        commit_parts = commit_output.split('|')
        latest_commit = {
            "hash": commit_parts[0] if len(commit_parts) > 0 else "",
            "author": commit_parts[1] if len(commit_parts) > 1 else "",
            "timestamp": commit_parts[2] if len(commit_parts) > 2 else "",
            "message": commit_parts[3] if len(commit_parts) > 3 else ""
        }
        
        return {
            "branches": branches,
            "latest_commit": latest_commit,
            "local_path": repo_path
        }

# This is the main function, which runs the validator.
if __name__ == "__main__":
    with Validator() as validator:
        while True:
            bt.logging.info("Validator running...", time.time())
            time.sleep(5)
