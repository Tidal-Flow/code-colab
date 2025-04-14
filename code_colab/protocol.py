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


import bittensor as bt
from typing import Optional, List, Dict, Any, Union

# Define the base Git operation metrics for scoring
class GitOperationMetrics:
    """
    Base metrics structure for Git operations performance
    
    Attributes:
    - latency: Time in milliseconds to complete operation
    - bandwidth: Bandwidth in bytes/second during transfer
    - integrity: Boolean indicating if operation maintained data integrity
    - success: Boolean indicating overall success
    - error: Optional error message if operation failed
    """
    latency: float = 0.0
    bandwidth: float = 0.0
    integrity: bool = True
    success: bool = True
    error: Optional[str] = None

class GitCloneSynapse(bt.Synapse):
    """
    Protocol for Git clone operations between validators and miners.
    
    Attributes:
    - repo_url: URL of the Git repository
    - ref: Optional Git reference (branch/tag/commit hash)
    - validator_hotkey: Validator's hotkey for isolation
    - clone_depth: Optional depth for shallow clones
    - response: JSON with repository data or error message
    - metrics: Performance metrics for the operation
    """
    
    # Used by the validator for timing
    time_elapsed = 0
    
    # Required request input
    repo_url: str
    validator_hotkey: str
    ref: Optional[str] = None
    clone_depth: Optional[int] = None
    
    # Optional request output
    response: Optional[Dict[str, Any]] = None
    metrics: Optional[Dict[str, Any]] = None
    
    def deserialize(self) -> Dict[str, Any]:
        """
        Deserialize the miner response.
        
        Returns:
        - Dict: Repository data with metadata
        """
        return self.response

class GitFetchSynapse(bt.Synapse):
    """
    Protocol for Git fetch operations between validators and miners.
    
    Attributes:
    - repo_url: URL of the Git repository
    - validator_hotkey: Validator's hotkey for isolation
    - ref: Optional Git reference to fetch
    - response: JSON with updated references or error message
    - metrics: Performance metrics for the operation
    """
    
    # Used by the validator for timing
    time_elapsed = 0
    
    # Required request input
    repo_url: str
    validator_hotkey: str
    ref: Optional[str] = None
    
    # Optional request output
    response: Optional[Dict[str, Any]] = None
    metrics: Optional[Dict[str, Any]] = None
    
    def deserialize(self) -> Dict[str, Any]:
        """
        Deserialize the miner response.
        
        Returns:
        - Dict: Updated repository data
        """
        return self.response

class GitPushSynapse(bt.Synapse):
    """
    Protocol for Git push operations between validators and miners.
    
    Attributes:
    - repo_url: URL of the Git repository
    - validator_hotkey: Validator's hotkey for isolation
    - branch: Branch name for the push
    - commit_data: Dictionary containing commit details (message, files, etc.)
    - response: JSON with push result information
    - metrics: Performance metrics for the operation
    """
    
    # Used by the validator for timing
    time_elapsed = 0
    
    # Required request input
    repo_url: str
    validator_hotkey: str
    branch: str
    commit_data: Dict[str, Any]
    
    # Optional request output
    response: Optional[Dict[str, Any]] = None
    metrics: Optional[Dict[str, Any]] = None
    
    def deserialize(self) -> Dict[str, Any]:
        """
        Deserialize the miner response.
        
        Returns:
        - Dict: Push result information
        """
        return self.response

class GitPullRequestSynapse(bt.Synapse):
    """
    Protocol for Git pull request operations between validators and miners.
    
    Attributes:
    - repo_url: URL of the Git repository
    - validator_hotkey: Validator's hotkey for isolation
    - source_branch: Source branch for the PR
    - target_branch: Target branch for the PR
    - title: PR title
    - description: PR description
    - response: JSON with PR result information
    - metrics: Performance metrics for the operation
    """
    
    # Used by the validator for timing
    time_elapsed = 0
    
    # Required request input
    repo_url: str
    validator_hotkey: str
    source_branch: str
    target_branch: str
    title: str
    description: str
    
    # Optional request output
    response: Optional[Dict[str, Any]] = None
    metrics: Optional[Dict[str, Any]] = None
    
    def deserialize(self) -> Dict[str, Any]:
        """
        Deserialize the miner response.
        
        Returns:
        - Dict: PR result information
        """
        return self.response

class GitChallengeSynapse(bt.Synapse):
    """
    Protocol for code challenge operations between validators and miners.
    
    Attributes:
    - repo_url: URL of the Git repository
    - validator_hotkey: Validator's hotkey for isolation
    - challenge_id: Unique identifier for this challenge
    - challenge_type: Type of challenge (feature, bugfix, refactor, etc.)
    - description: Detailed description of the challenge requirements
    - base_branch: Starting branch for the challenge
    - constraints: Optional constraints or requirements for the solution
    - response: Miner's response containing their solution branch name
    - metrics: Performance metrics for the operation
    """
    
    # Used by the validator for timing
    time_elapsed = 0
    
    # Required request input
    repo_url: str
    validator_hotkey: str
    challenge_id: str
    challenge_type: str  
    description: str
    base_branch: str
    constraints: Optional[Dict[str, Any]] = None
    
    # Optional request output
    response: Optional[Dict[str, str]] = None
    metrics: Optional[Dict[str, Any]] = None
    
    def deserialize(self) -> Dict[str, str]:
        """
        Deserialize the miner response.
        
        Returns:
        - Dict: Solution information including branch name
        """
        return self.response

class GitValidationSynapse(bt.Synapse):
    """
    Protocol for code validation operations between validators and miners.
    
    Attributes:
    - repo_url: URL of the Git repository
    - validator_hotkey: Validator's hotkey for isolation
    - solution_branch: Branch containing the solution to validate
    - challenge_id: ID of the associated challenge
    - validation_type: Type of validation to perform (tests, linting, etc.)
    - response: Detailed validation results with scores
    - metrics: Performance metrics for the operation
    """
    
    # Used by the validator for timing
    time_elapsed = 0
    
    # Required request input
    repo_url: str
    validator_hotkey: str
    solution_branch: str
    challenge_id: str
    validation_type: str
    
    # Optional request output
    response: Optional[Dict[str, Any]] = None
    metrics: Optional[Dict[str, Any]] = None
    
    def deserialize(self) -> Dict[str, Any]:
        """
        Deserialize the miner response.
        
        Returns:
        - Dict: Validation results with scores
        """
        return self.response

class PerformanceMetricsSynapse(bt.Synapse):
    """
    Protocol for collecting performance metrics from the miner.
    
    Attributes:
    - validator_hotkey: Validator's hotkey for isolation
    - metric_type: Type of metric to collect (uptime, resource_usage, etc.)
    - timeframe: Optional timeframe for metrics collection
    - response: Detailed metrics data
    """
    
    # Used by the validator for timing
    time_elapsed = 0
    
    # Required request input
    validator_hotkey: str
    metric_type: str  # uptime, resource_usage, git_operations, etc.
    timeframe: Optional[str] = None  # e.g., "24h", "7d", etc.
    
    # Optional request output
    response: Optional[Dict[str, Any]] = None
    
    def deserialize(self) -> Dict[str, Any]:
        """
        Deserialize the miner response.
        
        Returns:
        - Dict: Performance metrics data
        """
        return self.response
