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
import hashlib
import psutil
import bittensor as bt
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

class GitPerformanceMetrics:
    """
    Class to collect and track Git operation performance metrics.
    
    This class provides methods to:
    1. Record latency for Git operations
    2. Track bandwidth usage
    3. Calculate data integrity (hash comparison)
    4. Generate performance reports
    5. Score miners based on their performance metrics
    """
    
    def __init__(self, metrics_dir: str = None):
        """
        Initialize the metrics collector.
        
        Args:
            metrics_dir: Directory to store metrics data
        """
        self.metrics_dir = metrics_dir or os.path.expanduser("~/.code_colab/metrics")
        os.makedirs(self.metrics_dir, exist_ok=True)
        self.current_metrics = {}
    
    def start_operation(self, operation_type: str, validator_hotkey: str, 
                         repo_name: str) -> str:
        """
        Start tracking metrics for a Git operation.
        
        Args:
            operation_type: Type of operation (clone, fetch, push)
            validator_hotkey: Validator's hotkey
            repo_name: Name of the repository
            
        Returns:
            str: Operation ID for tracking
        """
        # Generate unique operation ID
        operation_id = hashlib.md5(
            f"{operation_type}_{validator_hotkey}_{repo_name}_{time.time()}".encode()
        ).hexdigest()
        
        # Initialize metrics for this operation
        self.current_metrics[operation_id] = {
            "operation_type": operation_type,
            "validator_hotkey": validator_hotkey,
            "repo_name": repo_name,
            "start_time": time.time(),
            "network_before": self._get_network_stats(),
            "status": "running"
        }
        
        return operation_id
    
    def end_operation(self, operation_id: str, success: bool = True, 
                       error: Optional[str] = None) -> Dict[str, Any]:
        """
        End tracking metrics for a Git operation.
        
        Args:
            operation_id: Operation ID from start_operation
            success: Whether the operation was successful
            error: Error message if operation failed
            
        Returns:
            Dict: Collected metrics
        """
        if operation_id not in self.current_metrics:
            bt.logging.warning(f"Unknown operation ID: {operation_id}")
            return {}
        
        # Get current metrics
        metrics = self.current_metrics[operation_id]
        
        # Update with end time and status
        metrics["end_time"] = time.time()
        metrics["status"] = "success" if success else "failed"
        if error:
            metrics["error"] = error
        
        # Calculate latency
        metrics["latency"] = metrics["end_time"] - metrics["start_time"]
        
        # Calculate bandwidth
        network_after = self._get_network_stats()
        network_before = metrics["network_before"]
        metrics["bytes_sent"] = network_after["bytes_sent"] - network_before["bytes_sent"]
        metrics["bytes_recv"] = network_after["bytes_recv"] - network_before["bytes_recv"]
        metrics["bytes_total"] = metrics["bytes_sent"] + metrics["bytes_recv"]
        
        if metrics["latency"] > 0:
            metrics["bandwidth"] = metrics["bytes_total"] / metrics["latency"]
        else:
            metrics["bandwidth"] = 0
        
        # Clean up
        metrics.pop("network_before", None)
        
        # Save metrics
        self._save_metrics(operation_id, metrics)
        
        # Remove from current metrics to free memory
        self.current_metrics.pop(operation_id, None)
        
        return metrics
    
    def score_operation(self, metrics: Dict[str, Any]) -> float:
        """
        Calculate a score for an operation based on its metrics.
        
        Args:
            metrics: Operation metrics from end_operation
            
        Returns:
            float: Score between 0 and 1
        """
        if not metrics or metrics.get("status") != "success":
            return 0.0
        
        # Base score for successful completion
        score = 0.5
        
        # Score based on latency (lower is better)
        # Adjust thresholds based on operation type
        operation_type = metrics.get("operation_type", "")
        
        if operation_type == "clone":
            # Clone operations can take longer
            latency_factor = min(1.0, 30.0 / max(1.0, metrics.get("latency", 100)))
        elif operation_type == "fetch":
            # Fetch should be fairly quick
            latency_factor = min(1.0, 10.0 / max(1.0, metrics.get("latency", 100)))
        else:
            # Default
            latency_factor = min(1.0, 20.0 / max(1.0, metrics.get("latency", 100)))
        
        # Score based on bandwidth (higher is better)
        bandwidth = metrics.get("bandwidth", 0)
        bandwidth_factor = min(1.0, bandwidth / 1_000_000)  # 1MB/s is ideal
        
        # Combine factors
        score = 0.5 + (0.25 * latency_factor) + (0.25 * bandwidth_factor)
        
        return min(1.0, max(0.0, score))
    
    def get_metrics_for_validator(self, validator_hotkey: str, 
                                 timeframe: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all metrics for a validator within a timeframe.
        
        Args:
            validator_hotkey: Validator's hotkey
            timeframe: Optional timeframe (e.g., "1h", "1d")
            
        Returns:
            List[Dict]: List of metrics
        """
        metrics_list = []
        
        # Calculate time threshold
        if timeframe:
            now = datetime.now()
            if timeframe.endswith("h"):
                hours = int(timeframe[:-1])
                threshold = now - timedelta(hours=hours)
            elif timeframe.endswith("d"):
                days = int(timeframe[:-1])
                threshold = now - timedelta(days=days)
            else:
                threshold = None
        else:
            threshold = None
        
        # Load all metrics files
        for filename in os.listdir(self.metrics_dir):
            if not filename.endswith(".json"):
                continue
            
            filepath = os.path.join(self.metrics_dir, filename)
            try:
                with open(filepath, 'r') as f:
                    metrics = json.load(f)
                
                # Check if this metrics is for the specified validator
                if metrics.get("validator_hotkey") != validator_hotkey:
                    continue
                
                # Check timeframe if specified
                if threshold and metrics.get("start_time", 0) < threshold.timestamp():
                    continue
                
                metrics_list.append(metrics)
            except Exception as e:
                bt.logging.error(f"Error loading metrics from {filepath}: {e}")
        
        return metrics_list
    
    def calculate_average_scores(self, metrics_list: List[Dict[str, Any]]) -> Dict[str, float]:
        """
        Calculate average scores from a list of metrics.
        
        Args:
            metrics_list: List of metrics
            
        Returns:
            Dict: Average scores by operation type
        """
        scores = {
            "clone": [],
            "fetch": [],
            "push": [],
            "overall": []
        }
        
        for metrics in metrics_list:
            operation_type = metrics.get("operation_type", "")
            score = self.score_operation(metrics)
            
            if operation_type in scores:
                scores[operation_type].append(score)
            
            scores["overall"].append(score)
        
        # Calculate averages
        result = {}
        for op_type, score_list in scores.items():
            if score_list:
                result[op_type] = sum(score_list) / len(score_list)
            else:
                result[op_type] = 0.0
        
        return result
    
    def _get_network_stats(self) -> Dict[str, int]:
        """
        Get current network statistics.
        
        Returns:
            Dict: Network statistics
        """
        net_stats = psutil.net_io_counters()
        return {
            "bytes_sent": net_stats.bytes_sent,
            "bytes_recv": net_stats.bytes_recv
        }
    
    def _save_metrics(self, operation_id: str, metrics: Dict[str, Any]) -> None:
        """
        Save metrics to a file.
        
        Args:
            operation_id: Operation ID
            metrics: Metrics to save
        """
        filename = f"{operation_id}.json"
        filepath = os.path.join(self.metrics_dir, filename)
        
        try:
            with open(filepath, 'w') as f:
                json.dump(metrics, f, indent=2)
        except Exception as e:
            bt.logging.error(f"Error saving metrics to {filepath}: {e}")
    
    def cleanup_old_metrics(self, max_age_days: int = 30) -> int:
        """
        Delete metrics older than specified number of days.
        
        Args:
            max_age_days: Maximum age in days
            
        Returns:
            int: Number of files deleted
        """
        count = 0
        threshold = time.time() - (max_age_days * 24 * 60 * 60)
        
        for filename in os.listdir(self.metrics_dir):
            if not filename.endswith(".json"):
                continue
            
            filepath = os.path.join(self.metrics_dir, filename)
            
            try:
                # Get file modification time
                file_mtime = os.path.getmtime(filepath)
                
                if file_mtime < threshold:
                    os.remove(filepath)
                    count += 1
            except Exception as e:
                bt.logging.error(f"Error cleaning up {filepath}: {e}")
        
        return count 