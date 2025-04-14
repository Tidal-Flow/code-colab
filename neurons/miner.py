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
from pathlib import Path
from datetime import datetime, timedelta
import hashlib
import psutil

# Code Collaboration Subnet
import code_colab as code_subnet
from code_colab.utils.storage import JSONStorage
from code_colab.utils.git_server import GitServer
from code_colab.utils.metrics import GitPerformanceMetrics

# Import base miner class which takes care of most of the boilerplate
from code_colab.base.miner import BaseMinerNeuron

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
        
        # Initialize storage for tracking repositories and challenges
        self.setup_storage()
        
        # Initialize Git server (HTTP server for Git operations)
        self.git_server = GitServer(
            base_dir=self.repo_dir,
            host=self.config.miner.git_server_host,
            port=self.config.miner.git_server_port
        )
        bt.logging.info(f"Git server initialized on {self.config.miner.git_server_host}:{self.config.miner.git_server_port}")
        
        # Start the Git server in background thread
        self.git_server.start()
        
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
            raise RuntimeError("Git must be installed to run the Code Collaboration Miner")
        
    def setup_storage(self):
        """Set up JSON storage for tracking repositories and challenges"""
        try:
            # Create storage directory
            storage_dir = os.path.join(self.repo_dir, "storage")
            self.storage = JSONStorage(storage_dir)
            bt.logging.info(f"JSON storage initialized at: {storage_dir}")
        except Exception as e:
            bt.logging.error(f"Storage initialization failed: {e}")
            self.storage = None

    async def forward(self, synapse: typing.Union[
        code_subnet.protocol.GitCloneSynapse,
        code_subnet.protocol.GitFetchSynapse,
        code_subnet.protocol.GitPushSynapse,
        code_subnet.protocol.GitPullRequestSynapse,
        code_subnet.protocol.GitChallengeSynapse,
        code_subnet.protocol.GitValidationSynapse,
        code_subnet.protocol.PerformanceMetricsSynapse
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
        elif isinstance(synapse, code_subnet.protocol.GitPullRequestSynapse):
            return await self._handle_git_pull_request(synapse)
        elif isinstance(synapse, code_subnet.protocol.GitChallengeSynapse):
            return await self._handle_git_challenge(synapse)
        elif isinstance(synapse, code_subnet.protocol.GitValidationSynapse):
            return await self._handle_git_validation(synapse)
        elif isinstance(synapse, code_subnet.protocol.PerformanceMetricsSynapse):
            return await self._handle_performance_metrics(synapse)
        else:
            bt.logging.error(f"Unknown synapse type: {type(synapse)}")
            synapse.response = {"error": "Unsupported operation type"}
            return synapse

    async def _handle_git_clone(self, synapse: code_subnet.protocol.GitCloneSynapse):
        """Handle Git clone requests"""
        bt.logging.info(f"Received clone request for: {synapse.repo_url} from validator {synapse.validator_hotkey}")
        
        # Start tracking metrics
        operation_id = self.metrics.start_operation(
            operation_type="clone",
            validator_hotkey=synapse.validator_hotkey,
            repo_name=self._extract_repo_name(synapse.repo_url)
        )
        
        try:
            repo_name = self._extract_repo_name(synapse.repo_url)
            
            # Check if repository already exists for this validator
            try:
                repo_info = self.git_server.get_repo_info(
                    validator_hotkey=synapse.validator_hotkey,
                    repo_name=repo_name
                )
                bt.logging.info(f"Repository {repo_name} already exists for validator {synapse.validator_hotkey}")
            except Exception:
                # Clone or create repository
                if synapse.repo_url.startswith(("http://", "https://", "git://")):
                    # Clone from remote URL
                    self.git_server.clone_to_local(
                        source_url=synapse.repo_url,
                        validator_hotkey=synapse.validator_hotkey,
                        repo_name=repo_name
                    )
                else:
                    # Create new empty repository
                    self.git_server.create_repository(
                        validator_hotkey=synapse.validator_hotkey,
                        repo_name=repo_name
                    )
            
            # Get repository info
            repo_info = self.git_server.get_repo_info(
                validator_hotkey=synapse.validator_hotkey,
                repo_name=repo_name
            )
            
            # End metrics tracking
            metrics_data = self.metrics.end_operation(operation_id)
            
            # Prepare response
            synapse.response = {
                "status": "success",
                "repo_info": repo_info,
                "timestamp": datetime.now().isoformat(),
                "clone_url": self.git_server.get_repo_url(
                    validator_hotkey=synapse.validator_hotkey,
                    repo_name=repo_name
                )
            }
            
            # Add metrics to response
            synapse.metrics = metrics_data
            
        except Exception as e:
            bt.logging.error(f"Error handling clone request: {e}")
            
            # End metrics tracking with error
            metrics_data = self.metrics.end_operation(
                operation_id=operation_id,
                success=False,
                error=str(e)
            )
            
            synapse.response = {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
            
            # Add metrics to response
            synapse.metrics = metrics_data
            
        return synapse

    async def _handle_git_fetch(self, synapse: code_subnet.protocol.GitFetchSynapse):
        """Handle Git fetch requests"""
        bt.logging.info(f"Received fetch request for: {synapse.repo_url} from validator {synapse.validator_hotkey}")
        
        # Start tracking metrics
        operation_id = self.metrics.start_operation(
            operation_type="fetch",
            validator_hotkey=synapse.validator_hotkey,
            repo_name=self._extract_repo_name(synapse.repo_url)
        )
        
        try:
            repo_name = self._extract_repo_name(synapse.repo_url)
            
            # Ensure repository exists
            try:
                repo_info = self.git_server.get_repo_info(
                    validator_hotkey=synapse.validator_hotkey,
                    repo_name=repo_name
                )
            except Exception as e:
                raise ValueError(f"Repository {repo_name} not found: {str(e)}")
            
            # For fetch, validator will use the HTTP URL to fetch directly
            # We just need to make sure the repository is ready and return the URL
            
            # End metrics tracking
            metrics_data = self.metrics.end_operation(operation_id)
            
            # Prepare response
            synapse.response = {
                "status": "success",
                "repo_info": repo_info,
                "timestamp": datetime.now().isoformat(),
                "fetch_url": self.git_server.get_repo_url(
                    validator_hotkey=synapse.validator_hotkey,
                    repo_name=repo_name
                )
            }
            
            # Add metrics to response
            synapse.metrics = metrics_data
            
        except Exception as e:
            bt.logging.error(f"Error handling fetch request: {e}")
            
            # End metrics tracking with error
            metrics_data = self.metrics.end_operation(
                operation_id=operation_id,
                success=False,
                error=str(e)
            )
            
            synapse.response = {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
            
            # Add metrics to response
            synapse.metrics = metrics_data
            
        return synapse

    async def _handle_git_push(self, synapse: code_subnet.protocol.GitPushSynapse):
        """Handle Git push requests"""
        bt.logging.info(f"Received push request for: {synapse.repo_url}, branch: {synapse.branch} from validator {synapse.validator_hotkey}")
        
        # Start tracking metrics
        operation_id = self.metrics.start_operation(
            operation_type="push",
            validator_hotkey=synapse.validator_hotkey,
            repo_name=self._extract_repo_name(synapse.repo_url)
        )
        
        try:
            repo_name = self._extract_repo_name(synapse.repo_url)
            
            # Ensure repository exists
            try:
                repo_info = self.git_server.get_repo_info(
                    validator_hotkey=synapse.validator_hotkey,
                    repo_name=repo_name
                )
            except Exception as e:
                raise ValueError(f"Repository {repo_name} not found: {str(e)}")
            
            # For direct push, validator will use the HTTP URL to push directly
            # If we're given commit data, we can apply it server-side as well
            if synapse.commit_data:
                # Apply changes to repository
                with tempfile.TemporaryDirectory() as temp_dir:
                    # Clone the repository to a temporary directory
                    temp_repo_url = self.git_server.get_repo_url(
                        validator_hotkey=synapse.validator_hotkey,
                        repo_name=repo_name
                    )
                    
                    # Clone to the temporary directory
                    subprocess.run(
                        ["git", "clone", temp_repo_url, temp_dir],
                        check=True,
                        capture_output=True
                    )
                    
                    # Switch to the branch or create it
                    try:
                        subprocess.run(
                            ["git", "checkout", synapse.branch],
                            cwd=temp_dir,
                            check=True,
                            capture_output=True
                        )
                    except subprocess.CalledProcessError:
                        # Branch doesn't exist, create it
                        subprocess.run(
                            ["git", "checkout", "-b", synapse.branch],
                            cwd=temp_dir,
                            check=True,
                            capture_output=True
                        )
                    
                    # Apply changes from commit_data
                    for file_path, content in synapse.commit_data.get("files", {}).items():
                        # Create directories if needed
                        full_path = os.path.join(temp_dir, file_path)
                        os.makedirs(os.path.dirname(full_path), exist_ok=True)
                        
                        # Write file content
                        with open(full_path, "w") as f:
                            f.write(content)
                    
                    # Add changes
                    subprocess.run(
                        ["git", "add", "."],
                        cwd=temp_dir,
                        check=True,
                        capture_output=True
                    )
                    
                    # Commit changes
                    commit_message = synapse.commit_data.get("message", "Commit via API")
                    subprocess.run(
                        ["git", "commit", "-m", commit_message],
                        cwd=temp_dir,
                        check=True,
                        capture_output=True
                    )
                    
                    # Push changes
                    subprocess.run(
                        ["git", "push", "origin", synapse.branch],
                        cwd=temp_dir,
                        check=True,
                        capture_output=True
                    )
                    
                    # Get commit hash
                    result = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=temp_dir,
                        check=True,
                        capture_output=True,
                        text=True
                    )
                    commit_hash = result.stdout.strip()
            else:
                # No commit data, just return the push URL
                commit_hash = None
            
            # Get updated repository info
            repo_info = self.git_server.get_repo_info(
                validator_hotkey=synapse.validator_hotkey,
                repo_name=repo_name
            )
            
            # End metrics tracking
            metrics_data = self.metrics.end_operation(operation_id)
            
            # Prepare response
            synapse.response = {
                "status": "success",
                "repo_info": repo_info,
                "commit_hash": commit_hash,
                "branch": synapse.branch,
                "timestamp": datetime.now().isoformat(),
                "push_url": self.git_server.get_repo_url(
                    validator_hotkey=synapse.validator_hotkey,
                    repo_name=repo_name
                )
            }
            
            # Add metrics to response
            synapse.metrics = metrics_data
            
        except Exception as e:
            bt.logging.error(f"Error handling push request: {e}")
            
            # End metrics tracking with error
            metrics_data = self.metrics.end_operation(
                operation_id=operation_id,
                success=False,
                error=str(e)
            )
            
            synapse.response = {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
            
            # Add metrics to response
            synapse.metrics = metrics_data
            
        return synapse

    async def _handle_git_pull_request(self, synapse: code_subnet.protocol.GitPullRequestSynapse):
        """Handle Git pull request operations"""
        bt.logging.info(f"Received pull request: {synapse.source_branch} -> {synapse.target_branch} from validator {synapse.validator_hotkey}")
        
        # Start tracking metrics
        operation_id = self.metrics.start_operation(
            operation_type="pull_request",
            validator_hotkey=synapse.validator_hotkey,
            repo_name=self._extract_repo_name(synapse.repo_url)
        )
        
        try:
            repo_name = self._extract_repo_name(synapse.repo_url)
            
            # Ensure repository exists
            try:
                repo_info = self.git_server.get_repo_info(
                    validator_hotkey=synapse.validator_hotkey,
                    repo_name=repo_name
                )
            except Exception as e:
                raise ValueError(f"Repository {repo_name} not found: {str(e)}")
            
            # Create a simple pull request implementation
            # In a real-world scenario, you'd use a Git hosting API (GitHub, GitLab, etc.)
            pr_id = hashlib.md5(
                f"{synapse.validator_hotkey}_{repo_name}_{synapse.source_branch}_{synapse.target_branch}_{time.time()}".encode()
            ).hexdigest()[:8]
            
            # Store pull request in storage
            if self.storage:
                self.storage.store_object(
                    object_type="pull_request",
                    object_id=pr_id,
                    data={
                        "id": pr_id,
                        "repo_name": repo_name,
                        "validator_hotkey": synapse.validator_hotkey,
                        "source_branch": synapse.source_branch,
                        "target_branch": synapse.target_branch,
                        "title": synapse.title,
                        "description": synapse.description,
                        "status": "open",
                        "created_at": datetime.now().isoformat()
                    }
                )
            
            # End metrics tracking
            metrics_data = self.metrics.end_operation(operation_id)
            
            # Prepare response
            synapse.response = {
                "status": "success",
                "pr_id": pr_id,
                "source_branch": synapse.source_branch,
                "target_branch": synapse.target_branch,
                "timestamp": datetime.now().isoformat(),
                "repo_url": self.git_server.get_repo_url(
                    validator_hotkey=synapse.validator_hotkey,
                    repo_name=repo_name
                )
            }
            
            # Add metrics to response
            synapse.metrics = metrics_data
            
        except Exception as e:
            bt.logging.error(f"Error handling pull request: {e}")
            
            # End metrics tracking with error
            metrics_data = self.metrics.end_operation(
                operation_id=operation_id,
                success=False,
                error=str(e)
            )
            
            synapse.response = {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
            
            # Add metrics to response
            synapse.metrics = metrics_data
            
        return synapse

    async def _handle_git_challenge(self, synapse: code_subnet.protocol.GitChallengeSynapse):
        """Handle Git challenge requests"""
        bt.logging.info(f"Received challenge for: {synapse.repo_url}, id: {synapse.challenge_id} from validator {synapse.validator_hotkey}")
        
        # Start tracking metrics
        operation_id = self.metrics.start_operation(
            operation_type="challenge",
            validator_hotkey=synapse.validator_hotkey,
            repo_name=self._extract_repo_name(synapse.repo_url)
        )
        
        try:
            repo_name = self._extract_repo_name(synapse.repo_url)
            
            # Ensure repository exists
            try:
                repo_info = self.git_server.get_repo_info(
                    validator_hotkey=synapse.validator_hotkey,
                    repo_name=repo_name
                )
            except Exception:
                # Clone repository if it doesn't exist locally
                if synapse.repo_url.startswith(("http://", "https://", "git://")):
                    # Clone from remote URL
                    self.git_server.clone_to_local(
                        source_url=synapse.repo_url,
                        validator_hotkey=synapse.validator_hotkey,
                        repo_name=repo_name
                    )
                else:
                    raise ValueError(f"Repository {repo_name} not found")
            
            # Create a solution branch
            solution_branch = f"solution/{synapse.challenge_id}"
            
            self.git_server.create_branch(
                validator_hotkey=synapse.validator_hotkey,
                repo_name=repo_name,
                branch_name=solution_branch,
                source_branch=synapse.base_branch
            )
            
            # Store challenge in storage
            if self.storage:
                self.storage.store_object(
                    object_type="challenge",
                    object_id=synapse.challenge_id,
                    data={
                        "id": synapse.challenge_id,
                        "repo_name": repo_name,
                        "validator_hotkey": synapse.validator_hotkey,
                        "challenge_type": synapse.challenge_type,
                        "description": synapse.description,
                        "base_branch": synapse.base_branch,
                        "solution_branch": solution_branch,
                        "status": "accepted",
                        "created_at": datetime.now().isoformat()
                    }
                )
            
            # End metrics tracking
            metrics_data = self.metrics.end_operation(operation_id)
            
            # Prepare response
            synapse.response = {
                "status": "accepted",
                "solution_branch": solution_branch,
                "repo_url": self.git_server.get_repo_url(
                    validator_hotkey=synapse.validator_hotkey,
                    repo_name=repo_name
                ),
                "timestamp": datetime.now().isoformat()
            }
            
            # Add metrics to response
            synapse.metrics = metrics_data
            
        except Exception as e:
            bt.logging.error(f"Error handling challenge request: {e}")
            
            # End metrics tracking with error
            metrics_data = self.metrics.end_operation(
                operation_id=operation_id,
                success=False,
                error=str(e)
            )
            
            synapse.response = {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
            
            # Add metrics to response
            synapse.metrics = metrics_data
            
        return synapse

    async def _handle_git_validation(self, synapse: code_subnet.protocol.GitValidationSynapse):
        """Handle Git validation requests"""
        bt.logging.info(f"Received validation request for: {synapse.repo_url}, solution: {synapse.solution_branch} from validator {synapse.validator_hotkey}")
        
        # Start tracking metrics
        operation_id = self.metrics.start_operation(
            operation_type="validation",
            validator_hotkey=synapse.validator_hotkey,
            repo_name=self._extract_repo_name(synapse.repo_url)
        )
        
        try:
            repo_name = self._extract_repo_name(synapse.repo_url)
            
            # Ensure repository exists
            try:
                repo_info = self.git_server.get_repo_info(
                    validator_hotkey=synapse.validator_hotkey,
                    repo_name=repo_name
                )
            except Exception as e:
                raise ValueError(f"Repository {repo_name} not found: {str(e)}")
            
            # Perform validation (simplified for this implementation)
            validation_result = {
                "status": "success",
                "challenge_id": synapse.challenge_id,
                "scores": {
                    "performance": 0.85,
                    "correctness": 0.90,
                    "style": 0.80,
                    "overall": 0.85
                },
                "details": {
                    "tests_passed": 5,
                    "tests_failed": 1,
                    "total_tests": 6
                }
            }
            
            # End metrics tracking
            metrics_data = self.metrics.end_operation(operation_id)
            
            # Prepare response
            synapse.response = {
                "status": "success",
                "validation": validation_result,
                "timestamp": datetime.now().isoformat()
            }
            
            # Add metrics to response
            synapse.metrics = metrics_data
            
        except Exception as e:
            bt.logging.error(f"Error handling validation request: {e}")
            
            # End metrics tracking with error
            metrics_data = self.metrics.end_operation(
                operation_id=operation_id,
                success=False,
                error=str(e)
            )
            
            synapse.response = {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
            
            # Add metrics to response
            synapse.metrics = metrics_data
            
        return synapse

    async def _handle_performance_metrics(self, synapse: code_subnet.protocol.PerformanceMetricsSynapse):
        """Handle metrics collection requests"""
        bt.logging.info(f"Received metrics request: {synapse.metric_type} from validator {synapse.validator_hotkey}")
        
        try:
            if synapse.metric_type == "uptime":
                # Return uptime information
                uptime_seconds = time.time() - self.start_time
                synapse.response = {
                    "status": "success",
                    "uptime_seconds": uptime_seconds,
                    "uptime_formatted": str(timedelta(seconds=int(uptime_seconds))),
                    "timestamp": datetime.now().isoformat()
                }
            elif synapse.metric_type == "git_operations":
                # Return Git operation metrics
                metrics_list = self.metrics.get_metrics_for_validator(
                    validator_hotkey=synapse.validator_hotkey,
                    timeframe=synapse.timeframe
                )
                
                # Calculate scores
                scores = self.metrics.calculate_average_scores(metrics_list)
                
                synapse.response = {
                    "status": "success",
                    "operation_count": len(metrics_list),
                    "scores": scores,
                    "timestamp": datetime.now().isoformat()
                }
            elif synapse.metric_type == "resource_usage":
                # Return resource usage information
                cpu_percent = psutil.cpu_percent(interval=0.1)
                memory = psutil.virtual_memory()
                disk = psutil.disk_usage(self.repo_dir)
                
                synapse.response = {
                    "status": "success",
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory.percent,
                    "disk_percent": disk.percent,
                    "timestamp": datetime.now().isoformat()
                }
            else:
                synapse.response = {
                    "status": "error",
                    "error": f"Unknown metric type: {synapse.metric_type}",
                    "timestamp": datetime.now().isoformat()
                }
        except Exception as e:
            bt.logging.error(f"Error handling metrics request: {e}")
            synapse.response = {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
        
        return synapse

    def _extract_repo_name(self, repo_url: str) -> str:
        """Extract repository name from URL or path"""
        # Remove .git extension if present
        if repo_url.endswith(".git"):
            repo_url = repo_url[:-4]
        
        # Handle various URL formats
        if "/" in repo_url:
            parts = repo_url.split("/")
            return parts[-1]
        else:
            return repo_url

    async def blacklist(self, synapse) -> typing.Tuple[bool, str]:
        """
        Check if the synapse should be blacklisted.
        
        Args:
            synapse: The synapse object to check.
            
        Returns:
            Tuple[bool, str]: A tuple containing a boolean indicating whether the synapse 
                              should be blacklisted and a string containing the reason.
        """
        if not synapse.validator_hotkey:
            return True, "Missing validator_hotkey"

        # If synapse includes a repo_url, ensure it's reasonably formatted
        if hasattr(synapse, 'repo_url') and synapse.repo_url:
            if len(synapse.repo_url) > 500:
                return True, "Repository URL too long"
            
            # Sanitize URL to prevent path traversal
            if ".." in synapse.repo_url or "~" in synapse.repo_url:
                return True, "Invalid repository URL"
        
        return False, "Synapse accepted"

    async def priority(self, synapse) -> float:
        """
        Return the priority of the synapse.
        
        Args:
            synapse: The synapse object to prioritize.
            
        Returns:
            float: The priority value
        """
        # All synapse types have the same priority for now
        # In a more complex implementation, you might prioritize based on
        # operation type, validator reputation, etc.
        return 1.0

# This is the main function, which runs the miner.
if __name__ == "__main__":
    with Miner() as miner:
        while True:
            bt.logging.info("Miner running...", time.time())
            time.sleep(5)
