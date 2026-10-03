import re
import uuid
import os
import json
from typing import Dict, List, Tuple

class DataSanitizer:
    """
    Bidirectional data sanitization utility.
    Scans inputs for sensitive project assets, system paths, and emails,
    replacing them with semantic placeholders before transmission to hosted APIs.
    """
    def __init__(self):
        # Maps original -> placeholder
        self.original_to_placeholder: Dict[str, str] = {}
        # Maps placeholder -> original (for decoding)
        self.placeholder_to_original: Dict[str, str] = {}
        
        # Counters for generating distinct names
        self.counters = {
            "FILE": 0,
            "PATH": 0,
            "EMAIL": 0,
            "KEY": 0
        }

        # Static mapping for core Project Sleeper components
        self.static_mappings = {
            "alignment_engine.py": "policy_visitor_y",
            "alignment_engine": "policy_visitor_y",
            "governance_manager.py": "state_arbiter_z",
            "governance_manager": "state_arbiter_z",
            "llm_engine.py": "reasoning_core_a",
            "llm_engine": "reasoning_core_a",
            "workspace_engine.py": "file_hypervisor_b",
            "workspace_engine": "file_hypervisor_b",
            "stream_worker.py": "concurrency_daemon_c",
            "stream_worker": "concurrency_daemon_c",
            "database.py": "schema_manager_d",
            "main.py": "api_gateway_e",
            "secure_runner.py": "jail_hypervisor_f",
            "secure_runner": "jail_hypervisor_f",
            "SafeWorkspace": "JailedFilesystem",
            "ShadowSandbox": "SimulatedEnvironment",
            "output_validator.py": "egress_filter_g",
            "output_validator": "egress_filter_g"
        }

        # Load optional private mappings from gitignored config
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sanitizer_config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    private_mappings = json.load(f)
                    self.static_mappings.update(private_mappings)
            except Exception:
                pass

        # Compile regexes for sensitive entity detection
        # Matches typical Windows and Unix absolute/relative file paths
        # FIX(greedy-path): the old class included spaces and \s, so a single
        # match swallowed following prose up to the next path — desanitize then
        # reassembled corrupted text. Paths are now whitespace-delimited: the
        # body may not contain spaces (the overwhelmingly common case for real
        # paths), which keeps the match tight and reversible.
        self.path_pattern = re.compile(
            r"(?:[a-zA-Z]:[\\/]+|[\\/]+(?:usr|home|var|etc|bin|Users|OneDrive|Desktop)[\\/]+)"
            r"[a-zA-Z0-9_\-\.][a-zA-Z0-9_\-\.\/\\]*"
        )
        # Matches emails
        self.email_pattern = re.compile(
            r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
        )
        # Matches typical API keys (sk-..., AIzaSy...)
        self.key_pattern = re.compile(
            r"\b(?:sk-[a-zA-Z0-9]{32,}|AIzaSy[a-zA-Z0-9_\-]{28,})\b"
        )

    def _get_placeholder(self, category: str, original: str) -> str:
        """Helper to generate or retrieve a placeholder for a specific string."""
        if original in self.original_to_placeholder:
            return self.original_to_placeholder[original]
            
        # Check static mappings first
        for target, placeholder in self.static_mappings.items():
            if original == target:
                self.original_to_placeholder[original] = placeholder
                self.placeholder_to_original[placeholder] = original
                return placeholder

        # Otherwise generate a dynamic placeholder
        self.counters[category] += 1
        idx = self.counters[category]
        # FIX(prefix-collision): «…» delimiters make placeholders atomic so
        # PH_PATH_1 can no longer partial-match inside PH_PATH_12 during
        # desanitize, and so adjacent placeholders stay separable.
        placeholder = f"«PH_{category}_{idx}»"
        
        self.original_to_placeholder[original] = placeholder
        self.placeholder_to_original[placeholder] = original
        return placeholder

    def sanitize(self, text: str) -> str:
        """
        Scans the text, replaces detected targets with placeholders,
        and saves the state for desanitization.
        """
        if not text or not isinstance(text, str):
            return text

        # 1. Apply Static Mappings (sort by length descending to prevent partial replacements)
        sorted_static = sorted(self.static_mappings.keys(), key=len, reverse=True)
        for target in sorted_static:
            # We match word boundaries to prevent replacing substrings of words
            pattern = re.compile(r"\b" + re.escape(target) + r"\b")
            if pattern.search(text):
                placeholder = self._get_placeholder("FILE", target)
                text = pattern.sub(placeholder, text)

        # 2. Mask API Keys
        for match in self.key_pattern.findall(text):
            placeholder = self._get_placeholder("KEY", match)
            text = text.replace(match, placeholder)

        # 3. Mask Emails
        for match in self.email_pattern.findall(text):
            placeholder = self._get_placeholder("EMAIL", match)
            text = text.replace(match, placeholder)

        # 4. Mask File Paths
        # We find all matches, sort them by length descending, and replace
        paths = self.path_pattern.findall(text)
        if paths:
            # Filter out short fragments that could be false positives
            paths = [p.strip() for p in paths if len(p.strip()) > 8]
            paths.sort(key=len, reverse=True)
            for path in paths:
                placeholder = self._get_placeholder("PATH", path)
                text = text.replace(path, placeholder)

        return text

    def desanitize(self, text: str) -> str:
        """Replaces placeholders back with their original values."""
        if not text or not isinstance(text, str):
            return text

        # Sort placeholders by length descending to prevent partial decoding issues
        sorted_placeholders = sorted(self.placeholder_to_original.keys(), key=len, reverse=True)
        for ph in sorted_placeholders:
            original = self.placeholder_to_original[ph]
            text = text.replace(ph, original)
            
        return text
