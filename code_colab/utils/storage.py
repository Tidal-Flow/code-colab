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
import json
import fcntl
import logging
from typing import Dict, List, Any, Optional, Union
from datetime import datetime


class JSONStorage:
    """
    A class for storing and retrieving data from JSON files.
    Acts as a replacement for PostgreSQL database in the validator.
    Uses file locking to prevent concurrent access issues.
    """

    def __init__(self, storage_dir: str):
        """
        Initialize the storage with a directory path.
        
        Args:
            storage_dir: Directory to store JSON files
        """
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)
        
        # Initialize storage files if they don't exist
        self.challenges_file = os.path.join(storage_dir, "challenges.json")
        self.submissions_file = os.path.join(storage_dir, "submissions.json")
        self.repositories_file = os.path.join(storage_dir, "repositories.json")
        
        # Create files if they don't exist
        self._initialize_file(self.challenges_file, {"challenges": []})
        self._initialize_file(self.submissions_file, {"submissions": []})
        self._initialize_file(self.repositories_file, {"repositories": []})
    
    def _initialize_file(self, file_path: str, default_data: Dict[str, Any]) -> None:
        """Initialize a JSON file with default data if it doesn't exist."""
        if not os.path.exists(file_path):
            with open(file_path, 'w') as f:
                json.dump(default_data, f, indent=2)
    
    def _read_file(self, file_path: str) -> Dict[str, Any]:
        """Read data from a JSON file with file locking."""
        with open(file_path, 'r') as f:
            # Get exclusive lock
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                return json.load(f)
            finally:
                # Release lock
                fcntl.flock(f, fcntl.LOCK_UN)
    
    def _write_file(self, file_path: str, data: Dict[str, Any]) -> None:
        """Write data to a JSON file with file locking."""
        with open(file_path, 'w') as f:
            # Get exclusive lock
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(data, f, indent=2)
            finally:
                # Release lock
                fcntl.flock(f, fcntl.LOCK_UN)
    
    # Challenge management methods
    
    def store_challenge(self, challenge_id: str, repo_url: str, 
                        challenge_type: str, description: str, 
                        base_branch: str, expiry_at: str) -> None:
        """Store a new challenge in the challenges file."""
        data = self._read_file(self.challenges_file)
        
        # Create challenge object
        challenge = {
            "challenge_id": challenge_id,
            "repo_url": repo_url,
            "challenge_type": challenge_type,
            "description": description,
            "base_branch": base_branch,
            "created_at": datetime.now().isoformat(),
            "expiry_at": expiry_at
        }
        
        # Add to challenges list
        data["challenges"].append(challenge)
        
        # Write updated data
        self._write_file(self.challenges_file, data)
    
    def get_challenge(self, challenge_id: str) -> Optional[Dict[str, Any]]:
        """Get a challenge by ID."""
        data = self._read_file(self.challenges_file)
        
        for challenge in data["challenges"]:
            if challenge["challenge_id"] == challenge_id:
                return challenge
        
        return None
    
    # Submission management methods
    
    def store_submission(self, submission_id: str, challenge_id: str, 
                         hotkey: str, solution_branch: str) -> None:
        """Store a new submission in the submissions file."""
        data = self._read_file(self.submissions_file)
        
        # Create submission object
        submission = {
            "submission_id": submission_id,
            "challenge_id": challenge_id,
            "hotkey": hotkey,
            "solution_branch": solution_branch,
            "score": None,
            "test_results": None,
            "submitted_at": datetime.now().isoformat(),
            "evaluated_at": None
        }
        
        # Add to submissions list
        data["submissions"].append(submission)
        
        # Write updated data
        self._write_file(self.submissions_file, data)
    
    def update_submission_score(self, submission_id: str, 
                               score: float, test_results: Dict[str, Any]) -> None:
        """Update a submission with score and test results."""
        data = self._read_file(self.submissions_file)
        
        for submission in data["submissions"]:
            if submission["submission_id"] == submission_id:
                submission["score"] = score
                submission["test_results"] = test_results
                submission["evaluated_at"] = datetime.now().isoformat()
                break
        
        # Write updated data
        self._write_file(self.submissions_file, data)
    
    def get_pending_submissions(self, limit: int) -> List[Dict[str, Any]]:
        """
        Get submissions that haven't been scored yet.
        Also fetches related challenge data and combines it.
        """
        submissions_data = self._read_file(self.submissions_file)
        challenges_data = self._read_file(self.challenges_file)
        
        # Create a dictionary of challenges for quick lookup
        challenge_dict = {c["challenge_id"]: c for c in challenges_data["challenges"]}
        
        # Find submissions without scores
        pending = []
        for submission in submissions_data["submissions"]:
            if submission["score"] is None:
                challenge_id = submission["challenge_id"]
                if challenge_id in challenge_dict:
                    challenge = challenge_dict[challenge_id]
                    # Check if challenge is still valid (not expired)
                    expiry_time = datetime.fromisoformat(challenge["expiry_at"])
                    if datetime.now() < expiry_time:
                        # Combine submission and challenge data
                        combined = {
                            "submission_id": submission["submission_id"],
                            "challenge_id": submission["challenge_id"],
                            "hotkey": submission["hotkey"],
                            "solution_branch": submission["solution_branch"],
                            "repo_url": challenge["repo_url"]
                        }
                        pending.append(combined)
                        
                        # Stop if we've reached the limit
                        if len(pending) >= limit:
                            break
        
        return pending
    
    def get_scored_submissions(self) -> List[Dict[str, Any]]:
        """Get all submissions that have been scored."""
        data = self._read_file(self.submissions_file)
        
        return [s for s in data["submissions"] if s["score"] is not None]
    
    # Repository management methods
    
    def store_repository(self, repo_url: str, local_path: str) -> None:
        """Store repository information."""
        data = self._read_file(self.repositories_file)
        
        # Check if repo already exists
        for repo in data["repositories"]:
            if repo["repo_url"] == repo_url:
                repo["local_path"] = local_path
                repo["last_updated"] = datetime.now().isoformat()
                break
        else:
            # Add new repository
            data["repositories"].append({
                "repo_url": repo_url,
                "local_path": local_path,
                "created_at": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat()
            })
        
        # Write updated data
        self._write_file(self.repositories_file, data)
    
    def get_repository_path(self, repo_url: str) -> Optional[str]:
        """Get local path for a repository."""
        data = self._read_file(self.repositories_file)
        
        for repo in data["repositories"]:
            if repo["repo_url"] == repo_url:
                return repo["local_path"]
        
        return None
    
    def get_average_scores_by_hotkey(self) -> Dict[str, float]:
        """Calculate average scores for each hotkey."""
        data = self._read_file(self.submissions_file)
        
        # Group scores by hotkey
        hotkey_scores = {}
        for submission in data["submissions"]:
            if submission["score"] is not None:
                hotkey = submission["hotkey"]
                if hotkey not in hotkey_scores:
                    hotkey_scores[hotkey] = []
                hotkey_scores[hotkey].append(submission["score"])
        
        # Calculate averages
        return {
            hotkey: sum(scores) / len(scores) 
            for hotkey, scores in hotkey_scores.items()
        }