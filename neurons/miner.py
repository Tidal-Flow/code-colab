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
import json
import typing
import asyncio
import tempfile
import subprocess
import bittensor as bt
import psycopg2
from pathlib import Path
from datetime import datetime

# Code Collaboration Subnet
import ocr_subnet as code_subnet

# Import base miner class which takes care of most of the boilerplate
from ocr_subnet.base.miner import BaseMinerNeuron

class Miner(BaseMinerNeuron):
    """
    Code Collaboration Subnet Miner implementation that acts as a Git server.
    This miner hosts Git repositories and handles clone, fetch, and push operations
    along with responding to code challenges.
    """

    def __init__(self, config=None):
        super(Miner, self).__init__(config=config)
        
        # Initialize repository storage directory
        bt.logging.info("Initializing Git repository storage...")
        self.repo_dir = os.path.expanduser(self.config.miner.repo_directory)
        os.makedirs(self.repo_dir, exist_ok=True)
        bt.logging.info(f"Repository directory initialized at: {self.repo_dir}")
        
        # Initialize database connection for tracking repositories and challenges
        self.setup_database()
        
        # Verify Git is installed and working
        try:
            result = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True)
            bt.logging.info(f"Git version: {result.stdout.strip()}")
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            bt.logging.error(f"Git installation error: {e}. Please ensure Git is installed.")
            raise RuntimeError("Git must be installed to run the Code Collaboration Miner")
        
    def setup_database(self):
        """Set up a local database to track repositories and challenges"""
        try:
            self.conn = psycopg2.connect(
                dbname=self.config.database.name,
                user=self.config.database.user,
                password=self.config.database.password,
                host=self.config.database.host,
                port=self.config.database.port
            )
            bt.logging.info("Database connection established")
            
            # Create repositories table
            cursor = self.conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS repositories (
                    repo_id SERIAL PRIMARY KEY,
                    repo_url TEXT UNIQUE NOT NULL,
                    local_path TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create challenges table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS challenges (
                    challenge_id TEXT PRIMARY KEY,
                    repo_url TEXT NOT NULL,
                    challenge_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    base_branch TEXT NOT NULL,
                    solution_branch TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (repo_url) REFERENCES repositories(repo_url)
                )
            """)
            
            self.conn.commit()
            cursor.close()
        except Exception as e:
            bt.logging.error(f"Database connection failed: {e}")
            self.conn = None

    async def forward(self, synapse: typing.Union[
        code_subnet.protocol.GitCloneSynapse,
        code_subnet.protocol.GitFetchSynapse,
        code_subnet.protocol.GitPushSynapse,
        code_subnet.protocol.GitChallengeSynapse
    ]):
        """
        Process incoming Git-related requests from validators.
        
        Args:
            synapse: The synapse object containing the Git operation request.
        
        Returns:
            The synapse object with the response field populated.
        """
        # Route to the appropriate handler based on synapse type
        if isinstance(synapse, code_subnet.protocol.GitCloneSynapse):
            return await self._handle_git_clone(synapse)
        elif isinstance(synapse, code_subnet.protocol.GitFetchSynapse):
            return await self._handle_git_fetch(synapse)
        elif isinstance(synapse, code_subnet.protocol.GitPushSynapse):
            return await self._handle_git_push(synapse)
        elif isinstance(synapse, code_subnet.protocol.GitChallengeSynapse):
            return await self._handle_git_challenge(synapse)
        else:
            bt.logging.error(f"Unknown synapse type: {type(synapse)}")
            synapse.response = {"error": "Unsupported operation type"}
            return synapse

    async def _handle_git_clone(self, synapse: code_subnet.protocol.GitCloneSynapse):
        """Handle Git clone requests"""
        bt.logging.info(f"Received clone request for: {synapse.repo_url}")
        
        try:
            # Check if repo already exists in our database
            repo_path = self._get_repo_path(synapse.repo_url)
            
            if not repo_path:
                # Clone the repository if it doesn't exist
                repo_path = self._clone_repository(synapse.repo_url)
            
            # Get repository metadata
            repo_info = self._get_repo_info(repo_path)
            
            # Prepare response
            synapse.response = {
                "status": "success",
                "repo_info": repo_info,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            bt.logging.error(f"Error handling clone request: {e}")
            synapse.response = {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
            
        return synapse

    async def _handle_git_fetch(self, synapse: code_subnet.protocol.GitFetchSynapse):
        """Handle Git fetch requests"""
        bt.logging.info(f"Received fetch request for: {synapse.repo_url}")
        
        try:
            # Get repository path
            repo_path = self._get_repo_path(synapse.repo_url)
            
            if not repo_path:
                raise ValueError(f"Repository {synapse.repo_url} not found")
            
            # Fetch updates
            self._fetch_repository(repo_path, synapse.ref)
            
            # Get updated repository info
            repo_info = self._get_repo_info(repo_path)
            
            # Prepare response
            synapse.response = {
                "status": "success",
                "repo_info": repo_info,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            bt.logging.error(f"Error handling fetch request: {e}")
            synapse.response = {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
            
        return synapse

    async def _handle_git_push(self, synapse: code_subnet.protocol.GitPushSynapse):
        """Handle Git push requests"""
        bt.logging.info(f"Received push request for: {synapse.repo_url}, branch: {synapse.branch}")
        
        try:
            # Get repository path
            repo_path = self._get_repo_path(synapse.repo_url)
            
            if not repo_path:
                raise ValueError(f"Repository {synapse.repo_url} not found")
            
            # Apply the changes from the commit data
            commit_hash = self._apply_commit(repo_path, synapse.branch, synapse.commit_data)
            
            # Prepare response
            synapse.response = {
                "status": "success",
                "commit_hash": commit_hash,
                "branch": synapse.branch,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            bt.logging.error(f"Error handling push request: {e}")
            synapse.response = {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
            
        return synapse

    async def _handle_git_challenge(self, synapse: code_subnet.protocol.GitChallengeSynapse):
        """Handle Git challenge requests"""
        bt.logging.info(f"Received challenge request: {synapse.challenge_id} for repo: {synapse.repo_url}")
        
        try:
            # Store challenge in database
            if self.conn:
                cursor = self.conn.cursor()
                # Check if repo exists, if not add it
                cursor.execute("SELECT repo_url FROM repositories WHERE repo_url = %s", (synapse.repo_url,))
                if not cursor.fetchone():
                    repo_path = self._clone_repository(synapse.repo_url)
                    cursor.execute(
                        "INSERT INTO repositories (repo_url, local_path) VALUES (%s, %s)",
                        (synapse.repo_url, repo_path)
                    )
                
                # Insert challenge
                cursor.execute(
                    """
                    INSERT INTO challenges 
                    (challenge_id, repo_url, challenge_type, description, base_branch, status) 
                    VALUES (%s, %s, %s, %s, %s, 'accepted')
                    ON CONFLICT (challenge_id) DO UPDATE SET 
                    status = 'accepted', completed_at = NULL
                    """,
                    (synapse.challenge_id, synapse.repo_url, synapse.challenge_type, 
                     synapse.description, synapse.base_branch)
                )
                self.conn.commit()
                cursor.close()
            
            # Create a solution branch for this challenge
            repo_path = self._get_repo_path(synapse.repo_url)
            solution_branch = f"solution/{synapse.challenge_id}"
            
            # Create solution branch from base branch
            self._create_solution_branch(repo_path, synapse.base_branch, solution_branch)
            
            # Prepare response
            synapse.response = {
                "status": "accepted",
                "solution_branch": solution_branch,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            bt.logging.error(f"Error handling challenge request: {e}")
            synapse.response = {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
            
        return synapse
    
    def _get_repo_path(self, repo_url):
        """Get the local path for a repository"""
        if self.conn:
            cursor = self.conn.cursor()
            cursor.execute("SELECT local_path FROM repositories WHERE repo_url = %s", (repo_url,))
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
                    "INSERT INTO repositories (repo_url, local_path) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (repo_url, repo_path)
                )
                self.conn.commit()
                cursor.close()
            return repo_path
            
        return None
    
    def _hash_repo_url(self, repo_url):
        """Create a filesystem-safe hash of the repository URL"""
        # Simple hashing to avoid filesystem issues with URLs
        return repo_url.replace('/', '_').replace(':', '_').replace('.', '_')
    
    def _clone_repository(self, repo_url):
        """Clone a Git repository to the local storage"""
        repo_hash = self._hash_repo_url(repo_url)
        repo_path = os.path.join(self.repo_dir, repo_hash)
        
        if os.path.exists(repo_path):
            # Repository already exists, just fetch latest
            self._fetch_repository(repo_path)
            return repo_path
        
        # Clone the repository
        os.makedirs(repo_path, exist_ok=True)
        subprocess.run(
            ["git", "clone", repo_url, repo_path],
            check=True, capture_output=True, text=True
        )
        
        # Add to database
        if self.conn:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO repositories (repo_url, local_path) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (repo_url, repo_path)
            )
            self.conn.commit()
            cursor.close()
            
        return repo_path
    
    def _fetch_repository(self, repo_path, ref=None):
        """Fetch updates for a repository"""
        if ref:
            subprocess.run(
                ["git", "-C", repo_path, "fetch", "origin", ref],
                check=True, capture_output=True, text=True
            )
        else:
            subprocess.run(
                ["git", "-C", repo_path, "fetch", "--all"],
                check=True, capture_output=True, text=True
            )
    
    def _get_repo_info(self, repo_path):
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
    
    def _apply_commit(self, repo_path, branch, commit_data):
        """Apply changes from commit data to a branch"""
        # Ensure we're on the right branch
        subprocess.run(
            ["git", "-C", repo_path, "checkout", "-B", branch],
            check=True, capture_output=True, text=True
        )
        
        # Create temporary directory for files
        with tempfile.TemporaryDirectory() as temp_dir:
            # Write files to temporary directory
            for file_path, file_content in commit_data.get("files", {}).items():
                full_path = os.path.join(temp_dir, file_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                
                with open(full_path, 'w') as f:
                    f.write(file_content)
                
                # Copy file to repository
                target_path = os.path.join(repo_path, file_path)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                
                # Use git to manage the file
                subprocess.run(
                    ["cp", full_path, target_path],
                    check=True, capture_output=True, text=True
                )
                
                # Add the file
                subprocess.run(
                    ["git", "-C", repo_path, "add", file_path],
                    check=True, capture_output=True, text=True
                )
        
        # Commit the changes
        subprocess.run(
            ["git", "-C", repo_path, "commit", "-m", commit_data.get("message", "Commit via Bittensor")],
            check=True, capture_output=True, text=True
        )
        
        # Get the commit hash
        commit_hash = subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True
        ).stdout.strip()
        
        return commit_hash
    
    def _create_solution_branch(self, repo_path, base_branch, solution_branch):
        """Create a new solution branch for a challenge"""
        # Fetch updates
        self._fetch_repository(repo_path)
        
        # Create branch from base
        subprocess.run(
            ["git", "-C", repo_path, "checkout", "-B", solution_branch, f"origin/{base_branch}"],
            check=True, capture_output=True, text=True
        )
        
        return solution_branch

    async def blacklist(self, synapse) -> typing.Tuple[bool, str]:
        """
        Determines whether an incoming request should be blacklisted and thus ignored.
        
        Args:
            synapse: A synapse object from an incoming request.
            
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

    async def priority(self, synapse) -> float:
        """
        Determines the priority of the request based on the caller's stake.
        
        Args:
            synapse: The synapse object that contains metadata about the incoming request.
            
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
